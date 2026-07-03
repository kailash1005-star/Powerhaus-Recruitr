"""
Standalone runner for Apify candidate enrichment (no DB required).

Usage (from BE/):
    python scratch_enrich_apify.py sudharsan2618
    python scratch_enrich_apify.py https://www.linkedin.com/in/satyanadella/ williamhgates

For each identifier it:
  1. calls the Apify actor (harvestapi/linkedin-profile-scraper),
  2. merges the result with a matching Apollo sample if one exists in
     apollo_samples/apollo_enrichment_<slug>.json (else Apify-only),
  3. prints a field-coverage summary, and
  4. dumps the merged enrichedData to apollo_samples/apify_enriched_<slug>.json.

This is how we validate the "just 10" flow and eyeball the FULL set of available
fields against the real LinkedIn profile. Requires APIFY_TOKEN in BE/.env.
"""
import json
import logging
import sys
from pathlib import Path

from app.services import candidate_merge
from app.services.apify_profile_service import (
    ApifyEnrichmentError,
    ApifyNotConfigured,
    get_apify_profile_service,
    normalize_identifier,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

SAMPLES_DIR = Path(__file__).parent / "apollo_samples"


def _load_apollo_sample(slug: str) -> dict | None:
    """Best-effort: load a saved Apollo enrichment for this slug, if present."""
    if not SAMPLES_DIR.exists():
        return None
    # Match on the slug appearing in the filename OR the linkedin_url in the file.
    for p in SAMPLES_DIR.glob("apollo_enrichment_*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        url = (data.get("linkedin_url") or "").lower()
        if slug in p.stem.lower() or slug in url:
            print(f"    (merging with Apollo sample: {p.name})")
            return data
    return None


def _coverage(profile: dict) -> None:
    """Print which matcher-relevant fields the enriched profile actually filled."""
    checks = [
        ("summary/about", bool(profile.get("summary"))),
        ("skills", len(profile.get("skills") or [])),
        ("experience", len(profile.get("experience") or [])),
        ("exp descriptions", sum(1 for e in (profile.get("experience") or []) if e.get("description"))),
        ("education", len(profile.get("education") or [])),
        ("certifications", len(profile.get("certifications") or [])),
        ("languages", len(profile.get("languages") or [])),
        ("totalYears", profile.get("totalYears")),
    ]
    print("    coverage:")
    for name, val in checks:
        print(f"      - {name:<18} {val}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    idents = sys.argv[1:]
    normalized = [normalize_identifier(i) for i in idents]
    normalized = [n for n in normalized if n]
    if not normalized:
        print("[x] no valid LinkedIn identifiers given")
        return 1
    print(f"-> enriching: {', '.join(normalized)}\n")

    service = get_apify_profile_service()
    try:
        profiles = service.enrich_profiles(normalized)
    except ApifyNotConfigured as exc:
        print(f"\n[!] {exc}")
        return 3
    except ApifyEnrichmentError as exc:
        print(f"\n[!] Apify enrichment failed: {exc}")
        return 3

    SAMPLES_DIR.mkdir(exist_ok=True)
    exit_code = 0
    for slug in normalized:
        print(f"\n=== {slug} ===")
        apify_profile = profiles.get(slug)
        if not apify_profile:
            print("    [x] no data returned (private / not found)")
            exit_code = 1
            continue

        apollo = _load_apollo_sample(slug)
        enriched = candidate_merge.merge_enriched(apollo, apify_profile)
        _coverage(enriched["profile"])

        out_path = SAMPLES_DIR / f"apify_enriched_{slug}.json"
        out_path.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"    -> wrote {out_path.name}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
