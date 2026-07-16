"""Round 3: is the ACTOR returning nothing, rather than our filters being wrong?

Arm K replays the EXACT filter set that returned 12 profiles at 12:27 today. If it
now returns 0, the actor/account is the variable — not our search construction, and
every "0 results" conclusion from the last hour is unsafe.
"""
import asyncio, os, sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

ARMS = {
    "K: EXACT replay of the search that returned 12": {
        "searchQuery": "SAP HR",
        "currentJobTitles": ["SAP Consultant", "Payroll Consultant", "HR Specialist",
                             "HR Consultant", "HR Administrator"],
        "locations": ["Germany"],
        "function": "10",
    },
    "L: bare sanity check — one common title, no query": {
        "currentJobTitles": ["Software Engineer"], "locations": ["Germany"],
    },
}


async def main():
    from app.services.apify_search_service import get_apify_search_service, parse_short_profile

    service = get_apify_search_service()
    for name, filters in ARMS.items():
        try:
            items = await asyncio.to_thread(service.search, filters, max_items=5)
            profiles = [p for p in (parse_short_profile(i) for i in items) if p]
        except Exception as e:
            print(f"### {name} -> ERROR: {str(e)[:160]}", flush=True)
            continue
        print(f"### {name} -> {len(profiles)} profile(s)", flush=True)
        for p in profiles[:5]:
            print(f"      - {str(p.get('currentTitle'))[:56]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
