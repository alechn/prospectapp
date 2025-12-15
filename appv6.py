
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
#             PART 0: CONFIGURATION & BLOCKLIST
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Powered by Gemini 2.5 Flash • Scoring & Ranking System")

# --- FILTER LIST ---
BLOCKLIST_SURNAMES = {
    "WANG", "LI", "ZHANG", "LIU", "CHEN", "YANG", "HUANG", "ZHAO", "WU", "ZHOU", 
    "XU", "SUN", "MA", "ZHU", "HU", "GUO", "HE", "GAO", "LIN", "LUO", 
    "LIANG", "SONG", "TANG", "ZHENG", "HAN", "FENG", "DONG", "YE", "YU", "WEI", 
    "CAI", "YUAN", "PAN", "DU", "DAI", "JIN", "FAN", "SU", "MAN", "WONG", 
    "CHAN", "CHANG", "LEE", "KIM", "PARK", "CHOI", "NG", "HO", "CHOW", "LAU",
    "SINGH", "PATEL", "KUMAR", "SHARMA", "GUPTA", "ALI", "KHAN", "TRAN", "NGUYEN"
}

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
#             PART 2: DATABASE & RANKS
# =========================================================
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
                    rank = i.get("rank", 0)
                    if n: data_map[n] = rank
                page += 1
            except: break
        return data_map
    return _fetch(IBGE_FIRST, limit_first), _fetch(IBGE_SURNAME, limit_surname)

st.sidebar.header("⚙️ Search Settings")
# INCREASED DEFAULT to ensure scoring works well (More names = Better relative ranking)
limit_first = st.sidebar.number_input("Common First Names", 10, 20000, 3000, 100)
limit_surname = st.sidebar.number_input("Common Surnames", 10, 20000, 3000, 100)

try:
    first_name_ranks, surname_ranks = fetch_ibge_data(limit_first, limit_surname)
    st.sidebar.success(f"✅ DB Loaded: {len(first_name_ranks)} Firsts / {len(surname_ranks)} Surnames")
except Exception as e:
    st.error(f"IBGE Error: {e}")
    st.stop()

# =========================================================
#             PART 3: ENGINES
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
#             PART 4: INTELLIGENCE
# =========================================================

def agent_learn_pattern(html_content, current_url):
    if not api_key: return None
    if len(html_content) < 500: return None

    prompt = f"""
    You are a web scraping expert. Analyze the HTML from {current_url}.
    
    1. Identify the CSS Selector for NAMES.
    2. Identify the CSS Selector for the "Next Page" CLICKABLE ELEMENT.
    3. CHECK PAGINATION: Look for text like "Page 1 of 50". Extract TOTAL NUMBER of pages.
    
    Return JSON:
    {{
      "names": ["Name 1", "Name 2"],
      "total_pages": 74 (or null if unknown),
      "selectors": {{
         "name_element": "e.g. div.alumni-name",
         "next_element": "e.g. input[value='Next'] or a.next-link"
      }},
      "navigation": {{ "type": "LINK" or "FORM", "url": "...", "form_data": {{...}} }}
    }}
    
    HTML:
    {clean_html_for_ai(html_content)} 
    """
    
    for _ in range(2): 
        try:
            model = genai.GenerativeModel('gemini-2.5-flash-lite', generation_config={"response_mime_type": "application/json"})
            response = model.generate_content(prompt)
            if not response.parts: continue
            return json.loads(clean_json_response(response.text))
        except: time.sleep(1)
    return None

def fast_extract_mode(html_content, selectors):
    soup = BeautifulSoup(html_content, "html.parser")
    extracted_names = []
    
    if selectors.get("name_element"):
        elements = soup.select(selectors["name_element"])
        for el in elements:
            extracted_names.append(el.get_text(strip=True))
            
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
                        if inp.get("name"):
                            form_data[inp.get("name")] = inp.get("value", "")
                    if element.get("name"):
                        form_data[element.get("name")] = element.get("value", "")
                    nav_result["form_data"] = form_data
                    if parent_form.get("action"):
                        nav_result["next_url"] = parent_form.get("action")

    return {"names": extracted_names, "nav": nav_result}

# --- NEW: SCORING LOGIC ---
def calculate_score(rank, limit):
    """
    Returns points (0-50) based on rank.
    Rank 1 = 50 pts. Rank Limit = 0 pts. Not Found = 0 pts.
    """
    if rank == 0: return 0
    # Inverse score: (Limit - Rank) / Limit
    # e.g. Limit 2000, Rank 1 -> (1999/2000) * 50 = 49.9 pts
    score = ((limit - rank) / limit) * 50
    return max(0, score)

def match_names_detailed(names, page_label):
    found = []
    for n in names:
        parts = n.strip().split()
        if not parts: continue
        f, l = normalize_token(parts[0]), normalize_token(parts[-1])
        
        # Blocklist Filter
        if l in BLOCKLIST_SURNAMES: continue
        
        # Get Ranks
        rank_f = first_name_ranks.get(f, 0)
        rank_l = surname_ranks.get(l, 0)
        
        # Calculate Scores
        score_f = calculate_score(rank_f, limit_first)
        score_l = calculate_score(rank_l, limit_surname)
        
        total_score = round(score_f + score_l, 1)
        
        if total_score > 0: # If at least one part matched
            if rank_f > 0 and rank_l > 0: m_type = "Strong"
            elif rank_f > 0: m_type = "First Name Only"
            else: m_type = "Surname Only"
            
            found.append({
                "Full Name": n, 
                "Brazil Score": total_score, # 0-100 Score
                "Match Type": m_type,
                "First Rank": rank_f if rank_f > 0 else "N/A",
                "Surname Rank": rank_l if rank_l > 0 else "N/A",
                "Source": page_label
            })
    return found

# =========================================================
#             PART 5: MAIN INTERFACE
# =========================================================

st.markdown("### 🤖 Auto-Pilot Control Center")
col1, col2 = st.columns([3, 1])
with col1:
    start_url = st.text_input("Target URL", placeholder="https://legacy.cs.stanford.edu/directory/masters-alumni")
with col2:
    max_pages = st.number_input("Max Pages (Safety Limit)", 1, 500, 100)

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
    
    learned_selectors = None
    detected_max_pages = None 
    
    if "Infinite" in mode:
        driver = get_driver()
        status_log.write("🔧 Browser Launched")

    page = 0
    while page < max_pages:
        page += 1
        
        if detected_max_pages and page > detected_max_pages:
            status_log.write(f"🛑 Reached detected last page ({detected_max_pages}). Stopping.")
            break
            
        status_log.update(label=f"Scanning Page {page}...", state="running")
        
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

        names = []
        nav_data = {}
        ai_required = True 
        
        if learned_selectors:
            status_log.write(f"⚡ Fast Template Active")
            fast_data = fast_extract_mode(raw_html, learned_selectors)
            names = fast_data["names"]
            fast_nav = fast_data["nav"]
            
            if len(names) > 0 and fast_nav["type"] != "NONE":
                ai_required = False
                if fast_nav["type"] == "LINK":
                    l = fast_nav["next_url"]
                    if "http" not in l: current_url = urljoin(current_url, l)
                    else: current_url = l
                    next_method = "GET"
                    next_data = None
                    status_log.write(f"🔗 Fast Link: {l}")
                elif fast_nav["type"] == "FORM":
                    f_data = fast_nav["form_data"]
                    next_method = "POST"
                    next_data = f_data
                    if fast_nav.get("next_url"):
                        act = fast_nav["next_url"]
                        if "http" not in act: current_url = urljoin(current_url, act)
                        else: current_url = act
                    status_log.write(f"📝 Fast Form Extracted.")
            elif len(names) == 0:
                 status_log.warning("⚠️ Template found 0 names. Re-learning...")
                 ai_required = True
            else:
                 status_log.warning("⚠️ Template lost navigation. Waking AI...")
                 ai_required = True

        if ai_required:
            if not learned_selectors: status_log.write(f"🧠 AI Analyzing Page Structure...")
            
            data = agent_learn_pattern(raw_html, current_url)
            
            if data:
                names = data.get("names", [])
                selectors = data.get("selectors", {})
                nav_data = data.get("navigation", {})
                
                if not detected_max_pages and data.get("total_pages"):
                    try:
                        detected_max_pages = int(data["total_pages"])
                        status_log.success(f"🎯 AI Detected Total Pages: {detected_max_pages}. Adjusting limit.")
                    except: pass
                
                if selectors.get("name_element"):
                    learned_selectors = selectors
                    status_log.write(f"🎓 Pattern Learned: {selectors['name_element']}")
            else:
                status_log.error("❌ AI failed to read page.")
                break

        # MATCHING
        new_matches = match_names_detailed(names, f"Page {page}")
        if new_matches:
            all_matches.extend(new_matches)
            # Sort by Brazil Score DESCENDING
            all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
            status_log.write(f"✅ Found {len(new_matches)} matches.")
            table_placeholder.dataframe(pd.DataFrame(all_matches), height=300)
        else:
            status_log.write("🤷 No matches found.")

        # NAVIGATION
        if ai_required and "Classic" in mode:
            ntype = nav_data.get("type", "NONE")
            if ntype == "LINK" and nav_data.get("url"):
                l = nav_data["url"]
                if "http" not in l: current_url = urljoin(current_url, l)
                else: current_url = l
                next_method = "GET"
                next_data = None
                status_log.write(f"🔗 AI Found Link: {l}")
            elif ntype == "FORM":
                form = nav_data.get("form_data", {})
                if form:
                    next_method = "POST"
                    next_data = form
                    fp = str(form)
                    if fp in visited_fingerprints: status_log.warning("Loop ended."); break
                    visited_fingerprints.add(fp)
                    status_log.write(f"📝 AI Found Form.")
                else: break
            else:
                status_log.write("🏁 AI sees no next page.")
                break
        elif ai_required and "Infinite" in mode:
             status_log.write("🏁 Scroll done.")
             break
        
        time.sleep(2)

    if driver: driver.quit()
    status_log.update(label="Complete!", state="complete")
    
    if all_matches:
        df = pd.DataFrame(all_matches)
        c1, c2 = st.columns(2)
        with c1: st.download_button("📥 Download CSV", df.to_csv(index=False).encode('utf-8'), "brazilian_alumni.csv")
        with c2: 
            try:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False, sheet_name='Sheet1')
                st.download_button("📥 Download Excel", buffer, "brazilian_alumni.xlsx")
            except: st.info("Install 'xlsxwriter' for Excel.")
