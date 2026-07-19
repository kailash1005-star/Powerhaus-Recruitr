"""
Seed the Outreach CRM with a realistic demo dataset.

This drives the SAME webhook endpoints Smartlead and Cal.com use in production
(/api/v1/outreach/webhooks/...), so what shows up in the UI is real data flowing
through the real ingestion pipeline — just seeded. Re-running clears first, so
the demo state is reproducible.

Usage (backend must be running on :8000):
    venv\\Scripts\\python.exe -m tests.seed_outreach_demo
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import requests

BASE = "http://127.0.0.1:8000/api/v1/outreach"
SL = f"{BASE}/webhooks/smartlead"
CC = f"{BASE}/webhooks/calcom"


def _ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _sl(event_type: str, email: str, when: float, **kw) -> None:
    lead = {"email": email}
    for k in ("first_name", "last_name", "title", "company_name"):
        if k in kw:
            lead[k] = kw.pop(k)
    custom = {}
    if "audience" in kw:
        custom["audience"] = kw.pop("audience")
    if "campaign_name" in kw:
        custom["campaign_name"] = kw.pop("campaign_name")
    body = {
        "event_type": event_type,
        "campaign_id": kw.pop("campaign_id", 900),
        "event_timestamp": _ts(when),
        "lead": {**lead, "custom_fields": custom},
        **kw,
    }
    r = requests.post(SL, json=body, timeout=15)
    r.raise_for_status()


def _meeting(email: str, name: str, when: float, title: str) -> None:
    body = {
        "triggerEvent": "BOOKING_CREATED",
        "payload": {
            "uid": f"bk-{email}",
            "title": title,
            "startTime": _ts(when),
            "attendees": [{"email": email, "name": name}],
        },
    }
    r = requests.post(CC, json=body, timeout=15)
    r.raise_for_status()


def progress(audience: str, *, email, name, title, company, role, stage, day):
    """Emit the event sequence needed to land a contact at `stage`."""
    first, _, last = name.partition(" ")
    base = dict(
        audience=audience, first_name=first, last_name=last,
        title=title, company_name=company, campaign_name=(role if audience == "candidate" else company),
    )
    # Everyone starts with a send.
    _sl("EMAIL_SENT", email, day, **base)
    if stage == "bounced":
        _sl("EMAIL_BOUNCE", email, day - 0.1, **base)
        return
    if stage == "unsubscribed":
        _sl("LEAD_UNSUBSCRIBED", email, day - 0.2, **base)
        return
    if stage in ("opened", "replied", "meeting"):
        _sl("EMAIL_OPEN", email, day - 0.3, **base)
    if stage in ("replied", "meeting"):
        _sl("EMAIL_REPLY", email, day - 0.5, reply_message="Thanks for reaching out — keen to learn more.", **base)
    if stage == "meeting":
        _meeting(email, name, day - 0.7, f"Intro · {role or company}")


LEADS = [
    ("Anja Bauer",     "a.bauer@siemens-energy.com",  "Head of Talent Acquisition", "Siemens Energy",  "meeting"),
    ("Markus Vogel",   "m.vogel@celonis.com",         "VP People",                  "Celonis",         "replied"),
    ("Sophie Wagner",  "s.wagner@personio.de",        "Recruiting Lead",            "Personio",        "opened"),
    ("Daniel Fischer", "d.fischer@teamviewer.com",    "HR Business Partner",        "TeamViewer",      "opened"),
    ("Laura Schmidt",  "laura.s@n26.com",             "Head of People Ops",         "N26",             "sent"),
    ("Thomas Becker",  "t.becker@traderepublic.com",  "Talent Partner",             "Trade Republic",  "sent"),
    ("Nina Hoffmann",  "n.hoffmann@aboutyou.com",     "Director of Recruiting",     "About You",       "unsubscribed"),
    ("Felix Braun",    "f.braun@flixbus.com",         "Senior Recruiter",           "Flixbus",         "bounced"),
]

CANDIDATES = [
    ("Priya Nair",     "priya.nair@gmail.com",    "Backend Engineer @ Zalando",        "Zalando",       "Senior Python Developer", "meeting"),
    ("Lukas Wolf",     "lukas.wolf@proton.me",    "Platform Engineer @ SAP",           "SAP",           "Senior Python Developer", "replied"),
    ("Aisha Khan",     "aisha.khan@outlook.com",  "Data Engineer @ Delivery Hero",     "Delivery Hero", "Data Engineer",           "replied"),
    ("Jonas Richter",  "jonas.richter@gmx.de",    "Software Engineer @ Bosch",         "Bosch",         "Senior Python Developer", "opened"),
    ("Elena Popova",   "elena.popova@gmail.com",  "Analytics Engineer @ HelloFresh",   "HelloFresh",    "Data Engineer",           "opened"),
    ("Marco Rossi",    "marco.rossi@gmail.com",   "Backend Dev @ Scout24",             "Scout24",       "Senior Python Developer", "sent"),
    ("Sara Lindqvist", "sara.l@gmail.com",        "BI Engineer @ Spotify",             "Spotify",       "Data Engineer",           "sent"),
    ("Ahmed Hassan",   "ahmed.hassan@gmail.com",  "Engineer @ Wayfair",                "Wayfair",       "Senior Python Developer", "unsubscribed"),
]


def main() -> int:
    try:
        requests.get("http://127.0.0.1:8000/health", timeout=5).raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[seed] backend not reachable on :8000 — start it first ({e})")
        return 1

    print("[seed] clearing existing outreach data…")
    # Reuse the same DB the app uses (sync pymongo).
    from pymongo import MongoClient
    from app.config import settings
    db = MongoClient(settings.MONGODB_URI)[settings.DATABASE_NAME]
    db["outreach_messages"].delete_many({})
    db["outreach_events"].delete_many({})

    print(f"[seed] seeding {len(LEADS)} leads…")
    for i, (name, email, title, company, stage) in enumerate(LEADS):
        progress("lead", email=email, name=name, title=title, company=company, role=None, stage=stage, day=5 - i * 0.4)

    print(f"[seed] seeding {len(CANDIDATES)} candidates…")
    for i, (name, email, title, company, role, stage) in enumerate(CANDIDATES):
        progress("candidate", email=email, name=name, title=title, company=company, role=role, stage=stage, day=4 - i * 0.4)

    lm = requests.get(f"{BASE}/metrics?audience=leads", timeout=10).json()
    cm = requests.get(f"{BASE}/metrics?audience=candidates", timeout=10).json()
    print(f"[seed] done. leads={lm}  candidates={cm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
