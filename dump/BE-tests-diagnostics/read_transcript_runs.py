import json
import os

path = r"C:\Users\WELCOME\Desktop\Resume\BE\tests\search_transcript.py"
# Let's write a new script to read transcript and print occurrences of 6a0d90
transcript_path = r"C:\Users\WELCOME\.gemini\antigravity\brain\bfd15890-1efd-4cf3-a5b2-f0da12410f3a\.system_generated\logs\transcript.jsonl"

with open(transcript_path, "r", encoding="utf-8") as f:
    for idx, line in enumerate(f):
        if "6a0d908" in line:
            try:
                data = json.loads(line)
                print(f"--- Line {idx+1} ({data.get('type')}) ---")
                content = data.get("content") or ""
                if "[Naukri]" in content or "Run created" in content:
                    print(content[:1000])
                    print("...")
                    print(content[-500:])
            except Exception as e:
                print(f"Error parse: {e}")
