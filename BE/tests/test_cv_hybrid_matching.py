"""Evals for the CV-matching hardening (FC-34..38).

Each test class pins one closed loophole — these are regression fixtures for
REAL failure modes, not coverage filler:

  * TestSemanticChunking — a CV splits on its meaning boundaries (experience
    entries, sections), never mid-entry; raw-document chunks always exist so
    extraction misses stay recoverable.
  * TestBM25 — the lexical channel ranks by rare-term evidence, saturates on
    keyword stuffing, drops 1-char noise tokens ("R" must not retrieve
    Arbeitsrecht CVs).
  * TestRRFFusion — a candidate found by ONLY one channel is still retrieved;
    agreement outranks single-channel hits; deterministic ordering.
  * TestHybridRescuesLexicalOnlyCandidate — the headline false negative: a CV
    naming every must-have but ranked outside the semantic top-K is scored
    anyway. Under semantic-only retrieval it simply did not exist.
  * TestRawTextEvidenceTier — a skill present ONLY in the raw CV text (LLM
    extraction dropped it) is credited by the scorer AND quotable by the QA
    auditor. Before, that miss was unrecoverable by design.
  * TestLocationVerdictInScoring — "Frankfurt am Main" vs "Frankfurt an der
    Oder" was a perfect fuzzy match (partial_ratio 100); the deterministic
    verdict now speaks first. Wrong country ≈ 0.1, wrong region 0.6.

All offline: no OpenAI, no Mongo, no network.
"""
from __future__ import annotations

from typing import List, Tuple

import pytest

from app.services import lexical_index as lx
from app.services import semantic_chunking as sc
from app.services.matching_service import (
    SCORING_VERSION,
    _free_text_entries,
    _score_candidate,
    _skill_variants,
)
from app.services.match_qa_service import _evidence_corpus, verify_quote


# ── Semantic chunking ────────────────────────────────────────────────────────

PROFILE = {
    "currentTitle": "SAP HCM Consultant",
    "skills": ["SAP HCM", "Payroll (PY)", "PA", "German payroll law"],
    "experience": [
        {"title": "SAP HCM Consultant", "company": "Acme",
         "summary": "Schwerpunkte SAP HCM PA, PY, OM. Betreuung der Entgeltabrechnung."},
        {"title": "HR Analyst", "company": "Beta GmbH",
         "summary": "Reporting und Zeitwirtschaft (PT)."},
    ],
    "education": ["BSc Business Informatics"],
    "certifications": ["SAP Certified — HCM"],
}
MARKDOWN = (
    "BERUFSERFAHRUNG\n"
    "SAP HCM Consultant — Acme (2019-2024)\n"
    "Schwerpunkte SAP HCM PA, PY, OM. Betreuung der Entgeltabrechnung fuer 3000 Mitarbeiter.\n"
    "\n\n"
    "KENNTNISSE\n"
    "SAP HCM, Payroll, Lohnsteuerrecht, Sozialversicherungsrecht\n"
    "\n\n"
    "AUSBILDUNG\n"
    "BSc Wirtschaftsinformatik, TU Muenchen\n"
)


class TestSemanticChunking:
    def test_experience_entries_are_their_own_chunks(self):
        chunks = sc.chunk_cv(PROFILE, MARKDOWN)
        exp = [c for c in chunks if c["kind"] == "experience"]
        assert len(exp) == 2
        assert "Entgeltabrechnung" in exp[0]["text"]
        assert "Zeitwirtschaft" in exp[1]["text"]

    def test_document_chunks_always_present(self):
        chunks = sc.chunk_cv(PROFILE, MARKDOWN)
        doc = [c for c in chunks if c["kind"] == "document"]
        assert doc, "raw-document chunks are the extraction-miss insurance — must exist"
        joined = " ".join(c["text"] for c in doc)
        assert "Lohnsteuerrecht" in joined  # present in raw text, NOT in profile

    def test_sections_split_on_headings(self):
        sections = sc.chunk_markdown_sections(MARKDOWN)
        assert any("KENNTNISSE" in s for s in sections)
        # The skills section and the experience section are separate chunks.
        assert not any("BERUFSERFAHRUNG" in s and "AUSBILDUNG" in s for s in sections)

    def test_oversized_section_splits_at_sentences_under_cap(self):
        big = "HEADER\n" + " ".join(f"Sentence number {i} about payroll systems." for i in range(200))
        out = sc.chunk_markdown_sections(big)
        assert len(out) > 1
        assert all(len(c) <= sc._CHUNK_MAX for c in out)

    def test_deterministic(self):
        assert sc.chunk_cv(PROFILE, MARKDOWN) == sc.chunk_cv(PROFILE, MARKDOWN)

    def test_empty_inputs(self):
        assert sc.chunk_cv({}, "") == []
        assert sc.chunk_markdown_sections("") == []


# ── BM25 ─────────────────────────────────────────────────────────────────────

def _mk_index() -> lx.BM25Index:
    return lx.BM25Index([
        ("cv_payroll", "SAP HCM Entgeltabrechnung Payroll PY Lohnsteuer experience with teams"),
        ("cv_fico", "SAP FICO financial accounting controlling experience with teams"),
        ("cv_dev", "Python developer React experience with teams building software"),
        ("cv_stuffed", "payroll " * 60),  # keyword stuffing
        ("cv_arbeitsrecht", "Arbeitsrecht Personalrecht juristische Beratung"),
    ])


class TestBM25:
    def test_rare_terms_dominate(self):
        hits = _mk_index().query(lx.tokenize("SAP HCM Entgeltabrechnung"), top_k=5)
        assert hits[0][0] == "cv_payroll"

    def test_zero_score_docs_are_not_retrieved(self):
        hits = _mk_index().query(lx.tokenize("Entgeltabrechnung"), top_k=5)
        ids = [h[0] for h in hits]
        assert "cv_dev" not in ids and "cv_fico" not in ids

    def test_stuffing_saturates_not_dominates_mixed_query(self):
        # On a multi-term query the genuine CV (matching several rare terms)
        # must outrank the one repeating a single term 60 times.
        hits = _mk_index().query(lx.tokenize("SAP Payroll Lohnsteuer"), top_k=5)
        ids = [h[0] for h in hits]
        assert ids.index("cv_payroll") < ids.index("cv_stuffed")

    def test_one_char_tokens_dropped(self):
        # "R" must not tokenize into a query term at all (the Arbeitsrecht trap).
        assert lx.tokenize("R") == []
        assert "c++" in lx.tokenize("C++ developer")

    def test_query_terms_double_must_haves(self):
        reqs = {"mustHaveSkills": ["Payroll (PY)"], "niceToHaveSkills": ["SAP"], "title": "Consultant"}
        terms = lx.build_query_terms(reqs, _skill_variants)
        assert terms.count("payroll") == 2   # must-have doubled
        assert terms.count("py") == 2        # variant of a must-have doubled
        assert terms.count("sap") == 1       # nice-to-have single


# ── RRF fusion ───────────────────────────────────────────────────────────────

class TestRRFFusion:
    def test_single_channel_hit_is_retained(self):
        fused = lx.rrf_fuse({
            "semantic": [("a", 0.9), ("b", 0.8)],
            "lexical": [("c", 7.0)],
        })
        ids = [f[0] for f in fused]
        assert "c" in ids, "a lexical-only hit is the rescued false negative"

    def test_agreement_outranks_single_channel(self):
        fused = lx.rrf_fuse({
            "semantic": [("both", 0.9), ("sem_only", 0.8)],
            "lexical": [("both", 5.0), ("lex_only", 4.0)],
        })
        assert fused[0][0] == "both"

    def test_ranks_recorded_per_channel(self):
        fused = lx.rrf_fuse({"semantic": [("a", 0.9)], "lexical": [("a", 3.0), ("b", 2.0)]})
        by_id = {f[0]: f[2] for f in fused}
        assert by_id["a"] == {"semantic": 1, "lexical": 1}
        assert by_id["b"] == {"lexical": 2}

    def test_deterministic_tie_break(self):
        r = {"semantic": [("x", 0.5)], "lexical": [("y", 0.5)]}
        assert lx.rrf_fuse(r) == lx.rrf_fuse(r)


# ── The headline FN: lexical-only candidate gets scored ─────────────────────

class TestHybridRescuesLexicalOnlyCandidate:
    def test_candidate_outside_semantic_topk_is_still_scored(self):
        # Semantic top-2 misses cv3; lexical finds it via exact must-have terms.
        k = 2
        sem = [("cv1", 0.55), ("cv2", 0.51)]            # semantic top-k
        lex_index = lx.BM25Index([
            ("cv1", "generalist HR administration"),
            ("cv2", "HR business partner recruiting"),
            ("cv3", "SAP HCM PY Entgeltabrechnung specialist"),
        ])
        reqs = {"mustHaveSkills": ["SAP HCM", "Entgeltabrechnung"], "title": "Payroll"}
        lex = lex_index.query(lx.build_query_terms(reqs, _skill_variants), top_k=k)
        fused = lx.rrf_fuse({"semantic": sem, "lexical": lex})
        assert "cv3" in [f[0] for f in fused], (
            "the exact-keyword candidate must reach the scorer despite being "
            "outside the semantic top-k")


# ── Raw-text evidence tier ───────────────────────────────────────────────────

class TestRawTextEvidenceTier:
    # Extraction DROPPED the skill: not in skills/titles/experience summaries.
    profile = {
        "currentTitle": "HR Consultant",
        "skills": ["HR administration"],
        "experience": [{"title": "HR Consultant", "summary": "generalist support"}],
        # …but the raw CV text (document chunks) still carries it:
        "rawTextBlocks": [
            "KENNTNISSE\nSAP HCM, Entgeltabrechnung, Lohnsteuerrecht",
        ],
    }
    reqs = {"mustHaveSkills": ["Entgeltabrechnung"], "title": "Payroll Clerk"}

    def test_scorer_credits_skill_found_only_in_raw_text(self):
        score, _, gaps, breakdown = _score_candidate(self.reqs, self.profile, sim=0.3)
        assert gaps == [], "skill present in raw CV text must not be a gap"
        skills_ev = next(c["skills"] for c in breakdown["components"]
                         if c["key"] == "skillCoverage")
        assert skills_ev[0]["credit"] == 1.0
        assert skills_ev[0]["method"] == "profile-text"

    def test_qa_corpus_contains_raw_text_so_quotes_verify(self):
        corpus = _evidence_corpus(self.profile)
        assert verify_quote("SAP HCM, Entgeltabrechnung", corpus), (
            "QA quote-verification must see the raw text tier, or a rescue "
            "quote from the CV body could never verify")

    def test_free_text_entries_cap_and_order(self):
        p = dict(self.profile)
        p["rawTextBlocks"] = [f"block {i} " + "x" * 3000 for i in range(20)]
        entries = _free_text_entries(p)
        raw_entries = [e for e in entries if e.startswith("block")]
        assert len(raw_entries) == 8              # capped
        assert all(len(e) <= 2000 for e in entries)  # bounded
        # extracted evidence (experience) comes BEFORE raw text blocks
        assert entries[0].startswith("HR Consultant")

    def test_pipeline_profiles_without_rawtext_are_unaffected(self):
        p = {"currentTitle": "X", "skills": ["Y"],
             "experience": [{"title": "X", "summary": "did Y"}]}
        entries = _free_text_entries(p)
        assert entries == ["X — did Y"]


# ── Location verdict inside CV scoring ───────────────────────────────────────

class TestLocationVerdictInScoring:
    reqs = {"mustHaveSkills": ["SAP"], "location": "Frankfurt am Main, Germany"}
    base_profile = {"skills": ["SAP"], "currentTitle": "SAP Consultant"}

    def _loc_score(self, cand_loc: str) -> float:
        profile = {**self.base_profile, "location": cand_loc}
        _, subscores, _, _ = _score_candidate(self.reqs, profile, sim=0.4)
        return subscores["location"]

    def test_same_city_matches(self):
        assert self._loc_score("Frankfurt am Main, Germany") == 100.0

    def test_fuzzy_twin_city_is_no_longer_a_perfect_match(self):
        # partial_ratio("frankfurt a...") used to hand this 1.0.
        s = self._loc_score("Frankfurt an der Oder, Germany")
        assert s < 100.0

    def test_wrong_country_is_heavily_penalised(self):
        assert self._loc_score("Bengaluru, Karnataka, India") == 10.0

    def test_right_country_other_region_soft(self):
        assert self._loc_score("Hamburg, Germany") == 60.0

    def test_missing_location_unchanged(self):
        assert self._loc_score("") == 60.0

    def test_scoring_version_bumped(self):
        assert SCORING_VERSION == "match-scoring-7"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
