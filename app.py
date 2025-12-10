import streamlit as st
import pandas as pd
import json
import google.generativeai as genai
import time
import re
from unidecode import unidecode
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# --- SELENIUM IMPORTS ---
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    st.error("❌ Missing libraries! Please run: pip install selenium webdriver-manager")
    st.stop()

# =========================================================
#             PART 0: CONFIGURATION
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide")
st.title("Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash • Selenium Infinite Scroll Engine")

# --- AUTH ---
st.sidebar.header("🔑 Authentication")
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
    st.sidebar.success("API Key loaded securely")
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
    """
    Cleaning is lighter now because Gemini 2.5 has a huge context window.
    We allow up to 500,000 characters to capture long lists.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup(["script", "style", "svg", "noscript", "img"]):
        element.decompose()
    return str(soup)[:500000] # INCREASED LIMIT for infinite scroll content

def clean_json_response(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match: return match.group(0)
        return text
    except: return text

# =========================================================
#             PART 2: DATABASE & SELENIUM SETUP
# =========================================================

@st.cache_data(ttl=86400)
def fetch_ibge_data(limit_first, limit_surname):
    # (Same IBGE logic as before, abbreviated for clarity)
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

# --- SIDEBAR SETTINGS ---
st.sidebar.header("⚙️ Settings")
limit_first = st.sidebar.number_input("Common First Names", 10, 10000, 2000, 100)
limit_surname = st.sidebar.number_input("Common Surnames", 10, 10000, 2000, 100)

# --- SCROLL SETTINGS ---
st.sidebar.subheader("🖱️ Scrolling Behavior")
scroll_count = st.sidebar.slider(
    "Scroll Depth (Pages)", 
    min_value=0, max_value=20, value=3,
    help="0 = Top of page only. 5 = Scroll down 5 times to load more items."
)
scroll_delay = st.sidebar.slider("Wait Time (Secs)", 1.0, 5.0, 2.0, help="Time to wait for new items to load after scrolling.")

try:
    brazil_first_names, brazil_surnames = fetch_ibge_data(limit_first, limit_surname)
    st.sidebar.success(f"✅ DB Loaded: {len(brazil_first_names)} Firsts / {len(brazil_surnames)} Surnames")
except Exception as e:
    st.error(f"IBGE Error: {e}")
    st.stop()

# =========================================================
#             PART 3: THE BROWSER AGENT (Selenium)
# =========================================================

def get_page_with_scroll(url, scrolls, delay):
    """
    Launches a headless Chrome browser, visits the URL, and physically scrolls down.
    """
    chrome_options = Options()
    chrome_options.add_argument("--headless") # Invisible mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # Spoof User Agent
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    status_box = st.empty()
    status_box.info("🚀 Launching Browser...")
    
    try:
        # Auto-install driver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        status_box.info(f"🌐 Navigating to {url}...")
        driver.get(url)
        time.sleep(2) # Initial load wait
        
        # --- THE SCROLL LOOP ---
        for i in range(scrolls):
            status_box.info(f"⬇️ Scrolling {i+1}/{scrolls}...")
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(delay) # Wait for spinning loaders to finish
            
        html = driver.page_source
        status_box.success("✅ Page Loaded & Scrolled.")
        driver.quit()
        return html
        
    except Exception as e:
        status_box.error(f"Browser Error: {e}")
        try: driver.quit()
        except: pass
        return None

def agent_analyze_page(html_content, current_url):
    if not api_key: return None
    
    # Analyze the MASSIVE scrolled HTML
    prompt = f"""
    You are a data extraction system.
    I have provided HTML content from: {current_url}
    
    TASK 1: Extract list of names (people/alumni). 
    - The HTML might be very long because I scrolled down multiple times.
    - Extract as many names as possible.
    
    TASK 2: Find the "Next Page" link (if any exist).
    
    Return ONLY a JSON object:
    {{
      "names": ["Name 1", "Name 2", ...],
      "navigation": {{ "type": "LINK" or "NONE", "url": "..." }}
    }}
    
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
            results.append({
                "Full Name": full_name,
                "Match Strength": match_type,
            })
    return results

# =========================================================
#             PART 4: EXECUTION
# =========================================================

st.markdown("### Auto-Pilot (Infinite Scroll Engine)")
st.write("Enter the URL. The AI will launch a browser, scroll down, and capture the full list.")

start_url = st.text_input("Directory URL:", placeholder="https://www.ycombinator.com/founders")

if st.button("Start Scraping", type="primary"):
    if not api_key:
        st.error("Please add your API Key.")
        st.stop()

    all_matches = []
    
    # For Infinite Scroll sites, we usually only process "Page 1" 
    # because Page 1 becomes infinite length.
    
    # 1. GET HTML WITH ROBOT SCROLLING
    raw_html = get_page_with_scroll(start_url, scroll_count, scroll_delay)
    
    if raw_html:
        st.info(f"captured {len(raw_html)} characters of HTML content.")
        
        # 2. AI ANALYSIS
        with st.spinner("🤖 AI is reading the list..."):
            data = agent_analyze_page(raw_html, start_url)
        
        if data:
            names = data.get("names", [])
            st.write(f"📝 AI extracted {len(names)} names from the page.")
            
            matches = analyze_matches(names)
            if matches:
                st.balloons()
                df = pd.DataFrame(matches)
                st.success(f"🎉 Found {len(df)} Brazilian matches!")
                st.dataframe(df)
            else:
                st.warning("No Brazilian matches found in the scrolled content.")
        else:
            st.error("AI failed to parse the page.")
