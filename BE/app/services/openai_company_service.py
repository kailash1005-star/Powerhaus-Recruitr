"""
OpenAI Company Service
LLM-based company industry resolution from a LinkedIn URL.
No keyword/slug matching — the user-selected target industry NAMES are passed
straight to the model, which determines which one (if any) best fits.
"""
import json
import logging
import re
import time
from typing import Optional, List

logger = logging.getLogger(__name__)


class OpenAICompanyService:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key)
        self._model = model

    @staticmethod
    def get_slug(linkedin_url: str) -> Optional[str]:
        """Extract company slug from a LinkedIn URL (e.g. linkedin.com/company/<slug>)."""
        if not linkedin_url:
            return None
        try:
            parts = linkedin_url.rstrip("/").split("/")
            idx = parts.index("company")
            return parts[idx + 1]
        except (ValueError, IndexError):
            return None

    def match_industry(
        self,
        company_industries: List[str],
        target_industries: List[str],
    ) -> Optional[str]:
        """Semantically match a company's REAL industry (from LinkedIn) against the
        user's selected target industries.

        Both inputs are dynamic:
          * company_industries — the industry/industries LinkedIn reports for the company
          * target_industries  — the industries the user selected in the UI (run config)

        No hardcoded keyword lists are involved. Returns the single best-matching
        target industry NAME (verbatim from the user's list) or None if none fit.
        """
        company_text = ", ".join([c for c in (company_industries or []) if c]).strip()
        if not company_text or not target_industries:
            return None

        target_list_text = "\n".join(f"- {name}" for name in target_industries)

        prompt = (
            f"A company's industry, as listed on LinkedIn, is: \"{company_text}\".\n\n"
            "Decide whether that industry semantically belongs to ONE of the user's "
            "target industries below. Use your understanding of synonyms and related "
            "fields (e.g. \"Renewables\" ≈ \"Clean Energy\", \"Hospitals\" ≈ "
            "\"Healthcare\", \"Staffing\" ≈ \"Recruitment\").\n\n"
            f"Target industries:\n{target_list_text}\n\n"
            "Pick the single best-matching target industry NAME (verbatim from the list "
            "above) or null if none of them fit.\n\n"
            "Return ONLY valid JSON, no markdown:\n"
            "{\n"
            '  "matched_industry": "one of the target names above, verbatim, or null"\n'
            "}\n"
        )

        lower_targets = {t.lower(): t for t in target_industries}

        raw = ""
        for attempt in range(1, 3):
            try:
                completion = self._client.chat.completions.create(
                    model=self._model,
                    temperature=0,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an industry-classification assistant. "
                                "You return only valid JSON, no commentary."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                )
                try:
                    from app.services import cost_service
                    cost_service.record_chat(completion, model=self._model,
                                             service="openai", operation="company_classify")
                except Exception:  # noqa: BLE001
                    pass
                raw = (completion.choices[0].message.content or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw.rsplit("```", 1)[0]
                raw = raw.strip()

                data = json.loads(raw)
                matched = data.get("matched_industry")
                if not matched:
                    return None
                # Map back to the exact target name (case-insensitive); reject hallucinations.
                return lower_targets.get(str(matched).strip().lower())
            except json.JSONDecodeError:
                logger.warning(
                    "[OpenAICompany] industry match JSON parse failed (attempt %d) — raw: %.200s",
                    attempt, raw,
                )
                if attempt < 2:
                    continue
                return None
            except Exception as e:
                logger.error("[OpenAICompany] industry match call failed (attempt %d): %s", attempt, e)
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None

        return None

    def fetch_company_info(
        self,
        linkedin_url: str,
        target_industries: List[str],
        max_staff_count: int = 10000,
    ) -> Optional[dict]:
        """
        Ask the LLM to identify a company by LinkedIn URL and classify it
        against the supplied list of target industry display names.

        Returns dict with:
          companyName, companyDomain, companyIndustry, matchedIndustry,
          staffCount, website, targeted
        """
        if not linkedin_url:
            return None

        target_list_text = "\n".join(f"- {name}" for name in target_industries) or "- (none)"

        prompt = (
            f"LinkedIn URL: {linkedin_url}\n\n"
            "Using your general knowledge, extract company information.\n\n"
            "Then classify the company against this list of TARGET industries the user provided:\n"
            f"{target_list_text}\n\n"
            "You understand industry synonyms (e.g. \"recruitment\" ≈ \"staffing\", "
            "\"healthcare\" ≈ \"hospitals\", \"clean tech\" ≈ \"renewables\"). "
            "Pick the single best matching target industry NAME (verbatim from the list) "
            "or null if none fits.\n\n"
            "Rules for `targeted`:\n"
            f"- true ONLY if matchedIndustry is not null AND staffCount < {max_staff_count} "
            "AND the company is NOT a staffing/recruitment agency itself.\n"
            "- false otherwise.\n\n"
            "Return ONLY valid JSON, no markdown:\n"
            "{\n"
            '  "company_name": "official company name",\n'
            '  "company_domain": "domain only (e.g. example.com)",\n'
            '  "company_industry": "primary industry as plain text",\n'
            '  "matched_industry": "one of the target names above, or null",\n'
            '  "company_size": employee_count_as_integer,\n'
            '  "company_website": "full website URL",\n'
            '  "targeted": true_or_false\n'
            "}\n"
        )

        max_retries = 2
        raw = ""
        for attempt in range(1, max_retries + 1):
            try:
                completion = self._client.chat.completions.create(
                    model=self._model,
                    temperature=0,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a data extraction assistant. "
                                "You return only valid JSON, no commentary."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                )
                try:
                    from app.services import cost_service
                    cost_service.record_chat(completion, model=self._model,
                                             service="openai", operation="company_classify")
                except Exception:  # noqa: BLE001
                    pass
                raw = (completion.choices[0].message.content or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw.rsplit("```", 1)[0]
                raw = raw.strip()

                data = json.loads(raw)

                staff_raw = data.get("company_size", 0)
                if isinstance(staff_raw, str):
                    nums = re.findall(r"\d+", staff_raw.replace(",", ""))
                    staff_count = int(nums[0]) if nums else 0
                else:
                    staff_count = int(staff_raw) if staff_raw else 0

                matched_industry = data.get("matched_industry")
                # Defensive: must be one of the user's targets (case-insensitive)
                if matched_industry:
                    lower_targets = {t.lower(): t for t in target_industries}
                    matched_industry = lower_targets.get(str(matched_industry).lower())

                targeted = bool(data.get("targeted", False))
                if not matched_industry or staff_count >= max_staff_count:
                    targeted = False

                return {
                    "companyName": data.get("company_name", "") or "",
                    "companyDomain": (data.get("company_domain", "") or "")
                    .replace("https://", "")
                    .replace("http://", "")
                    .rstrip("/"),
                    "companyIndustry": data.get("company_industry", "") or "",
                    "matchedIndustry": matched_industry,
                    "staffCount": staff_count,
                    "website": data.get("company_website", "") or "",
                    "targeted": targeted,
                }
            except json.JSONDecodeError:
                logger.warning(
                    "[OpenAICompany] JSON parse failed (attempt %d/%d) for %s — raw: %.200s",
                    attempt, max_retries, linkedin_url, raw,
                )
                if attempt < max_retries:
                    continue
                return None
            except Exception as e:
                logger.error(
                    "[OpenAICompany] OpenAI call failed (attempt %d/%d) for %s: %s",
                    attempt, max_retries, linkedin_url, e,
                )
                if attempt < max_retries:
                    time.sleep(1)
                    continue
                return None

        return None
