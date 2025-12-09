import google.generativeai as genai
import os

# 1. Paste your key here directly just for this test
API_KEY = "PASTE_YOUR_API_KEY_HERE"

genai.configure(api_key=API_KEY)

print("Listing available models...")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(f"- {m.name}")
