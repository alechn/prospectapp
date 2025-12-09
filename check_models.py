import google.generativeai as genai
import os

# 1. Paste your key here directly just for this test
API_KEY = "AIzaSyBX69fyaTceINn3NQoD8Tn5rS8JaCj3W8E"

genai.configure(api_key=API_KEY)

print("Listing available models...")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(f"- {m.name}")
