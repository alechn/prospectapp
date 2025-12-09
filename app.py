import streamlit as st
import requests
import pandas as pd
import json
import google.generativeai as genai
import time
from unidecode import unidecode
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from typing import Set, Tuple

# --- CONFIGURATION ---
st.set_page_config(page_title="Universal Alumni Finder", layout="wide")
st.title("🇧🇷 Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash-Lite • Robust Crawler")

# =========================================================
#            PART 0: API KEY SETUP
# =========================================================
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
    st.sidebar.success("🔑 API Key loaded securely")
else:
    api_key = st.sidebar.text_input("Google Gemini API Key", type="password")
    st.sidebar.markdown("[Get a free Gemini Key here](https://aistudio.google.com/app/apikey)")

if api_key:
    genai.configure(api_key=api_key)

# =========================================================
#            PART 1: HELPER FUNCTIONS
# =========================================================

def normalize_token(s: str) -> str:
    if not s: return ""
    s = str(s).strip()
    s = unidecode(s)
    s = s.upper()
    s = "".join(ch for ch in s if "A" <= ch <= "Z")
    return s

def get_page_content(url):
    """
    Tries to get clean text. 
    1. Tries Jina Reader.
    2. If that fails, falls back to raw HTML extraction.
    """
    # Mimic a real browser to avoid blocks
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # Method A: Jina Reader (Best for cleaning)
    try:
        jina_url = f"https://r.jina.ai/{url}"
        resp = requests.get(jina_url, headers=headers, timeout=15)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text, "Jina Reader"
    except:
        pass

    # Method B: Direct HTML (Fallback)
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # Kill scripts and styles
            for script in soup(["script", "style"]):
                script.decompose()
            return soup.get_text(separator="\n"), "Raw HTML"
    except Exception as e:
        return None, str(e)
    
    return None, "Failed to load"

# =========================================================
#            PART 2: IBGE DATA (Cached)
# =========================================================

@st.cache_data(ttl=86400, show_spinner="Downloading IBGE Census Data...")
def fetch_ibge_data() -> Tuple[Set[str], Set[str]]:
    IBGE_FIRST_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"
    TARGET = 2000
    MAX_PAGES = 50

    def _fetch_paginated(base_url):
        names_set = set()
        page = 1
        while len(names_set) < TARGET and page <= MAX_PAGES:
            try:
                time.sleep(0.05)
                resp = requests.get(base_url, params={"page": page}, timeout=10)
                items = resp.json().get("items", [])
                if not items: break
                for item in items:
                    norm = normalize_token(item.get("nome"))
                    if norm: names_set.add(norm)
                page += 1
            except:
                break
        return names_set

    return _fetch_paginated(IBGE_FIRST_API), _fetch_paginated(IBGE_SURNAME_API)

try:
    brazil_first_names, brazil_surnames = fetch_ibge_data()
    st.sidebar.info(f"✅ Database Ready: {len(brazil_first_names)} names, {len(brazil_surnames)} surnames.")
except Exception as e:
    st.error(f"Failed to load IBGE data: {e}")
    st.stop()

# =========================================================
#            PART 3: AGENTIC AI LOGIC
# =========================================================

def analyze_page_with_agent(text_content, current_url):
    if not api_key: return {"names": [], "next_url": None}
    
    truncated_text = text_content[:100000] 

    prompt = f"""
    You are a scraping agent.
    1. Extract a list of all personal names (Students, Alumni). Ignore generic terms like "University" or "Department".
    2. Find the "Next Page" link URL. Look for buttons like "Next", "Next >", "2", "Older".

    Current URL: {current_url}

    Return ONLY raw JSON:
    {{
      "names": ["Name 1", "Name 2"],
      "next_url": "/directory?page=2" 
    }}
    
    If no next link is found, set "next_url": null.

    TEXT TO ANALYZE:
    {truncated_text}
    """
    
    try:
        model = genai.GenerativeModel('gemini-2.5-flash-lite') 
        response = model.generate_content(prompt)
        content = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(content)
    except Exception as e:
        return {"names": [], "next_url": None}

def analyze_matches(found_names_list, source_label):
    results = []
    for full_name in found_names_list:
        parts = full_name.strip().split()
        if not parts: continue
        
        first_norm = normalize_token(parts[0])
        last_norm = normalize_token(parts[-1]) if len(parts) > 1 else ""
        
        is_first = first_norm in brazil_first_names
        is_last = last_norm in brazil_surnames
        
        if is_first or is_last:
            match_type = "Weak"
            if is_first and is_last: match_type = "Strong"
            elif is_first: match_type = "First Name Only"
            elif is_last: match_type = "Surname Only"
                
            results.append({
                "Full Name": full_name,
                "Match Strength": match_type,
                "Source": source_label,
            })
    return results

# =========================================================
#            PART 4: USER INTERFACE
# =========================================================

tab1, tab2 = st.tabs(["🕷️ Smart Auto-Crawler", "📋 Manual Paste"])

with tab1:
    st.markdown("### The \"One Size Fits All\" Crawler")
    start_url = st.text_input("Starting URL:", placeholder="https://...")
    max_pages = st.slider("Max Pages", 1, 50, 5)
    
    # DEBUG TOGGLE
    show_debug = st.checkbox("Show Debug Info (See what the AI reads)")
    
    if st.button("Start Crawling"):
        if not api_key:
            st.error("Please add your API Key.")
            st.stop()
            
        all_matches = []
        visited_urls = set()
        current_url = start_url
        page_count = 0
        
        status_box = st.status("🕷️ Starting Agent...", expanded=True)
        progress_bar = st.progress(0)
        
        while current_url and page_count < max_pages:
            if current_url in visited_urls:
                status_box.write(f"⚠️ Loop detected. Stopping.")
                break
            visited_urls.add(current_url)
            page_count += 1
            
            status_box.update(label=f"Scanning Page {page_count}...", state="running")
            status_box.write(f"**Page {page_count}:** `{current_url}`")
            
            # 1. Get Content (Robust Method)
            text_content, method_used = get_page_content(current_url)
            
            if not text_content:
                status_box.error(f"❌ Failed to read page. Blocked or empty.")
                break
            
            status_box.write(f"📖 Read {len(text_content)} chars using {method_used}")
            
            # Show debug text if enabled
            if show_debug:
                with st.expander(f"See Text for Page {page_count}"):
                    st.text(text_content[:2000] + "...")

            # 2. AI Analysis
            try:
                data = analyze_page_with_agent(text_content, current_url)
                
                # Check results
                names = data.get("names", [])
                if not names:
                    status_box.warning("⚠️ AI found 0 names. (Check 'Show Debug' to see if text is valid)")
                
                matches = analyze_matches(names, f"Page {page_count}")
                
                if matches:
                    status_box.success(f"✅ Found {len(matches)} potential Brazilians!")
                    all_matches.extend(matches)
                else:
                    if names:
                        status_box.info(f"Found {len(names)} names, but none were Brazilian.")
                
                # 3. Next Page Logic
                next_raw = data.get("next_url")
                if next_raw:
                    current_url = urljoin(current_url, next_raw)
                    status_box.write(f"🔗 Following link: `{next_raw}`")
                else:
                    status_box.write("🛑 No 'Next' link found. Stopping.")
                    current_url = None
                    
            except Exception as e:
                status_box.error(f"Error during analysis: {e}")
                break
            
            progress_bar.progress(page_count / max_pages)
            time.sleep(1) 
            
        status_box.update(label="Crawling Complete!", state="complete", expanded=False)
        
        if all_matches:
            st.success(f"🎉 Found {len(all_matches)} matches!")
            df = pd.DataFrame(all_matches)
            st.dataframe(df)
            st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "matches.csv")
        else:
            st.warning("No matches found.")

with tab2:
    st.info("Backup: Copy/Paste full text here if the crawler gets blocked.")
    raw_paste = st.text_area("Paste Content:", height=300)
    
    if st.button("Analyze Text"):
        if raw_paste and api_key:
            with st.spinner("Analyzing..."):
                data = analyze_page_with_agent(raw_paste, "Manual Paste")
                matches = analyze_matches(data.get("names", []), "Manual Paste")
                
                if matches:
                    df = pd.DataFrame(matches)
                    st.success(f"Found {len(df)} matches!")
                    st.dataframe(df)
                    st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "manual_matches.csv")
                else:
                    st.warning("No matches found.")
