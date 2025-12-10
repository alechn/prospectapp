import streamlit as st
import requests
import pandas as pd
import json
import google.generativeai as genai
import time
import re
from unidecode import unidecode
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# =========================================================
#             PART 0: CONFIGURATION & SETUP
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide")
st.title("Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash • Hybrid Engine (Native + Jina Fallback)")

# --- SIDEBAR: API KEY ---
st.sidebar.header("🔑 Authentication")
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
    st.sidebar.success("API Key loaded securely")
else:
    api_key = st.sidebar.text_input("Google Gemini API Key", type="password")
    st.sidebar.markdown("[Get a free Gemini Key here](https://aistudio.google.com/app/apikey)")

if api_key:
    genai.configure(api_key=api_key)

# =========================================================
#             PART 1: HELPER FUNCTIONS
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
    Preserves HTML structure but removes noise.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup(["script", "style", "head", "svg", "footer", "iframe", "noscript", "img", "meta", "link"]):
        element.decompose()
    return str(soup)[:60000]

def clean_json_response(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return match.group(0)
        return text
    except:
        return text

# =========================================================
#             PART 2: DYNAMIC IBGE DATA
# =========================================================

@st.cache_data(ttl=86400, show_spinner="Updating Brazilian Name Database...")
def fetch_ibge_data(limit_first: int, limit_surname: int):
    IBGE_FIRST_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"
    MAX_PAGES = 100 

    def _fetch_paginated(base_url, target_limit):
        names_set = set()
        page = 1
        while len(names_set) < target_limit and page <= MAX_PAGES:
            try:
                time.sleep(0.05) 
                resp = requests.get(base_url, params={"page": page}, timeout=10)
                if resp.status_code != 200: break
                items = resp.json().get("items", [])
                if not items: break
                for item in items:
                    norm = normalize_token(item.get("nome"))
                    if norm: names_set.add(norm)
                    if len(names_set) >= target_limit: break
                page += 1
            except: break
        return names_set

    return _fetch_paginated(IBGE_FIRST_API, limit_first), _fetch_paginated(IBGE_SURNAME_API, limit_surname)

# --- SIDEBAR: DB SETTINGS ---
st.sidebar.header("⚙️ Scraper Settings")
st.sidebar.subheader("First Name Sensitivity")
limit_first = st.sidebar.number_input("Common First Names", 10, 10000, 50, 20)
st.sidebar.subheader("Surname Sensitivity")
limit_surname = st.sidebar.number_input("Common Surnames", 10, 10000, 1000, 20)

try:
    brazil_first_names, brazil_surnames = fetch_ibge_data(limit_first, limit_surname)
    st.sidebar.success(f"✅ DB Loaded: {len(brazil_first_names)} Firsts / {len(brazil_surnames)} Surnames")
except Exception as e:
    st.error(f"Failed to load IBGE data: {e}")
    st.stop()

# =========================================================
#             PART 3: THE UNIVERSAL AGENT (Hybrid)
# =========================================================

def agent_analyze_page(content_text, current_url, is_markdown=False):
    if not api_key: return None
    
    # Prompt adapts based on content type
    format_type = "MARKDOWN" if is_markdown else "HTML"
    
    prompt = f"""
    You are a data extraction system.
    I have provided {format_type} content below from: {current_url}
    
    TASK 1: Extract list of names (people/alumni).
    TASK 2: Find the "Next Page" mechanism.
    - If HTML: Look for <form> inputs (HIDDEN) or <a> links.
    - If Markdown: Look for [Next](url) links.
    
    Return ONLY a JSON object with this schema:
    {{
      "names": ["Name 1", "Name 2"],
      "navigation": {{
         "type": "LINK" or "FORM" or "NONE",
         "url": "full_next_url_or_path", 
         "form_data": {{ "key": "value" }}
      }}
    }}
    
    CONTENT:
    {content_text[:60000]} 
    """
    
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    for attempt in range(3):
        try:
            model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
            response = model.generate_content(prompt, safety_settings=safety_settings)
            if not response.parts: continue
            return json.loads(clean_json_response(response.text))
        except Exception:
            time.sleep(1) 
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
#             PART 4: INTERFACE & EXECUTION
# =========================================================

st.markdown("### Auto-Pilot (Hybrid Engine)")
st.write("Enter the URL. The AI attempts direct connection first, then falls back to proxy if needed.")

start_url = st.text_input("Directory URL:", placeholder="https://legacy.cs.stanford.edu/directory/undergraduate-alumni")
max_pages = st.number_input("Max Pages Limit", min_value=1, value=5)

if st.button("Start Scraping", type="primary"):
    if not api_key:
        st.error("Please add your API Key.")
        st.stop()
        
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.google.com/"
    })
    
    all_matches = []
    current_url = start_url
    
    # Navigation State
    next_method = "GET" 
    next_data = None 
    
    status_box = st.status("Starting Hybrid Agent...", expanded=True)
    progress_bar = st.progress(0)
    visited_fingerprints = set()
    
# ... (Keep previous setup code) ...
    
    for page_num in range(1, max_pages + 1):
        status_box.update(label=f"Scanning Page {page_num}...", state="running")
        status_box.write(f"**Target:** {current_url} ({next_method})")
        
        # ============================================================
        # STRATEGY: "Double Tap"
        # 1. Try Native Request.
        # 2. If it finds names -> Great.
        # 3. If 0 names -> Force Retry with Jina Proxy.
        # ============================================================
        
        # --- ATTEMPT 1: NATIVE MODE ---
        try:
            if next_method == "POST":
                resp = session.post(current_url, data=next_data, timeout=30)
            else:
                resp = session.get(current_url, timeout=30)
            
            # Initial AI Analysis (Native)
            # We treat this as a "Draft" attempt
            cleaned_html = clean_html_for_ai(resp.text)
            data = agent_analyze_page(cleaned_html, current_url, is_markdown=False)
            
            # Check results
            names = data.get("names", []) if data else []
            
            # --- DECISION POINT: RETRY? ---
            # If we found 0 names and we used GET, it's likely a JS issue.
            # Triggers "Double Tap"
            if len(names) == 0 and next_method == "GET":
                status_box.warning("⚠️ Native mode found 0 names. Retrying with Jina Proxy...")
                
                jina_url = f"https://r.jina.ai/{current_url}"
                jina_resp = session.get(jina_url, timeout=45)
                
                if jina_resp.status_code == 200:
                    # Overwrite data with Jina's findings
                    status_box.write("✅ Jina Proxy Connected. Re-analyzing...")
                    data = agent_analyze_page(jina_resp.text, current_url, is_markdown=True)
                    names = data.get("names", []) if data else []
                else:
                    status_box.error("❌ Jina Proxy also failed.")

            # --- FINAL PROCESSING ---
            if not data:
                status_box.warning(f"⚠️ AI could not read page {page_num}.")
                break
                
            matches = analyze_matches(names, f"Page {page_num}")
            if matches:
                all_matches.extend(matches)
                status_box.write(f"✅ Found {len(matches)} matches.")
            else:
                status_box.write(f"🤷 0 matches found (after retry).")

            # --- NAVIGATION LOGIC ---
            nav = data.get("navigation", {})
            nav_type = nav.get("type", "NONE")
            
            if nav_type == "LINK":
                raw_link = nav.get("url")
                if raw_link:
                    if "r.jina.ai" in raw_link:
                        raw_link = raw_link.replace("https://r.jina.ai/", "")
                    
                    if not raw_link.startswith("http"):
                        current_url = urljoin(current_url, raw_link)
                    else:
                        current_url = raw_link
                    
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
                    status_box.write(f"📝 Form detected. Posting data...")
                    
                    fingerprint = str(form_data)
                    if fingerprint in visited_fingerprints:
                        status_box.warning("⚠️ Loop detected. Finishing.")
                        break
                    visited_fingerprints.add(fingerprint)
                else:
                    break
            else:
                status_box.write("🏁 No next page detected. Job done.")
                break
                
        except Exception as e:
            status_box.error(f"Critical Error: {e}")
            break
            
        progress_bar.progress(page_num / max_pages)
        time.sleep(4)

    status_box.update(label="Complete!", state="complete", expanded=False)
    
    if all_matches:
        st.balloons()
        df = pd.DataFrame(all_matches)
        st.success(f"🎉 Found {len(df)} total matches!")
        st.dataframe(df)
        st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "brazilian_alumni.csv")
    else:
        st.warning("No matches found.")
