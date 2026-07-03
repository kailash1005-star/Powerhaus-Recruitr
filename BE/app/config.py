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
    CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "https://job-hunt-kappa-two.vercel.app"],
        description="Allowed CORS origins",
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
    FIRECRAWL_API_KEY: str = Field(
        default="fc-a5218360c4624ed9b764dc0305c9d0ba",
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
    # Hard cap on profiles per enrichment call — a runaway-cost guard.
    APIFY_ENRICH_MAX: int = Field(default=25, description="Max profiles enriched per call")
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
