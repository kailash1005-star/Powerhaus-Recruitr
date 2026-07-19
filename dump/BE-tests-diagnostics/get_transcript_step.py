import json
import os

path = r"C:\Users\WELCOME\.gemini\antigravity\brain\bfd15890-1efd-4cf3-a5b2-f0da12410f3a\.system_generated\logs\transcript.jsonl"
with open(path, "r", encoding="utf-8") as f:
    for idx, line in enumerate(f):
        # We want to print any line containing "Integration Test Run"
        if "Integration Test Run" in line:
            print(f"--- Line {idx+1} ---")
            try:
                data = json.loads(line)
                print(f"Type: {data.get('type')}, Status: {data.get('status')}")
                content = data.get("content") or ""
                print(content[:1500])
                print("...")
                print(content[-1500:])
            except Exception as e:
                print(line[:500])
