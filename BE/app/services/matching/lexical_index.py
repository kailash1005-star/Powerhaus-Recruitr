"""Lexical retrieval — BM25 keyword channel for the CV matcher.

Why this exists
---------------
Embedding retrieval ranks by how a WHOLE text reads, and that is exactly its
blind spot: a CV that literally names every must-have skill can still rank
below the semantic top-K because the rest of the document reads differently
(career changer, multilingual CV, terse formatting). Below the retrieval cap,
a candidate is never scored, never judged, never audited — an invisible false
negative that no downstream QA can catch, because QA only sees what retrieval
surfaced. The lexical channel makes "the CV contains the words the JD used"
sufficient, on its own, to get a candidate in front of the scorer.

Why BM25 and not TF-IDF/substring
---------------------------------
BM25's IDF makes rare terms (SAP, Entgeltabrechnung, FS-CD) dominate common
ones (experience, team) with no stopword list to maintain — important here
because the corpus is German/English mixed. Term-frequency saturation (k1)
stops a keyword-stuffed CV from buying rank linearly with repetition — the
same guardrail the deterministic scorer applies to prose, applied at retrieval.

Implementation notes
--------------------
Pure Python, no dependency, deterministic. The index is built per match run
over the embedded corpus — the same O(N) full scan the Mongo brute-force
vector store already does, so this adds no new scale ceiling. When the corpus
outgrows brute force, both channels move server-side together (Atlas Search
gives BM25 + kNN natively; Pinecone adds sparse vectors).

Fusion is Reciprocal Rank Fusion (RRF): rank-based, so the two channels'
incomparable score scales (cosine ∈ [-1,1] vs unbounded BM25) never need
calibrating against each other, and one channel can never drown the other by
having numerically bigger scores. k=60 is the literature-standard damping.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Sequence, Tuple

BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60

# Tokens keep +/#/. so C++, C#, React.js survive; 1-char tokens are dropped —
# the deterministic scorer learned that lesson the hard way ("R" matched every
# German compound), and retrieval does not need to relearn it.
_TOKEN_RE = re.compile(r"[\w+#.]+", flags=re.UNICODE)


def tokenize(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 1]


class BM25Index:
    """BM25 over (doc_id, text) pairs. Build once per run, query many."""

    def __init__(self, docs: Sequence[Tuple[str, str]]):
        self._ids: List[str] = []
        self._tf: List[Counter] = []
        self._len: List[int] = []
        df: Counter = Counter()
        for doc_id, text in docs:
            toks = tokenize(text)
            tf = Counter(toks)
            self._ids.append(doc_id)
            self._tf.append(tf)
            self._len.append(len(toks))
            for term in tf:
                df[term] += 1
        n = max(1, len(self._ids))
        self._avg_len = (sum(self._len) / n) if self._ids else 0.0
        # BM25+-style floor at 0 keeps very common terms from going negative.
        self._idf: Dict[str, float] = {
            term: max(0.0, math.log((n - d + 0.5) / (d + 0.5) + 1.0))
            for term, d in df.items()
        }

    def query(self, terms: Sequence[str], top_k: int) -> List[Tuple[str, float]]:
        """Rank docs for the (already tokenized/expanded) query terms.

        Only docs with a positive score are returned — BM25 zero means "shares
        no query term", which must not count as retrieved.
        """
        q = Counter(t for t in terms if t)
        if not q or not self._ids:
            return []
        scores = [0.0] * len(self._ids)
        for term, q_weight in q.items():
            idf = self._idf.get(term)
            if not idf:
                continue
            for i, tf in enumerate(self._tf):
                f = tf.get(term)
                if not f:
                    continue
                denom = f + BM25_K1 * (1 - BM25_B + BM25_B * (self._len[i] / self._avg_len))
                scores[i] += q_weight * idf * (f * (BM25_K1 + 1)) / denom
        ranked = sorted(
            ((self._ids[i], s) for i, s in enumerate(scores) if s > 0),
            key=lambda x: (-x[1], x[0]),
        )
        return ranked[:top_k]


def build_query_terms(requirements: Dict, skill_variants) -> List[str]:
    """The lexical query for a JD: title + skills, must-haves counted double.

    Doubling a must-have term doubles its BM25 contribution — the lexical
    channel should chase what the JD REQUIRES harder than what it merely
    mentions. `skill_variants` is matching_service._skill_variants, injected to
    keep the alias logic ("Payroll (PY)" → PY) in exactly one place.
    """
    terms: List[str] = []
    for skill in (requirements.get("mustHaveSkills") or []):
        # Dedupe across variants first — "Payroll (PY)" expands to variants that
        # share tokens, and doubling each variant separately would weight a skill
        # by how many aliases it has rather than by being required.
        toks = sorted({t for v in skill_variants(skill) for t in tokenize(v)})
        terms.extend(toks * 2)
    for skill in (requirements.get("niceToHaveSkills") or []):
        toks = sorted({t for v in skill_variants(skill) for t in tokenize(v)})
        terms.extend(toks)
    terms.extend(tokenize(requirements.get("title") or ""))
    terms.extend(tokenize(requirements.get("seniority") or ""))
    return terms


def rrf_fuse(
    rankings: Dict[str, List[Tuple[str, float]]], k: int = RRF_K
) -> List[Tuple[str, float, Dict[str, int]]]:
    """Fuse channel rankings with Reciprocal Rank Fusion.

    Returns [(doc_id, rrf_score, {channel: rank})] sorted best-first; `rank` is
    1-based within its channel. A doc found by both channels accumulates both
    reciprocal terms — agreement is rewarded, but a single-channel hit still
    surfaces (that single-channel hit IS the false negative being rescued).
    """
    fused: Dict[str, float] = {}
    ranks: Dict[str, Dict[str, int]] = {}
    for channel, ranking in rankings.items():
        for pos, (doc_id, _score) in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + pos)
            ranks.setdefault(doc_id, {})[channel] = pos
    ordered = sorted(fused.items(), key=lambda x: (-x[1], x[0]))
    return [(doc_id, score, ranks[doc_id]) for doc_id, score in ordered]
