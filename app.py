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

# --- SELENIUM SETUP (Only loaded if needed) ---
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
st.caption("Powered by Gemini 2.5 Flash • Session Lock Engine")

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
    # Aggressive cleaning to keep it fast like the original
    for element in soup(["script", "style", "svg", "noscript", "img", "iframe", "footer", "meta", "head"]):
        element.decompose()
    return str(soup)[:100000] # Cap size for speed

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
#             PART 3: ENGINES (Session Locked)
# =========================================================

# --- ENGINE A: NATIVE (The fast one that worked before) ---
def fetch_native(session, url, method="GET", data=None):
    try:
        if method == "POST":
            return session.post(url, data=data, timeout=15)
        return session.get(url, timeout=15)
    except Exception as e:
        return None

# --- ENGINE B: SELENIUM (Only for Infinite Scroll) ---
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    return webdriver.Chrome(
        service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
        options=options
    )

def fetch_selenium(driver, url, scroll_count=0):
    try:
        driver.get(url)
        time.sleep(3)
        if scroll_count > 0:
            for i in range(scroll_count):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
        return driver.page_source
    except: return None

# --- AI BRAIN ---
def agent_analyze_page(html_content, current_url):
    if not api_key: return None
    
    # Prompt from the original working code
    prompt = f"""
    You are a data extraction system.
    Analyze the HTML from: {current_url}
    
    1. Extract list of names.
    2. Find the "Next Page" mechanism.
       - LOOK FOR <form> tags. If found, extract HIDDEN INPUTS.
       - LOOK FOR <a> tags.
    
    Return JSON:
    {{
      "names": ["Name 1", "Name 2"],
      "navigation": {{
         "type": "LINK" or "FORM" or "NONE",
         "url": "next_url",
         "form_data": {{ "key": "value" }}
      }}
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
    
    for _ in range(2): 
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
        
        is_first = f in brazil_first_names
        is_last = l in brazil_surnames
        
        if is_first or is_last:
            m_type = "Strong" if (is_first and is_last) else ("First Name Only" if is_first else "Surname Only")
            found.append({"Full Name": n, "Match Strength": m_type, "Source": page_label})
    return found

# =========================================================
#             PART 4: MAIN LOOP
# =========================================================

st.markdown("### 🤖 Auto-Pilot Control Center")
col1, col2 = st.columns([3, 1])
with col1:
    start_url = st.text_input("Target URL", placeholder="https://legacy.cs.stanford.edu/directory/masters-alumni")
with col2:
    max_pages = st.number_input("Max Pages", 1, 100, 5)

# Clear strategy selection
mode = st.radio("Select Strategy", ["Native (Fast/Forms)", "Infinite Scroll (Selenium)"], horizontal=True, 
                help="Use 'Native' for directories like Stanford. Use 'Infinite Scroll' for sites like YCombinator.")

scroll_depth = 0
if mode == "Infinite Scroll (Selenium)":
    scroll_depth = st.slider("Scroll Depth", 1, 20, 3)

if st.button("🚀 Start Mission", type="primary"):
    if not api_key: st.error("Missing API Key"); st.stop()

    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()
    all_matches = []
    
    # SETUP SESSION ONCE
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.com/"
    })

    current_url = start_url
    next_method = "GET"
    next_data = None
    driver = None
    
    # Initialize Driver ONCE if needed
    if mode == "Infinite Scroll (Selenium)":
        driver = get_driver()
        status_log.write("🔧 Browser Launched")

    visited_fingerprints = set()

    for page in range(1, max_pages + 1):
        status_log.update(label=f"Scanning Page {page}/{max_pages}...", state="running")
        status_log.write(f"**Target:** {current_url} ({next_method})")
        
        raw_html = None
        
        # --- EXECUTE REQUEST ---
        try:
            if mode == "Native (Fast/Forms)":
                # NATIVE MODE (Locks to Requests)
                resp = fetch_native(session, current_url, next_method, next_data)
                if resp and resp.status_code == 200:
                    raw_html = resp.text
                else:
                    status_log.error(f"❌ HTTP Error: {resp.status_code if resp else 'Connection Failed'}")
                    break
            else:
                # SELENIUM MODE (Locks to Browser)
                raw_html = fetch_selenium(driver, current_url, scroll_count=scroll_depth)
        except Exception as e:
            status_log.error(f"Critical Error: {e}")
            break

        if not raw_html:
            status_log.error("❌ Failed to retrieve content.")
            break

        # --- AI ANALYSIS ---
        data = agent_analyze_page(raw_html, current_url)
        
        if not data:
            status_log.warning(f"⚠️ AI could not read page {page}.")
            break

        # --- MATCHING ---
        names = data.get("names", [])
        new_matches = match_names(names, f"Page {page}")
        
        if new_matches:
            all_matches.extend(new_matches)
            status_log.write(f"✅ Found {len(new_matches)} matches.")
            table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
        else:
            status_log.write("🤷 No matches found.")

        # --- NAVIGATION (Only for Native Mode) ---
        # Selenium infinite scroll doesn't need "Next Page" logic usually
        if mode == "Native (Fast/Forms)":
            nav = data.get("navigation", {})
            nav_type = nav.get("type", "NONE")
            
            if nav_type == "LINK" and nav.get("url"):
                raw_link = nav["url"]
                if "http" not in raw_link: current_url = urljoin(current_url, raw_link)
                else: current_url = raw_link
                next_method = "GET"
                next_data = None
                status_log.write(f"🔗 Link found: {raw_link}")
                
            elif nav_type == "FORM":
                form_data = nav.get("form_data", {})
                if form_data:
                    next_method = "POST"
                    next_data = form_data
                    
                    # Loop Check
                    fp = str(form_data)
                    if fp in visited_fingerprints:
                        status_log.warning("⚠️ Loop detected. Finishing.")
                        break
                    visited_fingerprints.add(fp)
                    status_log.write(f"📝 Form detected. Sending data...")
                else:
                    status_log.write("🏁 Form found but empty. Job done.")
                    break
            else:
                status_log.write("🏁 No next page found. Job Complete.")
                break
        else:
            # Infinite Scroll usually grabs everything in one go
            status_log.write("🏁 Scroll complete. Job done.")
            break
        
        time.sleep(2)

    if driver: driver.quit()
    
    status_log.update(label="Mission Complete!", state="complete", expanded=False)
    
    if all_matches:
        st.balloons()
        df = pd.DataFrame(all_matches)
        
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("📥 Download CSV", df.to_csv(index=False).encode('utf-8'), "brazilian_alumni.csv")
        with c2:
            try:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False, sheet_name='Sheet1')
                st.download_button("📥 Download Excel", buffer, "brazilian_alumni.xlsx")
            except:
                st.info("💡 Install 'xlsxwriter' for Excel export.")
