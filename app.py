import streamlit as st
import pandas as pd
import json
import google.generativeai as genai
import time
import re
import requests
import io
from unidecode import unidecode
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# --- SELENIUM SETUP (Cloud & Local Compatible) ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType

# =========================================================
#             PART 0: CONFIGURATION
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash • Auto-Switching Engine (Native / Selenium / Scroll)")

# --- AUTH ---
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
else:
    api_key = st.sidebar.text_input("Google Gemini API Key", type="password")

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
    soup = BeautifulSoup(html_text, "html.parser")
    # Remove clutter but keep structure
    for element in soup(["script", "style", "svg", "noscript", "img", "iframe", "footer"]):
        element.decompose()
    return str(soup)[:500000] # Large context window

def clean_json_response(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match: return match.group(0)
        return text
    except: return text

# =========================================================
#             PART 2: DATABASE (IBGE)
# =========================================================
@st.cache_data(ttl=86400)
def fetch_ibge_data(limit_first, limit_surname):
    IBGE_FIRST = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"
    
    def _fetch(url, limit):
        s = set()
        page = 1
        while len(s) < limit:
            try:
                r = requests.get(url, params={"page": page}, timeout=5)
                if r.status_code!=200: break
                items = r.json().get("items", [])
                if not items: break
                for i in items:
                    n = normalize_token(i.get("nome"))
                    if n: s.add(n)
                page += 1
            except: break
        return s
    return _fetch(IBGE_FIRST, limit_first), _fetch(IBGE_SURNAME, limit_surname)

st.sidebar.header("⚙️ Search Settings")
limit_first = st.sidebar.number_input("Common First Names", 10, 10000, 2000, 100)
limit_surname = st.sidebar.number_input("Common Surnames", 10, 10000, 2000, 100)

try:
    brazil_first_names, brazil_surnames = fetch_ibge_data(limit_first, limit_surname)
    st.sidebar.success(f"✅ DB Loaded: {len(brazil_first_names)} Firsts / {len(brazil_surnames)} Surnames")
except Exception as e:
    st.error(f"IBGE Error: {e}")
    st.stop()

# =========================================================
#             PART 3: THE ENGINES
# =========================================================

# --- ENGINE A: NATIVE (Fast, Requests) ---
def fetch_native(url, method="GET", data=None):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        if method == "POST":
            return requests.post(url, data=data, headers=headers, timeout=15)
        return requests.get(url, headers=headers, timeout=15)
    except Exception as e:
        return None

# --- ENGINE B: SELENIUM (Smart, Browser) ---
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    # Auto-detects environment (Cloud vs Local)
    return webdriver.Chrome(
        service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
        options=options
    )

def fetch_selenium(driver, url, scroll_count=0, scroll_delay=2.0):
    try:
        driver.get(url)
        time.sleep(3) # Initial load
        
        # Scroll logic
        if scroll_count > 0:
            for i in range(scroll_count):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(scroll_delay)
        
        return driver.page_source
    except Exception as e:
        return None

# --- THE BRAIN: AI ANALYZER ---
def agent_analyze_page(html_content, current_url):
    if not api_key: return None
    
    prompt = f"""
    You are a data extraction system.
    Analyze the HTML from: {current_url}
    
    TASK 1: Extract list of names (people/alumni).
    TASK 2: Determine Navigation Strategy.
    
    Return JSON:
    {{
      "names": ["Name 1", "Name 2"],
      "navigation": {{
         "type": "LINK" or "FORM" or "NONE",
         "url": "next_url",
         "form_data": {{...}}
      }},
      "is_empty": true/false (Set to true if you see NO names and NO content)
    }}
    
    HTML:
    {clean_html_for_ai(html_content)} 
    """
    
    safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    
    for _ in range(2): # 2 Retries
        try:
            model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
            response = model.generate_content(prompt, safety_settings=safety)
            if not response.parts: continue
            return json.loads(clean_json_response(response.text))
        except: time.sleep(1)
    return None

def match_names(names, page_label):
    found = []
    for n in names:
        parts = n.strip().split()
        if not parts: continue
        f, l = normalize_token(parts[0]), normalize_token(parts[-1])
        
        # Match Logic
        score = 0
        match_type = "Weak"
        if f in brazil_first_names: score += 1
        if l in brazil_surnames: score += 1
        
        if score > 0:
            if score == 2: match_type = "Strong"
            elif f in brazil_first_names: match_type = "First Name Only"
            else: match_type = "Surname Only"
            
            found.append({"Full Name": n, "Match Strength": match_type, "Source": page_label})
    return found

# =========================================================
#             PART 4: MASTER LOGIC (THE LOOP)
# =========================================================

st.markdown("### 🤖 Auto-Pilot Control Center")
col1, col2 = st.columns([3, 1])
with col1:
    start_url = st.text_input("Target URL", placeholder="https://www.ycombinator.com/founders")
with col2:
    max_pages = st.number_input("Max Pages", 1, 100, 5)

# Strategy settings hidden in expander
with st.expander("Advanced Strategy Settings"):
    force_mode = st.radio("Scraping Mode", ["Auto-Detect (Recommended)", "Force Native (Requests)", "Force Browser (Selenium)"])
    scroll_depth = st.slider("Scroll Depth (for Infinite Scroll sites)", 0, 20, 3)

if st.button("🚀 Start Mission", type="primary"):
    if not api_key:
        st.error("Missing API Key")
        st.stop()

    # Session State Setup
    results_container = st.empty()
    status_log = st.status("Initializing Agent...", expanded=True)
    all_matches = []
    
    # 1. DETERMINE STRATEGY
    current_mode = "NATIVE"
    driver = None
    
    if force_mode == "Force Browser (Selenium)":
        current_mode = "SELENIUM"
        status_log.write("🔧 Mode: Forced Browser")
        driver = get_driver()
    elif force_mode == "Force Native (Requests)":
        current_mode = "NATIVE"
        status_log.write("🔧 Mode: Forced Native")
    else:
        status_log.write("🧠 Mode: Auto-Detect (Starting Native, will escalate if needed)")

    # 2. NAVIGATION STATE
    current_url = start_url
    visited = set()
    
    # 3. LIVE TABLE
    table_placeholder = st.empty()

    for page in range(1, max_pages + 1):
        status_log.update(label=f"Scanning Page {page}/{max_pages}...", state="running")
        status_log.write(f"**Target:** {current_url}")
        
        raw_html = None
        
        # --- EXECUTE FETCH ---
        if current_mode == "NATIVE":
            resp = fetch_native(current_url)
            if resp and resp.status_code == 200:
                raw_html = resp.text
                # Check for "Ghost Page" (Too small/empty)
                if len(raw_html) < 10000 and force_mode == "Auto-Detect (Recommended)":
                    status_log.warning("⚠️ Native page suspicious. Escalating to Browser...")
                    current_mode = "SELENIUM"
                    driver = get_driver()
                    # Fall through to Selenium block below
            else:
                status_log.warning("⚠️ Native request failed. Escalating to Browser...")
                current_mode = "SELENIUM"
                if not driver: driver = get_driver()

        if current_mode == "SELENIUM":
            # If we just switched, or were already in Selenium
            raw_html = fetch_selenium(driver, current_url, scroll_count=scroll_depth)
        
        if not raw_html:
            status_log.error("❌ Failed to read page in all modes.")
            break

        # --- AI ANALYSIS ---
        data = agent_analyze_page(raw_html, current_url)
        
        if not data:
            status_log.error("⚠️ AI failed to parse content.")
            break
            
        # Check if AI says page is empty (Double check for Auto-Detect)
        if data.get("is_empty") and current_mode == "NATIVE" and force_mode == "Auto-Detect (Recommended)":
            status_log.warning("⚠️ AI sees empty page. Retrying with Browser...")
            current_mode = "SELENIUM"
            if not driver: driver = get_driver()
            raw_html = fetch_selenium(driver, current_url, scroll_count=scroll_depth)
            data = agent_analyze_page(raw_html, current_url) # Re-analyze

        # --- PROCESS RESULTS ---
        names = data.get("names", [])
        new_matches = match_names(names, f"Page {page}")
        
        if new_matches:
            all_matches.extend(new_matches)
            status_log.write(f"✅ Found {len(new_matches)} matches.")
            # Update Live Table
            table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
        else:
            status_log.write("🤷 No matches found on this page.")

        # --- NAVIGATION ---
        nav = data.get("navigation", {})
        if nav.get("type") == "LINK" and nav.get("url"):
            next_link = nav["url"]
            if "http" not in next_link:
                next_link = urljoin(current_url, next_link)
            
            if next_link in visited:
                status_log.write("🛑 Loop detected. Stopping.")
                break
            
            visited.add(current_url)
            current_url = next_link
            status_log.write(f"🔗 Moving to: {next_link}")
        else:
            status_log.write("🏁 No next page found. Job Complete.")
            break
        
        time.sleep(2) # Polite delay

    # --- CLEANUP ---
    if driver:
        driver.quit()
    
    status_log.update(label="Mission Complete!", state="complete", expanded=False)
    
    if all_matches:
        st.balloons()
        df = pd.DataFrame(all_matches)
        
        # EXPORT BUTTONS
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("📥 Download CSV", df.to_csv(index=False).encode('utf-8'), "brazilian_alumni.csv")
        with c2:
            # Excel Export
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Sheet1')
            st.download_button("📥 Download Excel", buffer, "brazilian_alumni.xlsx")
    else:
        st.warning("No matches found during this session.")
