"""Smoke test: confirm the Recall.ai API key in .env actually works.

Run from the project root:
    python scripts/smoke_test_recall.py
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("RECALL_API_KEY")
region = os.getenv("RECALL_REGION", "us-west-2")

if not api_key:
    print("ERROR: RECALL_API_KEY is missing from .env")
    sys.exit(1)

print(f"Loaded API key (last 4 chars): ...{api_key[-4:]}")
print(f"Region: {region}")

url = f"https://{region}.recall.ai/api/v1/bot/"
headers = {"Authorization": f"Token {api_key}"}

print(f"\nGET {url}")
response = requests.get(url, headers=headers, timeout=10)

print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    bot_count = len(data.get("results", []))
    print(f"Auth works. You have {bot_count} bot(s) on record.")
elif response.status_code == 401:
    print("Auth FAILED — the API key was rejected. Double-check RECALL_API_KEY in .env.")
    sys.exit(1)
else:
    print(f"Unexpected response: {response.text[:300]}")
    sys.exit(1)
