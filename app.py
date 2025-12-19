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

if "running" not in st.session_state: st.session_state.running = False

# --- SIDEBAR: AI BRAIN ---
st.sidebar.header("🧠 AI Brain")
ai_provider = st.sidebar.selectbox(
    "Choose your Model:",
    ["Google Gemini (Flash 2.0)", "OpenAI (GPT-4o)", "Anthropic (Claude 3.5)", "DeepSeek (V3)"]
)
api_key = st.sidebar.text_input(f"Enter {ai_provider.split()[0]} API Key", type="password")

st.sidebar.markdown("---")
# --- GLOBAL SETTINGS ---
search_delay = st.sidebar.slider("⏳ Search/Scroll Wait Time (Sec)", 5, 60, 15, help="Time to wait for results.")
use_ai_cleaning = st.sidebar.checkbox("✨ Batch AI Cleaning", value=True, help="Wait until the end to clean all names in one go.")

# --- DEBUG & MANUAL OVERRIDES ---
with st.sidebar.expander("🛠️ Advanced / Debug"):
    manual_search_selector = st.text_input("Manual Search Box Selector", placeholder="e.g. input[name='q']")
    manual_name_selector = st.text_input("Manual Name Selector", placeholder="e.g. h3 or div.alumni-name")

if st.sidebar.button("🛑 ABORT MISSION", type="primary"):
    st.session_state.running = False
    st.sidebar.warning("Mission Aborted.")
    st.stop()

# --- BLOCKLIST (Aggressive Filter) ---
BLOCKLIST_SURNAMES = {
    "WANG", "LI", "ZHANG", "LIU", "CHEN", "YANG", "HUANG", "ZHAO", "WU", "ZHOU", 
    "XU", "SUN", "MA", "ZHU", "HU", "GUO", "HE", "GAO", "LIN", "LUO", 
    "LIANG", "SONG", "TANG", "ZHENG", "HAN", "FENG", "DONG", "YE", "YU", "WEI", 
    "CAI", "YUAN", "PAN", "DU", "DAI", "JIN", "FAN", "SU", "MAN", "WONG", 
    "CHAN", "CHANG", "LEE", "KIM", "PARK", "CHOI", "NG", "HO", "CHOW", "LAU",
    "SINGH", "PATEL", "KUMAR", "SHARMA", "GUPTA", "ALI", "KHAN", "TRAN", "NGUYEN",
    # Junk Words
    "RESULTS", "WEBSITE", "SEARCH", "MENU", "SKIP", "CONTENT", "FOOTER", "HEADER", 
    "OVERVIEW", "PROJECTS", "PEOPLE", "PROFILE", "VIEW", "CONTACT", "SPOTLIGHT", 
    "EDITION", "JEWELS", "COLAR", "PAINTER", "GUIDE", "LOG", "REVIEW", "PDF",
    "CALCULATION", "EXPERIENCE", "WAGE", "LIVING", "GOING", "FAST", "ANTONY", "CLEOPATRA"
}

# =========================================================
#             PART 1: AI FUNCTIONS & HELPERS
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
            payload = {"model": "gpt-4o", "messages": [{"role": "system", "content": "You are a Data Cleaning Assistant. Return JSON only."}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
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
    except Exception as e: 
        st.error(f"API Call Failed: {e}")
        return None
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

def clean_extracted_name(raw_text):
    if not isinstance(raw_text, str): return None
    upper = raw_text.upper()
    junk_phrases = [
        "RESULTS FOR", "SEARCH", "WEBSITE", "EDITION", "JEWELS", "SPOTLIGHT",
        "EXPERIENCE IN", "CALCULATION FOR", "LIVING WAGE", "GOING FAST", 
        "ANTONY AND", "GUIDE TO", "LOG OF", "REVIEW OF", "MENU", "SKIP TO", 
        "CONTENT", "FOOTER", "HEADER", "OVERVIEW", "PROJECTS", "PEOPLE",
        "PROFILE", "VIEW", "CONTACT"
    ]
    if any(phrase in upper for phrase in junk_phrases): return None
    if ":" in raw_text: raw_text = raw_text.split(":")[-1]
    clean = re.split(r'[|,\-–—»\(\)]', raw_text)[0]
    clean = " ".join(clean.split())
    if len(clean.split()) > 5: return None 
    if len(clean) < 3: return None
    return clean.strip()

def ai_janitor_clean_names(raw_list, provider, key):
    if not raw_list or not key: return []
    clean_results = []
    chunk_size = 30
    progress_bar = st.progress(0)
    
    for i in range(0, len(raw_list), chunk_size):
        batch = raw_list[i:i + chunk_size]
        prompt = f"""
        You are a Data Cleaning Expert.
        INPUT LIST: {json.dumps(batch)}
        RULES:
        1. Extract ONLY valid PERSON names. 
        2. DELETE entries that are products, titles, or junk.
        3. Remove prefixes/suffixes.
        4. Fix spacing.
        RETURN JSON: {{ "cleaned_names": ["Name 1", "Name 2"] }}
        """
        try:
            resp_text = call_ai_api(prompt, provider, key)
            if not resp_text: 
                clean_results.extend(batch)
                continue
            clean_text = clean_json_response(resp_text)
            data = json.loads(clean_text)
            if isinstance(data, dict) and "cleaned_names" in data:
                clean_results.extend(data["cleaned_names"])
            elif isinstance(data, list):
                clean_results.extend(data)
            else:
                clean_results.extend(batch)
        except:
            clean_results.extend(batch)
        progress_bar.progress(min((i + chunk_size) / len(raw_list), 1.0))
            
    return list(set(clean_results))

# =========================================================
#             PART 2: DATA LOADING
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
except Exception as e:
    st.error(f"IBGE API Error: {e}")
    st.stop()

# =========================================================
#             PART 3: DRIVERS
# =========================================================
def fetch_native(session, url, method="GET", data=None):
    try:
        if method == "POST": return session.post(url, data=data, timeout=15)
        return session.get(url, timeout=15)
    except: return None

def get_driver(headless=True):
    if not HAS_SELENIUM: return None
    options = Options()
    if headless or os.name == 'posix': options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    try:
        if os.name == 'posix':
            return webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)
        else:
            return webdriver.Chrome(service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()), options=options)
    except Exception as e:
        st.error(f"❌ Driver Error: {e}")
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
        2. Identify CSS for SEARCH INPUT.
        3. Identify CSS for SUBMIT BUTTON.
        """
        json_desc = '"search_input": "input[name=q]", "search_button": "button[type=submit]"'
    else:
        # Full Prompt for Navigation
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
        for el in elements:
            clean = clean_extracted_name(el.get_text(" ", strip=True))
            if clean: extracted_names.append(clean)
            
    # Full Navigation Logic (Restored)
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
    seen = set()
    for n in names:
        clean_n = clean_extracted_name(n)
        if not clean_n or clean_n in seen: continue
        parts = clean_n.strip().split()
        if len(parts) < 2: continue 
        f, l = normalize_token(parts[0]), normalize_token(parts[-1])
        if l in BLOCKLIST_SURNAMES or f in BLOCKLIST_SURNAMES: continue
        rank_f = first_name_ranks.get(f, 0)
        rank_l = surname_ranks.get(l, 0)
        score = 0
        if rank_f > 0: score += ((limit_first - rank_f)/limit_first)*50
        if rank_l > 0: score += ((limit_surname - rank_l)/limit_surname)*50
        if score > 0:
            m_type = "Strong" if (rank_f > 0 and rank_l > 0) else ("First Only" if rank_f > 0 else "Surname Only")
            found.append({"Full Name": clean_n, "Brazil Score": round(score, 1), "Match Type": m_type, "Source": source})
            seen.add(clean_n)
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
    
    # ------------------------------------------------------------------
    # BRANCH A: CLASSIC & INFINITE SCROLL (HEURISTICS + FULL AI FALLBACK)
    # ------------------------------------------------------------------
    if "Search Injection" not in mode:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        driver = get_driver(headless=run_headless) if "Infinite" in mode else None
        if "Infinite" in mode and not driver: status_log.error("Driver Failed"); st.stop()
        
        current_url = start_url
        # Initialize navigation variables
        next_method, next_data = "GET", None
        visited_fps = set()
        
        for page in range(1, max_pages + 1):
            if not st.session_state.running: break
            status_log.update(label=f"Scanning Page {page}...", state="running")
            
            raw_html = None
            try:
                if "Classic" in mode:
                    # Uses next_method and next_data for recursion
                    r = fetch_native(session, current_url, next_method, next_data)
                    if r and r.status_code == 200: raw_html = r.text
                else:
                    raw_html = fetch_selenium(driver, current_url, 3)
            except: pass
            
            if not raw_html: break
            
            names = []
            nav_data = {}
            ai_needed = False
            
            # 1. TEMPLATE MODE (If we learned from AI previously)
            if learned_selectors:
                status_log.write("⚡ Using Fast Template")
                fast_res = fast_extract_mode(raw_html, learned_selectors)
                names = fast_res["names"]
                nav_data = fast_res["nav"]
                
                # If template fails suddenly (0 names), retry with AI
                if len(names) == 0:
                    status_log.warning("⚠️ Template failed. Retrying with AI...")
                    ai_needed = True
            else:
                ai_needed = True

            # 2. VACUUM MODE (Heuristic check before AI)
            if ai_needed:
                status_log.write("🧹 Running Vacuum (Dumb Scraper)...")
                soup = BeautifulSoup(raw_html, "html.parser")
                raw_set = set()
                for tag in ["h3", "h4", "h2", "h5", "li", "tr", "div.name", "a", "strong", "b"]:
                    for el in soup.select(tag):
                        txt = el.get_text(" ", strip=True)
                        if 5 < len(txt) < 40 and len(txt.split()) in [2,3,4]:
                            clean = clean_extracted_name(txt)
                            if clean: raw_set.add(clean)
                
                # If Vacuum found plenty of names (>5), we might skip AI
                # BUT we still need AI for navigation if it's Page 1
                if len(raw_set) > 5 and page > 1:
                    names = list(raw_set)
                    ai_needed = False
                    status_log.success(f"🧹 Vacuum found {len(names)} names. Skipping AI.")
                else:
                    # On Page 1, or if vacuum failed, we force AI to learn structure + navigation
                    names = list(raw_set) # Keep what we found
            
            # 3. AI TEACHER (The Fallback)
            if ai_needed:
                status_log.write(f"🧠 {ai_provider.split()[0]} Analyzing Page Structure...")
                data = agent_analyze_page(raw_html, current_url, ai_provider, api_key, "PAGINATION")
                
                if data:
                    ai_names = data.get("names", [])
                    selectors = data.get("selectors", {})
                    nav_data = data.get("navigation", {})
                    
                    if selectors.get("name_element"): 
                        learned_selectors = selectors
                        status_log.success(f"🎓 Learned Selector: {selectors['name_element']}")
                    
                    # Merge AI names with Vacuum names
                    names = list(set(names + ai_names))
            
            # --- PROCESS NAMES ---
            if names:
                matches = match_names_detailed(names, f"Page {page}")
                if matches:
                    all_matches.extend(matches)
                    table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
                    status_log.write(f"✅ Found {len(matches)} matches.")
            else:
                status_log.write("🤷 Empty page.")

            # --- NAVIGATION LOGIC (Restored) ---
            if "Classic" in mode:
                ntype = nav_data.get("type", "NONE")
                if ntype == "LINK" and nav_data.get("url"):
                    l = nav_data["url"]
                    current_url = urljoin(current_url, l) if "http" not in l else l
                    next_method, next_data = "GET", None
                    status_log.write(f"🔗 Next Page: {l}")
                elif ntype == "FORM":
                    form = nav_data.get("form_data", {})
                    if form:
                        next_method = "POST"
                        next_data = form
                        fp = str(form)
                        if fp in visited_fps: 
                            status_log.warning("⚠️ Loop detected. Stopping.")
                            break
                        visited_fps.add(fp)
                        status_log.write("📝 Posting Form Data for Next Page.")
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
        
        # 1. Find Search Box (Heuristic First)
        sel_input = manual_search_selector
        if not sel_input:
            for f in ["input[type='search']", "input[name='q']", "input[name='query']", "input[aria-label='Search']"]:
                if len(driver.find_elements(By.CSS_SELECTOR, f)) > 0:
                    sel_input = f; status_log.success(f"🎯 Target Locked (Heuristic): {sel_input}"); break
        
        # 2. AI Fallback for Search Box
        if not sel_input:
            status_log.write("⚠️ Standard input not found. Asking AI...")
            data = agent_analyze_page(driver.page_source, start_url, ai_provider, api_key, "SEARCH_BOX")
            if data and data.get("selectors", {}).get("search_input"):
                sel_input = data["selectors"]["search_input"]
        
        if not sel_input: st.error("No Search Box"); driver.quit(); st.stop()

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
                
                # Heuristics for Results
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
