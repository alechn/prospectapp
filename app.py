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

# --- SELENIUM SETUP ---
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

# =========================================================
#             PART 0: CONFIGURATION
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash • Template Learning Engine (Fast Mode)")

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
    for element in soup(["script", "style", "svg", "noscript", "img", "iframe", "footer"]):
        element.decompose()
    return str(soup)[:500000]

def clean_json_response(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match: return match.group(0)
        return text
    except: return text

# =========================================================
#             PART 2: DATABASE & TEMPLATE LOGIC
# =========================================================
@st.cache_data(ttl=86400)
def fetch_ibge_data(limit_first, limit_surname):
    # (Same IBGE logic as before)
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
limit_first = st.sidebar.number_input("Common First Names", 10, 20000, 2000, 100)
limit_surname = st.sidebar.number_input("Common Surnames", 10, 20000, 2000, 100)

try:
    brazil_first_names, brazil_surnames = fetch_ibge_data(limit_first, limit_surname)
    st.sidebar.success(f"✅ DB Loaded: {len(brazil_first_names)} Firsts / {len(brazil_surnames)} Surnames")
except Exception as e:
    st.error(f"IBGE Error: {e}")
    st.stop()

# =========================================================
#             PART 3: ENGINES (Native & Selenium)
# =========================================================

def fetch_native(session, url, method="GET", data=None):
    try:
        if method == "POST":
            return session.post(url, data=data, timeout=15)
        return session.get(url, timeout=15)
    except Exception as e:
        return None

def get_driver():
    if not HAS_SELENIUM: return None
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    return webdriver.Chrome(service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()), options=options)

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

# =========================================================
#             PART 4: THE INTELLIGENCE (Hybrid Mode)
# =========================================================

def agent_learn_pattern(html_content, current_url):
    """
    Learns the pattern (CSS Selectors) from the page.
    """
    if not api_key: return None
    if len(html_content) < 500: return None

    prompt = f"""
    You are a web scraping expert. Analyze the HTML from {current_url}.
    
    1. Identify the CSS Selector that targets the NAMES of people/alumni.
    2. Identify the CSS Selector for the "Next Page" link (anchor tag).
    3. Extract the names using your own logic to verify.
    
    Return JSON:
    {{
      "names": ["Name 1", "Name 2"],
      "selectors": {{
         "name_element": "e.g. div.alumni-name or table tr td:nth-child(2)",
         "next_link": "e.g. a.next-page-link or a[rel='next']"
      }},
      "navigation": {{ "type": "LINK" or "FORM", "url": "...", "form_data": {{...}} }}
    }}
    
    HTML:
    {clean_html_for_ai(html_content)} 
    """
    
    for _ in range(2): 
        try:
            model = genai.GenerativeModel('gemini-2.5-flash', generation_config={"response_mime_type": "application/json"})
            response = model.generate_content(prompt)
            if not response.parts: continue
            return json.loads(clean_json_response(response.text))
        except: time.sleep(1)
    return None

def fast_extract_mode(html_content, selectors):
    """
    Uses stored CSS Selectors to extract data WITHOUT AI.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    extracted_names = []
    
    # 1. Extract Names
    if selectors.get("name_element"):
        elements = soup.select(selectors["name_element"])
        for el in elements:
            extracted_names.append(el.get_text(strip=True))
            
    # 2. Find Next Link
    next_url = None
    if selectors.get("next_link"):
        link = soup.select_one(selectors["next_link"])
        if link and link.get("href"):
            next_url = link.get("href")
            
    return {"names": extracted_names, "next_url": next_url}

# =========================================================
#             PART 5: MAIN INTERFACE
# =========================================================

st.markdown("### 🤖 Auto-Pilot Control Center")
col1, col2 = st.columns([3, 1])
with col1:
    start_url = st.text_input("Target URL", placeholder="https://legacy.cs.stanford.edu/directory/masters-alumni")
with col2:
    max_pages = st.number_input("Max Pages", 1, 100, 5)

st.write("---")
st.subheader("🛠️ Engine Selection")
mode = st.radio(
    "Choose Scraping Method:", 
    ["Classic Directory (Stanford/Wikipedia)", "Infinite Scroller (YCombinator/JS Sites)"]
)

if "Infinite" in mode:
    if not HAS_SELENIUM: st.error("❌ Selenium missing."); st.stop()
    scroll_depth = st.slider("Scroll Depth", 1, 20, 3)

if st.button("🚀 Start Mission", type="primary"):
    if not api_key: st.error("Missing API Key"); st.stop()

    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()
    all_matches = []
    
    # SETUP
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
    visited_fingerprints = set()
    
    # INTELLIGENCE STATE
    learned_selectors = None
    
    if "Infinite" in mode:
        driver = get_driver()
        status_log.write("🔧 Browser Launched")

    for page in range(1, max_pages + 1):
        status_log.update(label=f"Scanning Page {page}/{max_pages}...", state="running")
        
        # --- EXECUTE REQUEST ---
        raw_html = None
        try:
            if "Classic" in mode:
                resp = fetch_native(session, current_url, next_method, next_data)
                if resp and resp.status_code == 200: raw_html = resp.text
            else:
                raw_html = fetch_selenium(driver, current_url, scroll_count=scroll_depth)
        except Exception as e:
            status_log.error(f"Critical Error: {e}")
            break

        if not raw_html: status_log.error("❌ Content failed."); break

        # --- INTELLIGENT EXTRACTION ---
        names = []
        nav_data = {}
        
        # STRATEGY: Try Fast Mode first if we have selectors
        if learned_selectors:
            status_log.write(f"⚡ using Fast Template (No AI)")
            fast_data = fast_extract_mode(raw_html, learned_selectors)
            names = fast_data["names"]
            
            # If Fast Mode fails (0 names), Fallback to AI
            if not names:
                status_log.warning("⚠️ Template failed. Re-learning with AI...")
                learned_selectors = None # Reset
        
        # STRATEGY: Use AI if no selectors or fallback triggered
        if not learned_selectors:
            status_log.write(f"🧠 AI Analyzing Page Structure...")
            data = agent_learn_pattern(raw_html, current_url)
            
            if data:
                names = data.get("names", [])
                selectors = data.get("selectors", {})
                nav_data = data.get("navigation", {})
                
                # Verify selectors logic
                if selectors.get("name_element"):
                    learned_selectors = selectors
                    status_log.write(f"🎓 Pattern Learned: {selectors['name_element']}")
            else:
                status_log.error("❌ AI failed to read page.")
                break

        # --- MATCHING ---
        found = []
        for n in names:
            parts = n.strip().split()
            if not parts: continue
            f, l = normalize_token(parts[0]), normalize_token(parts[-1])
            if (f in brazil_first_names) or (l in brazil_surnames):
                m_type = "Strong" if (f in brazil_first_names and l in brazil_surnames) else "Weak"
                found.append({"Full Name": n, "Match Strength": m_type, "Source": f"Page {page}"})
        
        if found:
            all_matches.extend(found)
            status_log.write(f"✅ Found {len(found)} matches.")
            table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
        else:
            status_log.write("🤷 No matches found.")

        # --- NAVIGATION ---
        # If we are in Fast Mode, we look for the link using CSS
        if learned_selectors and "Classic" in mode:
            # Re-parse quickly to find link
            fast_res = fast_extract_mode(raw_html, learned_selectors)
            if fast_res["next_url"]:
                link = fast_res["next_url"]
                if "http" not in link: current_url = urljoin(current_url, link)
                else: current_url = link
                next_method = "GET"
                next_data = None
                status_log.write(f"🔗 Fast Link Found: {link}")
                time.sleep(1)
                continue # Skip the rest, loop again
        
        # If Fast Mode navigation failed or didn't exist, use AI Navigation Data
        if "Classic" in mode:
            ntype = nav_data.get("type", "NONE")
            if ntype == "LINK" and nav_data.get("url"):
                l = nav_data["url"]
                if "http" not in l: current_url = urljoin(current_url, l)
                else: current_url = l
                next_method = "GET"
                next_data = None
            elif ntype == "FORM":
                form = nav_data.get("form_data", {})
                if form:
                    next_method = "POST"
                    next_data = form
                    fp = str(form)
                    if fp in visited_fingerprints: status_log.warning("Loop ended."); break
                    visited_fingerprints.add(fp)
                else: break
            else:
                status_log.write("🏁 No next page."); break
        else:
            status_log.write("🏁 Scroll done."); break
        
        time.sleep(2)

    if driver: driver.quit()
    status_log.update(label="Complete!", state="complete")
    
    if all_matches:
        df = pd.DataFrame(all_matches)
        st.download_button("📥 Download CSV", df.to_csv(index=False).encode('utf-8'), "brazilian_alumni.csv")
