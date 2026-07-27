"""Pick the right LinkedIn profile photo for a candidate.

Two things make this less trivial than reading a field:

  * The stored top-level ``photoUrl`` is the 800x800 crop — ~57KB to render a 40px
    avatar. The Apify payload also carries 400/200/100 variants, so a list of 50
    candidates can cost ~250KB instead of ~2.8MB.
  * Those variants CANNOT be derived from the 800px URL by swapping the size in the
    path: every size is signed separately (a different ``t=`` token), so a rewritten
    URL 403s. The sizes array is the only source.

The URLs are public (no cookie or referer needed) but SIGNED AND EXPIRING — the
``e=`` param is a unix expiry, typically a few weeks out. Callers must therefore
treat a photo as best-effort and fall back to initials, and a run opened months
later will legitimately have dead links.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _sizes(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = ((doc.get("apifyEnrichment") or {}).get("raw") or {}).get("apify") or {}
    pic = raw.get("profilePicture") or {}
    return [s for s in (pic.get("sizes") or []) if s.get("url") and s.get("width")]


def pick(doc: Dict[str, Any], min_px: int = 200) -> Optional[str]:
    """Smallest stored variant at least `min_px` wide; else the largest available;
    else the top-level `photoUrl`. None when the candidate has no photo at all."""
    sizes = sorted(_sizes(doc), key=lambda s: s["width"])
    for s in sizes:
        if s["width"] >= min_px:
            return s["url"]
    if sizes:
        return sizes[-1]["url"]
    return (doc.get("photoUrl") or "").strip() or None
