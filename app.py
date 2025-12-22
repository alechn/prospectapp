import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import random
from unidecode import unidecode
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup

# --- STEALTH REQUESTS SETUP ---
try:
    from curl_cffi import requests as crequests
    HAS_CURL = True
except ImportError:
    HAS_CURL = False

# --- SELENIUM SETUP ---
try:
    import undetected_chromedriver as uc
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

# =========================================================
#             PART 0: CONFIGURATION & SESSION
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder v7", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder (Production)")
st.caption("Hybrid Engine • Stealth Mode • Non-Destructive AI Verification")

# --- SESSION STATE (Persistence) ---
if "matches" not in st.session_state:
    st.session_state.matches = []
if "visited_urls" not in st.session_state:
    st.session_state.visited_urls = set()
if "learned_selectors" not in st.session_state:
    st.session_state.learned_selectors = {}

# =========================================================
#             PART 1: SETTINGS & TOOLS
# =========================================================
with st.sidebar:
    st.header("🧠 Brain & Credentials")
    ai_provider = st.selectbox(
        "AI Model (Fallback & Verify)",
        ["Google Gemini (Flash 2.0)", "OpenAI (GPT-4o)", "Anthropic (Claude 3.5)", "DeepSeek (V3)"]
    )
    api_key = st.text_input("API Key", type="password")
    
    st.divider()
    
    st.header("💾 Selector Memory")
    # Load previously learned selectors to save money
    uploaded_selectors = st.file_uploader("Load Selectors (JSON)", type="json")
    if uploaded_selectors:
        st.session_state.learned_selectors = json.load(uploaded_selectors)
        st.success("Selectors Loaded!")
    
    # Download button appears if we have learned something
    if st.session_state.learned_selectors:
        st.download_button(
            "💾 Save Current Selectors",
            data=json.dumps(st.session_state.learned_selectors, indent=2),
            file_name="site_selectors.json",
            mime="application/json"
        )

    st.divider()
    
    st.header("⚙️ Scraper Logic")
    search_delay = st.slider("Wait Time (Sec)", 1.0, 10.0, 3.0)
    strict_ibge = st.checkbox("Strict IBGE Filtering", value=True, help="Only keep names found in the Brazilian Census DB.")
    verify_mode = st.checkbox("AI Verify (Add Observations)", value=False, help="Uses AI to comment on validity. DOES NOT DELETE ROWS.")

# --- DATA: BLOCKLIST & IBGE ---
BLOCKLIST_SURNAMES = {
    "WANG", "LI", "ZHANG", "LIU", "CHEN", "YANG", "HUANG", "ZHAO", "WU", "ZHOU",
    "XU", "SUN", "MA", "ZHU", "HU", "GUO", "HE", "GAO", "LIN", "LUO",
    "LIANG", "SONG", "TANG", "ZHENG", "HAN", "FENG", "DONG", "YE", "YU", "WEI",
    "CAI", "YUAN", "PAN", "DU", "DAI", "JIN", "FAN", "SU", "MAN", "WONG",
    "RESULTS", "WEBSITE", "SEARCH", "MENU", "SKIP", "CONTENT", "FOOTER", "HEADER",
    "OVERVIEW", "PROJECTS", "PEOPLE", "PROFILE", "VIEW", "CONTACT", "SPOTLIGHT"
}

@st.cache_data(ttl=86400)
def fetch_ibge_data():
    # Load a decent chunk of names to be safe
    url_first = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    url_last = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"
    
    def _get(u, limit=3000):
        s = {}
        p = 1
        while len(s) < limit:
            try:
                r = requests.get(u, params={"page": p}, timeout=5)
                if r.status_code != 200: break
                items = r.json().get("items", [])
                if not items: break
                for i in items:
                    n = unidecode(i["nome"]).upper().strip()
                    s[n] = i.get("rank", 0)
                p += 1
            except: break
        return s
    return _get(url_first), _get(url_last)

try:
    first_ranks, surname_ranks = fetch_ibge_data()
    st.sidebar.success(f"✅ DB: {len(first_ranks)} Firsts / {len(surname_ranks)} Surnames")
except:
    st.error("IBGE Failed. Running without filters.")
    first_ranks, surname_ranks = {}, {}

# =========================================================
#             PART 2: INTELLIGENCE FUNCTIONS
# =========================================================

def clean_json_response(text: str) -> str:
    # Extracts JSON from markdown or raw text
    text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else "{}"

def call_ai_api(prompt: str, provider: str, key: str):
    """Universal AI Caller"""
    if not key: return None
    headers = {"Content-Type": "application/json"}
    
    try:
        if "Gemini" in provider:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            
        elif "OpenAI" in provider:
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {
                "model": "gpt-4o",
                "messages": [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            return resp.json()["choices"][0]["message"]["content"]
            
    except Exception as e:
        return f'{{"error": "{str(e)}"}}'
    return None

def agent_learn_selectors(html, url, provider, key):
    """Uses AI to learn the page structure ONCE."""
    prompt = f"""
    Analyze HTML from {url}. Find CSS Selectors.
    1. 'name_element': Selector for person names.
    2. 'next_element': Selector for 'Next Page' button/link.
    Return JSON: {{ "selectors": {{ "name_element": "...", "next_element": "..." }} }}
    HTML Snippet: {html[:60000]}
    """
    resp = call_ai_api(prompt, provider, key)
    if resp:
        data = json.loads(clean_json_response(resp))
        return data.get("selectors")
    return None

def batch_verify_names_nondestructive(df, provider, key):
    """
    Adds an 'AI_Observation' column. Does NOT delete rows.
    """
    if df.empty or not key: return df
    
    names = df["Full Name"].unique().tolist()
    observations = {}
    
    # Process in chunks of 20
    chunk_size = 20
    my_bar = st.progress(0)
    
    for i in range(0, len(names), chunk_size):
        chunk = names[i:i+chunk_size]
        prompt = f"""
        Analyze these names. Are they likely real people (not headers/junk) and could they be Brazilian/Portuguese?
        Return JSON object where keys are the names and values are SHORT observations (e.g., "Valid", "Company Name", "Likely Spanish").
        Input: {json.dumps(chunk)}
        """
        resp = call_ai_api(prompt, provider, key)
        if resp:
            try:
                data = json.loads(clean_json_response(resp))
                # Flatten or normalize keys
                for k, v in data.items():
                    observations[k] = v
            except: pass
        my_bar.progress(min((i+chunk_size)/len(names), 1.0))
        
    df["AI_Observation"] = df["Full Name"].map(observations).fillna("Pending/Error")
    return df

# =========================================================
#             PART 3: FETCHERS (STEALTH)
# =========================================================

def fetch_stealth(url):
    """Uses curl_cffi to mimic Chrome 110 fingerprint"""
    if HAS_CURL:
        try:
            return crequests.get(url, impersonate="chrome110", timeout=20)
        except Exception as e:
            st.error(f"Curl Error: {e}")
            return None
    else:
        # Fallback to standard requests
        return requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)

def get_selenium_driver():
    """Uses Undetected Chromedriver if available"""
    options = Options()
    # options.add_argument("--headless=new") # Comment out to see browser for debugging
    options.add_argument("--no-sandbox")
    
    if HAS_SELENIUM:
        try:
            # Try undetected-chromedriver first (Best Stealth)
            return uc.Chrome(options=options)
        except:
            # Fallback to standard
            return webdriver.Chrome(
                service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
                options=options
            )
    return None

# =========================================================
#             PART 4: MAIN EXECUTION FLOW
# =========================================================

st.subheader("🚀 Mission Control")
col1, col2 = st.columns([3, 1])
start_url = col1.text_input("Target URL", "https://legacy.cs.stanford.edu/directory/undergraduate-alumni")
max_pages = col2.number_input("Pages", 1, 500, 5)

if st.button("Start / Resume", type="primary"):
    
    # Init scraping variables
    current_url = start_url
    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()
    
    # Display existing matches if resuming
    if st.session_state.matches:
        table_placeholder.dataframe(pd.DataFrame(st.session_state.matches))

    # --- MAIN LOOP ---
    for page in range(1, max_pages + 1):
        if current_url in st.session_state.visited_urls:
            status_log.warning("🔄 Loop detected or already visited.")
            break
        st.session_state.visited_urls.add(current_url)
        
        status_log.update(label=f"Scanning Page {page}: {current_url}", state="running")
        
        # 1. FETCH HTML
        raw_html = ""
        resp = fetch_stealth(current_url)
        if resp and resp.status_code == 200:
            raw_html = resp.text
        else:
            status_log.error(f"Failed to fetch {current_url}")
            break
            
        # 2. EXTRACT NAMES (Heuristic First)
        soup = BeautifulSoup(raw_html, "html.parser")
        extracted_names = []
        
        # A. Try Learned Selectors
        if st.session_state.learned_selectors.get("name_element"):
            sel = st.session_state.learned_selectors["name_element"]
            extracted_names = [el.get_text(" ", strip=True) for el in soup.select(sel)]
            status_log.write(f"⚡ Used saved selector: {sel}")
        
        # B. Fallback to Heuristics (if A failed)
        if not extracted_names:
            # Simple heuristic: Look for list items or table cells
            # This is a simplified heuristic for demo; the previous versions had more complex ones
            for tag in soup.select("li, td, h3, h4"):
                txt = tag.get_text(strip=True)
                if 5 < len(txt) < 40 and " " in txt: # Basic name validation
                    extracted_names.append(txt)
            status_log.write("🧩 Used Heuristics (No selector found)")

        # C. AI Learning (Last Resort - only if enabled)
        if not extracted_names and api_key and not st.session_state.learned_selectors:
            status_log.info("🧠 Learning page structure with AI...")
            new_selectors = agent_learn_selectors(raw_html, current_url, ai_provider, api_key)
            if new_selectors:
                st.session_state.learned_selectors = new_selectors
                # Retry extraction immediately
                sel = new_selectors.get("name_element")
                if sel:
                    extracted_names = [el.get_text(" ", strip=True) for el in soup.select(sel)]
                    status_log.success(f"🎓 Learned! Names: {sel}")

        # 3. FILTER & SCORE (IBGE)
        page_matches = []
        for name in set(extracted_names):
            # Clean
            clean_name = unidecode(name).upper().strip()
            parts = clean_name.split()
            if len(parts) < 2: continue
            
            f, l = parts[0], parts[-1]
            if l in BLOCKLIST_SURNAMES: continue
            
            # Score
            score = 0
            rank_f = first_ranks.get(f, 0)
            rank_l = surname_ranks.get(l, 0)
            
            if rank_f > 0: score += 50
            if rank_l > 0: score += 50
            
            # Logic: If strict, require at least one match. If not strict, keep everything.
            if strict_ibge and score == 0:
                continue
            
            page_matches.append({
                "Full Name": name,
                "Brazil Score": score,
                "Source": current_url,
                "AI_Observation": "Not Run" # Placeholder
            })
            
        if page_matches:
            st.session_state.matches.extend(page_matches)
            status_log.write(f"✅ Found {len(page_matches)} candidates.")
            table_placeholder.dataframe(pd.DataFrame(st.session_state.matches))
        
        # 4. NAVIGATION (Next Page)
        next_url = None
        # Try Selector
        if st.session_state.learned_selectors.get("next_element"):
            nxt = soup.select_one(st.session_state.learned_selectors["next_element"])
            if nxt and nxt.get("href"):
                next_url = urljoin(current_url, nxt.get("href"))
        
        # Try Heuristic
        if not next_url:
            # Find links with "Next" or "›"
            for a in soup.select("a[href]"):
                if any(x in a.get_text(strip=True).lower() for x in ["next", "prox", "›", "»"]):
                    next_url = urljoin(current_url, a.get("href"))
                    break
        
        if next_url:
            current_url = next_url
            time.sleep(search_delay)
        else:
            status_log.success("🏁 No next page found. Scraping done.")
            break

    status_log.update(label="Scraping Phase Complete", state="complete")

# =========================================================
#             PART 5: VERIFICATION & EXPORT
# =========================================================

if st.session_state.matches:
    st.divider()
    st.markdown("### 🔍 Verification & Export")
    
    df = pd.DataFrame(st.session_state.matches)
    
    c1, c2 = st.columns(2)
    
    # BUTTON: Run AI Verification
    if c1.button("🤖 Run AI Verification (Add Observations)"):
        if not api_key:
            st.error("Need API Key for verification.")
        else:
            with st.spinner("Asking AI to analyze names... (Rows will NOT be deleted)"):
                df_verified = batch_verify_names_nondestructive(df, ai_provider, api_key)
                st.session_state.matches = df_verified.to_dict("records") # Update Session
                st.success("Verification Complete!")
                st.rerun()

    # EXPORT
    c2.download_button(
        "📥 Download Excel",
        data=io.BytesIO(b""), # Placeholder, real logic needs xlsxwriter
        disabled=True, 
        help="Use CSV below if xlsxwriter is missing"
    )
    
    csv = df.to_csv(index=False).encode('utf-8')
    c2.download_button(
        "📥 Download CSV",
        data=csv,
        file_name="brazilian_alumni_verified.csv",
        mime="text/csv"
    )

    st.dataframe(df, use_container_width=True)
