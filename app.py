import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import random
import os
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
#             PART 0: CONFIGURATION
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Powered by Multi-Model AI • 3-in-1 Engine • Scoring System")

if "running" not in st.session_state: st.session_state.running = False

# --- SIDEBAR ---
st.sidebar.header("🧠 AI Brain")
ai_provider = st.sidebar.selectbox(
    "Choose your Model:",
    ["Google Gemini (Flash 2.0)", "OpenAI (GPT-4o)", "Anthropic (Claude 3.5)", "DeepSeek (V3)"]
)
api_key = st.sidebar.text_input(f"Enter {ai_provider.split()[0]} API Key", type="password")

st.sidebar.markdown("---")
search_delay = st.sidebar.slider("⏳ Search/Scroll Wait Time (Sec)", 5, 60, 15)
use_ai_cleaning = st.sidebar.checkbox("✨ Batch AI Cleaning", value=True, help="Clean final list with AI.")

with st.sidebar.expander("🛠️ Advanced / Debug"):
    manual_search_selector = st.text_input("Manual Search Box Selector", placeholder="e.g. input[name='q']")
    manual_name_selector = st.text_input("Manual Name Selector", placeholder="e.g. h3 or div.alumni-name")

if st.sidebar.button("🛑 ABORT MISSION", type="primary"):
    st.session_state.running = False
    st.sidebar.warning("Mission Aborted.")
    st.stop()

# --- DATA ---
BLOCKLIST_SURNAMES = {
    "WANG", "LI", "ZHANG", "LIU", "CHEN", "YANG", "HUANG", "ZHAO", "WU", "ZHOU", 
    "XU", "SUN", "MA", "ZHU", "HU", "GUO", "HE", "GAO", "LIN", "LUO", 
    "LIANG", "SONG", "TANG", "ZHENG", "HAN", "FENG", "DONG", "YE", "YU", "WEI", 
    "CAI", "YUAN", "PAN", "DU", "DAI", "JIN", "FAN", "SU", "MAN", "WONG", 
    "CHAN", "CHANG", "LEE", "KIM", "PARK", "CHOI", "NG", "HO", "CHOW", "LAU",
    "SINGH", "PATEL", "KUMAR", "SHARMA", "GUPTA", "ALI", "KHAN", "TRAN", "NGUYEN",
    "RESULTS", "WEBSITE", "SEARCH", "MENU", "SKIP", "CONTENT", "FOOTER", "HEADER", 
    "OVERVIEW", "PROJECTS", "PEOPLE", "PROFILE", "VIEW", "CONTACT", "SPOTLIGHT", 
    "EDITION", "JEWELS", "COLAR", "PAINTER", "GUIDE", "LOG", "REVIEW", "PDF"
}

# =========================================================
#             PART 1: AI WRAPPER
# =========================================================
def call_ai_api(prompt, provider, key):
    if not key: return None
    headers = {"Content-Type": "application/json"}
    try:
        if "Gemini" in provider:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200: return resp.json()['candidates'][0]['content']['parts'][0]['text']
        elif "OpenAI" in provider:
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {"model": "gpt-4o", "messages": [{"role": "system", "content": "JSON Extractor"}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200: return resp.json()['choices'][0]['message']['content']
        elif "Anthropic" in provider:
            url = "https://api.anthropic.com/v1/messages"
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
            payload = {"model": "claude-3-5-sonnet-20241022", "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]}
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200: return resp.json()['content'][0]['text']
        elif "DeepSeek" in provider:
            url = "https://api.deepseek.com/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {"model": "deepseek-chat", "messages": [{"role": "system", "content": "JSON Extractor"}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200: return resp.json()['choices'][0]['message']['content']
    except: return None
    return None

def clean_json_response(text):
    if not text: return "{}"
    text = re.sub(r'```json', '', text)
    text = re.sub(r'```', '', text)
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match: return match.group(0)
        return text
    except: return text

# =========================================================
#             PART 2: HELPERS & DB
# =========================================================
def normalize_token(s: str) -> str:
    if not s: return ""
    return "".join(ch for ch in unidecode(str(s).strip().upper()) if "A" <= ch <= "Z")

def clean_html_for_ai(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup(["script", "style", "svg", "noscript", "img", "iframe", "footer"]):
        element.decompose()
    return str(soup)[:500000]

def clean_extracted_name(raw_text):
    if not isinstance(raw_text, str): return None
    upper = raw_text.upper()
    junk = ["RESULTS FOR", "SEARCH", "WEBSITE", "EDITION", "JEWELS", "SPOTLIGHT", "EXPERIENCE", "CALCULATION", "WAGE", "LIVING", "GOING", "FAST", "GUIDE", "LOG", "REVIEW"]
    if any(j in upper for j in junk): return None
    if ":" in raw_text: raw_text = raw_text.split(":")[-1]
    clean = re.split(r'[|,\-–—»\(\)]', raw_text)[0]
    clean = " ".join(clean.split())
    if len(clean.split()) > 5 or len(clean) < 3: return None
    return clean.strip()

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
except: st.error("IBGE Error"); st.stop()

# =========================================================
#             PART 3: CORE LOGIC (RESTORED FROM REFERENCE)
# =========================================================

def agent_learn_pattern(html_content, current_url, provider, key, task_type="PAGINATION"):
    if len(html_content) < 500: return None
    
    if task_type == "SEARCH_BOX":
        task_desc = "2. Identify CSS for SEARCH INPUT.\n3. Identify CSS for SUBMIT BUTTON."
        json_desc = '"search_input": "input[name=q]", "search_button": "button[type=submit]"'
    else:
        # **RESTORED: Specific Prompt for Navigation**
        task_desc = """
        2. Identify the CSS Selector for the "Next Page" CLICKABLE ELEMENT.
           - It might be an <a> tag (link).
           - It might be an <input type="submit"> or <button> inside a form.
        """
        json_desc = '"next_element": "a.next"'

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
      }}
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
    """
    **RESTORED:** Form-Aware Extractor. Can find Links OR submit Forms.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    extracted_names = []
    
    if selectors.get("name_element"):
        elements = soup.select(selectors["name_element"])
        for el in elements:
            clean = clean_extracted_name(el.get_text(" ", strip=True))
            if clean: extracted_names.append(clean)
            
    # Navigation Extraction logic from your reference code
    nav_result = {"next_url": None, "form_data": None, "type": "NONE"}
    
    # Check for next element
    next_selector = selectors.get("next_element") or selectors.get("next_link")
    
    if next_selector:
        element = soup.select_one(next_selector)
        if element:
            # CASE A: It's a Link
            if element.name == "a" and element.get("href"):
                nav_result["type"] = "LINK"
                nav_result["next_url"] = element.get("href")
            
            # CASE B: It's a Button/Input (Form)
            elif element.name in ["input", "button"]:
                parent_form = element.find_parent("form")
                if parent_form:
                    nav_result["type"] = "FORM"
                    form_data = {}
                    # Scrape inputs
                    for inp in parent_form.find_all("input"):
                        if inp.get("name"): 
                            form_data[inp.get("name")] = inp.get("value", "")
                    # Add button itself
                    if element.get("name"):
                        form_data[element.get("name")] = element.get("value", "")
                    
                    nav_result["form_data"] = form_data
                    if parent_form.get("action"): 
                        nav_result["next_url"] = parent_form.get("action")

    return {"names": extracted_names, "nav": nav_result}

def match_names_detailed(names, source):
    found = []
    seen = set()
    for n in names:
        parts = n.strip().split()
        if len(parts) < 2: continue
        f, l = normalize_token(parts[0]), normalize_token(parts[-1])
        if l in BLOCKLIST_SURNAMES or f in BLOCKLIST_SURNAMES: continue
        
        rank_f = first_name_ranks.get(f, 0)
        rank_l = surname_ranks.get(l, 0)
        score = 0
        if rank_f > 0: score += 50
        if rank_l > 0: score += 50
        
        if score > 0:
            found.append({"Full Name": n, "Brazil Score": score, "Match Type": "Strong", "Source": source})
            seen.add(n)
    return found

def ai_janitor_clean_names(raw_list, provider, key):
    if not raw_list or not key: return []
    clean_results = []
    chunk_size = 30
    prog = st.progress(0)
    for i in range(0, len(raw_list), chunk_size):
        batch = raw_list[i:i + chunk_size]
        prompt = f"""You are a Data Cleaner. Extract HUMAN NAMES. Fix spacing. Remove titles/junk.
        INPUT: {json.dumps(batch)}
        RETURN JSON: {{ "cleaned_names": ["Name 1", "Name 2"] }}"""
        try:
            resp_text = call_ai_api(prompt, provider, key)
            if not resp_text: 
                clean_results.extend(batch); continue
            data = json.loads(clean_json_response(resp_text))
            if isinstance(data, dict) and "cleaned_names" in data: clean_results.extend(data["cleaned_names"])
            elif isinstance(data, list): clean_results.extend(data)
            else: clean_results.extend(batch)
        except: clean_results.extend(batch)
        prog.progress(min((i+chunk_size)/len(raw_list), 1.0))
    return list(set(clean_results))

# =========================================================
#             PART 4: MAIN EXECUTION
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", placeholder="https://directory.example.com")
max_pages = c2.number_input("Max Pages", 1, 500, 10)

st.write("---")
mode = st.radio("Mode:", ["Classic Directory (Native/Fast)", "Infinite Scroller (Selenium)", "Active Search Injection (Brute Force Surnames)"])

run_headless = True
# Driver setup only for needed modes
if "Search" in mode or "Infinite" in mode:
    if not HAS_SELENIUM: st.error("Selenium Missing"); st.stop()
    run_headless = st.checkbox("Headless Mode", value=True)

def get_driver(headless=True):
    options = Options()
    if headless or os.name == 'posix': options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    if os.name == 'posix': return webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)
    return webdriver.Chrome(service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()), options=options)

def fetch_selenium(driver, url, scroll_count=0):
    driver.get(url); time.sleep(3)
    if scroll_count > 0:
        for _ in range(scroll_count):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);"); time.sleep(1.5)
    return driver.page_source

def fetch_native(session, url, method="GET", data=None):
    try: return session.post(url, data=data, timeout=15) if method == "POST" else session.get(url, timeout=15)
    except: return None

if st.button("🚀 Start Mission", type="primary"):
    st.session_state.running = True

if st.session_state.running:
    if not api_key: st.error("Missing API Key"); st.stop()
    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()
    all_matches = []
    learned_selectors = None
    
    # ------------------------------------------------------------------
    # BRANCH A: CLASSIC & INFINITE SCROLL
    # ------------------------------------------------------------------
    if "Search Injection" not in mode:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://google.com"})
        
        driver = get_driver(headless=run_headless) if "Infinite" in mode else None
        if "Infinite" in mode and not driver: status_log.error("Driver Failed"); st.stop()
        
        current_url = start_url
        next_method, next_data = "GET", None
        visited_fps = set()
        
        for page in range(1, max_pages + 1):
            if not st.session_state.running: break
            status_log.update(label=f"Scanning Page {page}...", state="running")
            
            raw_html = None
            try:
                if "Classic" in mode:
                    r = fetch_native(session, current_url, next_method, next_data)
                    if r and r.status_code == 200: raw_html = r.text
                else:
                    raw_html = fetch_selenium(driver, current_url, scroll_count=3)
            except: pass
            
            if not raw_html: break
            
            names = []
            nav_data = {}
            ai_needed = True
            
            # 1. FAST MODE (Template)
            if learned_selectors:
                status_log.write("⚡ Using Fast Template")
                fast_res = fast_extract_mode(raw_html, learned_selectors)
                names = fast_res["names"]
                nav_data = fast_res["nav"]
                
                # Validation: If template returns valid data, we skip AI
                if len(names) > 0 and nav_data["type"] != "NONE":
                    ai_needed = False
                    
                    # Apply Navigation
                    if nav_data["type"] == "LINK":
                        l = nav_data["next_url"]
                        current_url = urljoin(current_url, l) if "http" not in l else l
                        next_method, next_data = "GET", None
                        status_log.write(f"🔗 Fast Link: {l}")
                    elif nav_data["type"] == "FORM":
                        form_data = nav_data["form_data"]
                        next_method = "POST"
                        next_data = form_data
                        
                        # Handle Action URL
                        if nav_data.get("next_url"):
                            act = nav_data["next_url"]
                            current_url = urljoin(current_url, act) if "http" not in act else act
                        
                        status_log.write("📝 Fast Form Data Extracted.")
                elif len(names) == 0:
                    status_log.warning("⚠️ Template found 0 names. Re-learning...")
                    ai_needed = True
            
            # 2. AI TEACHER
            if ai_needed:
                if not learned_selectors: status_log.write(f"🧠 {ai_provider.split()[0]} Analyzing Page Structure...")
                data = agent_learn_pattern(raw_html, current_url, ai_provider, api_key, "PAGINATION")
                
                if data:
                    names = data.get("names", [])
                    selectors = data.get("selectors", {})
                    
                    if selectors.get("name_element"): 
                        learned_selectors = selectors
                        status_log.success(f"🎓 Pattern Learned: {selectors['name_element']}")
                    
                    # Re-run extraction to get nav data using the new selectors
                    fast_res = fast_extract_mode(raw_html, selectors)
                    nav_data = fast_res["nav"] # Use the robust extractor for nav
                
                else:
                    status_log.error("❌ AI failed to read page.")
                    
            # MATCHING
            if names:
                matches = match_names_detailed(names, f"Page {page}")
                if matches:
                    all_matches.extend(matches)
                    table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
                    status_log.write(f"✅ Found {len(matches)} matches.")
            else:
                status_log.write("🤷 No matches.")

            # NAVIGATION FALLBACK (If AI analyzed but Fast Mode failed to apply nav)
            if ai_needed and "Classic" in mode:
                ntype = nav_data.get("type", "NONE")
                if ntype == "LINK" and nav_data.get("next_url"):
                    l = nav_data["next_url"]
                    current_url = urljoin(current_url, l) if "http" not in l else l
                    next_method, next_data = "GET", None
                    status_log.write(f"🔗 AI Found Link: {l}")
                elif ntype == "FORM":
                    form = nav_data.get("form_data", {})
                    if form:
                        next_method = "POST"
                        next_data = form
                        fp = str(form)
                        if fp in visited_fps: break
                        visited_fps.add(fp)
                        status_log.write("📝 AI Found Form.")
                    else: break
                else:
                    status_log.info("🏁 No more pages detected.")
                    break
            
            time.sleep(1.5)
        
        if driver: driver.quit()

    # ------------------------------------------------------------------
    # BRANCH B: SEARCH INJECTION (UNCHANGED)
    # ------------------------------------------------------------------
    else:
        driver = get_driver(headless=run_headless)
        if not driver: st.stop()
        try: driver.get(start_url); time.sleep(5)
        except: st.stop()
        
        # 1. Find Search Box
        sel_input = manual_search_selector
        if not sel_input:
            data = agent_learn_pattern(driver.page_source, start_url, ai_provider, api_key, "SEARCH_BOX")
            if data and data.get("selectors", {}).get("search_input"):
                sel_input = data["selectors"]["search_input"]
        
        # Fallback
        if not sel_input:
            for f in ["input[type='search']", "input[name='q']", "input[name='query']", "input[aria-label='Search']"]:
                if len(driver.find_elements(By.CSS_SELECTOR, f)) > 0: sel_input = f; break
        
        if not sel_input: st.error("No Search Box"); driver.quit(); st.stop()
        status_log.success(f"🎯 Target: {sel_input}")

        for i, surname in enumerate(sorted_surnames[:max_pages]):
            if not st.session_state.running: break
            status_log.update(label=f"🔎 Checking '{surname}' ({i+1}/{max_pages})", state="running")
            
            try:
                inp = driver.find_element(By.CSS_SELECTOR, sel_input)
                driver.execute_script(f"arguments[0].value = '{surname}';", inp)
                inp.send_keys(Keys.RETURN)
                time.sleep(search_delay)
                
                soup = BeautifulSoup(driver.page_source, "html.parser")
                raw_names = []
                
                # Heuristics for Results (Search results are usually headings or links)
                for tag in ["h3", "h4", "h2", ".result-title", "a"]:
                    for el in soup.select(tag):
                        clean = clean_extracted_name(el.get_text(" ", strip=True))
                        if clean: raw_names.append(clean)
                
                raw_names = list(set(raw_names))
                if raw_names:
                    matches = match_names_detailed(raw_names, f"Search: {surname}")
                    if matches:
                        all_matches.extend(matches)
                        table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
                        status_log.write(f"✅ Found {len(matches)} results.")
                
                try: driver.execute_script("window.history.go(-1)"); time.sleep(2)
                except: driver.get(start_url); time.sleep(2)
            except: driver.get(start_url); time.sleep(2)
            
        driver.quit()

    # --- BATCH CLEANING ---
    if use_ai_cleaning and all_matches:
        status_log.write(f"🧹 AI Cleaning {len(all_matches)} items...")
        raw = list(set([m["Full Name"] for m in all_matches]))
        clean = ai_janitor_clean_names(raw, ai_provider, api_key)
        if clean:
            all_matches = match_names_detailed(clean, "Batch Processed")
            table_placeholder.dataframe(pd.DataFrame(all_matches), height=500)

    status_log.update(label="Done!", state="complete")
    st.session_state.running = False
    
    if all_matches:
        df = pd.DataFrame(all_matches)
        c1, c2 = st.columns(2)
        with c1: st.download_button("📥 CSV", df.to_csv(index=False).encode('utf-8'), "results.csv")
        with c2: 
            try:
                b = io.BytesIO()
                with pd.ExcelWriter(b, engine='xlsxwriter') as w: df.to_excel(w, index=False)
                st.download_button("📥 Excel", b, "results.xlsx")
            except: pass
