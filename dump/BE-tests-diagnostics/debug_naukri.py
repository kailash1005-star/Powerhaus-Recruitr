import asyncio
import sys
import os
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings
from firecrawl import Firecrawl
from app.services.naukri_service import collect_naukri_jobs

# Set up logging to stdout
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def test_naukri():
    api_key = settings.FIRECRAWL_API_KEY
    print(f"Using Firecrawl API Key: {api_key}")
    if not api_key:
        print("Error: FIRECRAWL_API_KEY is not set.")
        return
        
    firecrawl = Firecrawl(api_key=api_key)
    search_url = "https://www.naukri.com/python-developer-jobs-in-chennai?k=python%20developer&l=chennai"
    print(f"Scraping URL: {search_url}")
    
    try:
        # Directly call Firecrawl to see what it returns
        result = firecrawl.scrape(
            search_url,
            formats=[{
                "type": "json",
                "schema": {
                    "type": "object",
                    "properties": {
                        "jobs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title":      {"type": "string"},
                                    "company":    {"type": "string"},
                                    "salary":     {"type": "string"},
                                    "location":   {"type": "string"},
                                    "experience": {"type": "string"},
                                    "skills":     {"type": "array", "items": {"type": "string"}},
                                    "url":        {"type": "string"},
                                },
                                "required": ["title", "company"]
                            }
                        }
                    },
                    "required": ["jobs"]
                },
                "prompt": "Extract job listings."
            }]
        )
        print("Scrape completed successfully!")
        print(f"Result properties: {dir(result)}")
        print(f"Metadata: {getattr(result, 'metadata', None)}")
        print(f"JSON data: {getattr(result, 'json', None) or getattr(result, 'JSON', None)}")
    except Exception as e:
        print("Scrape failed with exception:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_naukri()
