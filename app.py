import streamlit as st
import requests
import pandas as pd
import json
import google.generativeai as genai
import time
from unidecode import unidecode
from urllib.parse import urljoin
from typing import Set, Tuple

# --- CONFIGURATION ---
st.set_page_config(page_title="Universal Alumni Finder", layout="wide")
st.title("🇧🇷 Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash-Lite • Auto-Crawling Agent")

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
    """Standardizes names: removes accents, uppercases, keeps A-Z only."""
    if not s: return ""
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
                resp.raise_for_status()
                items = resp.json().get("items", [])
                if not items: break
                for item in items:
                    norm = normalize_token(item.get("nome"))
                    if norm: names_set.add(norm)
                page += 1
            except Exception:
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
    """
    Asks Gemini to find names AND the 'Next Page' link.
    """
    if not api_key: return {"names": [], "next_url": None}
    
    truncated_text = text_content[:100000] 

    # We ask for a JSON object containing both names and the navigation link
    prompt = f"""
    You are a scraping agent. Your job is to read the text from a website and extract two things:
    1. A list of all personal names (Students, Alumni). Ignore generic terms.
    2. The URL for the "Next Page" or "Load More" button.
       - Look for links labeled "Next", "Next >", "Older Entries", "Page 2", ">", etc.
       - If you find it, extract the URL.
       - If there is NO next page link, return null.

    Current Page Context: {current_url}

    Return ONLY raw JSON in this format:
    {{
      "names": ["Name 1", "Name 2"],
      "next_url": "/directory?page=2" 
    }}

    TEXT TO ANALYZE:
    {truncated_text}
    """
    
    try:
        model = genai.GenerativeModel('gemini-2.5-flash-lite') # Fast & Cheap
        response = model.generate_content(prompt)
        content = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(content)
        return data
    except Exception as e:
        # st.error(f"AI Error: {e}")
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

# --- TAB 1: SMART CRAWLER ---
with tab1:
    st.markdown("### The \"One Size Fits All\" Crawler")
    st.write("Paste the **First URL**. The AI will read it, find the 'Next' button, and follow it automatically.")
    
    start_url = st.text_input("Starting URL:", placeholder="https://legacy.cs.stanford.edu/directory/undergraduate-alumni")
    max_pages = st.slider("Max Pages to Crawl (Safety Limit)", 1, 50, 10)
    
    if st.button("Start Crawling"):
        if not api_key:
            st.error("Please add your API Key.")
            st.stop()
            
        all_matches = []
        visited_urls = set()
        current_url = start_url
        page_count = 0
        
        status_box = st.status("🕷️ Agent Status", expanded=True)
        progress_bar = st.progress(0)
        
        while current_url and page_count < max_pages:
            # 1. Avoid loops
            if current_url in visited_urls:
                status_box.write(f"⚠️ Already visited {current_url}, stopping loop.")
                break
            visited_urls.add(current_url)
            page_count += 1
            
            # 2. Update Status
            status_box.update(label=f"Scanning Page {page_count}...", state="running")
            status_box.write(f"📄 **Page {page_count}:** Reading `{current_url}`")
            
            try:
                # 3. Fetch & Analyze
                jina_url = f"https://r.jina.ai/{current_url}"
                resp = requests.get(jina_url, timeout=15)
                
                if resp.status_code != 200:
                    status_box.write(f"❌ Failed to load page (Status {resp.status_code})")
                    break

                # Ask AI for names AND next link
                data = analyze_page_with_agent(resp.text, current_url)
                
                # 4. Process Names
                names = data.get("names", [])
                matches = analyze_matches(names, f"Page {page_count}")
                if matches:
                    status_box.write(f"✅ Found {len(matches)} potential Brazilians.")
                    all_matches.extend(matches)
                else:
                    status_box.write("🤷 No matches on this page.")

                # 5. Handle "Next Page"
                next_raw = data.get("next_url")
                if next_raw:
                    # Resolve relative URLs (e.g., "?page=2" -> "site.com/list?page=2")
                    next_absolute = urljoin(current_url, next_raw)
                    status_box.write(f"🔗 Agent found next page: `{next_raw}`")
                    current_url = next_absolute
                else:
                    status_box.write("🛑 No 'Next' link found. Finishing.")
                    current_url = None
                    
            except Exception as e:
                status_box.write(f"⚠️ Error: {e}")
                break
            
            progress_bar.progress(page_count / max_pages)
            time.sleep(1) # Be polite
            
        status_box.update(label="Crawling Complete!", state="complete", expanded=False)
        
        if all_matches:
            st.success(f"🎉 Completed! Found {len(all_matches)} matches across {page_count} pages.")
            df = pd.DataFrame(all_matches)
            st.dataframe(df)
            st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "crawled_matches.csv")
        else:
            st.warning("No matches found during the crawl.")

# --- TAB 2: MANUAL PASTE ---
with tab2:
    st.info("Backup Method: If the crawler gets stuck (e.g., Infinite Scroll), copy/paste the full text here.")
    raw_paste = st.text_area("Paste Content:", height=300)
    
    if st.button("Analyze Text"):
        if raw_paste and api_key:
            with st.spinner("Analyzing..."):
                # We reuse the same function but ignore the 'next_url' output
                data = analyze_page_with_agent(raw_paste, "Manual Paste")
                matches = analyze_matches(data.get("names", []), "Manual Paste")
                
                if matches:
                    df = pd.DataFrame(matches)
                    st.success(f"Found {len(df)} matches!")
                    st.dataframe(df)
                    st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "manual_matches.csv")
                else:
                    st.warning("No matches found.")
