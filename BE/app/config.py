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

# Reject these titles (non-executive roles that might contain exec keywords)
REJECTED_TITLE_KEYWORDS = [
    "assistant to", "secretary to", "office of the ceo",
    "executive assistant", "admin assistant",
    "coordinator", "analyst", "intern", "junior",
    "mayor", "councillor", "council member", "elected",
    "board directors", "non-director committee", "volunteer",
]

# ────────────────────────────────────────────────────────────────────────────
# Apollo API settings
# ────────────────────────────────────────────────────────────────────────────

APOLLO_BASE_URL = "https://api.apollo.io/api/v1"
APOLLO_PER_PAGE = 100
APOLLO_BULK_BATCH_SIZE = 10
APOLLO_SENIORITIES = ["c_suite", "vp", "head", "director"]

# ────────────────────────────────────────────────────────────────────────────
# Buyer-persona filter rules (used by ProspectPreFilter / ProspectPostFilter)
# ────────────────────────────────────────────────────────────────────────────

HR_KEYWORDS = ["hr", "human resource", "people", "talent", "recruitment", "workforce", "culture"]
OPS_KEYWORDS = ["operation", "ops"]
CSUITE_SENIORITIES = {"c_suite", "owner", "founder", "partner"}
WANTED_FUNCTIONS = ["hr", "human resource", "people", "talent", "operation", "ops"]
UNWANTED_FUNCTIONS = [
    "finance", "financial", "marketing", "sales", "technology", "tech",
    "analytics", "asset", "culinary", "food", "recreation", "care",
    "clinical", "medical", "information technology",
]

# Per-industry exclusions from UNWANTED_FUNCTIONS — keyed by normalized industry name
INDUSTRY_UNWANTED_EXCLUSIONS = {
    "healthcare":           ["care", "clinical", "medical"],
    "medical_technology":   ["care", "clinical", "medical"],
    "clean_technology":     ["technology", "tech"],
    "education_technology": ["technology", "tech"],
    "accounting":           ["finance", "financial"],
}

# ────────────────────────────────────────────────────────────────────────────
# Persona titles per industry (used as Apollo title-search seed)
# Keyed by normalized industry name. Unknown industries fall back to DEFAULT.
# ────────────────────────────────────────────────────────────────────────────

INDUSTRY_PERSONA_MAP = {
    "government": [
        "Chief Administrative Officer",
        "City Manager",
        "Deputy City Manager",
        "Director of Human Resources",
    ],
    "not_for_profit": [
        "Board Chair",
        "Board Director",
        "President",
        "Executive Director",
        "VP of People & Culture",
    ],
    "clean_technology": [
        "Founder",
        "Co-Founder",
        "Chief Executive Officer",
        "Chief Operating Officer",
        "Chief Technology Officer",
    ],
    "engineering_construction": [
        "Founder",
        "Co-Founder",
        "Chief Executive Officer",
        "Chief Operating Officer",
        "Chief Technology Officer",
    ],
    "healthcare": [
        "Hospital Administrator",
        "Chief Medical Officer",
        "VP of Operations",
        "VP of Talent Acquisition",
    ],
    "medical_technology": [
        "Hospital Administrator",
        "Chief Medical Officer",
        "VP of Operations",
        "VP of Talent Acquisition",
    ],
    "education": [
        "Chief Executive Officer",
        "Chief Operating Officer",
        "VP of HR",
        "Chief Human Resources Officer",
        "Director of Talent Acquisition",
    ],
    "education_technology": [
        "Chief Executive Officer",
        "Chief Operating Officer",
        "VP of HR",
        "Director of Talent Acquisition",
    ],
    "legal": [
        "Managing Partner",
        "Chief Operating Officer",
        "Director of Human Resources",
    ],
    "accounting": [
        "Managing Partner",
        "Chief Executive Officer",
        "Chief Operating Officer",
        "Director of Human Resources",
    ],
    "mining_resources": [
        "VP of Operations",
        "VP of Talent Acquisition",
        "Chief Operating Officer",
        "Director of Human Resources",
    ],
}

# Fallback persona titles when company industry is outside the map above
DEFAULT_PERSONA_TITLES = [
    "Chief Executive Officer",
    "Chief Operating Officer",
    "Chief Human Resources Officer",
    "VP of HR",
    "VP People & Culture",
    "Director of Talent Acquisition",
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
