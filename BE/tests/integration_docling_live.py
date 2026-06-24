"""
Live Docling path check against a running backend.

Uploads an HTML CV (routed through Docling's DocumentConverter — the same path
real PDF/DOCX CVs use), waits for ingest, then runs a JD match. The first call
loads/downloads Docling models, so allow a few minutes.
"""
import os
import time

import requests

BASE = "http://127.0.0.1:8000/api/v1/matching"
HERE = os.path.dirname(__file__)

JD = (
    "Senior Python Engineer, Hamburg or remote. Must have: Python, AWS, Kubernetes. "
    "5+ years. Nice to have: FastAPI."
)


def main():
    path = os.path.join(HERE, "sample_cv.html")
    with open(path, "rb") as f:
        files = [("files", ("sample_cv.html", f, "text/html"))]
        r = requests.post(f"{BASE}/cv/upload", files=files, timeout=120)
    r.raise_for_status()
    batch = r.json()["batchId"]
    print("uploaded (Docling path):", r.json())

    final = None
    for _ in range(120):  # up to ~4 min for first-time model load
        time.sleep(2)
        st = requests.get(f"{BASE}/cv/batch/{batch}", timeout=30).json()
        if st["counts"] != (final or {}):
            print("  status:", st["counts"])
            final = st["counts"]
        if st["complete"]:
            break

    # Inspect what Docling extracted
    page = requests.get(f"{BASE}/cv?limit=5", timeout=30).json()
    for item in page["items"]:
        if item.get("sourceFileName") == "sample_cv.html":
            prof = item.get("profile") or {}
            print("\nDocling+LLM extracted profile:")
            print("  name:", prof.get("fullName"), "| title:", prof.get("currentTitle"),
                  "| years:", prof.get("totalYears"))
            print("  skills:", prof.get("skills"))
            print("  status:", item.get("status"), "| error:", item.get("error"))
            assert item.get("status") == "embedded", f"expected embedded, got {item.get('status')}"

    # Run a match
    r = requests.post(f"{BASE}/run/json", json={"jdText": JD}, timeout=120)
    r.raise_for_status()
    data = r.json()
    print("\nMatch — JD:", data["jdTitle"], "| considered:", data["candidatesConsidered"])
    for i, c in enumerate(data["results"], 1):
        print(f"  #{i} {c['fullName']} score={c['score']} :: {c['reasons'][:1]}")
    assert data["results"], "expected results from Docling-parsed CV"
    print("\nPASS: Docling document path works end-to-end.")


if __name__ == "__main__":
    main()
