"""Picking a candidate's avatar.

The LinkedIn CDN signs every size separately, so the 200px variant cannot be
derived from the 800px URL by rewriting the path — it must come from the stored
sizes array. These pin that, and the fallbacks around it.
"""
import importlib

import pytest

pp = importlib.import_module("app.services.profile_photo")


def _doc(*widths):
    return {"apifyEnrichment": {"raw": {"apify": {"profilePicture": {
        "sizes": [{"url": f"u{w}", "width": w} for w in widths]}}}}}


def test_picks_the_smallest_variant_at_or_above_the_target():
    assert pp.pick(_doc(800, 400, 200, 100), min_px=200) == "u200"
    assert pp.pick(_doc(800, 400, 200, 100), min_px=400) == "u400"


def test_falls_back_to_the_largest_when_none_are_big_enough():
    assert pp.pick(_doc(100, 200), min_px=2000) == "u200"


def test_falls_back_to_the_stored_top_level_photo():
    assert pp.pick({"photoUrl": "https://cdn/800.jpg"}) == "https://cdn/800.jpg"


def test_no_photo_at_all_is_none_not_empty_string():
    """The UI switches to initials on a falsy value; '' and None must both work,
    but None keeps the JSON honest about absence."""
    assert pp.pick({}) is None
    assert pp.pick({"photoUrl": "   "}) is None


def test_ignores_malformed_size_entries():
    d = {"apifyEnrichment": {"raw": {"apify": {"profilePicture": {"sizes": [
        {"url": None, "width": 200}, {"width": 400}, {"url": "ok", "width": 300}]}}}}}
    assert pp.pick(d, min_px=200) == "ok"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
