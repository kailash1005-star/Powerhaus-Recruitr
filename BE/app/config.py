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
    CORS_ORIGINS: list[str] = Field(default=["http://localhost:3000"], description="Allowed CORS origins")

    # LinkedIn Credentials (for company info API)
    LINKEDIN_EMAIL: str = Field(default="", description="LinkedIn login email")
    LINKEDIN_PASSWORD: str = Field(default="", description="LinkedIn login password")

    # Company rejection thresholds
    MAX_STAFF_COUNT: int = Field(default=10000, description="Reject companies with more employees than this")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    

# Accept these titles (Gen AI / Software Engineering roles)
ACCEPTED_TITLE_KEYWORDS = [
    # Gen AI Engineer
    "gen ai engineer", "generative ai engineer", "gen ai",
    "generative ai",

    # Agentic AI Engineer
    "agentic ai engineer", "agentic ai", "ai agent engineer",
    "autonomous ai engineer",

    # Python Engineer/Developer
    "python engineer", "python developer", "python programmer",
    "python software engineer",

    # Software Engineer
    "software engineer", "software developer", "software programmer",
    "swe", "sde",

    # Machine Learning
    "machine learning engineer", "machine learning developer", "machine learning programmer",
    "ml engineer", "ml developer", "ml programmer","AI/ML Engineer","AI/ML Developer","AI/ML Programmer","AI/ML"
]

# Reject all other titles
REJECTED_TITLE_KEYWORDS = [
    # Everything else gets rejected by default (catch-all approach)
    # Explicitly reject common non-target roles to be safe
    "manager", "director", "vp", "vice president", "president",
    "analyst", "consultant", "architect", "devops", "data engineer",
    "data scientist","Remote",
    "backend engineer", "frontend engineer", "full stack", "fullstack",
    "qa engineer", "test engineer", "security engineer",
    "product manager", "project manager", "scrum master",
    "designer", "ux", "ui", "researcher", "scientist",
    "intern", "junior", "associate", "coordinator", "assistant",
    "recruiter", "hr", "marketing", "sales", "finance", "legal",
    "operations", "support", "customer success",
]

# Industries to ACCEPT (IT & Software only)
TARGET_INDUSTRY_KEYWORDS = [
    # Information Technology
    "information technology", "it services", "it consulting",
    "it solutions", "managed services", "tech services",
    # Software
    "software", "saas", "software development", "software solutions",
    "software company", "software house",
    # Cloud & Infrastructure
    "cloud", "cloud computing", "cloud services",
    # Internet & Digital
    "internet", "digital services", "digital solutions",
    # Cybersecurity
    "cybersecurity", "cyber security", "information security",
    # Data & AI
    "data", "artificial intelligence", "machine learning",
    "data analytics", "big data",
    # Computer & Hardware
    "computer", "semiconductor", "hardware",
]

# Reject all other industries
REJECTED_INDUSTRY_KEYWORDS = [
    # Government
    "government", "public administration", "public policy", "municipal",
    "provincial", "federal", "crown corporation", "civic", "public sector",
    # Not-for-profit
    "non-profit", "nonprofit", "not-for-profit", "ngo", "foundation",
    "charity", "charitable", "social enterprise", "philanthropy",
    # Clean Technology
    "renewable", "solar", "wind energy", "clean tech", "cleantech",
    "sustainability", "carbon", "environmental services", "green energy",
    # Engineering & Construction
    "construction", "civil engineering", "building materials",
    "architecture", "infrastructure", "real estate",
    # Healthcare
    "hospital", "health care", "healthcare", "medical practice",
    "mental health", "wellness", "clinics", "health authority",
    # Medical Technology
    "medical device", "medtech", "biotech", "biotechnology",
    "diagnostics", "pharmaceutical", "digital health",
    # Education
    "education", "higher education", "primary", "secondary",
    "school", "university", "college", "k-12",
    # Education Technology
    "e-learning", "edtech", "online training", "education technology",
    # Legal
    "law practice", "legal", "law firm",
    # Accounting
    "accounting", "audit", "bookkeeping", "tax",
    # Mining & Resources
    "mining", "oil", "gas", "natural resources", "metals",
    "quarrying", "petroleum",
    # Staffing & HR
    "staffing", "recruiting", "recruitment", "employment",
    "human resources services", "temporary help",
    # Consulting
    "consulting", "management consulting", "business consulting",
    "outsourcing", "professional services",
    # Marketing & Advertising
    "advertising", "marketing", "public relations",
    # Finance
    "venture capital", "private equity", "investment",
    "banking", "financial services", "insurance",
    # Design
    "design services",
]

settings = Settings()
