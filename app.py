import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import random
import os
import shutil
from unidecode import unidecode
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# --- SELENIUM SETUP ---
try:
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
#             PART 0: CONFIGURATION & SETUP
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Powered by Multi-Model AI • 3-in-1 Engine • Scoring System")

# --- SESSION STATE (Abort Logic) ---
if "running" not in st.session_state:
    st.session_state.running = False

# --- SIDEBAR: AI BRAIN ---
st.sidebar.header("🧠 AI Brain")
ai_provider = st.sidebar.selectbox(
    "Choose your Model:",
    ["Google Gemini (Flash 2.0)", "OpenAI (GPT-4o)", "Anthropic (Claude 3.5)", "DeepSeek (V3)"]
)
api_key = st.sidebar.text_input(f"Enter {ai_provider.split()[0]} API Key", type="password")

# --- ABORT BUTTON ---
st.sidebar.markdown("---")
if st.sidebar.button("🛑 ABORT MISSION", type="primary"):
    st.session_state.running = False
    st.sidebar.warning("Mission Aborted.")
    st.stop()

# --- BLOCKLIST (Anti-False Positive) ---
BLOCKLIST_SURNAMES = {
    "WANG", "LI", "ZHANG", "LIU", "CHEN", "YANG", "HUANG", "ZHAO", "WU", "ZHOU", 
    "XU", "SUN", "MA", "ZHU", "HU", "GUO", "HE", "GAO", "LIN", "LUO", 
    "LIANG", "SONG", "TANG", "ZHENG", "HAN", "FENG", "DONG", "YE", "YU", "WEI", 
    "CAI", "YUAN", "PAN", "DU", "DAI", "JIN", "FAN", "SU", "MAN", "WONG", 
    "CHAN", "CHANG", "LEE", "KIM", "PARK", "CHOI", "NG", "HO", "CHOW", "LAU",
    "SINGH", "PATEL", "KUMAR", "SHARMA", "GUPTA", "ALI", "KHAN", "TRAN", "NGUYEN"
}

# =========================================================
#             PART 1: UNIVERSAL AI ADAPTER
# =========================================================
def call_ai_api(prompt, provider, key):
    if not key: return None
    headers = {"Content-Type": "application/json"}
    
    try:
        # GEMINI
        if "Gemini" in provider:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code == 200: return resp.json()['candidates'][0]['content']['parts'][0]['text']
            
        # OPENAI
        elif "OpenAI" in provider:
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {"model": "gpt-4o", "messages": [{"role": "system", "content": "JSON Extractor"}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code == 200: return resp.json()['choices'][0]['message']['content']
            
        # ANTHROPIC
        elif "Anthropic" in provider:
            url = "https://api.anthropic.com/v1/messages"
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
            payload = {"model": "claude-3-5-sonnet-20241022", "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]}
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code == 200: return resp.json()['content'][0]['text']
            
        # DEEPSEEK
        elif "DeepSeek" in provider:
            url = "https://api.deepseek.com/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {"model": "deepseek-chat", "messages": [{"role": "system", "content": "JSON Extractor"}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code == 200: return resp.json()['choices'][0]['message']['content']

    except Exception as e: return None
    return None

def clean_json_response(text):
    if not text: return "{}"
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match: return match.group(0)
        return text
    except: return text

# =========================================================
#             PART 2: DATA & HELPERS
# =========================================================
def normalize_token(s: str) -> str:
    if not s: return ""
    s = str(s).strip().upper()
    return "".join(ch for ch in unidecode(s) if "A" <= ch <= "Z")

def clean_html_for_ai(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup(["script", "style", "svg", "noscript", "img", "iframe", "footer"]):
        element.decompose()
    return str(soup)[:500000]

@st.cache_data(ttl=86400)
def fetch_ibge_data(limit_first, limit_surname):
    IBGE_FIRST = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"
    
    def _fetch(url, limit):
        data_map = {} 
        page = 1
        while len(data_map) < limit:
            try:
                r = requests.get(url, params={"page": page}, timeout=5)
                if r.status_code!=200: break
                items = r.json().get("items", [])
                if not items: break
                for i in items:
                    n = normalize_token(i.get("nome"))
                    if n: data_map[n] = i.get("rank", 0)
                page += 1
            except: break
        return data_map
    return _fetch(IBGE_FIRST, limit_first), _fetch(IBGE_SURNAME, limit_surname)

st.sidebar.header("⚙️ Settings")
limit_first = st.sidebar.number_input("DB: First Names", 100, 20000, 3000, 100)
limit_surname = st.sidebar.number_input("DB: Surnames", 100, 20000, 3000, 100)

try:
    first_name_ranks, surname_ranks = fetch_ibge_data(limit_first, limit_surname)
    sorted_surnames = sorted(surname_ranks.keys(), key=lambda k: surname_ranks[k])
    st.sidebar.success(f"✅ DB Loaded: {len(first_name_ranks)} Firsts / {len(surname_ranks)} Surnames")
except: st.stop()

# =========================================================
#             PART 3: DRIVERS (THE CLOUD FIX)
# =========================================================
def fetch_native(session, url, method="GET", data=None):
    try:
        if method == "POST": return session.post(url, data=data, timeout=15)
        return session.get(url, timeout=15)
    except: return None

def get_driver(headless=True):
    """
    Intelligent Driver Loader:
    1. Checks if running on Streamlit Cloud (Linux).
    2. If yes, forces usage of /usr/bin/chromedriver (Prevents Crashes).
    3. If no (Local PC), falls back to WebDriver Manager.
    """
    if not HAS_SELENIUM: return None
    
    options = Options()
    
    # --- CLOUD MANDATORY FLAGS ---
    # Streamlit Cloud runs on Linux and HAS NO DISPLAY.
    # We must force headless mode if we detect Linux, otherwise it crashes immediately.
    if os.name == 'posix': 
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
    elif headless:
        # Local execution (Windows/Mac) allows headless toggle
        options.add_argument("--headless")

    # --- PATH SELECTION ---
    # Case A: Streamlit Cloud (Standard Paths)
    # We explicitly check for the binaries installed by packages.txt
    if os.path.exists("/usr/bin/chromium") and os.path.exists("/usr/bin/chromedriver"):
        options.binary_location = "/usr/bin/chromium"
        service = Service("/usr/bin/chromedriver")
        return webdriver.Chrome(service=service, options=options)
    
    # Case B: Local Fallback (WebDriver Manager)
    # This runs if the user is on their own machine
    try:
        return webdriver.Chrome(
            service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
            options=options
        )
    except Exception as e:
        st.error(f"❌ Local Driver Failed: {e}")
        return None

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
#             PART 4: INTELLIGENCE
# =========================================================
def agent_analyze_page(html_content, current_url, provider, key, task_type="PAGINATION"):
    if len(html_content) < 500: return None
    
    if task_type == "SEARCH_BOX":
        task_desc = """
        2. Identify the CSS Selector for the SEARCH INPUT BOX.
        3. Identify the CSS Selector for the SEARCH SUBMIT BUTTON.
        """
        json_desc = '"search_input": "input[name=q]", "search_button": "button[type=submit]"'
    else:
        task_desc = """
        2. Identify the CSS Selector for the "Next Page" CLICKABLE ELEMENT (Link or Button).
        3. CHECK PAGINATION: Look for 'Page 1 of X'. Extract TOTAL PAGES.
        """
        json_desc = '"next_element": "a.next", "total_pages": 50'

    prompt = f"""
    You are a web scraping expert. Analyze the HTML from {current_url}.
    
    1. Identify the CSS Selector for NAMES.
    {task_desc}
    
    Return JSON:
    {{
      "names": ["Name 1", "Name 2"],
      "selectors": {{
         "name_element": "div.alumni-name",
         {json_desc}
      }},
      "navigation": {{ "type": "LINK" or "FORM", "url": "...", "form_data": {{...}} }}
    }}
    
    HTML:
    {clean_html_for_ai(html_content)} 
    """
    
    for _ in range(2): 
        raw = call_ai_api(prompt, provider, key)
        if raw: return json.loads(clean_json_response(raw))
        time.sleep(1)
    return None

def fast_extract_mode(html_content, selectors):
    soup = BeautifulSoup(html_content, "html.parser")
    extracted_names = []
    
    if selectors.get("name_element"):
        elements = soup.select(selectors["name_element"])
        for el in elements: extracted_names.append(el.get_text(strip=True))
            
    nav_result = {"next_url": None, "form_data": None, "type": "NONE"}
    next_selector = selectors.get("next_element") or selectors.get("next_link")
    
    if next_selector:
        element = soup.select_one(next_selector)
        if element:
            if element.name == "a" and element.get("href"):
                nav_result["type"] = "LINK"
                nav_result["next_url"] = element.get("href")
            elif element.name in ["input", "button"]:
                parent_form = element.find_parent("form")
                if parent_form:
                    nav_result["type"] = "FORM"
                    form_data = {}
                    for inp in parent_form.find_all("input"):
                        if inp.get("name"): form_data[inp.get("name")] = inp.get("value", "")
                    if element.get("name"): form_data[element.get("name")] = element.get("value", "")
                    nav_result["form_data"] = form_data
                    if parent_form.get("action"): nav_result["next_url"] = parent_form.get("action")

    return {"names": extracted_names, "nav": nav_result}

def match_names_detailed(names, source):
    found = []
    for n in names:
        parts = n.strip().split()
        if not parts: continue
        f, l = normalize_token(parts[0]), normalize_token(parts[-1])
        if l in BLOCKLIST_SURNAMES: continue
        
        rank_f, rank_l = first_name_ranks.get(f, 0), surname_ranks.get(l, 0)
        
        score = 0
        if rank_f > 0: score += ((limit_first - rank_f)/limit_first)*50
        if rank_l > 0: score += ((limit_surname - rank_l)/limit_surname)*50
        
        if score > 0:
            m_type = "Strong" if (rank_f > 0 and rank_l > 0) else ("First Only" if rank_f > 0 else "Surname Only")
            found.append({
                "Full Name": n, "Brazil Score": round(score, 1),
                "Match Type": m_type, "Source": source
            })
    return found

# =========================================================
#             PART 5: MAIN INTERFACE
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", placeholder="https://directory.example.com")
max_pages = c2.number_input("Max Pages / Search Cycles", 1, 500, 10)

st.write("---")
st.subheader("🛠️ Strategy Selection")

mode = st.radio("Choose Operation Mode:", [
    "Classic Directory (Native/Fast)",
    "Infinite Scroller (Selenium)",
    "Active Search Injection (Brute Force Surnames)"
])

run_headless = True
scroll_depth = 0

if "Infinite" in mode:
    if not HAS_SELENIUM: st.error("❌ Selenium required."); st.stop()
    scroll_depth = st.slider("Scroll Depth", 1, 20, 3)

if "Search Injection" in mode:
    if not HAS_SELENIUM: st.error("❌ Selenium required."); st.stop()
    run_headless = st.checkbox("Run in Background (Headless)", value=True, help="Uncheck to solve CAPTCHAs manually.")

if st.button("🚀 Start Mission", type="primary"):
    st.session_state.running = True

# --- EXECUTION ---
if st.session_state.running:
    if not api_key: st.error("Missing API Key"); st.stop()

    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()
    all_matches = []
    learned_selectors = None
    
    # ----------------------------------------------------
    # BRANCH A: PAGINATION & SCROLLING
    # ----------------------------------------------------
    if "Search Injection" not in mode:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://google.com"})
        
        driver = None
        if "Infinite" in mode: 
            driver = get_driver(headless=True)
            if not driver: status_log.error("Aborted: Driver failed."); st.stop()
        
        current_url = start_url
        next_method, next_data = "GET", None
        visited_fps = set()
        detected_limit = None
        
        page = 0
        while page < max_pages and st.session_state.running:
            page += 1
            if detected_limit and page > detected_limit: break
            status_log.update(label=f"Scanning Page {page}...", state="running")
            
            raw_html = None
            try:
                if "Classic" in mode:
                    resp = fetch_native(session, current_url, next_method, next_data)
                    if resp and resp.status_code == 200: raw_html = resp.text
                else:
                    raw_html = fetch_selenium(driver, current_url, scroll_count=scroll_depth)
            except: pass
            
            if not raw_html: break

            names, nav_data, ai_needed = [], {}, True
            
            if learned_selectors:
                status_log.write("⚡ Fast Template")
                fast_res = fast_extract_mode(raw_html, learned_selectors)
                names = fast_res["names"]
                nav_res = fast_res["nav"]
                
                if len(names) > 0 and nav_res["type"] != "NONE":
                    ai_needed = False
                    if nav_res["type"] == "LINK":
                        l = nav_res["next_url"]
                        current_url = urljoin(current_url, l) if "http" not in l else l
                        next_method, next_data = "GET", None
                    elif nav_res["type"] == "FORM":
                        next_method, next_data = "POST", nav_res["form_data"]
                        if nav_res.get("next_url"): 
                            act = nav_res["next_url"]
                            current_url = urljoin(current_url, act) if "http" not in act else act
                elif len(names) == 0: ai_needed = True 
            
            if ai_needed:
                if not learned_selectors: status_log.write(f"🧠 {ai_provider.split()[0]} Analyzing...")
                data = agent_analyze_page(raw_html, current_url, ai_provider, api_key, "PAGINATION")
                
                if data:
                    names = data.get("names", [])
                    selectors = data.get("selectors", {})
                    nav_data = data.get("navigation", {})
                    if not detected_limit and data.get("total_pages"):
                         try: detected_limit = int(data["total_pages"]); status_log.info(f"Limit: {detected_limit}")
                         except: pass
                    if selectors.get("name_element"): learned_selectors = selectors
                else: break

            matches = match_names_detailed(names, f"Page {page}")
            if matches:
                all_matches.extend(matches)
                all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
                status_log.write(f"✅ Found {len(matches)} matches.")
            else: status_log.write("🤷 No matches.")

            if ai_needed:
                if "Classic" in mode:
                    ntype = nav_data.get("type", "NONE")
                    if ntype == "LINK" and nav_data.get("url"):
                        l = nav_data["url"]
                        current_url = urljoin(current_url, l) if "http" not in l else l
                        next_method, next_data = "GET", None
                    elif ntype == "FORM" and nav_data.get("form_data"):
                        next_method, next_data = "POST", nav_data["form_data"]
                        fp = str(next_data)
                        if fp in visited_fps: break
                        visited_fps.add(fp)
                    else: break
                else: break
            
            time.sleep(1.5)
        
        if driver: driver.quit()

    # ----------------------------------------------------
    # BRANCH B: SEARCH INJECTION (Brute Force)
    # ----------------------------------------------------
    else:
        # NOTE: On Cloud, run_headless is forced True by get_driver()
        driver = get_driver(headless=run_headless)
        if not driver: status_log.error("Aborted: Driver failed."); st.stop()
        
        status_log.write("🔧 Browser Launched")
        try: driver.get(start_url)
        except: status_log.error("Could not load URL"); driver.quit(); st.stop()
        
        time.sleep(3)
        status_log.write("🧠 Finding Search Box...")
        data = agent_analyze_page(driver.page_source, start_url, ai_provider, api_key, "SEARCH_BOX")
        
        if not data or not data.get("selectors", {}).get("search_input"):
            status_log.error("❌ Could not find search box.")
            driver.quit(); st.stop()
            
        sel_input = data["selectors"]["search_input"]
        sel_btn = data["selectors"].get("search_button")
        
        for i, surname in enumerate(sorted_surnames[:max_pages]):
            if not st.session_state.running: break
            status_log.update(label=f"Checking '{surname}' ({i+1}/{max_pages})", state="running")
            try:
                inp = driver.find_element(By.CSS_SELECTOR, sel_input)
                inp.clear()
                for ch in surname: inp.send_keys(ch); time.sleep(random.uniform(0.05, 0.15))
                time.sleep(0.5)
                
                if sel_btn:
                    try: driver.find_element(By.CSS_SELECTOR, sel_btn).click()
                    except: inp.send_keys(Keys.RETURN)
                else: inp.send_keys(Keys.RETURN)
                
                time.sleep(3)
                
                # Basic CAPTCHA warning (Will only show in logs on Cloud)
                if "captcha" in driver.page_source.lower():
                     status_log.warning("⚠️ CAPTCHA Detected.")
                
                soup = BeautifulSoup(driver.page_source, "html.parser")
                if not learned_selectors:
                    res_data = agent_analyze_page(driver.page_source, start_url, ai_provider, api_key, "PAGINATION")
                    if res_data and res_data.get("selectors", {}).get("name_element"):
                        learned_selectors = res_data["selectors"]
                
                current_names = []
                if learned_selectors:
                    els = soup.select(learned_selectors["name_element"])
                    current_names = [e.get_text(strip=True) for e in els]
                
                matches = match_names_detailed(current_names, f"Search: {surname}")
                if matches:
                    all_matches.extend(matches)
                    all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                    table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
                    status_log.write(f"✅ Found {len(matches)} for '{surname}'.")
                
                driver.get(start_url)
                time.sleep(1.5)
            except: driver.get(start_url)
        
        driver.quit()

    status_log.update(label="Mission Complete!", state="complete")
    st.session_state.running = False
    
    if all_matches:
        df = pd.DataFrame(all_matches)
        c1, c2 = st.columns(2)
        with c1: st.download_button("📥 Download CSV", df.to_csv(index=False).encode('utf-8'), "results.csv")
        with c2: 
            try:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False, sheet_name='Sheet1')
                st.download_button("📥 Download Excel", buffer, "results.xlsx")
            except: st.info("Install 'xlsxwriter' for Excel.")
