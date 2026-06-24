"""
Outreach Email Service.

Two jobs:
  1. generate_outreach_email(...) — an LLM writes a warm, professional, HR-toned
     email inviting a candidate to discuss a role. Needs only OPENAI_API_KEY.
  2. send_email(...) — delivers via SMTP. Disabled (clear error) until SMTP creds
     are set, so the demo can preview drafts without any mail setup.
"""
from __future__ import annotations

import asyncio
import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set — required to draft emails.")
        from openai import OpenAI
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def email_configured() -> bool:
    """True only when SMTP is fully set up (sending enabled)."""
    return bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD and settings.SMTP_FROM)


# ── Draft generation (LLM) ───────────────────────────────────────────────────
_SYSTEM = (
    "You are an experienced HR / talent acquisition professional writing a first "
    "outreach email to a candidate. Tone: warm, professional, concise, respectful "
    "— never salesy or robotic. Reference the candidate's relevant strengths "
    "briefly and authentically, express genuine interest, and invite them to a "
    "short introductory conversation to share role expectations and learn about "
    "them. Do NOT fabricate company names, salaries, or specifics not provided. "
    "Return ONLY a JSON object."
)


def _draft_sync(candidate: Dict[str, Any], role_title: Optional[str], sender_name: str) -> Dict[str, str]:
    name = candidate.get("fullName") or "there"
    title = candidate.get("currentTitle") or ""
    skills = ", ".join((candidate.get("skills") or [])[:8])
    role = role_title or "an opportunity that matches your background"

    user = (
        f"Candidate first name (for greeting): {name}\n"
        f"Candidate current title: {title}\n"
        f"Candidate key skills: {skills}\n"
        f"Role we are reaching out about: {role}\n"
        f"Sender / signature name: {sender_name}\n\n"
        "Write the email. Return ONLY JSON:\n"
        '{ "subject": "<concise subject line>", "body": "<3 short paragraphs, '
        'greeting by first name, plain text with line breaks, signed off as the sender>" }'
    )

    client = _get_client()
    raw = ""
    for attempt in range(1, 3):
        try:
            resp = client.chat.completions.create(
                model=settings.REASONING_MODEL,
                temperature=0.5,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            )
            raw = (resp.choices[0].message.content or "").strip()
            data = json.loads(raw)
            return {
                "subject": (data.get("subject") or f"Exploring a {role} opportunity with you").strip(),
                "body": (data.get("body") or "").strip(),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("[Email] draft attempt %d failed: %s", attempt, e)
    # deterministic fallback so the UI always has something
    return {
        "subject": f"Interested in connecting about {role}",
        "body": (
            f"Hi {name},\n\n"
            f"Your background{(' as a ' + title) if title else ''} stood out to us, and we'd love to "
            f"learn more about you and share what we're looking for in this role.\n\n"
            "Would you be open to a short introductory call this week? We'll walk you through the "
            "role expectations and answer any questions you have.\n\n"
            f"Warm regards,\n{sender_name}"
        ),
    }


async def generate_outreach_email(candidate: Dict[str, Any], role_title: Optional[str] = None) -> Dict[str, str]:
    return await asyncio.to_thread(_draft_sync, candidate, role_title, settings.OUTREACH_SENDER_NAME)


# ── Sending (SMTP) ───────────────────────────────────────────────────────────
def _send_sync(to: str, subject: str, body: str) -> None:
    msg = MIMEMultipart()
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as server:
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_FROM, [to], msg.as_string())


async def send_email(to: str, subject: str, body: str) -> Dict[str, Any]:
    if not email_configured():
        raise RuntimeError(
            "Email sending is not configured. Add SMTP_HOST / SMTP_USER / "
            "SMTP_PASSWORD / SMTP_FROM to BE/.env to enable sending."
        )
    if not to:
        raise ValueError("no recipient email")
    await asyncio.to_thread(_send_sync, to, subject, body)
    return {"sent": True, "to": to}
