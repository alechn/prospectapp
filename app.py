import streamlit as st
import requests
import pandas as pd
import json
import google.generativeai as genai
import time
import re
from unidecode import unidecode
from urllib.parse import urljoin

# =========================================================
#             PART 0: CONFIGURATION & SETUP
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide")
st.title("Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash • Jina Reader (Universal Mode)")

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

def clean_json_response(text):
    """
    Uses Regex to find the first valid JSON object in the text.
    """
    try:
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
def fetch_ibge_data(limit_first: int, limit_surname: int):
    IBGE_FIRST_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME_API = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"
    
    # Safety cap to prevent browser freezing if user types 1,000,000
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
                    if len(names_set) >= target_limit:
                        break
                page += 1
            except: 
                break
        return names_set

    return _fetch_paginated(IBGE_FIRST_API, limit_first), _fetch_paginated(IBGE_SURNAME_API, limit_surname)

# --- SIDEBAR: DB SETTINGS ---
st.sidebar.header("⚙️ Scraper Settings")

st.sidebar.subheader("First Name Sensitivity")
# CHANGED: Number Input allows precise typing
limit_first = st.sidebar.number_input(
    "Common First Names (Count)", 
    min_value=10, 
    max_value=10000, 
    value=2000, 
    step=100,
    help="How many top Brazilian first names to load. (e.g. 100 = Only 'João', 'Maria'...)"
)

st.sidebar.subheader("Surname Sensitivity")
# CHANGED: Number Input allows precise typing
limit_surname = st.sidebar.number_input(
    "Common Surnames (Count)", 
    min_value=10, 
    max_value=10000, 
    value=2000, 
    step=100,
    help="How many top Brazilian surnames to load. Higher = catches rarer family names."
)

try:
    brazil_first_names, brazil_surnames = fetch_ibge_data(limit_first, limit_surname)
    st.sidebar.success(f"✅ DB Loaded: {len(brazil_first_names)} Firsts / {len(brazil_surnames)} Surnames")
except Exception as e:
    st.error(f"Failed to load IBGE data: {e}")
    st.stop()

# =========================================================
#             PART 3: THE UNIVERSAL AGENT (Jina Enhanced)
# =========================================================

def agent_analyze_page(markdown_content, current_url):
    if not api_key: return None
    
    # Prompt updated for Markdown analysis
    prompt = f"""
    You are a data extraction system.
    I have converted a website into Markdown text below.
    
    Source URL: {current_url}
    
    TASK 1: Extract list of names (people/alumni).
    TASK 2: Find the "Next Page" link. Look for links like [Next](url) or [2](url).
    
    Return ONLY a JSON object with this schema:
    {{
      "names": ["Name 1", "Name 2"],
      "navigation": {{
         "type": "LINK" or "NONE",
         "url": "full_next_url_if_found"
      }}
    }}
    
    MARKDOWN CONTENT:
    {markdown_content[:60000]} 
    """
    
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    for attempt in range(3):
        try:
            model = genai.GenerativeModel(
                'gemini-2.5-flash', 
                generation_config={"response_mime_type": "application/json"}
            )
            
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

st.markdown("### Auto-Pilot (Universal Mode)")
st.write("Enter the URL. The AI will use a proxy to read JavaScript pages (like YC).")

start_url = st.text_input("Directory URL:", placeholder="https://www.ycombinator.com/companies")
max_pages = st.number_input("Max Pages Limit", min_value=1, value=5)

if st.button("Start Scraping", type="primary"):
    if not api_key:
        st.error("Please add your API Key.")
        st.stop()
        
    session = requests.Session()
    # Spoofing headers
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    all_matches = []
    current_url = start_url
    
    status_box = st.status("Starting Universal Agent...", expanded=True)
    progress_bar = st.progress(0)
    
    for page_num in range(1, max_pages + 1):
        status_box.update(label=f"Scanning Page {page_num}...", state="running")
        status_box.write(f"**Target:** {current_url}")
        
        try:
            # --- THE MAGIC TRICK (Jina Proxy) ---
            jina_url = f"https://r.jina.ai/{current_url}"
            status_box.write(f"**Proxying via Jina:** {jina_url}")
            
            resp = session.get(jina_url, timeout=45)
            
            if resp.status_code != 200:
                status_box.error(f"❌ Error: Jina Proxy returned {resp.status_code}")
                break

            # AI Analysis on Markdown
            data = agent_analyze_page(resp.text, current_url)
            
            if not data:
                status_box.warning(f"⚠️ AI could not read page {page_num}. Content length: {len(resp.text)}")
                break
                
            # Process Names
            names = data.get("names", [])
            matches = analyze_matches(names, f"Page {page_num}")
            if matches:
                all_matches.extend(matches)
                status_box.write(f"✅ Found {len(matches)} matches.")
            else:
                status_box.write(f"🤷 0 matches found.")

            # Navigation
            nav = data.get("navigation", {})
            raw_link = nav.get("url")
            
            if raw_link and nav.get("type") == "LINK":
                # Clean up Jina prefix if it leaks into the link
                if "r.jina.ai" in raw_link:
                    raw_link = raw_link.replace("https://r.jina.ai/", "")
                
                # Handle relative links
                if not raw_link.startswith("http"):
                    current_url = urljoin(current_url, raw_link)
                else:
                    current_url = raw_link
                    
                status_box.write(f"🔗 Next Page: {current_url}")
            else:
                status_box.write("🏁 No next page detected. Job done.")
                break
                
        except Exception as e:
            status_box.error(f"Critical Error: {e}")
            break
            
        progress_bar.progress(page_num / max_pages)
        # 4-second delay for API safety
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
