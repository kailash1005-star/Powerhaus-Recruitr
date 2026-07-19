"""
Standalone runner for Apollo People Enrichment (/people/match).

Goal: given ONE identifier for a prospect (an Apollo person id, a LinkedIn URL,
an email, or a name + company), call Apollo's enrichment endpoint, save the FULL
raw response as JSON, and print a complete field inventory so we can see exactly
what data Apollo returns — including the LinkedIn URL, work history, and (when
revealed) email / phone.

────────────────────────────────────────────────────────────────────────────
Why this endpoint
────────────────────────────────────────────────────────────────────────────
We decided NOT to use linkedin-api / the Voyager dash GraphQL path (get_profile
is 410-dead; the dash path throttles without a proxy and never returns a
non-connection's private mobile anyway). Apollo enrichment resolves the person
AND returns the whole record — linkedin_url, employment_history, organization,
seniority, contact — in one call. So enrichment IS the data source.

────────────────────────────────────────────────────────────────────────────
Cost warning (credits)
────────────────────────────────────────────────────────────────────────────
People SEARCH is free; ENRICHMENT (/people/match) consumes credits, and
`--phone` (reveal_phone_number) consumes an ADDITIONAL credit. This script makes
exactly ONE match call per run (plus one free search call in --search mode).

────────────────────────────────────────────────────────────────────────────
Usage (from BE/)
────────────────────────────────────────────────────────────────────────────
    # by LinkedIn URL (handiest for a one-off test)
    python scratch_enrich_apollo.py --linkedin https://www.linkedin.com/in/satyanadella/

    # by Apollo person id (what the real pipeline has after a free search)
    python scratch_enrich_apollo.py --id 5f1b...

    # by email
    python scratch_enrich_apollo.py --email jane@acme.com

    # by name + company
    python scratch_enrich_apollo.py --name "Jane Doe" --company "Acme Corp"

    # end-to-end: free search first, enrich the first hit
    python scratch_enrich_apollo.py --search --title "Head of Talent" --domain acme.com

    # also reveal the phone number (extra credit)
    python scratch_enrich_apollo.py --linkedin <url> --phone

    # also reveal personal emails is ON by default; turn it off to save a reveal
    python scratch_enrich_apollo.py --linkedin <url> --no-personal-emails

Outputs (written next to this script under ./apollo_samples/):
    apollo_enrichment_<slug>.json    — the full raw Apollo person record
    apollo_enrichment_<slug>.txt     — the human-readable field inventory
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

import requests

from app.config import APOLLO_BASE_URL, settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("apollo_enrich")

OUT_DIR = Path(__file__).parent / "apollo_samples"


# ──────────────────────────────────────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    return {
        "x-api-key": settings.APOLLO_API_KEY,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }


def free_search_first_id(title: str, domain: str) -> Optional[str]:
    """FREE people-search (no credits) → return the first person's Apollo id.

    Mirrors the real pipeline: a free search yields ids, then we enrich one.
    """
    body = {
        "person_titles[]": [title],
        "include_similar_titles": "true",
        "q_organization_domains_list[]": [domain],
        "per_page": 5,
        "page": 1,
    }
    logger.info("Free search: title=%r domain=%r", title, domain)
    resp = requests.post(
        f"{APOLLO_BASE_URL}/mixed_people/api_search",
        headers=_headers(),
        params=body,
        timeout=30,
    )
    resp.raise_for_status()
    people = resp.json().get("people", [])
    if not people:
        logger.warning("Free search returned nobody for %r at %s", title, domain)
        return None
    p = people[0]
    logger.info("Free search hit: %s — %s (id=%s)",
                p.get("name"), p.get("title"), p.get("id"))
    return p.get("id")


def enrich(
    *,
    apollo_id: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    email: Optional[str] = None,
    name: Optional[str] = None,
    organization_name: Optional[str] = None,
    reveal_personal_emails: bool = True,
    reveal_phone_number: bool = False,
) -> dict[str, Any]:
    """POST /people/match — the credit-consuming enrichment call.

    Any one identifier is enough; more identifiers = a more confident match.
    Returns the `person` dict from Apollo's response (may be {} if no match).
    """
    body: dict[str, Any] = {
        "reveal_personal_emails": reveal_personal_emails,
        "reveal_phone_number": reveal_phone_number,
    }
    if apollo_id:
        body["id"] = apollo_id
    if linkedin_url:
        body["linkedin_url"] = linkedin_url
    if email:
        body["email"] = email
    if name:
        body["name"] = name
    if organization_name:
        body["organization_name"] = organization_name
    # A phone reveal that isn't cached is delivered later to a webhook.
    if reveal_phone_number and settings.APOLLO_WEBHOOK_URL:
        body["webhook_url"] = settings.APOLLO_WEBHOOK_URL

    logger.info("Enriching via /people/match (reveal_email=%s reveal_phone=%s)",
                reveal_personal_emails, reveal_phone_number)
    resp = requests.post(
        f"{APOLLO_BASE_URL}/people/match",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("person") or {}


# ──────────────────────────────────────────────────────────────────────────────
# Field inventory — "what data is available in the response"
# ──────────────────────────────────────────────────────────────────────────────

def _short(value: Any, limit: int = 80) -> str:
    """One-line, length-capped sample of a scalar for the inventory."""
    s = str(value)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _walk(value: Any, prefix: str, lines: list[str]) -> None:
    """Recursively record every key path with its type and a sample value.

    dict  → recurse into each key
    list  → note length; recurse into [0] for shape (scalars sampled inline)
    scalar→ record `path : type = sample`  (empty/None marked <empty>)
    """
    if isinstance(value, dict):
        if not value:
            lines.append(f"{prefix} : dict (empty)")
            return
        for k, v in value.items():
            child = f"{prefix}.{k}" if prefix else k
            _walk(v, child, lines)
    elif isinstance(value, list):
        n = len(value)
        if n == 0:
            lines.append(f"{prefix} : list (empty)")
            return
        first = value[0]
        if isinstance(first, (dict, list)):
            lines.append(f"{prefix} : list[{n}] (item shape below)")
            _walk(first, f"{prefix}[0]", lines)
        else:
            sample = ", ".join(_short(x, 40) for x in value[:5])
            more = " …" if n > 5 else ""
            lines.append(f"{prefix} : list[{n}] of scalar = [{sample}{more}]")
    else:
        typ = type(value).__name__
        shown = "<empty>" if value in (None, "", [], {}) else _short(value)
        lines.append(f"{prefix} : {typ} = {shown}")


def build_inventory(person: dict[str, Any]) -> str:
    """Full inventory + a curated recruiting highlights block."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("APOLLO ENRICHMENT — FIELD INVENTORY")
    lines.append("=" * 78)

    # ── Curated highlights (the fields the pipeline actually uses) ──
    org = person.get("organization") or {}
    hist = person.get("employment_history") or []
    phones = person.get("phone_numbers") or []
    lines.append("\n── RECRUITING HIGHLIGHTS ─────────────────────────────────")
    highlights = {
        "name": person.get("name"),
        "title": person.get("title"),
        "headline": person.get("headline"),
        "seniority": person.get("seniority"),
        "departments": person.get("departments"),
        "functions": person.get("functions"),
        "linkedin_url": person.get("linkedin_url"),
        "location": ", ".join(
            filter(None, [person.get("city"), person.get("state"), person.get("country")])
        ),
        "email": person.get("email"),
        "email_status": person.get("email_status"),
        "personal_emails": person.get("personal_emails"),
        "phone (extracted)": _extract_phone(phones),
        "current_org": org.get("name"),
        "org_website": org.get("website_url"),
        "org_industry": org.get("industry"),
        "org_employees": org.get("estimated_num_employees"),
        "employment_history_count": len(hist),
    }
    for k, v in highlights.items():
        lines.append(f"  {k:<26}: {_short(v, 120) if v not in (None, '', []) else '<empty>'}")

    # ── Contact reveal report (did the credits actually reveal anything?) ──
    lines.append("\n── CONTACT REVEAL STATUS ─────────────────────────────────")
    lines.append(f"  email revealed      : {bool(person.get('email'))} ({person.get('email_status')})")
    lines.append(f"  personal emails     : {len(person.get('personal_emails') or [])}")
    lines.append(f"  phone numbers       : {len(phones)}"
                 + ("  (may arrive later via webhook)" if not phones else ""))

    # ── Employment history detail ──
    if hist:
        lines.append("\n── EMPLOYMENT HISTORY ────────────────────────────────────")
        for i, job in enumerate(hist):
            title = job.get("title") or "?"
            company = job.get("organization_name") or "?"
            start = job.get("start_date") or "?"
            end = "present" if job.get("current") else (job.get("end_date") or "?")
            lines.append(f"  [{i}] {title} @ {company}  ({start} → {end})")

    # ── Full recursive inventory of every key path ──
    lines.append("\n── FULL FIELD INVENTORY (every key path) ─────────────────")
    full: list[str] = []
    _walk(person, "", full)
    lines.extend("  " + ln for ln in full)

    lines.append("\n" + "=" * 78)
    lines.append(f"TOTAL TOP-LEVEL KEYS: {len(person)}   TOTAL LEAF PATHS: {len(full)}")
    lines.append("=" * 78)
    return "\n".join(lines)


def _extract_phone(phones: list) -> Optional[str]:
    """Best mobile/phone from an Apollo phone_numbers list."""
    if not phones:
        return None
    mobiles = [p for p in phones if isinstance(p, dict)
               and (p.get("type") == "mobile" or p.get("type_cd") == "mobile")]
    pick = (mobiles or phones)[0]
    if isinstance(pick, dict):
        return pick.get("sanitized_number") or pick.get("raw_number")
    return pick if isinstance(pick, str) else None


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _slugify(person: dict[str, Any], fallback: str) -> str:
    base = person.get("name") or person.get("id") or fallback
    return re.sub(r"[^a-z0-9]+", "_", str(base).lower()).strip("_") or "person"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apollo People Enrichment — standalone tester")
    parser.add_argument("--id", help="Apollo person id (from a prior free search)")
    parser.add_argument("--linkedin", help="LinkedIn profile URL")
    parser.add_argument("--email", help="Known email address")
    parser.add_argument("--name", help="Full name (pair with --company)")
    parser.add_argument("--company", help="Organization name (pair with --name)")
    parser.add_argument("--search", action="store_true",
                        help="Free-search first, then enrich the top hit (needs --title + --domain)")
    parser.add_argument("--title", help="Job title for --search mode")
    parser.add_argument("--domain", help="Company domain for --search mode (e.g. acme.com)")
    parser.add_argument("--phone", action="store_true",
                        help="Also reveal phone number (consumes an EXTRA credit)")
    parser.add_argument("--no-personal-emails", action="store_true",
                        help="Do NOT reveal personal emails (saves a reveal)")
    parser.add_argument("--out", help="Output directory (default ./apollo_samples/)")
    args = parser.parse_args()

    if not settings.APOLLO_API_KEY:
        print("[x] APOLLO_API_KEY is not set (check BE/.env).")
        return 2

    apollo_id = args.id
    if args.search:
        if not (args.title and args.domain):
            print("[x] --search requires --title and --domain")
            return 2
        try:
            apollo_id = free_search_first_id(args.title, args.domain)
        except Exception as e:  # noqa: BLE001
            print(f"[x] Free search failed: {e}")
            return 1
        if not apollo_id:
            return 1

    if not any([apollo_id, args.linkedin, args.email, (args.name and args.company)]):
        print("[x] Provide one of: --id | --linkedin | --email | (--name AND --company) | --search")
        parser.print_usage()
        return 2

    try:
        person = enrich(
            apollo_id=apollo_id,
            linkedin_url=args.linkedin,
            email=args.email,
            name=args.name,
            organization_name=args.company,
            reveal_personal_emails=not args.no_personal_emails,
            reveal_phone_number=args.phone,
        )
    except requests.HTTPError as e:
        body = e.response.text[:400] if e.response is not None else ""
        print(f"[x] Apollo HTTP error: {e}\n{body}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[x] Enrichment failed: {e}")
        return 1

    if not person:
        print("[x] Apollo returned no match for the given identifier(s).")
        return 1

    out_dir = Path(args.out) if args.out else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(person, apollo_id or "person")
    json_path = out_dir / f"apollo_enrichment_{slug}.json"
    txt_path = out_dir / f"apollo_enrichment_{slug}.txt"

    json_path.write_text(json.dumps(person, indent=2, ensure_ascii=False), encoding="utf-8")
    inventory = build_inventory(person)
    txt_path.write_text(inventory, encoding="utf-8")

    print(inventory)
    print(f"\n[ok] Raw JSON  → {json_path}")
    print(f"[ok] Inventory → {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
