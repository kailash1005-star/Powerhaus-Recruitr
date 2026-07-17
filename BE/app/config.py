"""
Application Configuration
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # MongoDB Configuration
    MONGODB_URI: str = Field(description="MongoDB connection string")
    DATABASE_NAME: str = Field(default="Job-Hunt", description="Database name")

    # API Configuration
    API_V1_PREFIX: str = "/api/v1"

    # CORS Configuration
    #
    # Under the BFF model the browser never calls this API directly — Next.js on
    # Vercel calls it server-to-server, and server-to-server requests aren't
    # subject to CORS at all. So this list only exists for direct browser access
    # during local development and for hitting /docs by hand.
    #
    # The old "https://job-hunt-kappa-two.vercel.app" entry was a leftover from a
    # different project and has been removed: every allowed origin is an origin
    # permitted to read authenticated responses, so stale entries are a real
    # (if small) hole, not just untidiness.
    CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:3000"],
        description="Allowed CORS origins (browser-direct only; the BFF proxy doesn't need these)",
    )

    # LinkedIn Credentials (for company info API)
    LINKEDIN_EMAIL: str = Field(default="", description="LinkedIn login email")
    LINKEDIN_PASSWORD: str = Field(default="", description="LinkedIn login password")
    # Directory where the authenticated LinkedIn session cookies are cached.
    # A valid cached session is reused across searches AND process restarts so we
    # log in once instead of per-search (fewer logins → far fewer captcha hits).
    # NOTE: linkedin_api treats this as a string prefix, so it MUST end with a separator.
    LINKEDIN_COOKIE_DIR: str = Field(
        default=".linkedin_cookies/",
        description="Directory (with trailing slash) to persist the LinkedIn session cookie jar",
    )
    # Residential proxy for LinkedIn calls. LinkedIn challenges datacenter IPs with
    # captchas, so production must route LinkedIn traffic through a residential IP.
    # Format: http://user:pass@host:port  (leave blank to disable / use direct connection).
    LINKEDIN_PROXY_URL: str = Field(default="", description="Residential proxy URL for LinkedIn requests")
    # Browser session cookies — the RELIABLE way to authenticate. Username/password
    # logins get CHALLENGE'd by LinkedIn (even on residential IPs). Instead, log into
    # LinkedIn in a browser, open DevTools → Application → Cookies → linkedin.com, and
    # copy the `li_at` and `JSESSIONID` values here. When both are set we inject the
    # session and skip the password login (no challenge). Refresh them when expired.
    LINKEDIN_LI_AT: str = Field(default="", description="LinkedIn li_at session cookie from a logged-in browser")
    LINKEDIN_JSESSIONID: str = Field(default="", description="LinkedIn JSESSIONID cookie from a logged-in browser")

    # Firecrawl Configuration
    # No default: a live key used to sit here and is therefore burned (it's in git
    # history — rotate it, see AUTH0_SETUP.md Step 10). An empty default makes a
    # missing key fail loudly at the call site instead of silently falling back to
    # a compromised credential.
    FIRECRAWL_API_KEY: str = Field(
        default="",
        description="Firecrawl API Key",
    )

    # OpenAI (Phase 2 — company industry resolution)
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key")

    # Apollo (Phase 3 — prospect search)
    APOLLO_API_KEY: str = Field(default="", description="Apollo.io API key")
    APOLLO_WEBHOOK_URL: str = Field(
        default="",
        description="Publicly reachable URL Apollo POSTs revealed phone numbers to "
                    "(e.g. https://<host>/api/v1/jobs/prospects/mobile-webhook).",
    )

    # ── Candidate enrichment (Apify / HarvestAPI LinkedIn profile scraper) ──
    # Apollo gives identity + company + verified email but NO résumé depth
    # (skills/education/certs/experience descriptions). The HarvestAPI actor on
    # Apify returns that depth for ~$0.004/profile with no cookies/proxy on our
    # side (the vendor runs its own authenticated account pool). We call it for
    # a SELECTED set of candidates on-demand, then merge with the Apollo record.
    APIFY_TOKEN: str = Field(default="", description="Apify API token (Console → Settings → Integrations)")
    APIFY_PROFILE_ACTOR: str = Field(
        default="harvestapi/linkedin-profile-scraper",
        description="Apify actor id for the LinkedIn profile scraper",
    )
    # Actor mode enum. We use the profile-only ($4/1k) mode and keep Apollo's
    # email; the "+ email search ($10/1k)" value is the alternative.
    APIFY_PROFILE_MODE: str = Field(
        default="Profile details no email ($4 per 1k)",
        description="Apify actor profileScraperMode enum value",
    )
    # LinkedIn people-SEARCH actor (discovery). Finds candidates by filters
    # (title/location/industry/seniority/…) and returns short profiles; we then
    # deep-enrich each via the profile scraper above. Short mode = $0.1/page.
    APIFY_SEARCH_ACTOR: str = Field(
        default="harvestapi/linkedin-profile-search",
        description="Apify actor id for the LinkedIn profile search",
    )
    APIFY_SEARCH_MODE: str = Field(
        default="Short",
        description='Apify search actor profileScraperMode — one of "Short", "Full", "Full + email search"',
    )
    # LinkedIn COMPANY details actor (Phase 2 industry/domain resolution). Replaces
    # the fragile self-hosted linkedin_api company lookup with a managed
    # account+proxy "no cookies" scraper at ~$0.004/company.
    APIFY_COMPANY_ACTOR: str = Field(
        default="harvestapi/linkedin-company",
        description="Apify actor id for the LinkedIn company details scraper",
    )
    # Hard cap on profiles per enrichment call — a runaway-cost guard.
    APIFY_ENRICH_MAX: int = Field(default=25, description="Max profiles enriched per call")
    # Profiles per actor RUN. The scraper (and free plans especially) cap items
    # per run — requesting more than the cap makes the actor refuse the WHOLE run
    # and return zero profiles. We chunk a batch into runs of this size so a large
    # request still yields partial data. Free Apify plan = 10 items/run.
    APIFY_ENRICH_BATCH: int = Field(default=10, description="Profiles per Apify actor run (chunk size)")
    # Don't re-enrich (and re-pay for) the same profile within this many days.
    PROFILE_CACHE_TTL_DAYS: int = Field(default=30, description="Profile enrichment cache TTL (days)")

    # ── AI Engineer agent (Pydantic AI, provider-swappable) ─────────────
    # Model is a Pydantic AI model string: "<provider>:<model>".
    #   OpenAI:    openai:gpt-4o
    #   Anthropic: anthropic:claude-sonnet-4-6
    #   Google:    google-gla:gemini-2.5-pro
    #   OpenRouter:openrouter:anthropic/claude-sonnet-4-6
    # Swap providers by changing this one string (set AGENT_MODEL in .env).
    AGENT_MODEL: str = Field(default="openai:gpt-4o", description="Pydantic AI model string for the agent")
    AGENT_SYSTEM_PROMPT: str = Field(default="", description="Override the agent's system prompt (blank = built-in default)")
    # Provider API keys (pushed into os.environ for Pydantic AI at agent build).
    ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic API key (for anthropic: models)")
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key (for google-gla: models)")
    OPENROUTER_API_KEY: str = Field(default="", description="OpenRouter API key (for openrouter: models)")

    # ── Agentic candidate sourcing (Strategist + Broadener) ─────────────
    # Two Pydantic AI agents that turn a job into LinkedIn search filters:
    #   • Strategist (smart) — reads the JD + recruiter brief ONCE and proposes
    #     the filters a real person would actually match, plus a broadening
    #     ladder to fall back on. Reasoning only, no tools, no vendor spend.
    #   • Broadener (fast) — called only when a search returns zero, to relax the
    #     filters for the next attempt. Runs up to MAX_BROADEN_ATTEMPTS times.
    # Both take a Pydantic AI model string, so providers swap the same way
    # AGENT_MODEL does.
    SOURCING_STRATEGY_MODEL: str = Field(
        default="openai:gpt-4o",
        description="Pydantic AI model for the search Strategist (one call per prefill)",
    )
    SOURCING_BROADEN_MODEL: str = Field(
        default="openai:gpt-4o-mini",
        description="Pydantic AI model for the Broadener (one call per zero-result retry)",
    )
    # Hard cost guard: a zero-result search triggers at most this many broadened
    # retries. Each retry is a fresh Apify search page (~$0.10) plus enrichment of
    # whatever it finds, so this bounds the worst-case spend per discovery run.
    SOURCING_MAX_BROADEN_ATTEMPTS: int = Field(
        default=3,
        description="Max agent-broadened retries after a zero-result candidate search",
    )

    # ── MCP tool servers the agent connects to ──────────────────────────
    # The agent's tools come from MCP server(s). Point it at the LinkedIn MCP
    # server we built. Prefer HTTP (run it as a service) OR stdio (spawn it).
    #   HTTP : set AGENT_MCP_LINKEDIN_HTTP_URL=http://127.0.0.1:8765/mcp
    #   stdio: set AGENT_MCP_LINKEDIN_DIR=C:/Users/WELCOME/Desktop/Linked-MCP/ai-version
    # Leave both blank to run the agent as a plain chat assistant (no tools).
    AGENT_MCP_LINKEDIN_HTTP_URL: str = Field(default="", description="Streamable-HTTP URL of the LinkedIn MCP server")
    AGENT_MCP_LINKEDIN_DIR: str = Field(default="", description="Project dir of the LinkedIn MCP server (spawned via 'uv run linkedin-mcp')")
    AGENT_MCP_AUTH_TOKEN: str = Field(default="", description="Bearer token for the LinkedIn MCP server (HTTP transport)")

    # ── Matching Engine (CV ↔ JD) ──────────────────────────────────────────
    # Embeddings (OpenAI). 3-small=1536 dim (cheap, strong); 3-large=3072.
    EMBEDDING_MODEL: str = Field(default="text-embedding-3-small", description="OpenAI embedding model")
    EMBEDDING_DIM: int = Field(default=1536, description="Embedding vector dimension (must match the model)")
    # LLM models: cheap one for structured field extraction, stronger for the
    # final top-N reasoning.
    EXTRACTION_MODEL: str = Field(default="gpt-4o-mini", description="LLM for CV/JD structured extraction")
    REASONING_MODEL: str = Field(default="gpt-4o-mini", description="LLM for top-N candidate reasoning")

    # Vector backend: "mongo" (default — in-DB brute-force cosine, no extra infra,
    # ideal for the 50-CV demo) or "pinecone" (for scale).
    VECTOR_BACKEND: str = Field(default="mongo", description='Vector store backend: "mongo" or "pinecone"')
    PINECONE_API_KEY: str = Field(default="", description="Pinecone API key (only if VECTOR_BACKEND=pinecone)")
    PINECONE_INDEX: str = Field(default="recruitr-cv", description="Pinecone index name")
    PINECONE_NAMESPACE: str = Field(default="default", description="Pinecone namespace (reserved for tenant isolation)")

    # Matching tunables
    MATCH_RETRIEVE_K: int = Field(default=50, description="How many candidates to pull from the vector store")
    MATCH_REASON_TOP_N: int = Field(default=10, description="How many top candidates get LLM reasoning")
    MATCH_RETURN_TOP: int = Field(default=5, description="Final number of candidates returned to the recruiter")

    # Pre-screen gate — judges a search hit on its free title/location BEFORE the
    # per-profile Apify enrichment spend. Deliberately lopsided: a false drop is
    # unrecoverable, a false keep costs one scrape, so only near-zero scores drop.
    # Calibrated on 39 real SAP-sourced candidates: at 25 it keeps every genuine
    # SAP consultant, drops only executive slop (CEO/Chairman/Geschäftsführer),
    # and drops ~93% of those same people when aimed at an unrelated payroll role.
    PRESCREEN_ENABLED: bool = Field(default=True, description="Gate search hits before paying to enrich them")
    PRESCREEN_MIN_SCORE: float = Field(default=25.0, description="Drop search hits scoring below this (0-100)")

    # Upload guard rails
    MAX_UPLOAD_MB: int = Field(default=10, description="Max size per uploaded document (MB)")

    # ── Outreach email (candidate contact) ─────────────────────────────────
    # Draft generation needs only OPENAI_API_KEY. Actual SENDING needs SMTP
    # creds below — leave blank to keep send disabled (draft-only).
    SMTP_HOST: str = Field(default="", description="SMTP server host (e.g. smtp.gmail.com)")
    SMTP_PORT: int = Field(default=587, description="SMTP port (587 STARTTLS)")
    SMTP_USER: str = Field(default="", description="SMTP username")
    SMTP_PASSWORD: str = Field(default="", description="SMTP password / app password")
    SMTP_FROM: str = Field(default="", description="From email address")
    SMTP_FROM_NAME: str = Field(default="Recruitr", description="From display name")
    OUTREACH_SENDER_NAME: str = Field(default="The Talent Team", description="Signature name used in drafted emails")

    # ── Outreach tracking (Smartlead sending + Cal.com meetings) ───────────
    # The CRM (Outreach → Leads/Candidates) renders off a read model fed by
    # provider WEBHOOKS. None of these are required for the UI to load — when
    # absent, the CRM simply shows no rows and /health reports "not configured".
    #   • Sending/enrollment needs SMARTLEAD_API_KEY (+ a campaign id).
    #   • Webhook ingestion needs only the endpoints to be reachable; the
    #     *_WEBHOOK_SECRET values turn on signature verification when set.
    OUTREACH_PROVIDER: str = Field(default="smartlead", description="Outreach sending provider")
    OUTREACH_TENANT_ID: str = Field(default="default", description="Tenant scope for outreach docs")
    SMARTLEAD_API_KEY: str = Field(default="", description="Smartlead API key (for enrolling leads/candidates)")
    SMARTLEAD_BASE_URL: str = Field(default="https://server.smartlead.ai/api/v1", description="Smartlead API base URL")
    SMARTLEAD_DEFAULT_CAMPAIGN_ID: str = Field(default="", description="Default Smartlead campaign id for enrollment")
    SMARTLEAD_WEBHOOK_SECRET: str = Field(default="", description="Shared secret to verify Smartlead webhook signatures")
    # Cal.com (meetings). Cal.com signs webhooks with HMAC-SHA256 over the raw body.
    CALCOM_API_KEY: str = Field(default="", description="Cal.com API key (optional, for future scheduling sync)")
    CALCOM_WEBHOOK_SECRET: str = Field(default="", description="Cal.com webhook signing secret (X-Cal-Signature-256)")

    # Company rejection threshold
    MAX_STAFF_COUNT: int = Field(
        default=10000,
        description="Reject companies with more employees than this",
    )

    # ── Auth0 ───────────────────────────────────────────────────────────
    # The API verifies RS256 access tokens against Auth0's published JWKS. There
    # is no shared secret here: the signing key is Auth0's PRIVATE key and we
    # only ever hold the public half, fetched from AUTH0_DOMAIN.
    #
    # Domain vs issuer is a deliberate, easily-fumbled asymmetry:
    #   AUTH0_DOMAIN = "tenant.eu.auth0.com"       — bare host, no scheme/slash
    #   AUTH0_ISSUER = "https://tenant.eu.auth0.com/"  — scheme AND trailing slash
    # The issuer is compared character-for-character against the token's `iss`,
    # so it must match exactly what Auth0 mints. We derive it from the domain by
    # default (see auth0_issuer) rather than make you keep both in sync by hand.
    AUTH0_DOMAIN: str = Field(default="", description="Auth0 tenant domain, e.g. recruitr-prod.eu.auth0.com (no scheme)")
    AUTH0_AUDIENCE: str = Field(default="", description="Auth0 API identifier, e.g. https://api.recruit.vanceltech.com")
    AUTH0_ISSUER: str = Field(default="", description="Override the issuer; blank derives it from AUTH0_DOMAIN")

    # Kill switch for local dev and the test suite ONLY. Guarded in
    # startup_checks: the app refuses to boot with auth off while a real Auth0
    # domain is configured, so this can't silently disable auth in production.
    AUTH_ENABLED: bool = Field(default=True, description="Verify bearer tokens. Never disable outside local dev/tests.")

    # Namespace for custom claims. Auth0 silently DROPS non-namespaced custom
    # claims, so this prefix is load-bearing, not cosmetic. Must match the
    # post-login Action (see AUTH0_SETUP.md, Step 7).
    AUTH0_CLAIM_NAMESPACE: str = Field(
        default="https://recruit.vanceltech.com/",
        description="URL prefix for custom claims (tenant_id, roles)",
    )

    # JWKS responses are cached in-process by PyJWKClient. Cloud Run scales to
    # many instances and each keeps its own cache; this only bounds how long a
    # rotated Auth0 signing key takes to be picked up.
    AUTH0_JWKS_CACHE_TTL: int = Field(default=600, description="Seconds to cache the Auth0 JWKS")

    # Small allowance for clock drift between Auth0 and Cloud Run when checking
    # exp/iat. Seconds. Keep tight — this is a window where an expired token is
    # still accepted.
    AUTH0_LEEWAY: int = Field(default=10, description="Clock-skew leeway in seconds for exp/iat")

    @property
    def auth0_issuer(self) -> str:
        """The expected `iss` claim. Explicit override wins; otherwise derive it
        from the domain in the exact shape Auth0 mints (https + trailing slash)."""
        if self.AUTH0_ISSUER:
            return self.AUTH0_ISSUER
        if not self.AUTH0_DOMAIN:
            return ""
        return f"https://{self.AUTH0_DOMAIN.strip().rstrip('/')}/"

    @property
    def auth0_jwks_url(self) -> str:
        """Where Auth0 publishes the public signing keys for this tenant."""
        if not self.AUTH0_DOMAIN:
            return ""
        return f"https://{self.AUTH0_DOMAIN.strip().rstrip('/')}/.well-known/jwks.json"

    @property
    def auth0_configured(self) -> bool:
        return bool(self.AUTH0_DOMAIN and self.AUTH0_AUDIENCE)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# ────────────────────────────────────────────────────────────────────────────
# Title filter keywords — executive / leadership level
# (ported from reference/config.py)
# ────────────────────────────────────────────────────────────────────────────

ACCEPTED_TITLE_KEYWORDS = [
    # C-Suite
    "ceo", "cfo", "coo", "cto", "cio", "cmo", "chro", "cro",
    "chief executive", "chief financial", "chief operating",
    "chief technology", "chief information", "chief medical",
    "chief human resources", "chief revenue", "chief administrative",
    "chief nursing", "chief product", "chief sustainability",
    "chief risk", "chief data", "chief marketing",
    # Executive
    "executive director", "managing director", "general manager",
    "president", "city manager", "town manager", "deputy city manager",
    "general counsel",
    # VP
    "vice president", "vp ", "vp,", "avp", "assistant vice president",
    "senior vice president", "svp",
    # Director
    "director",
    # Head
    "head of",
    # Senior Advisor
    "senior advisor",
    # Other leadership
    "plant manager", "general superintendent",
    "board director",
]



# ────────────────────────────────────────────────────────────────────────────
# Apollo API settings
# ────────────────────────────────────────────────────────────────────────────

APOLLO_BASE_URL = "https://api.apollo.io/api/v1"
APOLLO_PER_PAGE = 100
APOLLO_BULK_BATCH_SIZE = 10
APOLLO_SENIORITIES = ["c_suite", "vp", "head", "director"]

# Restrict prospect (buyer) search to the HR department only. Apollo's
# `person_department_or_subdepartments[]` accepts a master department value that
# covers every HR sub-department (HR, People Ops, Talent Acquisition, Recruiting,
# Comp & Benefits, L&D, etc.). Sending this on every people search makes Apollo
# return ONLY HR-function profiles — no general Operations, Finance, Tech, etc.
# NOTE: applies to prospect/buyer search only, NOT recruitment candidate search.
APOLLO_HR_DEPARTMENTS = ["master_human_resources"]

# ────────────────────────────────────────────────────────────────────────────
# Buyer-persona filter rules (used by ProspectPreFilter / ProspectPostFilter)
# ────────────────────────────────────────────────────────────────────────────

HR_KEYWORDS = ["hr", "human resource", "people", "talent", "recruitment", "recruiting", "workforce", "culture"]
# Pure operations keywords — kept for reference but NO LONGER treated as a wanted
# signal. We source HR-operations profiles only, not general business operations.
OPS_KEYWORDS = ["operation", "ops"]
CSUITE_SENIORITIES = {"c_suite", "owner", "founder", "partner"}
# Only HR-domain functions are wanted. General "operation"/"ops" are intentionally
# excluded so the search returns HR profiles, not other operations roles.
WANTED_FUNCTIONS = ["hr", "human resource", "people", "talent", "recruiting", "recruitment"]
UNWANTED_FUNCTIONS = [
    "operation", "ops", "finance", "financial", "marketing", "sales",
    "technology", "tech", "analytics", "asset", "culinary", "food",
    "recreation", "care", "clinical", "medical", "information technology",
]

# Per-industry exclusions from UNWANTED_FUNCTIONS. Now empty: every non-HR
# function is unwanted across all industries since we source HR profiles only.
INDUSTRY_UNWANTED_EXCLUSIONS: dict[str, list[str]] = {}

# ────────────────────────────────────────────────────────────────────────────
# Persona titles (used as Apollo title-search seed)
#
# We source HR / People / Talent leadership ONLY — no general Operations, CEO,
# CTO, CFO, medical, etc. Because the search is now restricted to a single
# HR-domain persona set (plus the Apollo HR-department filter), there is no
# longer a per-industry split: every industry uses DEFAULT_PERSONA_TITLES.
# INDUSTRY_PERSONA_MAP is kept (empty) so existing call sites that look up by
# industry transparently fall back to the HR-only default.
# ────────────────────────────────────────────────────────────────────────────

INDUSTRY_PERSONA_MAP: dict[str, list[str]] = {}

# HR / People / Talent leadership titles used to seed Apollo prospect search.
DEFAULT_PERSONA_TITLES = [
    "Chief Human Resources Officer",
    "Chief People Officer",
    "VP of Human Resources",
    "VP of People & Culture",
    "VP of Talent Acquisition",
    "Head of People",
    "Head of HR",
    "Head of Talent Acquisition",
    "Director of Human Resources",
    "Director of Talent Acquisition",
    "Head of People Operations",
    "HR Director",
]


def normalize_industry_name(name: str) -> str:
    """Normalize an industry display name to a lookup key (lowercase, underscores)."""
    if not name:
        return ""
    return name.lower().strip().replace("&", "and").replace("-", " ").replace("/", " ").replace(",", " ").replace("  ", " ").replace(" ", "_")


def get_persona_titles(industry_name: str | None) -> list[str]:
    """Resolve an industry display name to persona titles, with fallback."""
    if not industry_name:
        return DEFAULT_PERSONA_TITLES
    key = normalize_industry_name(industry_name)
    return INDUSTRY_PERSONA_MAP.get(key, DEFAULT_PERSONA_TITLES)


settings = Settings()
