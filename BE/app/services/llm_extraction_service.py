"""
LLM Extraction & Reasoning Service.

Three narrow LLM jobs (the only places an LLM is used in the matching engine):
  1. extract_cv_fields(markdown)  → structured CvProfile + contact (cheap model)
  2. parse_jd(markdown)           → structured JD requirements (cheap model)
  3. reason_candidates(jd, cands) → grounded "why fit / gaps" for the top N

Mirrors the existing OpenAI structured-JSON pattern (temperature=0, strict JSON,
defensive parse, retries) used in openai_company_service.py. Bias control: the
reasoning prompt is given ONLY skills/experience, never name/contact/demographics.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List

from app.config import settings

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set — required for LLM extraction.")
        from openai import OpenAI
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def _chat_json(model: str, system: str, user: str, retries: int = 2) -> Dict[str, Any]:
    """Call the chat API expecting a JSON object back. Returns {} on hard failure."""
    from app.services import cost_service

    client = _get_client()
    raw = ""
    for attempt in range(1, retries + 1):
        try:
            completion = client.chat.completions.create(
                model=model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            cost_service.record_chat(completion, model=model, operation="extract")
            raw = _strip_fences(completion.choices[0].message.content or "")
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[LLM] JSON parse failed (attempt %d): %.200s", attempt, raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("[LLM] call failed (attempt %d): %s", attempt, e)
            time.sleep(min(2 ** attempt, 6))
    return {}


# ── Public async API ─────────────────────────────────────────────────────────
def extraction_version() -> str:
    return settings.EXTRACTION_MODEL


def reasoning_version() -> str:
    return settings.REASONING_MODEL


_CV_SYSTEM = (
    "You extract structured data from a candidate CV. Return ONLY a JSON object, "
    "no commentary. Use null/empty when unknown. Do not invent skills."
)


def _extract_cv_sync(markdown: str) -> Dict[str, Any]:
    user = (
        "From this CV, extract JSON with EXACTLY these keys:\n"
        "{\n"
        '  "fullName": string|null,\n'
        '  "email": string|null,\n'
        '  "phone": string|null,\n'
        '  "linkedin": string|null,\n'
        '  "location": string|null,\n'
        '  "totalYears": number|null,   // best estimate of total years of experience\n'
        '  "currentTitle": string|null,\n'
        '  "skills": string[],          // normalized skill names\n'
        '  "titles": string[],          // past job titles\n'
        '  "education": string[],\n'
        '  "certifications": string[],\n'
        '  "experience": [{"company": string|null, "title": string|null, '
        '"start": string|null, "end": string|null, "summary": string|null}]\n'
        "}\n\n"
        f"CV:\n{markdown[:12000]}"
    )
    data = _chat_json(settings.EXTRACTION_MODEL, _CV_SYSTEM, user)
    return data or {}


_JD_SYSTEM = (
    "You extract structured hiring requirements from a job description. Return "
    "ONLY a JSON object, no commentary. Use null/empty when unknown."
)


def _parse_jd_sync(markdown: str) -> Dict[str, Any]:
    user = (
        "From this job description, extract JSON with EXACTLY these keys:\n"
        "{\n"
        '  "title": string|null,\n'
        '  "mustHaveSkills": string[],\n'
        '  "niceToHaveSkills": string[],\n'
        '  "minYears": number|null,\n'
        '  "location": string|null,\n'
        '  "seniority": string|null,\n'
        '  "responsibilities": string[]\n'
        "}\n\n"
        f"Job description:\n{markdown[:12000]}"
    )
    data = _chat_json(settings.EXTRACTION_MODEL, _JD_SYSTEM, user)
    return data or {}


_REASON_SYSTEM = (
    "You are a recruiting assistant. For each candidate, explain in plain language "
    "why they fit (or don't) the role, grounded ONLY in the skills/experience given. "
    "Never reference name, gender, age, or contact details. Return ONLY JSON."
)


def _reason_sync(jd: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    user = (
        "Role requirements:\n"
        f"{json.dumps(jd, ensure_ascii=False)}\n\n"
        "Candidates (anonymized):\n"
        f"{json.dumps(candidates, ensure_ascii=False)}\n\n"
        "Return JSON: {\n"
        '  "candidates": [\n'
        '    {"id": string, "reasons": string[2-3], "gaps": string[0-2]}\n'
        "  ]\n"
        "}\n"
        "`reasons` = concrete, evidence-based bullets on why to call this person. "
        "`gaps` = missing must-haves, if any."
    )
    data = _chat_json(settings.REASONING_MODEL, _REASON_SYSTEM, user)
    return data or {}


async def extract_cv_fields(markdown: str) -> Dict[str, Any]:
    return await asyncio.to_thread(_extract_cv_sync, markdown)


async def parse_jd(markdown: str) -> Dict[str, Any]:
    return await asyncio.to_thread(_parse_jd_sync, markdown)


async def reason_candidates(jd: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    return await asyncio.to_thread(_reason_sync, jd, candidates)
