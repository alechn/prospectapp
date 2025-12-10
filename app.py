import streamlit as st
import pandas as pd
import json
import google.generativeai as genai
import time
import re
import requests
from unidecode import unidecode
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# --- SELENIUM SETUP ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType

# =========================================================
#             PART 0: CONFIGURATION
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide")
st.title("Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash • Infinite Scroll Engine (Cloud Version)")

# --- AUTH (Use Streamlit Secrets for Cloud) ---
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
    for element in soup(["script", "style", "svg", "noscript", "img"]):
        element.decompose()
    return str(soup)[:500000]

def clean_json_response(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match: return match.group(0)
        return text
    except: return text

# =========================================================
#             PART 2: DATABASE
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

st.sidebar.header("⚙️ Settings")
limit_first = st.sidebar.number_input("Common First Names", 10, 10000, 2000, 100)
limit_surname = st.sidebar.number_input("Common Surnames", 10, 10000, 2000, 100)

st.sidebar.subheader("🖱️ Scrolling Behavior")
scroll_count = st.sidebar.slider("Scroll Depth (Pages)", 0, 20, 3)
scroll_delay = st.sidebar.slider("Wait Time (Secs)", 1.0, 5.0, 2.0)

try:
    brazil_first_names, brazil_surnames = fetch_ibge_data(limit_first, limit_surname)
    st.sidebar.success(f"✅ DB Loaded: {len(brazil_first_names)} Firsts / {len(brazil_surnames)} Surnames")
except Exception as e:
    st.error(f"IBGE Error: {e}")
    st.stop()

# =========================================================
#             PART 3: CLOUD-READY SELENIUM
# =========================================================
def get_page_with_scroll(url, scrolls, delay):
    status_box = st.empty()
    status_box.info("🚀 Launching Cloud Browser...")
    
    try:
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        
        # This handles the driver installation automatically on both Cloud and Local
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
            options=options
        )
        
        status_box.info(f"🌐 Navigating to {url}...")
        driver.get(url)
        time.sleep(2)
        
        for i in range(scrolls):
            status_box.info(f"⬇️ Scrolling {i+1}/{scrolls}...")
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(delay)
            
        html = driver.page_source
        status_box.success("✅ Page Loaded & Scrolled.")
        driver.quit()
        return html
        
    except Exception as e:
        status_box.error(f"Browser Error: {e}")
        return None

def agent_analyze_page(html_content, current_url):
    if not api_key: return None
    
    prompt = f"""
    You are a data extraction system.
    I have provided HTML content from: {current_url}
    TASK 1: Extract list of names (people/alumni). 
    TASK 2: Find the "Next Page" link (if any exist).
    Return ONLY a JSON object:
    {{ "names": ["Name 1", "Name 2"], "navigation": {{ "type": "LINK" or "NONE", "url": "..." }} }}
    HTML CONTENT:
    {clean_html_for_ai(html_content)} 
    """
    safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    for attempt in range(3):
        try:
            model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
            response = model.generate_content(prompt, safety_settings=safety)
            if not response.parts: continue
            return json.loads(clean_json_response(response.text))
        except Exception: time.sleep(1)
    return None

def analyze_matches(found_names_list):
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
            results.append({"Full Name": full_name, "Match Strength": match_type})
    return results

# =========================================================
#             PART 4: EXECUTION
# =========================================================
st.markdown("### Auto-Pilot (Cloud Engine)")
start_url = st.text_input("Directory URL:", placeholder="https://www.ycombinator.com/founders")

if st.button("Start Scraping", type="primary"):
    if not api_key:
        st.error("Please add your API Key in the Sidebar or Cloud Secrets.")
        st.stop()
    
    raw_html = get_page_with_scroll(start_url, scroll_count, scroll_delay)
    
    if raw_html:
        with st.spinner("🤖 AI is reading the list..."):
            data = agent_analyze_page(raw_html, start_url)
        
        if data:
            names = data.get("names", [])
            st.write(f"📝 AI extracted {len(names)} names.")
            matches = analyze_matches(names)
            if matches:
                st.balloons()
                df = pd.DataFrame(matches)
                st.success(f"🎉 Found {len(df)} Brazilian matches!")
                st.dataframe(df)
            else:
                st.warning("No Brazilian matches found.")
        else:
            st.error("AI failed to parse the page.")
