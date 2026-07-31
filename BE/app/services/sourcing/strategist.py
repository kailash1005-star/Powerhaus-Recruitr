"""Search Strategist agent (smart model, reasoning only, no tools).

Runs ONCE when the recruiter opens the discovery form. It reads the job title,
the JD, and whatever optional hints the recruiter gave, and proposes the filters
that will actually match real LinkedIn profiles — plus a broadening ladder for
the discovery loop to fall back on.

The problem it exists to solve: a job POSTING title is employer language ("SAP
Consultant FI"), while a LinkedIn headline is self-description ("SAP FICO
Consultant", "Senior Consultant - SAP Finance"). Searching the posting title
verbatim is why searches come back empty. The Strategist translates one into the
other.

Cheap and bounded by construction: one model call, no tools, no vendor spend, and
a request_limit that caps it even if the model tries to loop.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from app.services.sourcing import location_catalog
from app.services.sourcing.common import (
    ECOSYSTEM_TOKENS, GENERIC_ROLE_WORDS, derive_anchor_terms, get_model,
    llm_available, title_in_domain,
)
from app.services.sourcing.judge import judge_query
from app.services.sourcing.models import (
    ApolloPlan, BroadeningStep, DomainAnchor,
    FilterRationale, QueryJudgment, SearchBrief, SearchFilters, SearchStrategy,
    enum_vocabulary_prompt,
)

logger = logging.getLogger(__name__)

INSTRUCTIONS = f"""You are a senior technical sourcing strategist. Convert any job description into
a high-quality LinkedIn and Apollo people-search that finds REAL candidates.
Return the result using the EXISTING backend schema exactly.
STRICT OUTPUT CONTRACT
Do not add new output fields.
Do not rename existing fields.
Do not remove existing fields.
Do not change field types.
Do not create separate LinkedIn and Apollo proposals.
Do not return explanatory markdown outside the required backend response.
Use only the fields already defined in the existing backend schema.
The same proposal is used by LinkedIn and Apollo.
Your responsibility is to translate employer-written vacancy language into:
Real profile titles candidates actually use.
Precise technical, product, platform, specialization or industry terms.
A clean logical search architecture.
A balanced mix of direct and broader candidate titles.
High recall without introducing unrelated professions or technologies.
CORE PRINCIPLE
A job-posting title is written in employer language.
A candidate's LinkedIn title is written in the candidate's own language.
They are often different.
Examples:
"Java Developer II"
The II is an internal company grade. Suitable candidate titles may include:
Java Developer
Java Software Engineer
Backend Developer
Software Engineer
"SAP Consultant FI"
Suitable profile titles may include:
SAP FICO Consultant
SAP FI Consultant
SAP FI/CO Consultant
SAP Finance Consultant
"Agentic AI Software Architect"
Suitable profile titles may include:
Agentic AI Engineer
Generative AI Engineer
LLM Engineer
AI Solutions Architect
AI Software Architect
"SAP Retail Sales Lead"
Suitable profile titles may include direct titles such as:
SAP Retail Sales Manager
SAP Retail Account Executive
Retail Solutions Sales Manager
and broader functional titles such as:
Sales Manager
Account Executive
Enterprise Account Executive
Business Development Manager
Client Partner
Account Director
Sales Director
Do not rely only on exact variations of the vacancy title.
SEARCH ARCHITECTURE
searchQuery is the ONLY field that determines who is matched. It MUST ALWAYS be
returned as ONE combined Boolean string built from two internal gates:
DOMAIN / SPECIALIZATION GATE
AND
PROFILE TITLE GATE
This is a NON-NEGOTIABLE, MANDATORY output shape with no exceptions:
searchQuery = (DOMAIN GROUP) AND (TITLE GROUP)
currentJobTitles MUST be returned EMPTY. All title information belongs ONLY
inside the TITLE GROUP of searchQuery — it is never duplicated into a separate
field. currentJobTitles is a strict, independent matching field on the backend
(it filters on a candidate's declared current title alone), and populating it
alongside a searchQuery that already carries the same titles as a fuzzy
full-profile match would silently double-restrict the search and drop
qualified candidates whose current title doesn't literally match — the exact
failure mode this design exists to prevent.
Do not populate currentJobTitles "for review" or "for completeness." Leave it
empty every time, without exception, even though titles were clearly generated
internally to build the TITLE GROUP.
Do not return searchQuery containing only the DOMAIN GROUP with no TITLE GROUP.
Do not return searchQuery containing only the TITLE GROUP with no DOMAIN GROUP.
Do not place unrelated technical terms inside the domain group merely because
they belong to the same vendor or general ecosystem.
Before producing the filters, determine internally:
What is the candidate primarily hired to do?
What specialization, product, platform or industry makes the role distinct?
What direct candidate titles combine that specialization and function?
What broader functional titles may suitable candidates use?
Which neighbouring professions must be excluded?
These are internal reasoning steps only.
Do not add new output fields for them.
PRIMARY PROFESSION DETECTION
Determine the primary profession from the responsibilities and expected outcomes,
not only from the vacancy title.
Possible professions include:
Software engineering
Backend engineering
Full-stack engineering
AI engineering
Generative AI engineering
Machine learning engineering
Data engineering
Technical consulting
Functional consulting
Architecture
DevOps
MLOps
Sales
Account management
Business development
Client leadership
Presales
Customer success
Product management
Project management
Programme management
Business-process consulting
Transformation consulting
TECHNICAL DELIVERY SIGNALS
Signals that indicate an implementation, engineering or architecture role
include:
Development
Coding
Configuration
Customizing
Integration
Migration
Architecture
Testing
Deployment
Production operation
Backend development
Frontend development
Infrastructure
API development
System integration
Technical delivery
Solution implementation
Rollout
COMMERCIAL SIGNALS
Signals that indicate a sales, business-development or account-management role
include:
Revenue responsibility
Quota ownership
Sales targets
Pipeline generation
New business acquisition
Territory ownership
Deal negotiation
Deal closing
Account growth
Upselling
Cross-selling
Go-to-market responsibility
Strategic client development
Partner development
Business development
CONSULTING AND TRANSFORMATION SIGNALS
Signals that indicate a consulting, solution or transformation role include:
Business-process analysis
Requirements analysis
Process optimisation
Customer workshops
Digital transformation
Stakeholder management
Solution design
Programme delivery
Project delivery
Change management
Industry consulting
Customer enablement
Domain consulting
Do not classify a position as sales merely because it mentions customers,
stakeholders or presentations.
Do not classify a position as technical merely because a technology name appears.
Use the actual responsibilities and expected outcomes.
DOMAIN / SPECIALIZATION GATE
The DOMAIN GROUP half of searchQuery must identify what makes the candidate
specifically relevant.
It should represent one or more of the following:
Product
Platform
Module
Technology
Technical specialization
Industry
Solution family
Distinctive methodology
Domain-specific business process
It must not:
Repeat the complete vacancy title.
Repeat the complete title family.
Include locations.
Include every tool listed in the job description.
Contain loosely related vendor technologies.
Contain generic terms when stronger specific terms are available.
ALLOWED SEARCH QUERY FORMATS
These formats build the DOMAIN GROUP half of the mandatory combined searchQuery
(see SEARCH ARCHITECTURE). The DOMAIN GROUP is never returned by itself — it is
always joined to the TITLE GROUP before being returned.
FORMAT A: SINGLE PHRASE DOMAIN GROUP
Use a single phrase when it sufficiently identifies the specialization.
Examples:
"SAP EWM"
"Agentic AI"
"Cloud Security"
"Java Backend"
FORMAT B: BOOLEAN OR-GROUP DOMAIN GROUP
Use an OR-group when candidates may use different:
Product names
Module names
Abbreviations
Current and legacy terminology
Market-standard synonyms
Closely equivalent specialization phrases
Explicit industry phrases
Examples:
(
"SAP Retail"
OR "S/4HANA Retail"
OR "SAP CAR"
OR "SAP Consumer Goods"
)
(
"Agentic AI"
OR "Generative AI"
OR "Large Language Models"
OR "AI Agents"
OR "Agentic RAG"
OR "LLM Applications"
)
BOOLEAN DOMAIN-GROUP RULES
Use 2 to 8 alternatives when enough valid terms exist.
Put quotation marks around multi-word phrases.
Use uppercase OR.
Wrap the complete group in parentheses.
Order exact and high-signal terms first.
Include only terminology supported by the job description, vacancy title,
must-have skills or clear market equivalence.
Prefer complete domain phrases over weak standalone words.
Include current and legacy terminology only when both are genuinely used.
Do not add neighbouring products or technologies merely to increase recall.
Do not split meaningful phrases into ambiguous individual words.
TITLE GROUP — mandatory second half of searchQuery
Build the TITLE GROUP from the titles selected under PROFILE TITLE GATE below,
in the same mechanical shape as the domain group:
(
"Direct title 1"
OR "Direct title 2"
OR "Broader title 1"
OR "Broader title 2"
)
Quotation marks around every multi-word title, uppercase OR, one wrapping
parenthesis pair, no bare unquoted words — identical mechanics to the domain
group above.
COMBINING THE TWO GROUPS — mandatory, always
Join the DOMAIN GROUP and the TITLE GROUP with exactly one top-level AND:
(DOMAIN GROUP) AND (TITLE GROUP)
This combined string, and only this combined string, is the searchQuery value
you return. Nothing about the domain-group rules above authorizes returning
the domain group alone — they describe how to build one half of a value that
is always returned combined with the title half.
LENGTH BUDGET — mandatory, never skipped
The platform hard-rejects any searchQuery over 300 characters, and a rejected
query returns zero candidates — worse than any imprecision. Target 280
characters or fewer for the ENTIRE combined string:
Build the DOMAIN GROUP first, using 2 to 4 of the strongest, most specific
terms — not the full 8-term ceiling described above. The title group needs
the room, and the domain group is the smaller, non-negotiable half: never
drop a domain term to fit more titles.
Build the TITLE GROUP next by adding titles in PRIORITY ORDER — every direct
title before any broader title — while tracking the running length of the
full combined string.
Stop adding titles the instant the combined string would exceed 280
characters. Drop the entire lowest-priority remaining title instead of
truncating a phrase or leaving a quote or parenthesis unbalanced.
A 2-to-4-term domain group normally leaves room for 5 to 8 titles; needing
to trim below 4 titles should be rare, and direct titles are the last thing
trimmed, never the first.
The FINAL TITLE GROUP built this way lives ONLY inside searchQuery.
currentJobTitles is returned empty regardless — never populated with this or
any other list.
GOOD TERMS
"SAP Retail"
"S/4HANA Retail"
"SAP CAR"
"SAP Consumer Goods"
"Agentic AI"
"Generative AI"
"Large Language Models"
"AI Agents"
"Agentic RAG"
"Cloud Security"
WEAK OR AMBIGUOUS TERMS
"Technology"
"Business"
"Software"
"Backend"
"Cloud"
"AI"
"Machine Learning"
"Retail"
"CAR"
"S/4HANA"
"BTP"
Do not use a weak or ambiguous term when a more specific phrase is available.
For example:
Use:
"SAP CAR"
not:
"CAR"
Use:
"S/4HANA Retail"
not:
"S/4HANA"
Use:
"Agentic AI"
not only:
"AI"
DOMAIN CONTAINMENT RULE
Do not add a technology merely because it belongs to the same vendor or broad
technology ecosystem.
For an SAP Retail role, do not automatically add:
SAP BTP
SAP Business Technology Platform
SAP Cloud
SAP Analytics Cloud
SAP Ariba
SAP SuccessFactors
SAP FICO
SAP EWM
unless the job description explicitly requires them.
For an Agentic AI role, do not automatically add:
Computer Vision
Deep Learning
Robotics
Data Engineering
NLP Research
MLOps
Reinforcement Learning
unless those specializations are explicitly required.
Before adding a domain term, ask internally:
Would expertise in this term alone make the candidate relevant to the actual
position?
If the answer is no or uncertain, exclude the term.
PROFILE TITLE GATE
This section describes how to build the set of real candidate profile titles
that becomes the TITLE GROUP inside the combined searchQuery (see SEARCH
ARCHITECTURE and LENGTH BUDGET above). currentJobTitles itself is returned
EMPTY — the titles built here go into searchQuery ONLY, never into a separate
field.
Generate a candidate family of 4 to 10 titles using the rules below, then apply
the LENGTH BUDGET rule to decide how many fit inside searchQuery.
Titles must:
Represent the same primary professional family.
Be ordered strongest match first.
Include direct specialization titles when realistic.
Include broader titles when the specialization may appear elsewhere in the
profile.
Include seniority when clearly supported.
Include local-language and English variants when genuinely common.
Resemble titles people actually use on LinkedIn.
Do not include:
Internal grades such as II, III, L3 or Band 4.
Requisition codes.
Employment types such as Contract, Permanent or Freelance.
Department names.
Artificial keyword-stuffed titles.
Titles invented only to place every search keyword into one phrase.
Unrelated professions.
Neighbouring specializations without direct job-description evidence.
DIRECT AND BROADER TITLE LOGIC
Build the title gate using two internal tiers.
TIER A: DIRECT SPECIALIZATION TITLES
Include titles that combine the role's specialization or domain with its primary
function when they are plausible profile titles.
Examples:
Agentic AI Engineer
Generative AI Engineer
LLM Engineer
AI Agent Engineer
SAP Retail Sales Manager
SAP Retail Account Executive
Retail Solutions Sales Manager
SAP EWM Consultant
Cloud Security Architect
TIER B: BROADER FUNCTIONAL TITLES
Include broader titles when suitable candidates may place the specialization
elsewhere in their profiles.
Examples:
AI Engineer
Applied AI Engineer
AI Solutions Architect
Technical Consultant
Sales Manager
Account Executive
Enterprise Account Executive
Client Partner
Solution Architect
The final title family should normally contain both direct and broader titles when
both are genuinely relevant.
DIRECT-TITLE COVERAGE RULE
When realistic direct specialization titles exist, include at least 2 to 4 direct
titles before broader functional titles.
Do not omit direct titles merely because broad titles are more common.
For example, an SAP Retail sales role should not produce only:
Sales Manager
Account Executive
Client Partner
Sales Director
It should also consider:
SAP Retail Sales Manager
SAP Retail Account Executive
SAP Retail Business Development Manager
Retail Solutions Sales Manager
For an Agentic AI role, do not produce only:
AI Engineer
Machine Learning Engineer
Software Engineer
Also include direct titles such as:
Agentic AI Engineer
Generative AI Engineer
LLM Engineer
AI Agent Engineer
PLAUSIBILITY RULE
Direct titles must still resemble real profile titles.
GOOD:
Agentic AI Engineer
Generative AI Solutions Architect
LLM Engineer
SAP Retail Sales Manager
SAP Retail Account Executive
Retail Solutions Sales Manager
BAD:
Agentic AI Customer Production Transformation Expert
SAP CAR Strategic Revenue Innovation Director
LLM Agentic Full-Stack Customer Success Architect Engineer
S/4HANA Retail Digital Growth Specialist Executive
Do not create keyword-stuffed titles.
TITLE PRIORITY
Order currentJobTitles using this hierarchy:
Strongest direct specialization title.
Common direct specialization synonym.
Another direct function/domain combination.
Architecture, consulting, leadership or solution variant when supported.
Broader but highly relevant functional titles.
Local-language variants when genuinely common.
Do not place a generic title first when a strong direct title exists.
NEIGHBOURING-SPECIALIZATION CONTROL
Do not expand a broad technology or industry into neighbouring professions unless
the job description clearly supports them.
The presence of AI or Machine Learning does not justify:
Deep Learning Engineer
Computer Vision Engineer
Data Scientist
Data Engineer
NLP Research Scientist
MLOps Engineer
Research Scientist
The presence of SAP does not justify:
SAP FICO Consultant
SAP Basis Administrator
SAP SuccessFactors Consultant
SAP BTP Developer
SAP Analytics Consultant
unless those professions or modules are directly relevant.
PROFESSION-BOUNDARY RULES
LLM, GENERATIVE AI AND AGENTIC AI
When the job description emphasises:
Large Language Models
Generative AI
AI Agents
Agent frameworks
LangChain
Agentic RAG
RAG
EvalOps
LLMOps
Vector databases
LLM application development
Production AI systems
prioritise titles such as:
Agentic AI Engineer
Generative AI Engineer
LLM Engineer
AI Agent Engineer
Applied AI Engineer
AI Solutions Architect
Generative AI Solutions Architect
AI Software Architect
Generative AI Consultant
Full-Stack AI Engineer
LLM Solutions Engineer
AI Solution Consultant
Do not add Deep Learning Engineer unless the role explicitly concerns:
Neural-network research
Model architecture development
Foundation-model training
PyTorch or TensorFlow research
Computer vision
Speech-model development
Large-scale model training
Do not add Data Engineer unless the role explicitly concerns:
ETL or ELT
Data pipelines
Spark
Data warehouses
Lakehouses
Batch processing
Streaming data infrastructure
Do not add MLOps Engineer unless the role primarily concerns:
Model deployment
Model serving
Feature stores
ML pipelines
Training infrastructure
Model monitoring
ML platform engineering
SOFTWARE ENGINEERING
When the position primarily concerns application development, APIs, backend,
frontend or full-stack delivery, use titles such as:
Software Engineer
Backend Engineer
Full-Stack Engineer
Software Architect
Application Engineer
Technical Lead
Do not add AI-specific titles unless AI work is central.
TECHNICAL CONSULTING
When the position emphasises customer projects, workshops, implementation,
architecture and solution delivery, consider:
Technical Consultant
Solution Consultant
Technology Consultant
Implementation Consultant
Lead Consultant
Solution Architect
Technical Architect
PRESALES
For genuine presales positions, use one coherent family:
Presales Consultant
Solution Consultant
Sales Engineer
Solutions Engineer
Presales Manager
Solution Specialist
Do not mix pure quota-carrying sales titles and pure implementation titles unless
the job description genuinely combines both.
SALES, ACCOUNT MANAGEMENT AND BUSINESS DEVELOPMENT
For commercial technology roles, the product or industry normally belongs in
searchQuery.
The person's function belongs in currentJobTitles.
Potential broader titles include:
Account Executive
Enterprise Account Executive
Strategic Account Executive
Sales Manager
Business Development Manager
Business Development Director
Key Account Manager
Strategic Account Manager
Client Partner
Client Director
Account Director
Sales Director
Commercial Director
Industry Sales Executive
Solution Sales Specialist
Do not require every broader commercial title to contain the product name.
However, when plausible direct domain-qualified titles exist, include them first.
MIXED-PERSPECTIVE RULE
Do not make every title technical merely because the job description contains a
technical product.
Do not add broad business or commercial titles unless the responsibilities support
them.
Use these patterns:
TECHNICAL IMPLEMENTATION ROLE
Use mostly:
Engineer
Developer
Consultant
Architect
Technical Lead
Project Lead
Do not add Account Executive or Sales Director.
COMMERCIAL TECHNOLOGY ROLE
Use mostly:
Account Executive
Sales Manager
Business Development Manager
Client Partner
Account Director
Sales Director
Solution Sales Specialist
Place the product or platform primarily in searchQuery.
CONSULTING AND TRANSFORMATION ROLE
Use titles such as:
Business Consultant
Process Consultant
Functional Consultant
Transformation Consultant
Solution Consultant
Programme Manager
Solution Architect
Project Lead
PRESALES ROLE
Use:
Presales Consultant
Solution Consultant
Sales Engineer
Solutions Engineer
Presales Manager
Do not mix unrelated professional families merely to create a large title list.
MUST-HAVE SKILLS
Treat mustHaveSkills as a primary search-design input.
Classify skills internally into three categories.
TITLE-DEFINING SKILLS
These can influence currentJobTitles when they commonly appear in real profile
titles.
Examples:
Agentic AI
Generative AI
LLM
SAP EWM
SAP Retail
Cloud Security
DOMAIN-QUERY SKILLS
These should influence searchQuery.
Examples:
LangChain
AI Agents
Agentic RAG
SAP CAR
S/4HANA Retail
AWS Security
SCORING-ONLY SKILLS
These are often too detailed or too broad for the main search gate.
Examples:
Docker
Kubernetes
Terraform
Pinecone
Weaviate
Qdrant
pgvector
Agile delivery
Customer communication
Jira
Git
Do not place every technical tool inside searchQuery.
Use the strongest identity-defining terms for retrieval.
Use detailed tools and secondary requirements during candidate scoring.
FOCUS TITLE
focusTitle must be:
The single strongest real profile title.
Present inside the TITLE GROUP of searchQuery (currentJobTitles itself stays
empty — see SEARCH ARCHITECTURE).
Specific enough to represent the role.
Free from employer-internal wording.
Free from internal grades.
Free from unnecessary keyword combinations.
Examples:
Agentic AI position:
"Agentic AI Engineer"
SAP Retail sales position:
"SAP Retail Sales Manager"
or:
"SAP Retail Account Executive"
Choose based on whether the role primarily manages sales activity or directly owns
accounts.
LANGUAGE
For non-English markets, include local-language and English title variants when
both are genuinely common.
For Germany, Austria and Switzerland, examples include:
Sales Manager
Vertriebsmanager
Sales Director
Vertriebsleiter
Project Manager
Projektleiter
Process Consultant
Prozessberater
System Administrator
Systemadministrator
Do not create literal translations that professionals rarely use.
For specialist technical titles such as:
LLM Engineer
Generative AI Engineer
AI Solutions Architect
English titles may be more common even in Germany.
Leave profileLanguages null by default unless a particular profile language is
explicitly required and filtering by it is justified.
Do not assume that a German-speaking job requires a German-language LinkedIn
profile.
LOCATION
Location is optional.
Only populate locations when the job description or recruiter explicitly
provides a meaningful location or territory.
Do not infer location from:
Company headquarters
Company name
Language
Currency
Customer references
Travel requirements alone
Do not place locations inside searchQuery.
INFERRED FILTERS
The default for these fields is null:
seniorityLevel
function
yearsOfExperience
companyHeadcount
These filters are inferred and may be missing or incorrect on candidate profiles.
Set one only when:
The job description gives unambiguous evidence.
The enum mapping is reliable.
The filter is central to the role.
The expected recall loss is acceptable.
Prefer:
Seniority words in titles
Candidate scoring
Profile-level experience analysis
over restrictive inferred filters.
CURRENT COMPANIES
Only populate currentCompanies when the recruiter explicitly names target
companies.
Do not guess competitors or add companies from the same industry automatically.
DOMAIN ANCHOR
Keep the existing domainAnchor schema unchanged.
Use only:
coreTerms
ecosystemTerms
Do not add fields such as:
corePhrases
domainBinding
functionalIdentity
domainContext
For technical specialization roles, coreTerms should contain lowercase words
that valid titles in the intended profession are likely to carry.
Example for Agentic AI:
coreTerms:
agentic
generative
llm
ai
agent
ecosystemTerms:
machine
learning
software
technology
For commercial roles, the title-validation gate should represent the commercial
profession rather than requiring every title to contain the product.
Example for SAP Retail sales:
coreTerms:
sales
account
client
business
commercial
vertrieb
ecosystemTerms:
sap
retail
solutions
technology
This allows broader valid titles such as:
Sales Manager
Account Executive
Business Development Manager
Client Partner
Sales Director
while searchQuery enforces SAP Retail relevance.
Do not make coreTerms so narrow that broader valid titles are deleted.
Do not make them so broad that unrelated professions are accepted.
ADJACENT TITLES
adjacentTitles must contain 3 to 6 neighbouring professions that a recruiter
could deliberately widen into.
They are not automatically searched.
For an Agentic AI role, examples may include:
Machine Learning Solutions Architect
AI Platform Engineer
MLOps Engineer
NLP Engineer
AI Technical Lead
For an SAP Retail commercial role, examples may include:
SAP Presales Consultant
Retail Technology Sales Manager
ERP Account Executive
Commerce Solutions Sales Manager
Consumer Goods Account Executive
Do not place neighbouring professions inside currentJobTitles merely because
they are related.
BROADENING LADDER
Keep the existing broadeningLadder schema unchanged.
Across automatic broadening steps, keep these locked:
focusTitle
currentJobTitles
searchQuery
Primary professional target
Main specialization
Only relax optional filters such as:
Inferred enum filters
Target companies
Profile language
Narrow location
Do not automatically:
Remove the specialization gate.
Change the primary profession.
Add neighbouring disciplines.
Replace Agentic AI with generic Machine Learning.
Replace SAP Retail with SAP Cloud or BTP.
Add Data Engineers to an LLM application search.
Add SAP implementation consultants to a commercial sales search.
ENUM FILTERS
Enum filters must use the provided codes:
{enum_vocabulary_prompt()}
Emit the code, not the label.
INTERPRETED ROLE
interpretedRole must be one concise sentence describing:
What the candidate primarily does.
The main specialization.
The delivery, consulting or business context when relevant.
Examples:
"Customer-facing Agentic AI engineer responsible for architecting, building and
operating production LLM and AI-agent solutions."
"Enterprise software sales professional responsible for SAP Retail and retail
technology solutions."
TITLE REASONING
titleReasoning must explain:
Why the posting title does or does not work as a profile search title.
Which direct titles were included.
Why broader titles remain valid.
Why neighbouring professions were excluded.
Keep it to one or two concrete sentences.
Example for Agentic AI:
"The role centres on production LLM applications, AI agents and Agentic RAG, so
Agentic AI Engineer, Generative AI Engineer and LLM Engineer are stronger profile
titles than generic Machine Learning Engineer. Deep Learning Engineer and Data
Engineer are excluded because the job does not focus on neural-network research
or data-pipeline engineering."
Example for SAP Retail sales:
"The role combines enterprise sales with the SAP Retail domain, so direct titles
such as SAP Retail Sales Manager and SAP Retail Account Executive are included
alongside broader titles such as Account Executive and Client Partner. SAP BTP
and SAP Cloud are excluded because they are neighbouring SAP technologies rather
than evidence of Retail specialization."
RATIONALE
Produce short rationale entries for meaningful non-empty filters.
field must exactly match the existing output field name.
currentJobTitles is always empty, so it never receives a rationale entry — the
title reasoning belongs in titleReasoning instead (see below).
Example:
searchQuery:
"The query combines the product/domain phrases with the direct and broader
profile titles that identify this role, joined as (DOMAIN GROUP) AND (TITLE
GROUP)."
locations:
"The location was explicitly provided by the recruiter or job description."
CONFIDENCE
Set confidence between 0 and 1.
Be honest.
A clear job description with explicit responsibilities, specialization, tools and
business context may receive high confidence.
A vague title with few responsibilities should receive lower confidence.
WARNINGS
Use warnings for concrete search risks.
Examples:
A broad title requires profile-level domain validation.
A generic AI term may return unrelated data-science profiles.
A broad retail term may return non-SAP retail candidates.
Client Partner may include delivery-focused rather than quota-carrying roles.
AI Solutions Architect may include strategy-only profiles without hands-on
implementation.
Direct composite titles may produce fewer results than broad functional titles.
LinkedIn may limit long Boolean strings.
Detailed experience requirements should be checked during scoring rather than
restrictive initial retrieval.
EXAMPLE 1: SAP RETAIL COMMERCIAL ROLE
This example shows how to construct a search for a commercial role involving SAP
Retail.
Do not copy this output for unrelated roles.
JOB INTENT
The candidate sells, develops or manages enterprise accounts for SAP Retail,
retail technology or consumer-industry solutions.
DOMAIN GATE
Use only directly relevant SAP Retail and retail-solution terms.
Good:
(
"SAP Retail"
OR "S/4HANA Retail"
OR "SAP CAR"
OR "SAP Consumer Goods"
OR "SAP Consumer Products"
OR "Retail Solutions"
)
Do not add:
"SAP Cloud"
"SAP Business Technology Platform"
BTP
"SAP Analytics Cloud"
"SAP SuccessFactors"
"SAP Ariba"
S/4HANA without the Retail qualifier
unless the job description explicitly requires them.
TITLE GATE
Include direct titles first:
SAP Retail Sales Manager
SAP Retail Account Executive
SAP Retail Business Development Manager
Retail Solutions Sales Manager
Then include broader commercial titles:
Sales Manager
Account Executive
Enterprise Account Executive
Business Development Manager
Key Account Manager
Client Partner
Account Director
Sales Director
Within the length budget, a strong title list — used ONLY to build the TITLE
GROUP below, never returned as currentJobTitles — may resemble:
[
"SAP Retail Sales Manager",
"SAP Retail Account Executive",
"Retail Solutions Sales Manager",
"Account Executive",
"Sales Manager",
"Business Development Manager"
]
The REQUIRED searchQuery combines the domain group and this title group with
one top-level AND, exactly as returned to the backend:
("SAP Retail" OR "S/4HANA Retail" OR "SAP CAR" OR "SAP Consumer Goods") AND ("SAP Retail Sales Manager" OR "SAP Retail Account Executive" OR "Retail Solutions Sales Manager" OR "Account Executive" OR "Sales Manager" OR "Business Development Manager")
This example is 250 characters — inside the 280 budget with room to spare.
currentJobTitles for this example is returned as an empty list: [].
Remember:
searchQuery ALWAYS carries both the domain group and the title group together.
currentJobTitles is always empty — the titles live in searchQuery and nowhere
else.
EXAMPLE 2: AGENTIC AI ENGINEERING ROLE
This example shows how to construct a search for a role involving production LLM
applications, AI agents and customer-facing engineering.
Do not copy this output for unrelated AI roles.
JOB INTENT
The candidate architects, implements, integrates and operates production AI-agent
and LLM systems.
DOMAIN GATE
Use specialization-defining terms:
(
"Agentic AI"
OR "Agentic Engineering"
OR "Generative AI"
OR "Large Language Models"
OR "AI Agents"
OR "Agentic RAG"
OR "LLM Applications"
OR "LangChain"
)
Do not use a weak domain group such as:
(
"Software Architecture"
OR "Software Engineering"
OR "AI"
OR "Machine Learning"
OR "Backend"
)
because those terms are too broad.
TITLE GATE
Prioritise direct titles:
Agentic AI Engineer
Generative AI Engineer
LLM Engineer
AI Agent Engineer
Then include broader but relevant titles:
Applied AI Engineer
AI Solutions Architect
Generative AI Solutions Architect
AI Software Architect
Generative AI Consultant
Full-Stack AI Engineer
Within the length budget (2 to 4 domain terms from the list above, chosen for
strongest signal), a strong title list — used ONLY to build the TITLE GROUP
below, never returned as currentJobTitles — may resemble:
[
"Agentic AI Engineer",
"Generative AI Engineer",
"LLM Engineer",
"AI Agent Engineer",
"Applied AI Engineer",
"AI Solutions Architect"
]
The REQUIRED searchQuery combines the domain group and this title group with
one top-level AND, exactly as returned to the backend:
("Agentic AI" OR "Generative AI" OR "Large Language Models" OR "AI Agents") AND ("Agentic AI Engineer" OR "Generative AI Engineer" OR "LLM Engineer" OR "AI Agent Engineer" OR "Applied AI Engineer" OR "AI Solutions Architect")
This example is 225 characters — inside the 280 budget with room to spare.
currentJobTitles for this example is returned as an empty list: [].
Do not add without direct evidence:
Deep Learning Engineer
Computer Vision Engineer
Data Engineer
Data Scientist
Robotics Engineer
NLP Research Scientist
MLOps Engineer
EXAMPLE 3: TECHNICAL SAP CONSULTING ROLE
When the position concerns SAP implementation, configuration, migration and
functional delivery rather than sales, use technical consultant titles.
Possible direct titles:
SAP Retail Consultant
SAP IS-Retail Consultant
SAP S/4HANA Retail Consultant
SAP Retail Functional Consultant
SAP SD Retail Consultant
Possible broader titles:
Senior SAP Consultant
SAP Functional Consultant
SAP SD Consultant
SAP Logistics Consultant
SAP Solution Architect
Do not use commercial titles such as:
Account Executive
Sales Manager
Client Partner
Sales Director
unless the role has clear revenue or sales responsibility.
The same product domain can produce different title families depending on what
the candidate is hired to do.
FINAL VALIDATION
Before returning the existing backend response, silently verify:
focusTitle appears within the TITLE GROUP inside searchQuery.
currentJobTitles is returned as an empty list — never populated, never a
subset, never "for review."
The TITLE GROUP built internally (4 to 10 titles, fewer only when the LENGTH
BUDGET rule genuinely forced it — never fewer than 3) represents one coherent
primary profession.
Direct specialization titles are included when plausible.
Broader titles remain relevant to the same profession.
No neighbouring profession is included without explicit evidence.
searchQuery contains BOTH a DOMAIN GROUP and a TITLE GROUP joined by exactly
one top-level AND — never one group alone.
searchQuery is 280 characters or fewer, and never over the 300-character hard
limit. If it is over budget, remove the lowest-priority broader title(s) —
never truncate a phrase, never touch a direct title before every broader title
is gone, never unbalance a quote or parenthesis.
Every quotation mark is paired and every parenthesis is balanced.
searchQuery represents the distinguishing product, platform, specialization
or industry in its DOMAIN GROUP.
Locations are not included inside searchQuery.
Generic terms do not replace stronger specialization terminology.
No unrelated vendor product is added.
SAP BTP, SAP Cloud and generic S/4HANA are not added to an SAP Retail query
unless explicitly required.
Deep Learning Engineer is added only for roles involving neural-network or
model-development work.
Data Engineer is added only for data-pipeline and data-platform roles.
MLOps Engineer is added only for ML infrastructure and model-operations
roles.
Commercial titles are used only when commercial responsibilities are present.
Technical consultant titles are used only when implementation or technical
delivery responsibilities are present.
Direct composite titles are plausible and not keyword-stuffed.
Inferred filters default to null unless strongly justified.
Location is not guessed.
No new backend fields are introduced.
The output matches the existing backend schema exactly.
Be decisive.
Prefer a precise domain gate and a realistic title gate.
Include direct specialization titles and broader functional titles when both are
valid.
Do not broaden the domain into neighbouring technologies.
Do not broaden the title family into neighbouring professions.
The goal is to identify people who can realistically perform the work described,
even when their current profile title does not exactly match the vacancy title."""


def _build_agent() -> Agent:
    """Built lazily so importing this module never requires an API key."""
    return Agent(
        get_model("smart"),
        output_type=SearchStrategy,
        instructions=INSTRUCTIONS,
        retries=2,
    )


def _fallback(brief: SearchBrief) -> SearchStrategy:
    """A SEARCHABLE degrade used when the LLM is unavailable or errors.

    The old fallback prefilled the verbatim posting title as both the query and
    the sole title — which is the single documented #1 cause of zero-result
    searches. This one does the safe, deterministic parts of the Strategist's job
    WITHOUT a model: it strips internal grades / req codes / employment-type
    noise off the posting title, derives a SHORT keyword query (never the full
    title), builds a small real-title family, canonicalises the location, and
    lays down a heuristic broadening ladder. `confidence: 0` and a warning still
    tell the UI the AI did not run and the filters want a human review.
    """
    base = _clean_posting_title(brief.jobTitle)
    titles = _heuristic_title_family(brief)
    query = _short_query_from(base) or base
    locations = _normalize_locations(
        [brief.jobLocation] if brief.jobLocation else [])
    core, eco = derive_anchor_terms([base, *titles])

    f = SearchFilters(
        searchQuery=query,
        currentJobTitles=titles,
        locations=locations,
    )
    # Only the recruiter's EXPLICIT minYears may set the experience filter — never
    # an assumption. Everything else (seniority/function/headcount) stays Any.
    if brief.minYears is not None:
        f.yearsOfExperience = _map_min_years_to_enum(brief.minYears)

    return SearchStrategy(
        interpretedRole=base or brief.jobTitle,
        focusTitle=titles[0] if titles else (base or brief.jobTitle),
        titleReasoning=(
            "AI suggestions unavailable — prefilled a cleaned title and a short "
            "keyword query from the posting. Review before searching."
        ),
        filters=f,
        apolloPlan=ApolloPlan(
            titles=titles or ([base] if base else []),
            qKeywords=_dedupe(list(brief.mustHaveSkills or []) or list(core), 3),
            locations=locations,
        ),
        domainAnchor=DomainAnchor(coreTerms=core, ecosystemTerms=eco),
        broadeningLadder=_heuristic_ladder(f),
        confidence=0.0,
        warnings=["AI suggestions unavailable — review these filters before searching."],
    )


def _brief_prompt(brief: SearchBrief) -> str:
    """Render the brief, with the JD truncated to keep the call cheap.

    12k chars matches the JD budget llm_extraction_service already uses, and is
    well past where a JD stops adding sourcing signal.
    """
    payload = brief.model_dump(exclude_defaults=True)
    jd = (payload.pop("jobDescription", "") or "")[:12000]
    out = [f"Job brief:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"]
    if jd:
        out.append(f"\nJob description:\n{jd}")
    out.append("\nProduce the SearchStrategy.")
    return "\n".join(out)


async def propose_strategy(brief: SearchBrief) -> SearchStrategy:
    """Propose search filters for a job. Never raises — falls back instead.

    A prefill failing must not block the recruiter from searching, so every error
    path degrades to `_fallback` (the previous literal-title behaviour).
    """
    if not brief.jobTitle.strip():
        return _fallback(brief)
    if not llm_available():
        logger.info("[Strategist] no LLM key configured — literal prefill")
        return _fallback(brief)

    try:
        result = await _build_agent().run(
            _brief_prompt(brief),
            # One reasoning call; the allowance covers a structured-output retry.
            usage_limits=UsageLimits(request_limit=3),
        )
        strategy = result.output
    except Exception as exc:  # noqa: BLE001 — prefill must never break the form
        logger.error("[Strategist] failed (%s) — literal prefill", exc, exc_info=True)
        return _fallback(brief)

    strategy = _sanitize(strategy, brief)
    # LLM-as-judge: verify the titles + Boolean query would actually return real
    # people BEFORE any vendor spend, and auto-correct them when they wouldn't.
    return await _apply_judgment(strategy, brief)


async def _apply_judgment(strategy: SearchStrategy, brief: SearchBrief) -> SearchStrategy:
    """Run the query Judge and fold its verdict into the strategy.

    Auto-correct + flag (the chosen policy): a repaired query/title family the
    Judge hands back is swapped in and re-sanitised through the same clamps, so a
    correction can never bypass the domain/location guarantees. Title repairs are
    additionally held to the ORIGINAL domain anchor — the Judge fixes phrasing,
    never the target profession. The verdict never blocks the run; it only lowers
    confidence and adds warnings the recruiter sees before clicking Run search.
    """
    try:
        judgment = await judge_query(strategy, brief.jobDescription)
    except Exception as exc:  # noqa: BLE001 — the vet is best-effort
        logger.error("[Judge] apply failed (%s) — leaving strategy as-is", exc, exc_info=True)
        return strategy
    if judgment is None:
        return strategy

    changed = False
    f = strategy.filters

    # Query repair: goes back through _sanitize's short-query / full-title clamps.
    new_query = (judgment.suggestedSearchQuery or "").strip()
    if new_query and new_query.lower() != (f.searchQuery or "").strip().lower():
        f.searchQuery = new_query
        changed = True

    # Title repair: only titles that stay in the ORIGINAL specialization.
    core = strategy.domainAnchor.coreTerms if strategy.domainAnchor else []
    suggested = [t.strip() for t in (judgment.suggestedTitles or []) if t and t.strip()]
    if suggested:
        in_domain = [t for t in suggested if title_in_domain(t, core)]
        if in_domain:
            f.currentJobTitles = in_domain
            changed = True
        else:
            logger.info("[Judge] ignored off-domain title repair %s (anchor %s)",
                        suggested, core)

    if changed:
        strategy = _sanitize(strategy, brief)

    # Flag: surface the Judge's concerns and temper confidence by verdict.
    for issue in judgment.issues:
        if issue and issue not in strategy.warnings:
            strategy.warnings.append(issue)
    if judgment.verdict == "unsearchable":
        strategy.warnings.append(
            "The query may return no results on LinkedIn — "
            + (judgment.reasoning or "review the titles and search query before running.")
        )
        strategy.confidence = min(strategy.confidence, 0.2)
    elif judgment.verdict == "risky":
        strategy.confidence = min(strategy.confidence, 0.6)

    return strategy


# ── Title / query / location quality clamps ─────────────────────────────────
# These catch the two hallucination shapes the friction analysis found on hard
# inputs (verbatim posting title as the search query; brand+module fragments
# like "SAP CO"/"SAP PS" as titles) and guarantee both engines get the SAME,
# correctly-spelled locations. A validator can't do this — it's about the CONTENT
# of the fields, not their types.

_WORD_RE = re.compile(r"[0-9a-zA-Zäöüßéèêëàâçîïôûùñ]+", re.UNICODE)

# Apify seniorityLevel code → Apollo person_seniorities code. Only used to carry
# a seniority the recruiter/AI explicitly set over to Apollo; left sparse.
_SENIORITY_APIFY_TO_APOLLO = {
    "100": "entry", "110": "entry", "120": "senior", "130": "senior",
    "200": "manager", "210": "manager", "220": "director",
    "300": "vp", "310": "c_suite", "320": "owner",
}


def _toks(s: Optional[str]) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(s or "")]


def _dedupe(xs: list[str], cap: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in xs:
        t = (x or "").strip()
        k = t.lower()
        if t and k not in seen:
            out.append(t)
            seen.add(k)
    return out[:cap]


def _short_query_from(title: str) -> str:
    """A SHORT fuzzy keyword phrase from a real title: drop trailing role words,
    keep the specialization, cap 3 tokens. 'SAP EWM Consultant' → 'SAP EWM'."""
    parts = [p for p in (title or "").replace("/", " ").split() if p]
    while parts and parts[-1].lower() in GENERIC_ROLE_WORDS:
        parts.pop()
    if not parts:
        parts = [p for p in (title or "").split() if p]
    return " ".join(parts[:3]).strip()


# The actor rejects a malformed or oversized Boolean outright, which reads
# downstream as "nobody matches" — worse than the verbatim-title problem this
# whole clamp exists to fix. A query recognised as Boolean skips the length/
# full-title clamp below, so it needs its OWN structural check instead.
MAX_SEARCH_QUERY_CHARS = 300  # actor limit


def _is_boolean_query(query: str) -> bool:
    """True if query contains Boolean search operators or grouping symbols."""
    q = (query or "").upper()
    return " OR " in q or " AND " in q or " NOT " in q or "(" in q or '"' in q


def _boolean_query_is_valid(query: str) -> bool:
    """Cheap structural check: balanced quotes/parens, within the actor's length
    limit. An unbalanced or oversized Boolean is worse than no Boolean at all —
    degrade to the derived short query instead of shipping it."""
    s = (query or "").strip()
    if not s or len(s) > MAX_SEARCH_QUERY_CHARS:
        return False
    if s.count('"') % 2:
        return False
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _looks_like_full_title(search_query: str, job_title: str) -> bool:
    """True when searchQuery is really the verbatim posting title (the #1 zero-result cause)."""
    if _is_boolean_query(search_query):
        return False
    sq = _toks(search_query)
    if not sq:
        return False
    if len(sq) > 5:
        return True
    s = " ".join(sq)
    jt = " ".join(_toks(job_title))
    return bool(jt) and (s == jt or jt in s)


def _is_degenerate_title(title: str) -> bool:
    """A brand+module fragment nobody carries as a headline: 'SAP CO', 'SAP PS'.
    ≤2 tokens, contains an ecosystem brand, and no profession word."""
    toks = _toks(title)
    if not toks or len(toks) > 2:
        return False
    return any(t in ECOSYSTEM_TOKENS for t in toks) and not any(
        t in GENERIC_ROLE_WORDS for t in toks)


# ── Structural repair: recover the title field and the domain gate from a ─────
# Boolean searchQuery that folds everything into one OR-block. The current
# instructions describe the Boolean string in detail but never mention
# `currentJobTitles` as its own field, and don't always survive into a query
# that keeps the mandated (DOMAIN) AND (TITLE) shape — the model sometimes
# emits every title it generated as a single OR-block and nothing else,
# leaving `currentJobTitles` empty and the domain gate missing entirely
# (observed live, "Sales Manager SAP Retail" run, 2026-07-30). These clamps
# repair the STRUCTURE without touching the prompt or inventing new domain
# terms — anything added comes from data the model or the JD extraction
# already vetted (the query's own quoted phrases, or `brief.mustHaveSkills`).

_QUOTED_PHRASE_RE = re.compile(r'"([^"]+)"')


def _phrase_is_title_shaped(phrase: str) -> bool:
    """True when a quoted Boolean phrase names a ROLE rather than a domain or
    product — it carries a generic role word (Manager, Director, Consultant,
    Executive, Partner...). This is how a single OR-block that mixes both
    without an explicit AND gets split back into its two gates."""
    return any(t in GENERIC_ROLE_WORDS for t in _toks(phrase))


def _title_gate_titles_from_query(query: str) -> list[str]:
    """Every quoted, title-shaped phrase in a Boolean searchQuery, in order,
    deduped. Used to recover `currentJobTitles` when the model folds its whole
    title family into searchQuery and leaves the dedicated field empty."""
    phrases = _QUOTED_PHRASE_RE.findall(query or "")
    return _dedupe([p for p in phrases if _phrase_is_title_shaped(p)], 20)


def _normalize_locations(locs: list[str]) -> list[str]:
    """Canonicalise each location to its catalogue label ('Frankfurt am Main' →
    'Frankfurt, Germany'; 'kolenz, germany' → 'Koblenz, Germany'), deduped. An
    unrecognised place is kept as-typed. This is what makes the two engines
    receive identical, correctly-spelled locations."""
    out: list[str] = []
    seen: set[str] = set()
    for loc in (locs or []):
        raw = (loc or "").strip()
        if not raw:
            continue
        canon = location_catalog.normalize(raw) or raw
        k = canon.lower()
        if k not in seen:
            out.append(canon)
            seen.add(k)
    return out


def _derive_apollo_plan(
    f: SearchFilters, brief: SearchBrief, focus_title: str, core_terms: list[str],
) -> ApolloPlan:
    """Build the Apollo people-search input from the SAME cleaned Apify plan.

    Single source of truth — the recruiter edits one set of titles/locations/
    skills and both engines get consistent, correctly-spelled input. This is the
    structural cure for the 'Koblenz' (Apify) vs 'Kolenz' (Apollo) divergence:
    there is no second, independently-hallucinated Apollo location to drift.
      • titles      — the cleaned title family (+ focus). Apollo OR-expands.
      • qKeywords   — the 1–3 defining skills (must-haves, else the anchor's core
                      specialization terms). Apollo ANDs these, so kept ≤3.
      • locations   — the SAME normalized locations as Apify.
      • seniorities — carried over only if a seniority was explicitly set.
    """
    titles = _dedupe([focus_title, *(f.currentJobTitles or [])], 12)
    qkw = _dedupe(list(brief.mustHaveSkills or []), 3)
    if not qkw:
        qkw = _dedupe(list(core_terms or []), 3)
    locs = _dedupe(list(f.locations or ([brief.jobLocation] if brief.jobLocation else [])), 5)
    seniorities: list[str] = []
    if f.seniorityLevel:
        code = _SENIORITY_APIFY_TO_APOLLO.get(str(f.seniorityLevel))
        if code:
            seniorities = [code]
    return ApolloPlan(titles=titles, qKeywords=qkw, locations=locs, seniorities=seniorities)


def _map_min_years_to_enum(min_years: float) -> str:
    """Convert a numeric years requirement to HarvestAPI enum code ('1'..'5')."""
    if min_years < 1:
        return "1"
    elif min_years < 3:
        return "2"
    elif min_years < 6:
        return "3"
    elif min_years < 10:
        return "4"
    else:
        return "5"


# LinkedIn-inferred enum filters that must NEVER carry an AI-assumed value: they
# are blank/wrong on most real profiles, so any value silently drops matches.
# Always forced to Any (None) — the recruiter can set them by hand if they truly
# need to narrow. `yearsOfExperience` is handled separately (allowed ONLY on an
# explicit basis), so it is deliberately NOT in this set.
_ENUM_INFERRED_FIELDS = (
    "seniorityLevel", "excludeSeniorityLevel",
    "function", "excludeFunction",
    "companyHeadcount", "yearsAtCurrentCompany",
)

# An explicit years-of-experience requirement in a JD, EN + DE. Matches a NUMBER
# bound to a years/Jahre unit: "5+ years", "3-5 years", "at least 2 years",
# "min. 3 Jahre", "3 Jahre Berufserfahrung". Deliberately strict: a number with
# no years unit (headcounts, dates), or the seniority of a title / the word
# "senior" / a bare "experience with X", is NOT a match — that is an assumption,
# and the whole point of the gate is to refuse assumptions.
_YEARS_EXPLICIT_RE = re.compile(
    r"\b\d{1,2}\s*(?:\+|to|-|–|bis)?\s*\d{0,2}\s*"
    r"(?:years?|yrs?|jahre?n?)\b"
    r"|\b\d{1,2}\s*(?:\+|plus)?\s*(?:years?|yrs?|jahre?n?)\b",
    re.IGNORECASE,
)

# Internal grades / bands / levels employers append that no one headlines:
# "II", "III", "L3", "Level 2", "Band 4", "Grade 3", "(m/w/d)", "(f/m/x)".
_GRADE_RE = re.compile(
    r"\(?\b(?:m\s*/\s*w\s*/\s*[dx]|f\s*/\s*m\s*/\s*[dx]|w\s*/\s*m\s*/\s*[dx])\b\)?"
    r"|\b(?:level|lvl|band|grade|stufe|tier)\s*[-]?\s*\d+\b"
    r"|\bl\d\b"
    r"|\b[IVX]{1,4}\b(?=\s*$|\s*[-–|(])"
    r"|\breq(?:uisition)?\s*#?\s*\d+\b",
    re.IGNORECASE,
)
# Employment-type noise that belongs in filters, not a title.
_EMPLOYMENT_WORDS = frozenset({
    "contract", "contractor", "freelance", "freelancer", "permanent",
    "fulltime", "full-time", "parttime", "part-time", "temporary", "temp",
    "intern", "internship", "werkstudent", "festanstellung", "vollzeit",
    "teilzeit", "remote", "onsite", "hybrid",
})


def _clean_posting_title(title: str) -> str:
    """Strip grades / req codes / employment-type / gender tags off a posting
    title, leaving the real role words. 'Senior Java Developer II (m/w/d)' →
    'Senior Java Developer'; 'SAP FICO Consultant - Contract' → 'SAP FICO
    Consultant'. Never returns empty when given a non-empty title (falls back to
    the original if cleaning would erase everything)."""
    raw = (title or "").strip()
    if not raw:
        return ""
    # Drop bracketed asides and trailing separators first.
    cut = re.sub(r"\([^)]*\)", " ", raw)
    cut = _GRADE_RE.sub(" ", cut)
    words = [w for w in re.split(r"\s+", cut) if w]
    words = [w for w in words if w.strip("-/,").lower() not in _EMPLOYMENT_WORDS]
    cleaned = " ".join(words).strip(" -/,")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned or raw


def _heuristic_title_family(brief: SearchBrief) -> list[str]:
    """A small, real-title family WITHOUT a model: the cleaned posting title,
    a seniority-stripped base, and the recruiter's seniority hint applied — the
    deterministic slice of what the Strategist would produce. Deduped, capped."""
    base = _clean_posting_title(brief.jobTitle)
    fam: list[str] = []
    if base:
        fam.append(base)
        # A seniority-stripped variant widens recall ("Senior SAP FI Consultant"
        # → "SAP FI Consultant") — many members drop the seniority word.
        stripped = " ".join(
            w for w in base.split()
            if w.lower() not in {"senior", "junior", "lead", "principal", "staff"}
        ).strip()
        if stripped and stripped.lower() != base.lower():
            fam.append(stripped)
    return _dedupe(fam, 8)


def _heuristic_ladder(f: SearchFilters) -> list[BroadeningStep]:
    """A deterministic broadening ladder for the fallback: drop the inferred
    enums, then widen the city to its OWN federal state. Titles/query stay locked
    (same rule the Broadener enforces). Empty when there's nothing to relax."""
    steps: list[BroadeningStep] = []

    # Step 1: drop any experience filter (the only enum the fallback may set).
    if f.yearsOfExperience:
        relaxed = f.model_copy(deep=True)
        relaxed.yearsOfExperience = None
        steps.append(BroadeningStep(
            step=len(steps) + 1, action="widen_years",
            detail="Dropped the years-of-experience filter — profiles rarely state tenure.",
            filters=relaxed,
        ))

    # Final step: widen a city to its own federal state, if it widens.
    widened = location_catalog.clamp_locations(
        list(f.locations or []),
        [location_catalog.state_widening(l) or l for l in (f.locations or [])],
    )
    if widened and widened != list(f.locations or []):
        relaxed = f.model_copy(deep=True)
        relaxed.yearsOfExperience = None
        relaxed.locations = widened
        steps.append(BroadeningStep(
            step=len(steps) + 1, action="widen_location",
            detail=f"Widened the city to its federal state ({', '.join(widened)}).",
            filters=relaxed,
        ))
    return steps


def _jd_states_years(text: str) -> bool:
    """True when the JD explicitly names a years-of-experience requirement.

    The gate behind "only use the experience filter when explicitly mentioned":
    a match here is the ONLY thing (besides the recruiter's own minYears) that
    lets an AI-proposed yearsOfExperience survive. Title seniority and the word
    'senior' deliberately do NOT match."""
    return bool(_YEARS_EXPLICIT_RE.search(text or ""))


def _sanitize(strategy: SearchStrategy, brief: SearchBrief) -> SearchStrategy:
    """Defensive clamps on model output (same spirit as account_intel's planner).

    The enum coercion already happened in SearchFilters' validator; this handles
    the structural things a validator can't: an empty proposal, a ladder that
    isn't ordered, and a runaway title list.
    """
    f = strategy.filters
    # An empty filter set is unusable — fall back rather than search on nothing.
    # (Checked BEFORE the currentJobTitles invariant below: is_empty() still
    # looks at currentJobTitles too, for a model/Judge response that hasn't run
    # through this function yet.)
    if f.is_empty():
        logger.warning("[Strategist] returned an empty filter set — literal prefill")
        return _fallback(brief)

    # Scavenging pool ONLY — never the output. If the model (against instructions)
    # still populated currentJobTitles, or an old/legacy caller did, its entries
    # are still useful for recovering a decent `best_title`/`focusTitle` when
    # searchQuery ITSELF is also degenerate (e.g. the model echoed the raw
    # posting title into both). Merged with searchQuery's own TITLE GROUP below;
    # the fallback chain that used to read `f.currentJobTitles` directly for this
    # purpose would otherwise go blind the moment the field is forced empty.
    _model_titles = list(f.currentJobTitles or [])

    # ── HARD INVARIANT: currentJobTitles is ALWAYS empty from here on, no
    # matter what the model or a later repair step wrote to it. Set FIRST, not
    # last, so every helper below that touches currentJobTitles (title cleanup,
    # best_title, the broadening-ladder lock) sees the same empty value the
    # final output has — otherwise a ladder step can lock in a stale non-empty
    # list from before this ran, while the main filters end up empty (observed
    # live, 2026-07-31: `_apply_judgment` — a SEPARATE LLM call with its own,
    # still title/query-split prompt in judge.py — can repair `f.currentJobTitles`
    # directly from its own `suggestedTitles`, then re-run this function). All
    # title information now lives only inside searchQuery's TITLE GROUP; every
    # place that used to read `f.currentJobTitles` for real title data has been
    # repointed to `_title_gate_titles_from_query(f.searchQuery)` (or, for
    # best_title/focusTitle recovery specifically, `_model_titles` above) instead.
    f.currentJobTitles = []

    # ── Inferred enum filters are FORCED to Any ─────────────────────────────
    # seniorityLevel / function / companyHeadcount / yearsAtCurrentCompany (and
    # their excludes) are blank or wrong on most real profiles, so an AI-assumed
    # value silently drops qualifying people. We never let the model set them —
    # the recruiter can narrow by hand in the Advanced panel if they must.
    for field in _ENUM_INFERRED_FIELDS:
        if getattr(f, field, None) is not None:
            setattr(f, field, None)

    # ── yearsOfExperience: allowed ONLY on an EXPLICIT basis ─────────────────
    # Two things count as explicit: the recruiter's own minYears (they typed a
    # number), or an explicit years requirement in the JD text. The recruiter's
    # number wins. An AI-proposed value with no explicit basis is an assumption,
    # so it is dropped — the discovery loop can still narrow later if needed.
    if brief.minYears is not None:
        f.yearsOfExperience = _map_min_years_to_enum(brief.minYears)
    elif f.yearsOfExperience and not _jd_states_years(brief.jobDescription):
        logger.info(
            "[Strategist] dropped AI-assumed yearsOfExperience=%s — JD states no "
            "explicit years requirement", f.yearsOfExperience,
        )
        f.yearsOfExperience = None

    # ── currentJobTitles is INTENTIONALLY left empty now (2026-07-31 redesign):
    # the title family lives only inside searchQuery's TITLE GROUP, so it never
    # acts as a second, independently-restrictive actor-side filter alongside
    # the query that already carries the same titles as a fuzzy full-profile
    # match. An earlier version of this function auto-recovered titles INTO
    # currentJobTitles from a Boolean-only query — that recovery is deliberately
    # gone: it would silently refill the field this design requires to stay
    # empty. `_title_gate_titles_from_query` still exists and is used by
    # `candidate_pipeline.py`'s QA-anchor fallback, which needs the same
    # extraction for a different purpose.

    # ── Title candidate pool for best_title/focusTitle recovery ONLY — never
    # written back to f.currentJobTitles, which stays empty per the invariant
    # above. Two sources, merged: searchQuery's own TITLE GROUP (the new
    # intended source of truth) and `_model_titles` (whatever the model put in
    # currentJobTitles despite instructions — a scavenging fallback for when
    # searchQuery ITSELF is also degenerate, e.g. the model echoed the raw
    # posting title into both). Brand+module fragments ("SAP CO", "SAP PS") that
    # no one carries as a headline are dropped, then deduped.
    title_pool = _dedupe([*_title_gate_titles_from_query(f.searchQuery), *_model_titles], 20)
    cleaned: list[str] = []
    seen_t: set[str] = set()
    for t in title_pool:
        t = (t or "").strip()
        key = " ".join(_toks(t))
        if not key or key in seen_t:
            continue
        if _is_degenerate_title(t):
            continue
        cleaned.append(t)
        seen_t.add(key)

    # The strongest REAL title: the first that is neither the posting title
    # restated (employer language) nor a fragment. Seeds the short query and the
    # focus title so neither inherits the model's junk when it emitted some.
    best_title = next(
        (t for t in cleaned if not _looks_like_full_title(t, brief.jobTitle)),
        (cleaned[0] if cleaned
         else (strategy.focusTitle or brief.jobTitle)),
    )

    # A location the recruiter gave should never be dropped silently; and every
    # location is canonicalised so the two engines get the SAME, correctly-spelled
    # place (the 'Koblenz'/'Kolenz' divergence, fixed at the source).
    if brief.jobLocation and not f.locations:
        f.locations = [brief.jobLocation]
    f.locations = _normalize_locations(f.locations)

    # ── Rebuild the mandatory (DOMAIN GATE) AND (TITLE GATE) structure whenever
    # the query is Boolean but has no top-level AND. Two distinct malformed
    # shapes reach here and need different repairs:
    #   (a) MIXED, UNSPLIT — one OR-block contains BOTH domain and title-shaped
    #       phrases with no AND at all. Observed live, 2026-07-31, "Inhouse
    #       Consultant SAP CO/PS": the query Judge (a separate LLM call, judge.py,
    #       still on the OLD split-field prompt) proposed a `suggestedSearchQuery`
    #       of exactly this shape and it was accepted verbatim as a "repair".
    #       Fix: split the EXISTING phrases into the two groups and AND them —
    #       nothing invented, just restructured.
    #   (b) TITLE-ONLY — every phrase is title-shaped, no domain phrase exists at
    #       all. Fix: keep the existing behaviour of adding vetted domain terms
    #       (`mustHaveSkills`, else the heuristic anchor) — never invented fresh.
    # currentJobTitles is always empty by design now (see SEARCH ARCHITECTURE),
    # so the title family used to detect/rebuild this comes from the query's OWN
    # quoted phrases via `_title_gate_titles_from_query`, not from that field.
    if _is_boolean_query(f.searchQuery) and " AND " not in f.searchQuery.upper():
        phrases = _QUOTED_PHRASE_RE.findall(f.searchQuery)
        domain_phrases = [p for p in phrases if not _phrase_is_title_shaped(p)]
        query_titles = _title_gate_titles_from_query(f.searchQuery)

        if domain_phrases and query_titles:
            # (a) Mixed and unsplit — restructure into (DOMAIN) AND (TITLE)
            # using the SAME phrases the query already had, just regrouped.
            domain_block = " OR ".join(f'"{d}"' for d in domain_phrases)
            title_block = " OR ".join(f'"{t}"' for t in query_titles)
            rebuilt = f"({domain_block}) AND ({title_block})"
            if _boolean_query_is_valid(rebuilt):
                logger.info(
                    "[Strategist] searchQuery mixed domain+title in one "
                    "unstructured OR-block — split and joined as "
                    "(DOMAIN) AND (TITLE)",
                )
                f.searchQuery = rebuilt
        elif not domain_phrases and query_titles:
            # (b) Title-only — add a domain group from vetted data.
            domain_terms = _dedupe(list(brief.mustHaveSkills or []), 6)
            if not domain_terms:
                core, _eco = derive_anchor_terms([brief.jobTitle, *query_titles])
                domain_terms = _dedupe(core, 6)
            if domain_terms:
                domain_block = " OR ".join(f'"{d}"' for d in domain_terms)
                title_block = " OR ".join(f'"{t}"' for t in query_titles)
                rebuilt = f"({domain_block}) AND ({title_block})"
                if _boolean_query_is_valid(rebuilt):
                    logger.info(
                        "[Strategist] searchQuery had no domain gate — rebuilt as "
                        "(DOMAIN) AND (TITLE) using %d domain term(s)",
                        len(domain_terms),
                    )
                    f.searchQuery = rebuilt

    # ── searchQuery: a SHORT keyword phrase, a valid Boolean, or the derived
    # fallback — never the posting title verbatim (the documented #1
    # zero-result cause) and never a malformed/oversized Boolean (an equally
    # empty result, just harder to diagnose).
    if _is_boolean_query(f.searchQuery) and not _boolean_query_is_valid(f.searchQuery):
        logger.warning("[Strategist] discarding malformed Boolean searchQuery: %r",
                       f.searchQuery[:120])
        derived = _short_query_from(best_title)
        if derived:
            f.searchQuery = derived
    elif not f.searchQuery.strip() or _looks_like_full_title(f.searchQuery, brief.jobTitle):
        derived = _short_query_from(best_title)
        if derived:
            f.searchQuery = derived

    # ── Domain anchor: validate the LLM's, or derive one ────────────────────
    # The anchor is load-bearing (the Broadener guard and the widen-suggestions
    # flow both read it), so it must ALWAYS exist and always be self-consistent
    # with the proposed titles.
    anchor = strategy.domainAnchor
    anchor.coreTerms = [t.strip().lower() for t in anchor.coreTerms if t and t.strip()][:12]
    anchor.ecosystemTerms = [t.strip().lower() for t in anchor.ecosystemTerms if t and t.strip()][:6]
    # A "core" term that is really a generic role word (consultant, manager…)
    # would let every profession through — strip them.
    anchor.coreTerms = [t for t in anchor.coreTerms if t not in GENERIC_ROLE_WORDS]
    # currentJobTitles is always empty now, so the title family used to seed or
    # cross-check the anchor comes from searchQuery's own TITLE GROUP instead.
    query_titles = _title_gate_titles_from_query(f.searchQuery)
    if anchor.is_empty():
        core, eco = derive_anchor_terms([brief.jobTitle, *query_titles])
        anchor.coreTerms, anchor.ecosystemTerms = core, eco
    else:
        # Self-consistency: if the anchor rejects most of the model's OWN titles,
        # the anchor is wrong (too narrow), not the titles — rebuild it from them.
        titles = query_titles
        if titles:
            passing = sum(1 for t in titles if title_in_domain(t, anchor.coreTerms))
            if passing * 2 < len(titles):
                logger.warning(
                    "[Strategist] anchor %s rejects %d/%d of its own titles — rebuilt",
                    anchor.coreTerms, len(titles) - passing, len(titles),
                )
                core, eco = derive_anchor_terms([brief.jobTitle, *titles])
                anchor.coreTerms, anchor.ecosystemTerms = core, eco

    # Adjacent titles are recruiter-opt-in ONLY. Anything that is actually
    # IN-specialty already lives in searchQuery's TITLE GROUP, so de-dupe
    # against that (currentJobTitles is always empty now), cap, and drop empties.
    seen = {t.strip().lower() for t in query_titles}
    strategy.adjacentTitles = [
        t.strip() for t in strategy.adjacentTitles
        if t and t.strip() and t.strip().lower() not in seen
    ][:6]

    # Renumber the ladder so `step` is authoritative regardless of what came
    # back — and LOCK its titles/query, clamp its locations: a fallback step may
    # relax enums, companies and language, plus at most widen a city to its OWN
    # federal state (location_catalog.clamp_locations — same rule the Broadener
    # enforces, so the two paths can't diverge). Never the country, never
    # another state: that's the recruiter's next run. This is the code-level
    # guarantee behind "widening never means a different job — or a different
    # place" (added after a Bamberg search silently widened to state level and,
    # via the vendor's loose filter, went Germany-wide).
    ladder: list[BroadeningStep] = []
    for i, step in enumerate(strategy.broadeningLadder[:5], start=1):
        step.step = i
        step.filters.currentJobTitles = list(f.currentJobTitles)
        step.filters.searchQuery = f.searchQuery
        step.filters.locations = location_catalog.clamp_locations(
            list(f.locations or []), list(step.filters.locations or []))
        # A ladder step may only ever RELAX. Force the inferred enums to Any (they
        # were never set on the main filters) and never let a step carry a years
        # value the main search didn't — a fallback can drop years, not add one.
        for field in _ENUM_INFERRED_FIELDS:
            setattr(step.filters, field, None)
        if step.filters.yearsOfExperience not in (None, f.yearsOfExperience):
            step.filters.yearsOfExperience = f.yearsOfExperience
        if not step.filters.is_empty():
            ladder.append(step)
    strategy.broadeningLadder = ladder

    # Rationale must reference real fields, or the UI renders orphan tooltips.
    valid = set(SearchFilters.model_fields)
    strategy.rationale = [r for r in strategy.rationale if r.field in valid]

    # ── focusTitle: always present, and never a fragment or a bare posting-title
    # restatement — headline the review screen with the strongest real title.
    if (not (strategy.focusTitle or "").strip()
            or _is_degenerate_title(strategy.focusTitle)
            or _looks_like_full_title(strategy.focusTitle, brief.jobTitle)):
        strategy.focusTitle = best_title or strategy.interpretedRole or brief.jobTitle

    # ── Apollo plan: DERIVED in code from the cleaned Apify plan, not trusted
    # from the model. One source of truth → the two engines can never diverge
    # (this is the structural fix for the 'Koblenz' vs 'Kolenz' bug), and the
    # model has ~40% less to emit, so it spends its budget on the title family.
    # NOTE: with currentJobTitles always empty, Apollo's titles list collapses to
    # just [focusTitle] — currently inert since Apollo search is disabled.
    strategy.apolloPlan = _derive_apollo_plan(
        f, brief, strategy.focusTitle, anchor.coreTerms)
    return strategy
