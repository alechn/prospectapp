import streamlit as st
import requests
import pandas as pd
import json
import google.generativeai as genai
import time
from unidecode import unidecode
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from typing import Set, Tuple, List

# --- CONFIGURATION ---
st.set_page_config(page_title="Universal Alumni Finder", layout="wide")
st.title("Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash-Lite • Robust Session Agent")

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

def clean_html_for_ai(html_text):
    """
    Preserves HTML structure (forms, inputs, links) but removes noise.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup(["script", "style", "head", "svg", "footer", "iframe", "noscript", "img"]):
        element.decompose()
    return str(soup)[:100000] # Cap at 100k chars

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
            except: break
        return names_set
    return _fetch_paginated(IBGE_FIRST_API), _fetch_paginated(IBGE_SURNAME_API)

try:
    brazil_first_names, brazil_surnames = fetch_ibge_data()
    st.sidebar.info(f"✅ IBGE Database Ready: {len(brazil_first_names)} names, {len(brazil_surnames)} surnames.")
except Exception as e:
    st.error(f"Failed to load IBGE data: {e}")
    st.stop()

# =========================================================
#            PART 3: THE UNIVERSAL AGENT (With Retry & JSON Mode)
# =========================================================

def agent_analyze_page(html_content, current_url):
    """
    Asks Gemini to find names AND navigation.
    Uses 'response_mime_type' to enforce valid JSON.
    """
    if not api_key: return None
    
    prompt = f"""
    You are an intelligent crawling agent.
    
    TASK 1: Extract all personal names (Alumni/Students). Return as list of strings.
    
    TASK 2: Analyze the HTML structure to find the "Next Page" mechanism.
    - LOOK CLOSELY at <form> tags. Does the "Next" button submit a form?
    - If it's a form, extract the HIDDEN INPUTS needed to submit it.
    - If it's a link <a>, extract the href.
    
    Current URL: {current_url}
    
    You must return a JSON object with this schema:
    {{
      "names": ["Name 1", "Name 2"],
      "navigation": {{
         "type": "LINK" or "FORM" or "NONE",
         "url": "next_url_here", 
         "form_data": {{ "key": "value" }}
      }}
    }}
    
    HTML CODE:
    {clean_html_for_ai(html_content)}
    """
    
    # RETRY LOGIC (Try 3 times if AI fails)
    for attempt in range(3):
        try:
            model = genai.GenerativeModel(
                'gemini-2.5-flash-lite',
                # THIS IS THE FIX: FORCE JSON OUTPUT
                generation_config={"response_mime_type": "application/json"}
            )
            
            response = model.generate_content(prompt)
            return json.loads(response.text)
            
        except Exception as e:
            if attempt == 2: # On last attempt, fail
                # print(f"Failed after 3 attempts: {e}")
                return None
            time.sleep(1) # Wait 1 sec before retry
    return None

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
#            PART 4: INTERFACE
# =========================================================

st.markdown("### Auto-Pilot")
st.write("Enter the first URL. The AI will navigate automatically.")

start_url = st.text_input("Directory URL:", placeholder="https://legacy.cs.stanford.edu/directory/undergraduate-alumni")
max_pages = st.number_input("Max Pages Limit", min_value=1, value=100)

if st.button("Start Scraping", type="primary"):
    if not api_key:
        st.error("Please add your API Key.")
        st.stop()
        
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    all_matches = []
    current_url = start_url
    
    next_method = "GET" 
    next_data = None 
    
    status_box = st.status("Starting Agent...", expanded=True)
    progress_bar = st.progress(0)
    
    visited_fingerprints = set() 
    
    for page_num in range(1, max_pages + 1):
        status_box.update(label=f"Scanning Page {page_num}...", state="running")
        status_box.write(f"**Requesting:** {current_url} ({next_method})")
        
        try:
            # 1. EXECUTE REQUEST
            if next_method == "GET":
                resp = session.get(current_url, timeout=30)
            else: # POST
                resp = session.post(current_url, data=next_data, timeout=30)
            
            if resp.status_code != 200:
                status_box.error(f"❌ Error: Status Code {resp.status_code}")
                break

            # 2. AI ANALYSIS (Now with Retries)
            data = agent_analyze_page(resp.text, current_url)
            
            if not data:
                status_box.warning(f"⚠️ AI could not read page {page_num}. Stopping.")
                # Optional: break or continue? Usually break if we lose navigation.
                break
                
            # 3. PROCESS NAMES
            names = data.get("names", [])
            matches = analyze_matches(names, f"Page {page_num}")
            if matches:
                all_matches.extend(matches)
                status_box.write(f"✅ Found {len(matches)} matches.")
            else:
                status_box.write(f"🤷 0 matches found.")

            # 4. DECIDE NEXT STEP
            nav = data.get("navigation", {})
            nav_type = nav.get("type", "NONE")
            
            if nav_type == "LINK":
                raw_link = nav.get("url")
                if raw_link:
                    current_url = urljoin(current_url, raw_link)
                    next_method = "GET"
                    next_data = None
                    status_box.write(f"🔗 Link found: {raw_link}")
                else:
                    status_box.write("🛑 AI found Link type but no URL.")
                    break
                    
            elif nav_type == "FORM":
                form_data = nav.get("form_data", {})
                if form_data:
                    next_method = "POST"
                    next_data = form_data
                    status_box.write(f"📝 Form detected. Posting: {form_data}")
                    
                    fingerprint = str(form_data)
                    if fingerprint in visited_fingerprints:
                        status_box.warning("⚠️ Loop detected (same form token). Finishing.")
                        break
                    visited_fingerprints.add(fingerprint)
                else:
                    status_box.write("🛑 AI found Form but no data.")
                    break
                    
            else:
                status_box.write("🏁 No next page detected. Job done.")
                break
                
        except Exception as e:
            status_box.error(f"Critical Error: {e}")
            break
            
        progress_bar.progress(page_num / max_pages)
        time.sleep(1) 

    status_box.update(label="Complete!", state="complete", expanded=False)
    
    if all_matches:
        st.balloons()
        df = pd.DataFrame(all_matches)
        st.success(f"🎉 Found {len(df)} total matches!")
        st.dataframe(df)
        st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "brazilian_alumni.csv")
    else:
        st.warning("No matches found.")
