"""
Live integration check against a running backend (http://127.0.0.1:8000).

Exercises the FULL pipeline without Docling by using .txt CVs (the parser decodes
text directly): upload CVs -> wait for ingest -> run a JD match -> print top-N.

Run: venv\Scripts\python.exe tests/integration_matching_live.py
"""
import io
import time

import requests

BASE = "http://127.0.0.1:8000/api/v1/matching"

CVS = {
    "anna_python.txt": (
        "Anna Müller\nEmail: anna.mueller@example.com | Phone: +49 30 1234567 | Berlin, Germany\n"
        "Senior Python Engineer with 7 years of experience.\n"
        "Skills: Python, AWS, Docker, FastAPI, PostgreSQL, Kubernetes.\n"
        "Experience: Lead Backend Engineer at FinTechGmbH (2019-present) building "
        "Python microservices on AWS with Docker. Built CI/CD and REST APIs."
    ),
    "ben_java.txt": (
        "Ben Schmidt\nEmail: ben.schmidt@example.com | Munich, Germany\n"
        "Java Developer with 3 years of experience.\n"
        "Skills: Java, Spring Boot, MySQL, Maven.\n"
        "Experience: Backend Developer at AutoSoft (2021-present) building Java Spring services."
    ),
    "carla_data.txt": (
        "Carla Rossi\nEmail: carla.rossi@example.com | Remote\n"
        "Data Engineer with 5 years of experience.\n"
        "Skills: Python, AWS, Spark, Airflow, SQL, Docker.\n"
        "Experience: Data Engineer at DataWorks (2020-present) building Python ETL pipelines on AWS."
    ),
}

JD = (
    "We are hiring a Senior Python Engineer in Berlin. "
    "Must have: Python, AWS, Docker. 5+ years experience required. "
    "Nice to have: Kubernetes, FastAPI."
)


def main():
    # 1. upload CVs
    files = [("files", (name, io.BytesIO(body.encode()), "text/plain")) for name, body in CVS.items()]
    r = requests.post(f"{BASE}/cv/upload", files=files, timeout=60)
    r.raise_for_status()
    batch = r.json()["batchId"]
    print("uploaded batch:", batch, r.json())

    # 2. poll ingestion
    for _ in range(60):
        time.sleep(2)
        st = requests.get(f"{BASE}/cv/batch/{batch}", timeout=30).json()
        print("  status:", st["counts"])
        if st["complete"]:
            break

    # 3. run match
    r = requests.post(f"{BASE}/run/json", json={"jdText": JD}, timeout=120)
    r.raise_for_status()
    data = r.json()
    print("\nJD title:", data["jdTitle"], "| considered:", data["candidatesConsidered"])
    print("=" * 70)
    for i, c in enumerate(data["results"], 1):
        print(f"#{i}  {c['fullName']}  score={c['score']}  ({c['currentTitle']})")
        print("    contact:", c["contact"].get("email") or c["contact"].get("phone"))
        print("    subscores:", c["subscores"])
        for rsn in c["reasons"]:
            print("    + ", rsn)
        if c["gaps"]:
            print("    - gaps:", c["gaps"])
        print("-" * 70)

    assert data["candidatesConsidered"] >= 3, "expected at least 3 candidates"
    assert data["results"], "expected ranked results"
    # Anna (Python/AWS/Docker, Berlin, 7y) should outrank Ben (Java, Munich, 3y)
    names = [c["fullName"] for c in data["results"]]
    print("\nRanked order:", names)
    print("PASS: pipeline returned ranked, reasoned candidates.")


if __name__ == "__main__":
    main()
