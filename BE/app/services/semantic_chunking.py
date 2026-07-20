"""Semantic chunking — a CV becomes retrieval units that follow its MEANING.

Why chunks at all
-----------------
The engine used to embed each CV as ONE vector over composed text truncated at
8,000 chars. Two failure modes follow directly:
  * a long or multi-role CV blurs into an average of itself — the JD-relevant
    third of the document is diluted by the other two thirds, and the candidate
    ranks below people with thinner but more monotone CVs;
  * everything past the truncation point simply does not exist for retrieval.

Why STRUCTURE-aware (and not fixed windows or embedding-drift splitting)
------------------------------------------------------------------------
A CV already has semantic units: each experience entry is one coherent claim
("this role, these years, this work"), the skills block is one, the summary is
one. Splitting on those boundaries yields chunks that are each ABOUT one thing —
which is the entire point of semantic chunking — without paying an embedding
call per boundary decision (drift-based splitters embed every sentence first).
Fixed token windows would cut entries mid-sentence and glue unrelated roles
together, which is how retrieval false-positives are born: a window spanning
role A's employer and role B's skills reads like a person who used those skills
at that employer.

Two chunk families come out:
  * profile chunks — from the LLM-extracted profile (identity/skills/experience/
    education/summary). Clean, deduplicated, high-precision.
  * document chunks (kind="document") — from the RAW parsed markdown, packed to
    section boundaries. These exist because extraction is lossy: a skill the LLM
    dropped is still in the raw text, and these chunks carry it into (a) the
    embedding index, (b) the BM25 lexical index, and (c) the scorer's free-text
    evidence tier. They are the insurance policy against extraction misses.

Deterministic by construction: same input → same chunks. No LLM, no randomness.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

CHUNKING_VERSION = "cv-chunks-1"

# Packing bounds for document chunks. Targets, not hard guarantees: a single
# oversized paragraph is split at sentence boundaries; a tiny trailing section
# is merged backwards rather than shipped as a 40-char chunk.
_CHUNK_TARGET = 900
_CHUNK_MAX = 1400
# Runt threshold: below this a fragment merges into its predecessor. Kept SMALL
# deliberately — a short section ("KENNTNISSE\nSAP HCM, Payroll") is a complete
# semantic unit that must stay its own chunk; only genuine debris (a stray
# continuation line) should merge.
_CHUNK_MIN = 40

_MAX_DOC_CHUNKS = 16
_MAX_TOTAL_CHUNKS = 28

# A line that LOOKS like a section heading: short, no terminal punctuation,
# either ALL-CAPS-ish, markdown-style (#, ##), or a known CV section word.
_SECTION_WORDS = (
    "experience", "employment", "work history", "berufserfahrung", "erfahrung",
    "skills", "kenntnisse", "fähigkeiten", "kompetenzen", "education",
    "ausbildung", "studium", "certifications", "zertifikate", "zertifizierungen",
    "projects", "projekte", "summary", "profil", "profile", "languages",
    "sprachen", "publications", "awards",
)
_HEADING_RE = re.compile(r"^(#{1,4}\s+\S|[A-ZÄÖÜ0-9][A-ZÄÖÜ0-9 \-/&]{2,40}$)")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9])")


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _looks_like_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 60 or line.endswith((".", ",", ";", ":")):
        return False
    low = line.lower().lstrip("# ").strip()
    if any(low.startswith(w) for w in _SECTION_WORDS):
        return True
    return bool(_HEADING_RE.match(line))


def _split_sections(markdown: str) -> List[str]:
    """Raw text → semantic sections (heading- and blank-line-bounded)."""
    lines = (markdown or "").splitlines()
    sections: List[List[str]] = [[]]
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            continue
        # A heading, or a gap of 2+ blank lines, starts a new section.
        if _looks_like_heading(line) or blank_run >= 2:
            if sections[-1]:
                sections.append([])
        blank_run = 0
        sections[-1].append(line.rstrip())
    return ["\n".join(s).strip() for s in sections if s and "\n".join(s).strip()]


def _pack(sections: List[str]) -> List[str]:
    """One section → one chunk; oversized sections split at sentences.

    Deliberately NO greedy merging of neighbouring sections: document chunks
    feed the scorer's co-occurrence evidence rule, and a chunk spanning the
    skills section plus an unrelated role would let a multi-word requirement
    be "evidenced" by terms scattered across different parts of the CV — a
    retrieval convenience becoming a scoring false positive. Section purity is
    the property; unevenly sized chunks are the acceptable price. Only a runt
    fragment (< _CHUNK_MIN, typically a stray continuation line) merges into
    its predecessor.
    """
    chunks: List[str] = []
    for sec in sections:
        if len(sec) > _CHUNK_MAX:
            # Oversized section — split at sentences, repack within the section.
            parts = _SENTENCE_SPLIT_RE.split(sec)
            piece = ""
            for p in parts:
                if piece and len(piece) + len(p) + 1 > _CHUNK_TARGET:
                    chunks.append(piece.strip())
                    piece = p
                else:
                    piece = f"{piece} {p}".strip()
            if piece.strip():
                chunks.append(piece.strip())
        elif len(sec) < _CHUNK_MIN and chunks:
            chunks[-1] = f"{chunks[-1]}\n{sec}"
        else:
            chunks.append(sec)
    return chunks


def chunk_markdown_sections(markdown: str, limit: int = _MAX_DOC_CHUNKS) -> List[str]:
    """Section-bounded chunks of the RAW document text (kind='document' source).

    Public because the matcher also calls it lazily for legacy docs ingested
    before chunking existed — identical output either way.
    """
    if not (markdown or "").strip():
        return []
    return _pack(_split_sections(markdown))[:limit]


def chunk_cv(profile: Dict[str, Any], markdown: str) -> List[Dict[str, str]]:
    """All retrieval chunks for one CV: profile chunks + document chunks.

    Returns [{"kind": ..., "text": ...}]. Order is stable and meaningful —
    profile chunks first (highest precision), document chunks after.
    """
    profile = profile or {}
    out: List[Dict[str, str]] = []
    seen: set = set()

    def add(kind: str, text: str) -> None:
        text = (text or "").strip()
        if not text or len(text) < 15:
            return
        key = _norm_ws(text).lower()[:300]
        if key in seen:
            return
        seen.add(key)
        out.append({"kind": kind, "text": text[:_CHUNK_MAX]})

    # identity: what this person IS
    identity = ". ".join(filter(None, [
        str(profile.get("currentTitle") or ""),
        str(profile.get("headline") or ""),
        ", ".join(str(t) for t in (profile.get("titles") or [])[:8]),
    ]))
    add("identity", identity)

    # skills: the explicit claim list
    skills = [str(s) for s in (profile.get("skills") or []) if s]
    for i in range(0, len(skills), 40):
        add("skills", ", ".join(skills[i:i + 40]))

    # one chunk per experience entry — the core semantic units of a CV
    for e in (profile.get("experience") or [])[:12]:
        e = e or {}
        block = " — ".join(filter(None, [
            str(e.get("title") or ""), str(e.get("company") or ""),
            str(e.get("summary") or ""),
        ])).strip(" —")
        add("experience", block)

    edu = "; ".join(str(x) for x in (profile.get("education") or [])[:6] if x)
    certs = "; ".join(str(x) for x in (profile.get("certifications") or [])[:8] if x)
    add("education", ". ".join(filter(None, [edu, certs])))

    for key in ("summary", "about"):
        add("summary", str(profile.get(key) or ""))

    # document chunks from the raw text — the extraction-miss insurance
    for text in chunk_markdown_sections(markdown):
        add("document", text)

    return out[:_MAX_TOTAL_CHUNKS]
