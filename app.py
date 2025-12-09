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
from typing import Set, Tuple, List

# =========================================================
#             PART 0: CONFIGURATION & SETUP
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide")
st.title("Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash • Robust Session Agent")

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
    Preserves HTML structure but removes noise to fit in context window.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    # Aggressive cleaning of non-visible/non-structural elements
    for element in soup(["script", "style", "head", "svg", "footer", "iframe", "noscript", "img", "meta", "link"]):
        element.decompose()
    return str(soup)[:50000] # Cap at 50k chars to save tokens

def clean_json_response(text):
    """
    Uses Regex to find the first valid JSON object in the text.
    Handles cases where AI adds markdown or chatty prefixes.
    """
    try:
        # Regex to find the outer-most curly braces
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return match.group(0)
        return text
    except:
        return text

# =========================================================
#             PART 2: DYNAMIC IBGE DATA (Cached)
# =========================================================

@st.cache_data(ttl=86400, show_spinner="Updating Brazilian Name Database...")
def fetch_ibge_data(limit_count: int) -> Tuple[Set[str], Set[str]]:
    IBGE_FIRST_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"
    
    # Use the slider limit
    TARGET = limit_count
    MAX_PAGES = 100 

    def _fetch_paginated(base_url):
        names_set = set()
        page = 1
        # Loop until we hit the user's target limit or max pages
        while len(names_set) < TARGET and page <= MAX_PAGES:
            try:
                time.sleep(0.05) # Polite delay
                resp = requests.get(base_url, params={"page": page}, timeout=10)
                
                if resp.status_code != 200: break
                
                items = resp.json().get("items", [])
                if not items: break
                
                for item in items:
                    norm = normalize_token(item.get("nome"))
                    if norm: names_set.add(norm)
                    # Optimization: Break immediately if target met
                    if len(names_set) >= TARGET:
                        break
                page += 1
            except: 
                break
        return names_set

    return _fetch_paginated(IBGE_FIRST_API), _fetch_paginated(IBGE_SURNAME_API)

# --- SIDEBAR: DB SETTINGS ---
st.sidebar.header("⚙️ Scraper Settings")
db_size = st.sidebar.slider(
    "🇧🇷 Database Scope", 
    min_value=100, 
    max_value=10000, 
    value=2000, 
    step=100,
    help="Low = Common names only (Fast). High = Includes rare names (Slower)."
)

# Load Data based on slider
try:
    brazil_first_names, brazil_surnames = fetch_ibge_data(db_size)
    st.sidebar.success(f"✅ DB Loaded: Top {len(brazil_first_names)} First Names & {len(brazil_surnames)} Surnames")
except Exception as e:
    st.error(f"Failed to load IBGE data: {e}")
    st.stop()

# =========================================================
#             PART 3: THE UNIVERSAL AGENT
# =========================================================

def agent_analyze_page(html_content, current_url):
    if not api_key: return None
    
    if len(html_content) < 500:
        st.error("HTML content too short. You might be blocked.")
        return None

    prompt = f"""
    You are a data extraction system. 
    Analyze the HTML below from: {current_url}
    
    1. Extract list of names (people/alumni).
    2. Find the "Next Page" link or form.
    
    Return ONLY a JSON object with this schema:
    {{
      "names": ["Name 1", "Name 2"],
      "navigation": {{
         "type": "LINK" or "FORM" or "NONE",
         "url": "full_next_url_or_path", 
         "form_data": {{ "key": "value" }}
      }}
    }}
    
    HTML:
    {clean_html_for_ai(html_content)}
    """
    
    # SAFETY SETTINGS: BLOCK_NONE is required for directories with names
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    for attempt in range(3):
        try:
            # Using 1.5-flash for higher rate limits (1500 req/day vs 20 req/day)
            model = genai.GenerativeModel(
                'gemini-2.5-flash',
                generation_config={"response_mime_type": "application/json"}
            )
            
            response = model.generate_content(prompt, safety_settings=safety_settings)
            
            # Check for safety blocks
            if not response.parts:
                print(f"Attempt {attempt}: AI returned empty response (Likely Safety Filter).")
                continue

            cleaned_text = clean_json_response(response.text)
            return json.loads(cleaned_text)
            
        except Exception as e:
            if attempt == 2:
                st.warning(f"⚠️ JSON Parsing Failed. Error: {e}")
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

st.markdown("### Auto-Pilot")
st.write("Enter the first URL. The AI will navigate automatically.")

start_url = st.text_input("Directory URL:", placeholder="https://legacy.cs.stanford.edu/directory/undergraduate-alumni")
max_pages = st.number_input("Max Pages Limit", min_value=1, value=100)

if st.button("Start Scraping", type="primary"):
    if not api_key:
        st.error("Please add your API Key.")
        st.stop()
        
    session = requests.Session()
    # Spoof a real browser to avoid 403 Forbidden
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.com/"
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
                status_box.code(resp.text[:500])
                break

            # 2. AI ANALYSIS
            data = agent_analyze_page(resp.text, current_url)
            
            if not data:
                status_box.warning(f"⚠️ AI could not read page {page_num}. Stopping.")
                status_box.write("Debug - Raw Content Length: " + str(len(resp.text)))
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
        # 4-second delay to stay safe within free tier limits (15 RPM)
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
