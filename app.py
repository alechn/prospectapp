import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import random
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
st.caption("Powered by Multi-Model AI • Search Injection • CAPTCHA Pausing")

# --- AI PROVIDER ---
st.sidebar.header("🧠 AI Brain")
ai_provider = st.sidebar.selectbox(
    "Choose your Model:",
    ["Google Gemini (Flash 2.0)", "OpenAI (GPT-4o)", "Anthropic (Claude 3.5)", "DeepSeek (V3)"]
)
api_key = st.sidebar.text_input(f"Enter {ai_provider.split()[0]} API Key", type="password")

# =========================================================
#             PART 1: UNIVERSAL AI ADAPTER
# =========================================================
def call_ai_api(prompt, provider, key):
    if not key: return None
    headers = {"Content-Type": "application/json"}
    try:
        if "Gemini" in provider:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code == 200: return resp.json()['candidates'][0]['content']['parts'][0]['text']
        elif "OpenAI" in provider:
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {"model": "gpt-4o", "messages": [{"role": "system", "content": "JSON Extractor"}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code == 200: return resp.json()['choices'][0]['message']['content']
        elif "Anthropic" in provider:
            url = "https://api.anthropic.com/v1/messages"
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
            payload = {"model": "claude-3-5-sonnet-20241022", "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]}
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code == 200: return resp.json()['content'][0]['text']
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

BLOCKLIST_SURNAMES = {"WANG", "LI", "ZHANG", "LIU", "CHEN", "YANG", "HUANG", "ZHAO", "WU", "ZHOU", "KIM", "LEE", "PARK", "SINGH", "PATEL", "NGUYEN"}

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
limit_first = st.sidebar.number_input("DB: Common First Names", 100, 20000, 3000)
limit_surname = st.sidebar.number_input("DB: Common Surnames", 100, 20000, 3000)

try:
    first_name_ranks, surname_ranks = fetch_ibge_data(limit_first, limit_surname)
    # SORT SURNAMES BY RANK (Most common first)
    sorted_surnames = sorted(surname_ranks.keys(), key=lambda k: surname_ranks[k])
    st.sidebar.success(f"✅ DB Loaded: {len(sorted_surnames)} surnames ready for injection.")
except: st.stop()

# =========================================================
#             PART 3: DRIVER & CAPTCHA UTILS
# =========================================================
def get_driver(headless=True):
    if not HAS_SELENIUM: return None
    options = Options()
    if headless:
        options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled") # Hide Robot Status
    return webdriver.Chrome(service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()), options=options)

def check_for_captcha(driver):
    """Simple check for common CAPTCHA text."""
    src = driver.page_source.lower()
    if "captcha" in src or "verify you are human" in src or "challenge" in src:
        return True
    return False

# =========================================================
#             PART 4: AI LOGIC (Search Aware)
# =========================================================
def agent_analyze_structure(html_content, current_url, provider, key, mode):
    if len(html_content) < 500: return None
    
    # Custom prompt based on mode
    task_prompt = "2. Identify the 'Next Page' CLICKABLE ELEMENT (Link or Button)."
    if mode == "Active Search Injection (Brute Force)":
        task_prompt = "2. Identify the CSS Selector for the SEARCH INPUT BOX (for names).\n3. Identify the CSS Selector for the SEARCH SUBMIT BUTTON."

    prompt = f"""
    You are a web scraping expert. Analyze the HTML from {current_url}.
    
    1. Identify the CSS Selector for NAMES (alumni/people).
    {task_prompt}
    
    Return JSON:
    {{
      "names": ["Name 1", "Name 2"],
      "selectors": {{
         "name_element": "e.g. div.alumni-name",
         "next_element": "e.g. a.next-page",
         "search_input": "e.g. input[name='q']",
         "search_button": "e.g. button[type='submit']"
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

def match_names_detailed(names, source):
    found = []
    for n in names:
        parts = n.strip().split()
        if not parts: continue
        f, l = normalize_token(parts[0]), normalize_token(parts[-1])
        if l in BLOCKLIST_SURNAMES: continue
        
        rank_f, rank_l = first_name_ranks.get(f, 0), surname_ranks.get(l, 0)
        
        # Scoring
        score = 0
        if rank_f > 0: score += ((limit_first - rank_f)/limit_first)*50
        if rank_l > 0: score += ((limit_surname - rank_l)/limit_surname)*50
        
        if score > 0:
            found.append({
                "Full Name": n, "Brazil Score": round(score, 1),
                "Source": source
            })
    return found

# =========================================================
#             PART 5: MAIN INTERFACE
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", placeholder="https://directory.example.com")
max_cycles = c2.number_input("Max Search Cycles", 1, 500, 10)

st.write("---")
mode = st.radio("Scraping Strategy:", [
    "Classic Pagination (Read list page by page)",
    "Active Search Injection (Brute Force Top Surnames)"
])

# Toggle Headless Mode for CAPTCHA solving
run_headless = st.checkbox("Run in Background (Headless)", value=True, help="Uncheck this to see the browser popup. Useful if you need to solve a CAPTCHA manually.")

if st.button("🚀 Start Mission", type="primary"):
    if not api_key: st.error("Missing API Key"); st.stop()
    if not HAS_SELENIUM: st.error("Selenium required."); st.stop()

    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()
    all_matches = []
    
    # LAUNCH BROWSER
    driver = get_driver(headless=run_headless)
    status_log.write("🔧 Browser Launched")
    
    # STATE
    driver.get(start_url)
    time.sleep(3)
    
    learned_selectors = None
    
    # --- SEARCH INJECTION LOOP ---
    if "Active Search" in mode:
        # 1. Analyze Page ONCE to find Search Box
        status_log.write("🧠 Analyzing Search Form...")
        html = driver.page_source
        data = agent_analyze_structure(html, start_url, ai_provider, api_key, mode)
        
        if not data or not data.get("selectors", {}).get("search_input"):
            status_log.error("❌ Could not find search box. Is the page loaded?")
            driver.quit()
            st.stop()
            
        selectors = data["selectors"]
        search_selector = selectors["search_input"]
        btn_selector = selectors.get("search_button")
        
        status_log.success(f"🎯 Target Acquired: {search_selector}")
        
        # 2. Iterate Surnames
        for i, surname in enumerate(sorted_surnames[:max_cycles]):
            status_log.update(label=f"Searching for '{surname}' ({i+1}/{max_cycles})", state="running")
            
            try:
                # Find Input & Clear
                inp = driver.find_element(By.CSS_SELECTOR, search_selector)
                inp.clear()
                # Human-like typing delay
                for char in surname:
                    inp.send_keys(char)
                    time.sleep(random.uniform(0.05, 0.2))
                
                time.sleep(0.5)
                
                # Submit (Click button OR press Enter)
                if btn_selector:
                    try:
                        driver.find_element(By.CSS_SELECTOR, btn_selector).click()
                    except: inp.send_keys(Keys.RETURN)
                else:
                    inp.send_keys(Keys.RETURN)
                
                time.sleep(3) # Wait for results
                
                # CAPTCHA CHECK
                if check_for_captcha(driver):
                    status_log.warning("⚠️ CAPTCHA DETECTED!")
                    if run_headless:
                        status_log.error("Cannot solve CAPTCHA in Headless mode. Restart with 'Run in Background' unchecked.")
                        break
                    else:
                        st.warning("CAPTCHA detected in browser! Please solve it manually.")
                        time.sleep(15) # Wait for human
                
                # SCRAPE RESULTS
                # We reuse the "names" logic from the AI response logic here simply
                # Or use the learned "name_element" if we have it
                
                if not learned_selectors:
                    # Learn Name pattern on first successful result
                    res_data = agent_analyze_structure(driver.page_source, start_url, ai_provider, api_key, "Classic")
                    if res_data and res_data.get("selectors", {}).get("name_element"):
                        learned_selectors = res_data["selectors"]
                
                # Fast Extract
                if learned_selectors:
                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    els = soup.select(learned_selectors["name_element"])
                    names = [e.get_text(strip=True) for e in els]
                    
                    matches = match_names_detailed(names, f"Search: {surname}")
                    if matches:
                        all_matches.extend(matches)
                        all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                        table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
                        status_log.write(f"✅ '{surname}': Found {len(matches)} people.")
                    else:
                        status_log.write(f"🤷 '{surname}': No matches.")

                # GO BACK (Important!)
                driver.get(start_url)
                time.sleep(2)
                
            except Exception as e:
                status_log.error(f"Error searching {surname}: {e}")
                driver.get(start_url)
                
    # --- CLASSIC LOOP (Simplified for this example) ---
    else:
        # (The previous Pagination Logic would go here)
        status_log.write("Run the previous code for Pagination Mode.")

    driver.quit()
    status_log.update(label="Mission Complete!", state="complete")
    
    if all_matches:
        df = pd.DataFrame(all_matches)
        st.download_button("📥 Download CSV", df.to_csv(index=False).encode('utf-8'), "results.csv")
