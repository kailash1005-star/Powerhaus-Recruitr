# Sourcing Input Case Study — what inputs actually raise recall + relevance

Grounding for the unified discovery redesign. Sources: HarvestAPI
`linkedin-profile-search` actor input-schema + README (Apify), and Apollo
`POST /mixed_people/api_search` API reference (docs.apollo.io). Scraped 2026-07-21.

The goal of the redesign: **one screen, AI proposes inputs for BOTH engines, both
run concurrently with fallbacks, results merge** — maximising quantity of
*related* candidates. This doc says what "good input" means for each engine so the
Strategist prompt and the filter UI stop fighting the APIs.

---

## Part A — Apify actor (`harvestapi/linkedin-profile-search`)

The actor is a thin driver over **LinkedIn's own people-search**. Every filter is
a real LinkedIn search-sidebar filter; the enum filters (`yearsOfExperienceIds`,
`seniorityLevelIds`, `functionIds`, `companyHeadcount`) are LinkedIn's own
structured IDs, sent as **arrays of codes**. So the question "does the API support
this filtering?" — yes, all of it maps 1:1 to LinkedIn. The real question is
**recall cost**, below.

### A1. Field-by-field contract (verified from input-schema)

| Field | Type | Notes that change how we should use it |
|---|---|---|
| `searchQuery` | string ≤300 | **Fuzzy keyword match, supports LinkedIn search operators** (`AND`/`OR`/`NOT`/quotes). Matches profile *content*, not just the title line. |
| `currentJobTitles` | array ≤50 | LinkedIn current-title filter. OR within the list. |
| `pastJobTitles` | array ≤50 | |
| `locations` | array ≤70 | **LinkedIn mis-parses short text** — `"UK"` → *Ukraine*. Must send `"United Kingdom"`, `"Koblenz, Germany"` etc. First autocomplete suggestion wins. |
| `yearsOfExperienceIds` | string[] codes | LinkedIn-**inferred** band. Sparse/wrong on many profiles. |
| `seniorityLevelIds` | string[] codes | LinkedIn-**inferred**. `120`=Senior etc. |
| `functionIds` | string[] codes | LinkedIn-**inferred** function. `6`=Consulting, `13`=IT. |
| `companyHeadcount` | string[] codes | Current employer size. |
| `profileLanguages` | string[] | Profile UI language, not spoken languages. |
| `industryIds` | array | LinkedIn industry v2 codes (numeric), **not free text**. |
| `recentlyChangedJobs` / `recentlyPostedOnLinkedIn` | bool | 90-day / 30-day activity. |
| `exclude*` | arrays | Symmetric excludes for every filter. |
| `maxItems` | int | Cap. `0` = up to 2500 per query (LinkedIn hard cap per query). |
| `takePages` | int | 1 page = **25 profiles**. Required when post-filtering. |
| `autoQuerySegmentation` | bool | **Splits a broad query into sub-queries to break past LinkedIn's 2500/query ceiling — up to 100k.** This is the single biggest recall lever we are not using. |
| `postFilteringMongoDbQuery` | object | Mongo filter applied *after* scrape (e.g. `skills.$all`). Costs full scrape — refines, does not save spend. |

### A2. The filter-recall trap (answers the "keep them generic?" question)

Every added filter is **AND**-ed and **shrinks** the pool. Three of the four enum
filters are *LinkedIn-inferred* fields that are missing or wrong on a large share
of profiles, so filtering on them silently drops good people:

- **`seniorityLevel`** — LinkedIn derives this; a "Senior SAP EWM Consultant" with
  8 yrs may be tagged `Entry`/blank. Filtering `seniorityLevelIds=[120]` throws
  them out. **The JD already carries seniority; encode it in the *title* words
  ("Senior …"), not in this filter.**
- **`yearsOfExperience`** — inferred from first-job date; contractors, career
  changers and non-linear histories are mis-banded. High false-negative rate.
- **`function`** — coarse. An SAP consultant can be tagged IT *or* Consulting;
  pick one and you drop the other half.
- **`companyHeadcount`** — only meaningful when the recruiter genuinely wants
  enterprise-vs-startup; irrelevant to most specialist searches.

**Verdict / best practice:** the *hard* filters that carry real signal and low
false-negatives are **`currentJobTitles` + `locations` + `searchQuery`** (and
explicit `excludeCurrentJobTitles`). The four enum filters should **default to Any
("" / unset)** and only be set when the JD gives *unambiguous* support AND the
recruiter opts in. This is what the current Strategist prompt already tells the
LLM ("LEAVE A FILTER NULL when the JD doesn't support it") — but the UI defaults
and the "80% confident" example screenshot show the model still filling
`seniorityLevel=Senior` + `function=Consulting` + `yearsOfExperience=6-10` on a
single role, which quietly compounds three inferred-field AND-narrows. The
redesign should make **Any the default and require justification to narrow.**

### A3. Recall levers we should turn on

1. **`autoQuerySegmentation: true`** for the primary title search when
   `maxItems` is large — breaks the 2500/query ceiling, the direct answer to
   "get the highest amount of related candidates."
2. **Two channels** (already implemented): title-filtered search + keyword-only
   `searchQuery` search, merged. Keyword channel catches self-described profiles
   ("IT-Consultant bei X" whose body says SAP EWM) the title filter misses.
3. **Title family, not one title.** LinkedIn OR-matches the list, so 4–10
   in-specialty variants (abbrev + expanded + local-language + vendor product
   names) is strictly higher recall with no relevance loss.
4. **`searchQuery` should use the domain phrase** (`"SAP EWM"` `"SAP LES"`), not
   the posting title string — it is a keyword match, not a title match.

---

## Part B — Apollo (`POST /mixed_people/api_search`)

Free people-search (no credits), returns masked contacts. **Different matching
model from LinkedIn** — must be tuned separately, not fed the same filters.

### B1. Field-by-field contract (verified from API reference)

| Param | Notes |
|---|---|
| `person_titles[]` | **OR-match; adding more titles EXPANDS results.** Word-order variants help (`"sales director"`, `"director sales"`, `"director, sales"`). |
| `include_similar_titles` | Default **true** — `"marketing manager"` also returns `"content marketing manager"`. Set **false** only for strict matching. Keep true for recall. |
| `q_keywords` | Free-text words filter over the profile. This is where skills go (Apollo has no structured skills filter). **AND-narrows** — too many keyword terms collapses results, so use the 1–3 defining skills, not the whole list. |
| `person_locations[]` | **Where the person lives**, `"City, Country"` / `"State, US"` format. |
| `organization_locations[]` | Employer **HQ** location — different axis; don't confuse with `person_locations`. |
| `person_seniorities[]` | Enum: `owner, founder, c_suite, partner, vp, head, director, manager, senior, entry, intern`. **Derived from current title only.** OR-match, expands. |
| `organization_num_employees_ranges[]` | Headcount as `"1,10"`, `"250,500"` string ranges. |
| `q_organization_domains_list[]` | Employer domains — use for target-company poach AND same-company exclusion (up to 1000). |
| `contact_email_status[]` | `verified` etc. — useful later at reveal time. |
| `per_page` / `page` | ≤100/page, ≤500 pages, **50,000 display cap**. |

### B2. Bug found — `person_industries[]` is not a real Apollo param

`apollo_service.search_people()` sends `person_industries[]` (from the form's
"Industries" field). **The People Search API reference does not document
`person_industries[]`** — Apollo filters people-industry via
`organization_industry_tag_ids[]` (requires tag IDs, not free text). So the
Apollo "Industries" chips almost certainly **do nothing** today. Options:
1. Drop the industry field from the Apollo path (honest), or
2. Map it through `q_keywords`, or
3. Resolve to `organization_industry_tag_ids` (needs a tag lookup — heavier).
Recommend **(1) drop** for v1 and fold any industry signal into `q_keywords`.

### B3. Apollo best practice for recruiting recall

- Keep `include_similar_titles: true`.
- Feed a **title family** (same OR-expansion as Apify), plus word-order variants.
- Put **1–3 defining skills** in `q_keywords`, never the full must-have list.
- Use `person_seniorities[]` sparingly — it is derived from current title, so a
  title-family search already captures most of it; adding it AND-narrows.
- `person_locations` = candidate's residence (what recruiters want), *not*
  `organization_locations`.

---

## Part C — Consequences for the redesign

1. **One AI proposal, two rendered plans.** The Strategist should emit a
   `SearchFilters` (Apify) *and* an `ApolloPlan` (titles + q_keywords skills +
   locations + optional seniorities) from the same brief, so both engines get
   engine-appropriate input rather than the Apify filters shoved into Apollo.
2. **Focus title.** Add an explicit `focusTitle` (the interpreted, LinkedIn-real
   title, e.g. *"Senior SAP EWM/LES Consultant" → "SAP EWM Consultant"*) that
   anchors both engines and headlines the review screen.
3. **Enum filters default to Any.** Ship `yearsOfExperience`, `seniorityLevel`,
   `function`, `companyHeadcount` unset unless the JD unambiguously supports one
   *and* the model marks it high-confidence; render them as opt-in, with the
   reasoning attached. Prefer encoding seniority in title words.
4. **Turn on `autoQuerySegmentation`** for the title channel to lift volume.
5. **Concurrent + fallbacks + merge.** Apify (title+keyword channels, broadening
   ladder) and Apollo (title-family + q_keywords, its own title-shrink/location
   fallback) run in parallel; both upsert into `candidates` with `source` tags;
   dedup by LinkedIn URL where present so a both-engines hit is one row.
6. **Fix `person_industries[]`** per B2.
