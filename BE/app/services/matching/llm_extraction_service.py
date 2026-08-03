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


# A tokens-per-MINUTE ceiling needs a wait measured against that same minute.
# The generic `2 ** attempt` backoff below tops out at 6s, so all three retries
# land inside the still-saturated window and are guaranteed to fail — confirmed
# live 2026-08-03: a 50-candidate QA audit burned its whole retry budget in ~6s
# against a 429 and degraded the run to un-audited.
_RATE_LIMIT_WAIT_SECS = 60.0
_RATE_LIMIT_WAIT_CAP = 90.0


def _rate_limit_wait(exc: Exception) -> Optional[float]:
    """Seconds to wait before retrying a RATE-LIMITED call, or None when `exc`
    isn't a rate-limit error (so the caller keeps its normal short backoff).

    Prefers the provider's own `retry-after` hint when present; falls back to a
    full minute, which is the window a per-minute budget actually resets over.
    Capped so a bogus header can't park a worker thread indefinitely.
    """
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None)
    text = str(exc).lower()
    if status != 429 and "429" not in text and "rate limit" not in text:
        return None
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None and hasattr(headers, "get"):
        for key, divisor in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
            raw = headers.get(key)
            if raw in (None, ""):
                continue
            try:
                return min(max(float(raw) / divisor, 0.0), _RATE_LIMIT_WAIT_CAP)
            except (TypeError, ValueError):
                continue
    return _RATE_LIMIT_WAIT_SECS


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
    from app.services.operations import cost_service

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
            # A rate limit needs to outwait the provider's window, not the
            # generic ≤6s network backoff (see `_rate_limit_wait`). Skip the
            # sleep entirely on the final attempt — nothing follows it.
            if attempt < retries:
                time.sleep(_rate_limit_wait(e) or min(2 ** attempt, 6))
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


ROLE_FAMILIES = ("commercial", "delivery", "engineering", "management", "other")


class JDRequirements(BaseModel):
    """What the scorer is allowed to believe a JD asked for.

    ``title`` is the POSTING title and is for display only. It must never be used
    as a target for candidate titles: a posting titled "SAP Retail Consultant"
    can describe a salesperson whose real headline reads "Account Executive",
    "Client Partner", or simply "Principal Consultant". Feeding that string to
    the prescreen and the QA auditor is what made both of them reject the very
    people the role wanted (Kastell feedback, 2026-07-28).

    The three fields below carry the CANDIDATE-side model instead — what the job
    is about, what people who do it call themselves, and which register its
    requirements should be read in. The JD contains all of this; we simply had
    nowhere to put it.
    """
    model_config = ConfigDict(extra="ignore")

    title: Optional[str] = None
    mustHaveSkills: List[str] = Field(default_factory=list)
    niceToHaveSkills: List[str] = Field(default_factory=list)
    minYears: Optional[float] = None
    location: Optional[str] = None
    seniority: Optional[str] = None
    responsibilities: List[str] = Field(default_factory=list)

    # ── candidate-side role model ────────────────────────────────────────────
    roleFamily: Optional[str] = None
    """commercial | delivery | engineering | management | other.

    The switch that decides which register must-haves are read in. A commercial
    role is evidenced by domain/product familiarity and sales results; a delivery
    role by hands-on implementation skills. Scoring a salesperson against
    implementation skills caps him at 8/100 however good he is."""

    domainTerms: List[str] = Field(default_factory=list)
    """What the person works on or sells — products, platforms, markets
    ("SAP Retail", "S/4HANA Retail", "SAP CAR", "Consumer Goods"). For roles
    whose titles vary wildly this is the ONLY stable identifying signal."""

    candidateTitles: List[str] = Field(default_factory=list)
    """What people doing this job call THEMSELVES — deliberately not the posting
    title. For a commercial role: Account Executive, Key Account Manager, Sales
    Director. Used for ranking and screening, never as a hard filter."""

    @field_validator("mustHaveSkills", "niceToHaveSkills", "responsibilities",
                     "domainTerms", "candidateTitles", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> List[str]:
        return _clean_str_list(v)

    @field_validator("roleFamily", mode="before")
    @classmethod
    def _family(cls, v: Any) -> Optional[str]:
        """Unknown values become None rather than failing the parse — a bad
        label must degrade to today's behaviour, never break the run."""
        s = str(v or "").strip().lower()
        return s if s in ROLE_FAMILIES else None

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
        "roleFamily": {"type": ["string", "null"], "enum": [*ROLE_FAMILIES, None]},
        "domainTerms": _str_array(),
        "candidateTitles": _str_array(),
    },
    "required": ["title", "mustHaveSkills", "niceToHaveSkills", "minYears",
                 "location", "seniority", "responsibilities",
                 "roleFamily", "domainTerms", "candidateTitles"],
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
# Bump whenever the JD SHAPE changes (new fields, changed semantics), not when
# the prompt is merely reworded. Cached specs in `parsed_jds` are keyed by the JD
# text hash alone, so without this a schema change would never reach any JD that
# had already been parsed — the new fields would silently be absent forever and
# every consumer would quietly fall back to the old behaviour.
#   v2 (2026-07-28): + roleFamily, domainTerms, candidateTitles
JD_SCHEMA_VERSION = "2"


def jd_schema_version() -> str:
    return JD_SCHEMA_VERSION


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
    "requirements.\n"
    "\n"
    "THE POSTING TITLE IS NOT THE CANDIDATE'S TITLE. This is the single most "
    "important judgement you make. Employers title openings in their own "
    "language, and for commercial roles the two routinely disagree: a posting "
    "called \"SAP Retail Consultant\" often describes someone who SELLS SAP "
    "Retail, and that person's own headline reads \"Account Executive\", \"Key "
    "Account Manager\", \"Client Partner\", \"Sales Director\" — or something "
    "with no signal at all, like \"Principal Consultant\". Read what the person "
    "will actually DO, not what the posting is called.\n"
    "\n"
    "`roleFamily` — classify from the RESPONSIBILITIES, never from the title:\n"
    "  commercial  — sells, owns revenue/quota, manages accounts, develops "
    "business, negotiates deals, owns a territory or pipeline.\n"
    "  delivery    — implements, configures, customises, migrates, advises on "
    "a product for a client, runs projects.\n"
    "  engineering — builds or operates software/infrastructure.\n"
    "  management  — leads people or a function as the primary job.\n"
    "  other       — none of the above fits.\n"
    "\n"
    "`domainTerms` — what the person works on or sells: products, platforms, "
    "modules, markets, industries (\"SAP Retail\", \"S/4HANA Retail\", \"SAP "
    "CAR\", \"Consumer Goods\"). For a role whose titles vary this is the only "
    "stable way to identify the right people, so be generous and specific: "
    "include the abbreviation AND the expanded form, product names the vendor "
    "uses for the same thing, and the industry served. Never put job-function "
    "words (sales, account, consultant) here.\n"
    "\n"
    "`candidateTitles` — 4-10 titles the RIGHT PEOPLE actually carry on their "
    "own profile. Never the posting title unless people genuinely use it. For a "
    "commercial role these are sales titles; for a delivery role they are "
    "consulting titles. Include local-language variants where the JD is not in "
    "English (\"Vertriebsleiter\", \"Key Account Manager\").\n"
    "\n"
    "MATCH THE REGISTER OF `mustHaveSkills` TO `roleFamily`. A commercial role's "
    "must-haves are domain/product familiarity, market knowledge and sales "
    "competencies — NOT implementation skills. Do not list \"SAP Retail "
    "customizing\" or \"ABAP\" as required for someone whose job is to sell SAP "
    "Retail; they will never evidence it and a correct candidate would be scored "
    "as unqualified."
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
  * `staleEvidence` entries are must-haves credited ONLY by experience that has since ended — real, but dated, not current. Name this explicitly in a reason (e.g. "evidences X, but only in a role that ended in 20XX — current experience is unrelated"); do not describe stale evidence as if it were current. fitScore must be ≤ 74 when `staleEvidence` is non-empty, same as a missing must-have — dated-only evidence does not mean the candidate does this job today.
  * `seniorityBandFlag`, when present, is a deterministic finding that the candidate's title reads as owner/executive-level, well above this role's seniority. It is authoritative — say so directly in a reason and fitScore must be ≤ 39 ("Not a fit": different profession/level).
  * `functionMismatchFlag`, when present, is a deterministic finding that the candidate's current title names a specific different function (e.g. Product Management, Solution Architecture, Partner/Ecosystem/Alliance management) than the commercial/sales role being hired for — even though the profile matches the domain. It is authoritative — say so directly in a reason (name the actual function) and fitScore must be ≤ 39, same as a missing must-have or wrong seniority band: matching domain vocabulary does not mean matching job.
  * `domainEvidenceFlag`, when present, is a deterministic finding that the profile names the role's SPECIALTY (e.g. "Retail") somewhere but shows no real evidence of the ECOSYSTEM/PLATFORM requirement (e.g. "SAP") anywhere in the full profile — a pattern confirmed to often mean the sourcing search matched on a coincidental shared word, not a genuine skill. It is authoritative — say so directly in a reason and fitScore must be ≤ 39, same as a missing must-have.
  * `inactiveCandidateFlag`, when present, is a deterministic finding that the candidate appears to be retired or out of the workforce. Say so directly in a reason and fitScore must be ≤ 39.
  * `tenureFlag`, when present, is a deterministic finding that the candidate's average tenure across recent roles reads as frequent job changes. This is a SOFT signal, not a disqualifier — name it as a verification point in a reason (e.g. "worth confirming stability directly") but do not treat it like a missing must-have; it alone does not cap fitScore.
  * Experience entries carry `startsAt`/`endsAt`/`isCurrent` dates. Use them: a skill or role only present in an entry that is not `isCurrent` and ended long ago describes the candidate's PAST, not their present — weigh it accordingly even where `staleEvidence` wasn't already flagged for it (that field only covers must-haves; the same reasoning applies to nice-to-haves and general fit).
  * Every reason must cite concrete evidence given to you (a skill, a title, years, an experience entry, or one of the flags above). No generic praise, no filler.
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
