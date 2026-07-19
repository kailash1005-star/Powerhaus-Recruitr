import json
import os

path = r"C:\Users\WELCOME\.gemini\antigravity\brain\bfd15890-1efd-4cf3-a5b2-f0da12410f3a\.system_generated\logs\transcript.jsonl"
if os.path.exists(path):
    print("Transcript found, searching...")
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if "total=20, inserted=" in line or "6a0d908d" in line:
                print(f"Line {i+1}:")
                try:
                    obj = json.loads(line)
                    print(f"Type: {obj.get('type')}, Status: {obj.get('status')}")
                    content = obj.get('content', '')
                    if content:
                        print(f"Content length: {len(content)}")
                        # Print the whole content if short, or parts of it
                        print("Content:")
                        print(content)
                    tool_calls = obj.get('tool_calls', [])
                    if tool_calls:
                        print(f"Tool calls: {tool_calls}")
                except Exception as e:
                    print(f"Parse error: {e}")
                    print(line[:500])
else:
    print("Transcript not found.")
