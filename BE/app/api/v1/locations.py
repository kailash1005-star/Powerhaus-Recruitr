"""Location typeahead — offline, keystroke-cheap place lookup.

Backs the LinkedIn-style location autocomplete on the discovery form: the
recruiter types "kobl", picks "Koblenz, Germany", and both search engines then
receive the same canonical, correctly-spelled label. No geocoding API, no key —
served from the in-process gazetteer (``services/location_catalog``), so it costs
nothing and can't rate-limit.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.services import location_catalog

router = APIRouter()


@router.get("/suggest")
async def suggest_locations(
    q: str = Query("", description="Partial place name, e.g. 'kobl'"),
    limit: int = Query(8, ge=1, le=20),
):
    """Typeahead suggestions for a partial location string.

    Returns ``{"suggestions": [{"label", "country", "kind"}, …]}`` ranked by
    match quality (exact → prefix → substring; cities before regions before
    countries). Empty/whitespace query → empty list.
    """
    return {"suggestions": location_catalog.suggest(q.strip(), limit=limit)}
