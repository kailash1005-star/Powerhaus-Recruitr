---
name: prospect-enrichment-outreach
description: How prospect (lead) Apollo enrichment + email outreach is wired in Recruitr
metadata:
  type: project
---

Prospects (HR-leadership leads, `prospects` collection) are found via Apollo's FREE people-search, which masks email. On-demand enrichment unlocks the email:

- Backend: `POST /api/v1/jobs/prospects/{id}/enrich` (in `BE/app/api/v1/jobs.py`) → `ApolloService._enrich_single` → Apollo `/people/match` with `reveal_personal_emails`; persists `email`, `isEnriched`, `prospectDetails.{linkedinUrl,phone,location}`. Mirrors the candidate enrich at `pipelines/candidates/{id}/enrich`.
- UI: `components/ProspectsSlideOut.tsx` has an "Enrich email" button (calls `enrichProspect` in `lib/api.ts`) and a static email template.

**Send delivery was intentionally deferred** (user decision, 2026-06-24). The "Send email" button is a `mailto:` link that opens the user's own mail client prefilled with the prospect email + static template — no SMTP/Smartlead needed. SMTP (`email_service.send_email`) and Smartlead enroll (`outreach_service.enroll`) exist in the codebase but are NOT configured in `.env` (no SMTP_*/SMARTLEAD_* keys). APOLLO_API_KEY and OPENAI_API_KEY are present. Email body uses the static template, not the LLM draft, per user preference.
