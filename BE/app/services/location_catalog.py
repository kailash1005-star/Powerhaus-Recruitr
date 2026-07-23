"""Offline location gazetteer — typeahead + canonicalization for candidate search.

Why this exists
---------------
Recruiters type locations by hand, and a single mis-spelling silently zeroes a
search: Apollo returns nothing for "Kolenz", the LinkedIn actor nothing for a
bare "Koblenz" with no country. The friction analysis caught exactly this — the
AI proposed "Kolenz, Germany" for one engine and "Koblenz" for the other IN THE
SAME CALL, and both mis-fired.

The cure is a single, offline, curated place list that does two jobs:

  * ``suggest(q)``   — LinkedIn-style typeahead: "kobl" → "Koblenz, Germany".
                       The recruiter picks a canonical label, so a typo never
                       reaches an engine.
  * ``normalize(s)`` — best-effort canonicalisation of a free-typed / AI-proposed
                       string to its catalogue label ("kolenz" is close enough to
                       "koblenz" to snap; "Frankfurt am Main" → "Frankfurt,
                       Germany"). Used to make the two engines' location inputs
                       identical, and to repair the AI's output.

Fully offline (no geocoding API, no key, CSP-safe). DACH-heavy because that is
the target market (Castle Personal, Koblenz), with EU + world anchors so genuine
wrong-country results still resolve. Matching folds diacritics so "Munchen",
"München" and "Muenchen" all land on the same entry.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

# ── The catalogue ────────────────────────────────────────────────────────────
# Each row: (city, country, [extra alias spellings]). The canonical label shown
# to the recruiter and sent to both engines is f"{city}, {country}". Aliases are
# ADDITIONAL match keys (English/German/ascii variants, historical spellings) —
# the city name itself is always a key, so only genuinely different spellings go
# in the alias list. Order within a country roughly tracks talent-pool size, so
# ties in the typeahead surface the bigger city first.
_CITIES: List[Tuple[str, str, List[str]]] = [
    # ── Germany ──────────────────────────────────────────────────────────────
    ("Berlin", "Germany", []),
    ("Munich", "Germany", ["München", "Muenchen"]),
    ("Frankfurt", "Germany", ["Frankfurt am Main", "Frankfurt am Main"]),
    ("Hamburg", "Germany", []),
    ("Cologne", "Germany", ["Köln", "Koeln"]),
    ("Stuttgart", "Germany", []),
    ("Düsseldorf", "Germany", ["Dusseldorf", "Duesseldorf"]),
    ("Dortmund", "Germany", []),
    ("Essen", "Germany", []),
    ("Leipzig", "Germany", []),
    ("Dresden", "Germany", []),
    ("Hanover", "Germany", ["Hannover"]),
    ("Nuremberg", "Germany", ["Nürnberg", "Nuernberg"]),
    ("Bremen", "Germany", []),
    ("Bonn", "Germany", []),
    ("Mannheim", "Germany", []),
    ("Karlsruhe", "Germany", []),
    ("Wiesbaden", "Germany", []),
    ("Münster", "Germany", ["Munster", "Muenster"]),
    ("Mainz", "Germany", []),
    ("Augsburg", "Germany", []),
    ("Freiburg", "Germany", ["Freiburg im Breisgau"]),
    ("Heidelberg", "Germany", []),
    ("Darmstadt", "Germany", []),
    ("Duisburg", "Germany", []),
    ("Wuppertal", "Germany", []),
    ("Bielefeld", "Germany", []),
    ("Bochum", "Germany", []),
    ("Kiel", "Germany", []),
    ("Aachen", "Germany", []),
    ("Braunschweig", "Germany", ["Brunswick"]),
    ("Kassel", "Germany", []),
    ("Potsdam", "Germany", []),
    ("Regensburg", "Germany", []),
    ("Ingolstadt", "Germany", []),
    ("Ulm", "Germany", []),
    ("Würzburg", "Germany", ["Wurzburg", "Wuerzburg"]),
    ("Koblenz", "Germany", ["Coblenz"]),
    ("Trier", "Germany", ["Treves"]),
    ("Kaiserslautern", "Germany", []),
    ("Ludwigshafen", "Germany", ["Ludwigshafen am Rhein"]),
    ("Siegen", "Germany", []),
    ("Osnabrück", "Germany", ["Osnabruck", "Osnabrueck"]),
    ("Paderborn", "Germany", []),
    ("Mönchengladbach", "Germany", ["Monchengladbach", "Moenchengladbach"]),
    ("Wolfsburg", "Germany", []),
    ("Erlangen", "Germany", []),
    ("Walldorf", "Germany", []),
    ("Heilbronn", "Germany", []),
    ("Reutlingen", "Germany", []),
    ("Chemnitz", "Germany", []),
    ("Erfurt", "Germany", []),
    ("Rostock", "Germany", []),
    ("Saarbrücken", "Germany", ["Saarbrucken", "Saarbruecken"]),
    ("Gütersloh", "Germany", ["Gutersloh", "Guetersloh"]),
    # ── Austria ──────────────────────────────────────────────────────────────
    ("Vienna", "Austria", ["Wien"]),
    ("Graz", "Austria", []),
    ("Linz", "Austria", []),
    ("Salzburg", "Austria", []),
    ("Innsbruck", "Austria", []),
    ("Klagenfurt", "Austria", []),
    # ── Switzerland ──────────────────────────────────────────────────────────
    ("Zurich", "Switzerland", ["Zürich", "Zuerich"]),
    ("Geneva", "Switzerland", ["Genève", "Geneve", "Genf"]),
    ("Basel", "Switzerland", ["Bâle"]),
    ("Bern", "Switzerland", ["Berne"]),
    ("Lausanne", "Switzerland", []),
    ("Zug", "Switzerland", []),
    ("Lucerne", "Switzerland", ["Luzern"]),
    ("Winterthur", "Switzerland", []),
    # ── Rest of Europe ───────────────────────────────────────────────────────
    ("Amsterdam", "Netherlands", []),
    ("Rotterdam", "Netherlands", []),
    ("The Hague", "Netherlands", ["Den Haag"]),
    ("Eindhoven", "Netherlands", []),
    ("Utrecht", "Netherlands", []),
    ("Brussels", "Belgium", ["Bruxelles", "Brussel"]),
    ("Antwerp", "Belgium", ["Antwerpen", "Anvers"]),
    ("Ghent", "Belgium", ["Gent", "Gand"]),
    ("Paris", "France", []),
    ("Lyon", "France", []),
    ("Marseille", "France", []),
    ("Toulouse", "France", []),
    ("Lille", "France", []),
    ("Bordeaux", "France", []),
    ("Nantes", "France", []),
    ("Strasbourg", "France", []),
    ("London", "United Kingdom", []),
    ("Manchester", "United Kingdom", []),
    ("Birmingham", "United Kingdom", []),
    ("Leeds", "United Kingdom", []),
    ("Edinburgh", "United Kingdom", []),
    ("Glasgow", "United Kingdom", []),
    ("Bristol", "United Kingdom", []),
    ("Dublin", "Ireland", []),
    ("Cork", "Ireland", []),
    ("Madrid", "Spain", []),
    ("Barcelona", "Spain", []),
    ("Valencia", "Spain", []),
    ("Seville", "Spain", ["Sevilla"]),
    ("Lisbon", "Portugal", ["Lisboa"]),
    ("Porto", "Portugal", ["Oporto"]),
    ("Milan", "Italy", ["Milano"]),
    ("Rome", "Italy", ["Roma"]),
    ("Turin", "Italy", ["Torino"]),
    ("Bologna", "Italy", []),
    ("Naples", "Italy", ["Napoli"]),
    ("Warsaw", "Poland", ["Warszawa"]),
    ("Kraków", "Poland", ["Krakow", "Cracow"]),
    ("Wrocław", "Poland", ["Wroclaw", "Breslau"]),
    ("Prague", "Czechia", ["Praha", "Prag"]),
    ("Brno", "Czechia", []),
    ("Budapest", "Hungary", []),
    ("Bucharest", "Romania", ["București", "Bucuresti"]),
    ("Copenhagen", "Denmark", ["København", "Kobenhavn"]),
    ("Stockholm", "Sweden", []),
    ("Gothenburg", "Sweden", ["Göteborg", "Goteborg"]),
    ("Oslo", "Norway", []),
    ("Helsinki", "Finland", []),
    ("Athens", "Greece", ["Athina"]),
    ("Luxembourg", "Luxembourg", ["Luxembourg City"]),
    # ── Rest of world (anchors) ──────────────────────────────────────────────
    ("New York", "United States", ["NYC", "New York City"]),
    ("San Francisco", "United States", []),
    ("Boston", "United States", []),
    ("Chicago", "United States", []),
    ("Austin", "United States", []),
    ("Seattle", "United States", []),
    ("Toronto", "Canada", []),
    ("Vancouver", "Canada", []),
    ("Montreal", "Canada", ["Montréal"]),
    ("Bengaluru", "India", ["Bangalore"]),
    ("Mumbai", "India", ["Bombay"]),
    ("Delhi", "India", ["New Delhi"]),
    ("Hyderabad", "India", []),
    ("Pune", "India", []),
    ("Chennai", "India", ["Madras"]),
    ("Gurugram", "India", ["Gurgaon"]),
    ("Noida", "India", []),
    ("Dubai", "United Arab Emirates", []),
    ("Singapore", "Singapore", []),
    ("Sydney", "Australia", []),
    ("Melbourne", "Australia", []),
]

# German federal states + common metro/region labels — a recruiter may want a
# whole state, and LinkedIn emits metro labels ("Rhine-Main Metropolitan Area").
# These resolve to a country and are offered in the typeahead as region entries.
_REGIONS: List[Tuple[str, str, List[str]]] = [
    ("Bavaria", "Germany", ["Bayern"]),
    ("Baden-Württemberg", "Germany", ["Baden-Wurttemberg", "Baden Wurttemberg"]),
    ("North Rhine-Westphalia", "Germany", ["Nordrhein-Westfalen", "NRW"]),
    ("Hesse", "Germany", ["Hessen"]),
    ("Lower Saxony", "Germany", ["Niedersachsen"]),
    ("Rhineland-Palatinate", "Germany", ["Rheinland-Pfalz"]),
    ("Saxony", "Germany", ["Sachsen"]),
    ("Brandenburg", "Germany", []),
    ("Schleswig-Holstein", "Germany", []),
    ("Thuringia", "Germany", ["Thüringen", "Thuringen"]),
    ("Saarland", "Germany", []),
    ("Rhine-Main", "Germany", ["Rhein-Main", "Rhine-Main Metropolitan Area"]),
    ("Ruhr", "Germany", ["Ruhrgebiet", "Ruhr Area"]),
    ("Rhineland", "Germany", ["Rheinland"]),
]

# Countries as first-class picks (for remote / country-wide searches).
_COUNTRIES: List[str] = [
    "Germany", "Austria", "Switzerland", "Netherlands", "Belgium", "France",
    "United Kingdom", "Ireland", "Spain", "Portugal", "Italy", "Poland",
    "Czechia", "Hungary", "Romania", "Denmark", "Sweden", "Norway", "Finland",
    "Greece", "Luxembourg", "United States", "Canada", "India",
    "United Arab Emirates", "Singapore", "Australia",
]


# ── Matching primitives ──────────────────────────────────────────────────────


def _fold(s: str) -> str:
    """Lowercase + strip diacritics + collapse separators for match keys.

    "Zürich" → "zurich", "Baden-Württemberg" → "baden wurttemberg". So a
    recruiter never has to reproduce an umlaut or a hyphen to find a place.
    """
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    ascii_ = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_ = ascii_.replace("ß", "ss")
    ascii_ = re.sub(r"[^0-9a-z]+", " ", ascii_.lower())
    return ascii_.strip()


class _Entry:
    __slots__ = ("label", "country", "kind", "keys", "primary", "order")

    def __init__(self, label: str, country: str, kind: str, keys: List[str],
                 order: int):
        self.label = label          # "Koblenz, Germany"
        self.country = country      # "Germany"
        self.kind = kind            # city | region | country
        self.keys = keys            # folded match keys
        self.primary = keys[0] if keys else ""
        # Catalogue position — cities are listed largest-talent-pool first within
        # a country, so this is the tiebreak that puts Berlin above Bern for "ber".
        self.order = order


@lru_cache(maxsize=1)
def _entries() -> List[_Entry]:
    out: List[_Entry] = []
    i = 0
    for city, country, aliases in _CITIES:
        keys = [_fold(city)] + [_fold(a) for a in aliases]
        out.append(_Entry(f"{city}, {country}", country, "city", keys, i)); i += 1
    for region, country, aliases in _REGIONS:
        keys = [_fold(region)] + [_fold(a) for a in aliases]
        out.append(_Entry(f"{region}, {country}", country, "region", keys, i)); i += 1
    for country in _COUNTRIES:
        out.append(_Entry(country, country, "country", [_fold(country)], i)); i += 1
    return out


@lru_cache(maxsize=1)
def _key_index() -> Dict[str, _Entry]:
    """Folded-key → entry, for O(1) exact and gazetteer lookups. First writer
    wins so a city keeps its slot over a same-named alias."""
    idx: Dict[str, _Entry] = {}
    for e in _entries():
        for k in e.keys:
            idx.setdefault(k, e)
    return idx


# ── Public API ───────────────────────────────────────────────────────────────


def suggest(q: str, limit: int = 8) -> List[Dict[str, str]]:
    """Typeahead: places whose name starts with / contains ``q``.

    Ranking: exact key, then prefix, then substring; cities before regions before
    countries within a rank (talent lives in cities). Returns display-ready rows:
    ``{"label", "country", "kind"}``. Empty query → []. Fully offline.
    """
    fq = _fold(q)
    if not fq:
        return []
    kind_rank = {"city": 0, "region": 1, "country": 2}
    scored: List[Tuple[Tuple[int, int, int], _Entry]] = []
    seen: set[str] = set()
    for e in _entries():
        if e.label in seen:
            continue
        best: Optional[int] = None
        for k in e.keys:
            if k == fq:
                rank = 0
            elif k.startswith(fq):
                rank = 1
            elif fq in k:
                rank = 2
            else:
                continue
            best = rank if best is None else min(best, rank)
        if best is not None:
            seen.add(e.label)
            scored.append(((best, kind_rank.get(e.kind, 3), e.order), e))
    scored.sort(key=lambda t: t[0])
    return [{"label": e.label, "country": e.country, "kind": e.kind}
            for _, e in scored[:max(1, limit)]]


def _entry_for(text: str, *, fuzzy: bool = True) -> Optional[_Entry]:
    """Resolve a free-typed location to a catalogue entry, or None.

    Three stages, tightening tolerance as they go:
      1. Exact folded match on the whole string or its leading (city) segment.
      2. Any folded catalogue key appearing as a whole word inside the string
         ("frankfurt am main" contains "frankfurt").
      3. (fuzzy only) repair of a single mis-typed token against city keys (edit
         distance ≤ 1, length ≥ 5) — this is what snaps "kolenz" → "koblenz".

    ``fuzzy`` gates stage 3. Input normalisation wants it on (repair the typo the
    recruiter/AI made); the location GATE wants it off — a fuzzy match must never
    manufacture a wrong-country rejection out of an unrelated place name.
    """
    if not text:
        return None
    idx = _key_index()
    fq = _fold(text)
    if not fq:
        return None
    # 1. exact whole string, then the first comma segment (the city part).
    if fq in idx:
        return idx[fq]
    head = _fold(text.split(",")[0])
    if head and head in idx:
        return idx[head]
    # 2. (fuzzy) repair the head/city segment BEFORE generic containment — the
    #    recruiter typed the city, and a one-off typo there ("kolenz") must win
    #    over the trailing country qualifier ("…, Germany") sitting next to it.
    if fuzzy:
        hit = _fuzzy_city(head)
        if hit:
            return hit
    # 3. whole-word containment. Prefer the most specific match: a city over a
    #    region over a country, then an earlier position, then a longer key
    #    (so "san francisco" beats a stray "san"). This keeps a real city name
    #    from being shadowed by a trailing country/region word.
    words = fq.split()
    wordset = set(words)
    kind_rank = {"city": 0, "region": 1, "country": 2}
    best: Optional[Tuple[Tuple[int, int, int], _Entry]] = None
    for key, e in idx.items():
        kw = key.split()
        if len(kw) == 1:
            pos = words.index(kw[0]) if kw[0] in wordset else None
        else:
            pos = _run_pos(words, kw)
        if pos is None:
            continue
        score = (kind_rank.get(e.kind, 3), pos, -len(kw))
        if best is None or score < best[0]:
            best = (score, e)
    if best is not None:
        return best[1]
    # 4. (fuzzy) last resort — repair any remaining token against city keys.
    if not fuzzy:
        return None
    for w in words:
        hit = _fuzzy_city(w)
        if hit:
            return hit
    return None


def _fuzzy_city(token: str) -> Optional[_Entry]:
    """The single-word city entry within edit-distance 1 of ``token`` (length ≥5
    so short words can't collide), or None. This is what snaps 'kolenz' →
    'Koblenz'."""
    if not token or len(token) < 5:
        return None
    for key, e in _key_index().items():
        if e.kind != "city" or " " in key:
            continue
        if abs(len(key) - len(token)) <= 1 and _edit_within_one(token, key):
            return e
    return None


def normalize(text: Optional[str]) -> Optional[str]:
    """Canonical catalogue label for a location string, or None if unrecognised.

    "kolenz, germany" → "Koblenz, Germany"; "Frankfurt am Main" → "Frankfurt,
    Germany"; "somewhereville" → None (caller keeps the raw text). Used to make
    both search engines receive the SAME, correctly-spelled location and to
    repair AI-proposed typos before they zero a search.
    """
    e = _entry_for(text or "", fuzzy=True)
    return e.label if e else None


def country_of(text: Optional[str], *, fuzzy: bool = False) -> Optional[str]:
    """The country a location resolves to, or None. Powers the gate's gazetteer.

    Defaults to STRICT (``fuzzy=False``): the gate must resolve only on a real
    name match, never a typo-repaired guess, so it can't invent a wrong-country
    rejection. Input normalisation calls ``normalize`` (fuzzy) instead."""
    e = _entry_for(text or "", fuzzy=fuzzy)
    return e.country if e else None


def place_country_map() -> Dict[str, str]:
    """folded single-word place key → lowercase country, for the resolver gate.

    Only unambiguous single-word city/region keys (so "koblenz" maps but "new
    york" — which the resolver matches via its own sequence logic — is skipped
    here to avoid partial-word hits)."""
    out: Dict[str, str] = {}
    for e in _entries():
        if e.kind == "country":
            continue
        for k in e.keys:
            if " " not in k:
                out.setdefault(k, e.country.lower())
    return out


# ── tiny helpers (no external deps) ──────────────────────────────────────────


def _run_pos(hay: List[str], needle: List[str]) -> Optional[int]:
    """Start index of ``needle`` as a contiguous whole-word run in ``hay``, else
    None."""
    n = len(needle)
    if not n:
        return None
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    return None


def _edit_within_one(a: str, b: str) -> bool:
    """True if ``a`` is within Levenshtein distance 1 of ``b`` (sub/ins/del).

    Small and allocation-light: the strings are single place tokens. Used only
    for the conservative typo repair in ``_entry_for`` (length-gated to ≥5 so
    short words can't collide)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:  # one substitution
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    # one insertion/deletion — walk the shorter against the longer.
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    i = j = 0
    diff = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            diff += 1
            if diff > 1:
                return False
            j += 1
    return True
