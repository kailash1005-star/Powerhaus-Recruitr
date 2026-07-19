"""
LLM Extraction & Judge Service.

Three narrow LLM jobs (the only places an LLM is used in the matching engine):
  1. extract_cv_fields(markdown)  → validated CvFields + contact (cheap model)
  2. parse_jd(markdown)           → validated JDRequirements (cheap model)
  3. judge_candidates(jd, cands)  → anchored-rubric fit score + grounded reasons

Contracts, not vibes:
  * Every call uses OpenAI Structured Outputs (`json_schema`, strict) so the
    model cannot return a shape we didn't ask for, and the parsed JSON is then
    validated through a Pydantic model at this boundary — nothing downstream
    ever sees an unvalidated dict.
  * Extraction FAILS LOUD. The old contract returned `{}` on hard failure, which
    silently collapsed the whole match score to raw embedding similarity while
    the run reported success. `parse_jd`/`extract_cv_fields` now raise
    ``ExtractionError`` after retries; callers surface it as a failed run.
  * The judge is best-effort by design (a lost judge call degrades to the
    deterministic score, visibly marked), so ``judge_candidates`` may raise and
    callers catch it per-candidate.
  * Client hardening: explicit timeout (settings.OPENAI_TIMEOUT_SECS — the SDK
    default is 600s), SDK retries off (we back off ourselves), `seed` +
    `system_fingerprint` recorded for reproducibility, `max_tokens` capped.

Bias control: the judge/reasoning prompt is given ONLY skills/experience,
never name/contact/demographics.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings

logger = logging.getLogger(__name__)

_client = None

# The last system_fingerprint seen per model — recorded on match runs so a
# score that shifts between identical runs is attributable to a backend change
# (OpenAI documents seed as best-effort; the fingerprint names the backend).
_last_fingerprint: Dict[str, str] = {}


class ExtractionError(RuntimeError):
    """An LLM extraction failed after retries. Callers must surface this as a
    failed run — never swallow it into a default value."""


def _get_client():
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise ExtractionError("OPENAI_API_KEY is not set — required for LLM extraction.")
        from openai import OpenAI
        # max_retries=0: retries are hand-rolled below with backoff, so the SDK
        # doubling them up just multiplies worst-case latency.
        _client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.OPENAI_TIMEOUT_SECS,
            max_retries=0,
        )
    return _client


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def _chat_json(
    model: str,
    system: str,
    user: str,
    *,
    schema_name: str,
    schema: Dict[str, Any],
    max_tokens: int,
    operation: str = "extract",
    retries: int = 3,
) -> Dict[str, Any]:
    """One structured-output chat call → parsed JSON dict.

    Raises ExtractionError after ``retries`` failed attempts. A refusal or a
    length-truncated response is a FAILURE, not something to json.loads anyway.
    """
    from app.services import cost_service

    client = _get_client()
    last_err: Optional[str] = None
    for attempt in range(1, retries + 1):
        try:
            completion = client.chat.completions.create(
                model=model,
                temperature=0,
                seed=settings.OPENAI_SEED,
                max_tokens=max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": True, "schema": schema},
                },
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            cost_service.record_chat(completion, model=model, operation=operation)
            fp = getattr(completion, "system_fingerprint", None)
            if fp:
                _last_fingerprint[model] = str(fp)

            choice = completion.choices[0]
            refusal = getattr(choice.message, "refusal", None)
            if refusal:
                raise ExtractionError(f"model refused: {str(refusal)[:200]}")
            if choice.finish_reason == "length":
                raise ExtractionError(
                    f"response truncated at max_tokens={max_tokens} — treat as failure, "
                    "a partial JSON is not a smaller answer.")
            raw = _strip_fences(choice.message.content or "")
            return json.loads(raw)
        except ExtractionError as e:
            last_err = str(e)
            logger.warning("[LLM] %s attempt %d/%d: %s", schema_name, attempt, retries, e)
        except json.JSONDecodeError as e:
            last_err = f"JSON parse failed: {e}"
            logger.warning("[LLM] %s attempt %d/%d: %s", schema_name, attempt, retries, last_err)
        except Exception as e:  # noqa: BLE001 — API/network errors, backoff then retry
            last_err = str(e)
            logger.warning("[LLM] %s attempt %d/%d failed: %s", schema_name, attempt, retries, e)
            time.sleep(min(2 ** attempt, 6))
    raise ExtractionError(f"{schema_name} failed after {retries} attempts: {last_err}")


# ── Boundary validation models ───────────────────────────────────────────────
# Strict Structured Outputs guarantees the SHAPE; these models guarantee the
# TYPES downstream code relies on (e.g. minYears is a number the scorer can
# compare with >=, never the string "5+ Jahre").

def _clean_str_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    out: List[str] = []
    for item in v or []:
        if item is None:
            continue
        s = str(item).strip()
        if s:
            out.append(s)
    return out


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _coerce_years(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if 0 <= float(v) <= 60 else None
    m = _NUM_RE.search(str(v))
    if not m:
        return None
    years = float(m.group().replace(",", "."))
    return years if 0 <= years <= 60 else None


class JDRequirements(BaseModel):
    """What the scorer is allowed to believe a JD asked for."""
    model_config = ConfigDict(extra="ignore")

    title: Optional[str] = None
    mustHaveSkills: List[str] = Field(default_factory=list)
    niceToHaveSkills: List[str] = Field(default_factory=list)
    minYears: Optional[float] = None
    location: Optional[str] = None
    seniority: Optional[str] = None
    responsibilities: List[str] = Field(default_factory=list)

    @field_validator("mustHaveSkills", "niceToHaveSkills", "responsibilities", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> List[str]:
        return _clean_str_list(v)

    @field_validator("minYears", mode="before")
    @classmethod
    def _years(cls, v: Any) -> Optional[float]:
        return _coerce_years(v)


class CvExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")
    company: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    summary: Optional[str] = None


class CvFields(BaseModel):
    """Validated CV extraction — profile + contact in one flat record."""
    model_config = ConfigDict(extra="ignore")

    fullName: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin: Optional[str] = None
    location: Optional[str] = None
    totalYears: Optional[float] = None
    currentTitle: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    titles: List[str] = Field(default_factory=list)
    education: List[str] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    experience: List[CvExperience] = Field(default_factory=list)

    @field_validator("skills", "titles", "education", "certifications", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> List[str]:
        return _clean_str_list(v)

    @field_validator("totalYears", mode="before")
    @classmethod
    def _years(cls, v: Any) -> Optional[float]:
        return _coerce_years(v)


class JudgeItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    fitScore: float = Field(ge=0, le=100)
    verdict: str = ""
    reasons: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)

    @field_validator("fitScore", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> float:
        try:
            return max(0.0, min(100.0, float(v)))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("reasons", "gaps", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> List[str]:
        return _clean_str_list(v)


class JudgeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    candidates: List[JudgeItem] = Field(default_factory=list)


# ── Strict JSON Schemas (what the model is FORCED to emit) ───────────────────
# Strict mode rules: additionalProperties false everywhere, every property
# required, optionality expressed as ["<type>", "null"].

def _nullable(t: str) -> Dict[str, Any]:
    return {"type": [t, "null"]}


def _str_array() -> Dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


_JD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": _nullable("string"),
        "mustHaveSkills": _str_array(),
        "niceToHaveSkills": _str_array(),
        "minYears": _nullable("number"),
        "location": _nullable("string"),
        "seniority": _nullable("string"),
        "responsibilities": _str_array(),
    },
    "required": ["title", "mustHaveSkills", "niceToHaveSkills", "minYears",
                 "location", "seniority", "responsibilities"],
}

_CV_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fullName": _nullable("string"),
        "email": _nullable("string"),
        "phone": _nullable("string"),
        "linkedin": _nullable("string"),
        "location": _nullable("string"),
        "totalYears": _nullable("number"),
        "currentTitle": _nullable("string"),
        "skills": _str_array(),
        "titles": _str_array(),
        "education": _str_array(),
        "certifications": _str_array(),
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "company": _nullable("string"),
                    "title": _nullable("string"),
                    "start": _nullable("string"),
                    "end": _nullable("string"),
                    "summary": _nullable("string"),
                },
                "required": ["company", "title", "start", "end", "summary"],
            },
        },
    },
    "required": ["fullName", "email", "phone", "linkedin", "location", "totalYears",
                 "currentTitle", "skills", "titles", "education", "certifications",
                 "experience"],
}

_JUDGE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "fitScore": {"type": "number"},
                    "verdict": {"type": "string"},
                    "reasons": _str_array(),
                    "gaps": _str_array(),
                },
                "required": ["id", "fitScore", "verdict", "reasons", "gaps"],
            },
        },
    },
    "required": ["candidates"],
}


# ── Public async API ─────────────────────────────────────────────────────────
def extraction_version() -> str:
    return settings.EXTRACTION_MODEL


def reasoning_version() -> str:
    return settings.REASONING_MODEL


def last_system_fingerprint(model: Optional[str] = None) -> Optional[str]:
    """The backend fingerprint of the most recent call for `model` (or any)."""
    if model:
        return _last_fingerprint.get(model)
    return next(iter(_last_fingerprint.values()), None)


_CV_SYSTEM = (
    "You extract structured data from a candidate CV. Use null/empty when the "
    "document does not state a value. Do not invent skills."
)


def _extract_cv_sync(markdown: str) -> Dict[str, Any]:
    user = (
        "Extract the candidate's structured fields from this CV. `totalYears` is "
        "your best numeric estimate of total professional experience. `skills` are "
        "normalized skill names as the CV evidences them.\n\n"
        f"CV:\n{markdown[:12000]}"
    )
    data = _chat_json(
        settings.EXTRACTION_MODEL, _CV_SYSTEM, user,
        schema_name="cv_fields", schema=_CV_SCHEMA, max_tokens=2500,
    )
    fields = CvFields.model_validate(data)
    out = fields.model_dump()
    out["experience"] = [e for e in out["experience"] if any(v for v in e.values())]
    return out


_JD_SYSTEM = (
    "You extract structured hiring requirements from a job description. Use "
    "null/empty when the text does not state a value.\n"
    "`mustHaveSkills` are the skills the text presents as REQUIRED (required/"
    "must/essential phrasing, or clearly core to the role). `niceToHaveSkills` "
    "are explicitly optional/preferred/plus items. Keep each skill a short noun "
    "phrase in the language the JD uses; never pad either list with generic "
    "traits (teamwork, communication) unless the JD literally names them as "
    "requirements."
)


def _parse_jd_sync(markdown: str) -> Dict[str, Any]:
    user = f"Job description:\n{markdown[:12000]}"
    data = _chat_json(
        settings.EXTRACTION_MODEL, _JD_SYSTEM, user,
        schema_name="jd_requirements", schema=_JD_SCHEMA, max_tokens=1500,
    )
    return JDRequirements.model_validate(data).model_dump()


# ── Anchored-rubric judge ────────────────────────────────────────────────────
# The rubric is the answer to "why did the LLM score this person 45 and that
# person 85": every band has an explicit, evidence-based description, the model
# must name the evidence, and hard rules tie the bands to the deterministic
# scorer's must-have findings so prose can never contradict the checklist.

FIT_RUBRIC = """Score fitScore on this anchored rubric (pick the band whose description the EVIDENCE matches, then a number inside it):
  90-100 "Ready now": does this exact job today. Role, domain, and seniority all line up; EVERY must-have is evidenced (fully or via a named variant); years meet the bar.
  75-89  "Strong": same role family and domain. Minor deltas only — a tooling variant, adjacent seniority, or one must-have evidenced only partially.
  60-74  "Plausible": real overlap in core skills but at least one genuine gap — a wholly missing must-have, a domain switch, or clearly short experience.
  40-59  "Stretch": some transferable skills; would need significant ramp-up to do this role.
  0-39   "Not a fit": different profession, or most must-haves have no evidence.

Hard rules (they override your impression):
  * `missingMustHave` is the deterministic scorer's finding that NOTHING in the profile evidences those skills. It is authoritative. If it is non-empty, fitScore must be ≤ 74.
  * fitScore ≥ 90 requires every must-have FULLY evidenced (none missing, none partial).
  * `partialMustHave` entries are real evidence that partly satisfies a requirement. NEVER describe one as missing/lacking/absent — say what the candidate DOES have and how it falls short.
  * `gaps` may contain ONLY entries from `missingMustHave`. Do not invent gaps.
  * Every reason must cite concrete evidence given to you (a skill, a title, years, an experience entry). No generic praise, no filler.
  * verdict is the band name you chose ("Ready now", "Strong", "Plausible", "Stretch", "Not a fit")."""

_JUDGE_SYSTEM = (
    "You are an exacting recruitment assessor. Score how well each candidate "
    "fits the role, grounded ONLY in the skills/experience provided. Never "
    "reference or infer name, gender, age, nationality, photo, or contact "
    "details. Be strict: an unsupported high score wastes a recruiter's day; "
    "an unjustified low score hides a good hire. Justify every score from the "
    "evidence."
)


def _judge_sync(jd: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    user = (
        "Role requirements:\n"
        f"{json.dumps(jd, ensure_ascii=False)}\n\n"
        f"{FIT_RUBRIC}\n\n"
        "Candidates (anonymized; `missingMustHave`/`partialMustHave` are the "
        "deterministic scorer's findings for that candidate):\n"
        f"{json.dumps(candidates, ensure_ascii=False)}\n\n"
        "Return one entry per candidate, same `id`, with fitScore, verdict, "
        "2-4 `reasons`, and `gaps`."
    )
    data = _chat_json(
        settings.REASONING_MODEL, _JUDGE_SYSTEM, user,
        schema_name="judge_verdicts", schema=_JUDGE_SCHEMA,
        max_tokens=min(4096, 300 * max(1, len(candidates)) + 200),
        operation="judge",
    )
    return JudgeResponse.model_validate(data).model_dump()


async def extract_cv_fields(markdown: str) -> Dict[str, Any]:
    """Validated CV fields. Raises ExtractionError on hard failure — the caller
    marks that one CV failed; it must not silently score an empty profile."""
    return await asyncio.to_thread(_extract_cv_sync, markdown)


async def parse_jd(markdown: str) -> Dict[str, Any]:
    """Validated JD requirements. Raises ExtractionError on hard failure — the
    caller must fail the run visibly, never fall back to similarity-only."""
    return await asyncio.to_thread(_parse_jd_sync, markdown)


async def judge_candidates(jd: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Anchored-rubric judge for a batch of candidates. Raises on failure;
    callers degrade to the deterministic score (visibly) per candidate."""
    return await asyncio.to_thread(_judge_sync, jd, candidates)
