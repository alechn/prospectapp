import streamlit as st
import requests
import pandas as pd
import json
import google.generativeai as genai
import time
from unidecode import unidecode
from typing import Set, Tuple

# --- CONFIGURATION ---
st.set_page_config(page_title="Universal Alumni Finder", layout="wide")
st.title("🇧🇷 Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash-Lite")

# =========================================================
#            PART 0: API KEY SETUP
# =========================================================
# 1. Try to get key from Streamlit Secrets
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
    st.sidebar.success("🔑 API Key loaded securely")
else:
    # 2. Fallback to manual entry
    api_key = st.sidebar.text_input("Google Gemini API Key", type="password")
    st.sidebar.markdown("[Get a free Gemini Key here](https://aistudio.google.com/app/apikey)")

# Configure Gemini if key is present
if api_key:
    genai.configure(api_key=api_key)

# =========================================================
#            PART 1: HELPER FUNCTIONS
# =========================================================

def normalize_token(s: str) -> str:
    """Standardizes names: removes accents, uppercases, keeps A-Z only."""
    if not s:
        return ""
    s = str(s).strip()
    s = unidecode(s)
    s = s.upper()
    s = "".join(ch for ch in s if "A" <= ch <= "Z")
    return s

# =========================================================
#            PART 2: IBGE DATA (Cached)
# =========================================================

@st.cache_data(ttl=86400, show_spinner="Downloading IBGE Census Data...")
def fetch_ibge_data() -> Tuple[Set[str], Set[str]]:
    """
    Fetches top 2000 First Names and Surnames from IBGE API.
    Cached for 24 hours.
    """
    IBGE_FIRST_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"
    TARGET = 2000
    MAX_PAGES = 50

    def _fetch_paginated(base_url):
        names_set = set()
        page = 1
        while len(names_set) < TARGET and page <= MAX_PAGES:
            try:
                # Tiny delay to be polite to IBGE servers
                time.sleep(0.05)
                resp = requests.get(base_url, params={"page": page}, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                
                if not items: break
                
                for item in items:
                    raw = item.get("nome")
                    norm = normalize_token(raw)
                    if norm: names_set.add(norm)
                page += 1
            except Exception:
                break
        return names_set

    return _fetch_paginated(IBGE_FIRST_API), _fetch_paginated(IBGE_SURNAME_API)

# Load data immediately
try:
    brazil_first_names, brazil_surnames = fetch_ibge_data()
    st.sidebar.info(f"✅ Database Ready: {len(brazil_first_names)} names, {len(brazil_surnames)} surnames.")
except Exception as e:
    st.error(f"Failed to load IBGE data: {e}")
    st.stop()

# =========================================================
#            PART 3: GEMINI AI LOGIC
# =========================================================

def extract_names_with_ai(text_content):
    """Sends website text to Gemini 2.5 Flash-Lite."""
    if not api_key: return []
    
    # Flash-Lite handles large context easily
    truncated_text = text_content[:100000] 

    prompt = f"""
    Analyze the text below and extract every full personal name (Student names, Alumni names).
    Ignore university staff, locations, or generic text.
    Return ONLY a valid JSON list of strings. Example: ["Name One", "Name Two"]
    Do NOT use markdown code blocks. Just the raw JSON.

    TEXT TO ANALYZE:
    {truncated_text}
    """
    
    try:
        # UPDATED MODEL NAME HERE
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        
        response = model.generate_content(prompt)
        content = response.text
        # Clean up markdown if Gemini adds it
        content = content.replace("```json", "").replace("```", "").strip()
        
        return json.loads(content)
    except Exception as e:
        st.error(f"Gemini Error: {e}")
        return []

def analyze_matches(found_names_list, source_label):
    """Compares AI-found names against IBGE lists."""
    results = []
    for full_name in found_names_list:
        parts = full_name.strip().split()
        if not parts: continue
        
        first_norm = normalize_token(parts[0])
        last_norm = normalize_token(parts[-1]) if len(parts) > 1 else ""
        
        is_first_brazilian = first_norm in brazil_first_names
        is_last_brazilian = last_norm in brazil_surnames
        
        if is_first_brazilian or is_last_brazilian:
            match_type = "Weak"
            if is_first_brazilian and is_last_brazilian: match_type = "Strong"
            elif is_first_brazilian: match_type = "First Name Only"
            elif is_last_brazilian: match_type = "Surname Only"
                
            results.append({
                "Full Name": full_name,
                "Match Strength": match_type,
                "Source": source_label,
            })
    return results

# =========================================================
#            PART 4: USER INTERFACE
# =========================================================

tab1, tab2 = st.tabs(["🤖 Auto-Scraper (URL)", "📋 Manual Paste (Text)"])

# --- TAB 1: AUTOMATION ---
with tab1:
    st.markdown("Use `{page}` as a placeholder. Example: `legacy.cs.stanford.edu/directory?page={page}`")
    url_template = st.text_input("URL Template:")
    
    c1, c2 = st.columns(2)
    start_p = c1.number_input("Start Page", 1, value=1)
    end_p = c2.number_input("End Page", 1, value=1)
    
    if st.button("Start Auto-Scrape"):
        if not api_key:
            st.error("Please add your API Key in the sidebar or Secrets.")
            st.stop()
            
        all_matches = []
        progress_bar = st.progress(0)
        status = st.empty()
        
        total_pages = end_p - start_p + 1
        
        for i, p_num in enumerate(range(start_p, end_p + 1)):
            target = url_template.replace("{page}", str(p_num)) if "{page}" in url_template else url_template
            status.text(f"Scanning: {target}")
            
            try:
                # 1. Fetch Clean Text with Jina Reader
                jina_url = f"https://r.jina.ai/{target}"
                resp = requests.get(jina_url)
                
                # 2. Extract with Gemini
                names = extract_names_with_ai(resp.text)
                
                # 3. Match
                matches = analyze_matches(names, f"Page {p_num}")
                all_matches.extend(matches)
                
            except Exception as e:
                st.warning(f"Error on page {p_num}: {e}")
                
            progress_bar.progress((i + 1) / total_pages)
            # Polite pause
            time.sleep(1)
            
        status.text("Done!")
        
        if all_matches:
            df = pd.DataFrame(all_matches)
            st.success(f"Found {len(df)} matches!")
            st.dataframe(df)
            st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "auto_matches.csv")
        else:
            st.warning("No matches found.")

# --- TAB 2: MANUAL PASTE ---
with tab2:
    st.info("Copy content from any website (Ctrl+A -> Ctrl+C) and paste it here.")
    raw_paste = st.text_area("Paste Content:", height=300)
    
    if st.button("Analyze Text"):
        if not api_key:
            st.error("Please add your API Key.")
            st.stop()
            
        if raw_paste:
            with st.spinner("Gemini is reading names..."):
                names = extract_names_with_ai(raw_paste)
                matches = analyze_matches(names, "Manual Paste")
                
                if matches:
                    df = pd.DataFrame(matches)
                    st.success(f"Found {len(df)} matches!")
                    st.dataframe(df)
                    st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "manual_matches.csv")
                else:
                    st.warning("No matches found.")
