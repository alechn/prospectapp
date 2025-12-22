import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import os
import subprocess
import sys
import concurrent.futures
from typing import Optional, Dict, Any, List, Tuple
from unidecode import unidecode
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup
import google.generativeai as genai

# --- Optional curl_cffi ---
try:
    from curl_cffi import requests as crequests  # type: ignore
    HAS_CURL = True
except Exception:
    HAS_CURL = False

# --- Selenium & Webdriver Manager ---
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    HAS_SELENIUM = True
except Exception:
    HAS_SELENIUM = False

try:
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    HAS_WEBDRIVER_MANAGER = True
except Exception:
    HAS_WEBDRIVER_MANAGER = False


# =========================================================
#             PART 0: CONFIGURATION & SESSION
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Universal Scraper • Parallel Active Search • AI Cleaning")

if "running" not in st.session_state:
    st.session_state.running = False
if "matches" not in st.session_state:
    st.session_state.matches = []
if "visited_fps" not in st.session_state:
    st.session_state.visited_fps = set()
if "visited_urls" not in st.session_state:
    st.session_state.visited_urls = set()

# =========================================================
#             SIDEBAR
# =========================================================
st.sidebar.header("🧠 AI Brain (Cleaning)")
ai_provider = st.sidebar.selectbox(
    "Choose your Model:",
    ["Google Gemini (Flash 2.0)", "OpenAI (GPT-4o)", "Anthropic (Claude 3.5)"]
)
api_key = st.sidebar.text_input(f"Enter {ai_provider.split()[0]} API Key", type="password")

st.sidebar.markdown("---")
st.sidebar.header("🚀 Performance")
num_browsers = st.sidebar.slider("⚡ Parallel Browsers (Active Search)", 1, 5, 1, 
    help="Opens multiple Chrome instances to search different surnames simultaneously.")

search_delay = st.sidebar.slider("⏳ Wait Time (Sec)", 5, 60, 15)
use_browserlike_tls = st.sidebar.checkbox("Use browser-like requests (curl_cffi)", value=False)
if use_browserlike_tls and not HAS_CURL:
    st.sidebar.warning("curl_cffi not installed; falling back to requests.")
    use_browserlike_tls = False

st.sidebar.markdown("---")
st.sidebar.header("🧪 Selenium")
run_headless = st.sidebar.checkbox("Run Selenium headless", value=True)
selenium_wait = st.sidebar.slider("Selenium wait timeout", 5, 60, 15)

with st.sidebar.expander("🛠️ Advanced / Debug"):
    manual_name_selector = st.text_input("Manual Name Selector", placeholder="e.g. h3, table td.name")
    manual_next_selector = st.text_input("Manual Next Selector", placeholder="e.g. a[rel='next'], input[value*='Next']")
    manual_search_selector = st.text_input("Manual Search Box Selector", placeholder="e.g. input[name='q']")
    manual_search_button = st.text_input("Manual Search Button Selector", placeholder="e.g. button[type='submit']")
    manual_search_param = st.text_input("Manual Search Param (URL mode)", placeholder="e.g. q or query")
    manual_no_results = st.text_input("No Results Text", placeholder="e.g. 'No matching records'")
    debug_show_candidates = st.checkbox("Debug: show extracted candidates", value=False)

st.sidebar.markdown("---")
allow_surname_only = st.sidebar.checkbox(
    "Allow single-token surname matches (Weak)",
    value=True,
    help="If a result is just 'Santos', count it as a weak match."
)

if st.sidebar.button("🧪 Check Drivers"):
    st.sidebar.write(f"HAS_SELENIUM: {HAS_SELENIUM}")
    st.sidebar.write(f"HAS_WEBDRIVER_MANAGER: {HAS_WEBDRIVER_MANAGER}")
    st.sidebar.write(f"Chromedriver path: {os.path.exists('/usr/bin/chromedriver')}")

if st.sidebar.button("🛑 STOP", type="primary"):
    st.session_state.running = False
    st.stop()

if st.sidebar.button("🧹 Clear"):
    st.session_state.matches = []
    st.session_state.visited_fps = set()
    st.session_state.visited_urls = set()
    st.sidebar.success("Cleared.")


# =========================================================
#             BLOCKLIST
# =========================================================
BLOCKLIST_SURNAMES = {
    "WANG","LI","ZHANG","LIU","CHEN","YANG","HUANG","ZHAO","WU","ZHOU",
    "XU","SUN","MA","ZHU","HU","GUO","HE","GAO","LIN","LUO",
    "KIM","PARK","LEE","CHOI","NG","SINGH","PATEL","KHAN","TRAN",
    "RESULTS","WEBSITE","SEARCH","MENU","SKIP","CONTENT","FOOTER","HEADER",
    "OVERVIEW","PROJECTS","PEOPLE","PROFILE","VIEW","CONTACT","SPOTLIGHT",
    "PDF","LOGIN","SIGNUP","HOME","ABOUT","CAREERS","NEWS","EVENTS",
    "EDUCATION","INNOVATION","CAMPUS","LIFELONG","GIVE","VISIT","MAP",
    "JOBS","PRIVACY","ACCESSIBILITY","TERMS","COPYRIGHT"
}

NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,6}$")

def normalize_token(s: str) -> str:
    if not s: return ""
    s = unidecode(str(s).strip().upper())
    return "".join(ch for ch in s if "A" <= ch <= "Z")

def clean_extracted_name(raw_text):
    if not isinstance(raw_text, str): return None
    raw_text = " ".join(raw_text.split()).strip()
    if not raw_text: return None

    upper = raw_text.upper()
    junk_phrases = [
        "RESULTS FOR", "SEARCH", "WEBSITE", "EDITION", "SPOTLIGHT",
        "EXPERIENCE", "CALCULATION", "LIVING WAGE", "GOING FAST",
        "GUIDE TO", "LOG OF", "REVIEW OF", "MENU", "SKIP TO",
        "CONTENT", "FOOTER", "HEADER", "OVERVIEW", "PROJECTS", "PEOPLE",
        "PROFILE", "VIEW", "CONTACT", "READ MORE", "LEARN MORE",
        "UNIVERSITY", "INSTITUTE", "SCHOOL", "DEPARTMENT", "COLLEGE",
        "PROGRAM", "INITIATIVE", "LABORATORY", "CENTER FOR", "CENTRE FOR",
        "ALUMNI", "DIRECTORY", "REAP", "MBA", "PHD", "MSC", "CLASS OF",
        "BRASIL", "BRAZIL", "PERU", "ARGENTINA", "CHILE", "USA", "UNITED STATES"
    ]
    
    if any(phrase in upper for phrase in junk_phrases): return None
    if re.search(r'\bMIT\b', upper): return None

    if "," in raw_text:
        parts = [p.strip() for p in raw_text.split(",") if p.strip()]
        if len(parts) >= 2: raw_text = f"{parts[1]} {parts[0]}"

    if ":" in raw_text: raw_text = raw_text.split(":")[-1].strip()

    clean = re.split(r"[|–—»\(\)]|\s-\s", raw_text)[0].strip()
    clean = " ".join(clean.split()).strip()

    if len(clean) < 3 or len(clean.split()) > 7: return None
    if any(x in clean for x in ["@", ".com", ".org", ".edu", ".net", "http", "www"]): return None
    if not NAME_REGEX.match(clean): return None

    return clean

def request_fingerprint(method: str, url: str, data: Optional[dict]) -> str:
    return f"{method.upper()}|{url}|{json.dumps(data or {}, sort_keys=True, ensure_ascii=False)}"

def fetch_native(method: str, url: str, data: Optional[dict] = None):
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
    try:
        if use_browserlike_tls and HAS_CURL:
            if method.upper() == "POST":
                return crequests.post(url, headers=headers, data=data or {}, impersonate="chrome110", timeout=25)
            return crequests.get(url, headers=headers, impersonate="chrome110", timeout=25)

        if method.upper() == "POST":
            return requests.post(url, headers=headers, data=data or {}, timeout=25)
        return requests.get(url, headers=headers, timeout=25)
    except Exception:
        return None


# =========================================================
#             IBGE: FULL FILE -> API FALLBACK
# =========================================================
IBGE_CACHE_FILE = "data/ibge_rank_cache.json"

st.sidebar.markdown("---")
st.sidebar.header("⚙️ IBGE Matching Scope")
limit_first = st.sidebar.number_input("Use Top N First Names", 1, 20000, 3000, 1)
limit_surname = st.sidebar.number_input("Use Top N Surnames", 1, 20000, 3000, 1)
allow_api = st.sidebar.checkbox("If JSON missing, fetch from IBGE API", value=True)
save_local = st.sidebar.checkbox("If fetched, save JSON locally", value=True)

@st.cache_data(ttl=60 * 60 * 24 * 30)
def fetch_ibge_full_from_api() -> Tuple[Dict[str, int], Dict[str, int], Dict[str, Any]]:
    IBGE_FIRST = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"

    def _fetch_all(url: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        page = 1
        while True:
            try:
                r = requests.get(url, params={"page": page}, timeout=30)
                if r.status_code != 200: break
                items = r.json().get("items", [])
                if not items: break
                for it in items:
                    n = normalize_token(it.get("nome"))
                    if n:
                        out[n] = int(it.get("rank", 0) or 0)
                page += 1
                if len(out) > 20000: break
                time.sleep(0.08)
            except: break
        return out

    first_full = _fetch_all(IBGE_FIRST)
    surname_full = _fetch_all(IBGE_SURNAME)
    meta = {
        "saved_at_unix": int(time.time()),
        "source": "IBGE API v3 nomes 2022 localidade/0 ranking",
        "first_count": len(first_full),
        "surname_count": len(surname_full),
    }
    return first_full, surname_full, meta

@st.cache_resource
def load_ibge_full_best_effort(allow_api_fallback: bool, save_if_fetched: bool):
    if os.path.exists(IBGE_CACHE_FILE):
        try:
            with open(IBGE_CACHE_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            first_full = {str(k): int(v) for k, v in (payload.get("first_name_ranks", {}) or {}).items()}
            surname_full = {str(k): int(v) for k, v in (payload.get("surname_ranks", {}) or {}).items()}
            meta = payload.get("meta", {"source": "local_json"})
            return first_full, surname_full, meta, "file"
        except Exception:
            pass

    if not allow_api_fallback:
        raise FileNotFoundError(f"Missing {IBGE_CACHE_FILE} and API fallback disabled.")

    first_full, surname_full, meta = fetch_ibge_full_from_api()
    if save_if_fetched and first_full:
        try:
            os.makedirs(os.path.dirname(IBGE_CACHE_FILE), exist_ok=True)
            with open(IBGE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"meta": meta, "first_name_ranks": first_full, "surname_ranks": surname_full}, f, ensure_ascii=False)
        except Exception:
            pass
    return first_full, surname_full, meta, "api"

@st.cache_data
def slice_ibge_by_rank(first_full: Dict[str, int], surname_full: Dict[str, int], n_first: int, n_surname: int):
    first = {k: v for k, v in first_full.items() if v > 0 and v <= n_first}
    surname = {k: v for k, v in surname_full.items() if v > 0 and v <= n_surname}
    sorted_surnames = sorted(surname.keys(), key=lambda k: surname[k])
    return first, surname, sorted_surnames

with st.sidebar.status("Loading IBGE...", expanded=False) as s:
    ibge_first_full, ibge_surname_full, ibge_meta, ibge_mode = load_ibge_full_best_effort(
        allow_api_fallback=allow_api, save_if_fetched=save_local
    )
    first_name_ranks, surname_ranks, sorted_surnames = slice_ibge_by_rank(
        ibge_first_full, ibge_surname_full, int(limit_first), int(limit_surname)
    )
    s.update(label=f"IBGE ready ({ibge_mode}) ✅", state="complete")
    st.sidebar.success(f"✅ Using Top {int(limit_first)}/{int(limit_surname)} → {len(first_name_ranks)} first / {len(surname_ranks)} surname")


# =========================================================
#             MATCHING
# =========================================================
def calculate_score(rank, limit, weight=50):
    if not rank or rank > limit: return 0
    return weight * (1 - (rank / limit))

def match_names(names: List[str], source: str) -> List[Dict[str, Any]]:
    found = []
    seen = set()

    for raw in names:
        n = clean_extracted_name(raw)
        if not n or n in seen:
            continue
        seen.add(n)

        parts = n.split()

        # --- allow single-token surname-only matches ---
        if len(parts) == 1:
            if not allow_surname_only:
                continue
            tok = normalize_token(parts[0])
            if not tok or tok in BLOCKLIST_SURNAMES:
                continue

            rl = surname_ranks.get(tok, 0)
            if rl > 0:
                score = calculate_score(rl, int(limit_surname), 50)
                found.append({
                    "Full Name": n,
                    "Brazil Score": round(score, 1),
                    "First Rank": None,
                    "Surname Rank": rl,
                    "Source": source,
                    "Match Type": "Surname Only (Weak)",
                    "Status": "Valid"
                })
            continue

        # --- 2+ tokens ---
        f = normalize_token(parts[0])
        l = normalize_token(parts[-1])
        if not f or not l:
            continue
        if f in BLOCKLIST_SURNAMES or l in BLOCKLIST_SURNAMES:
            continue

        rf = first_name_ranks.get(f, 0)
        rl = surname_ranks.get(l, 0)

        score_f = calculate_score(rf, int(limit_first), 50)
        score_l = calculate_score(rl, int(limit_surname), 50)
        total_score = round(score_f + score_l, 1)

        if total_score > 5: 
            found.append({
                "Full Name": n,
                "Brazil Score": total_score,
                "First Rank": rf if rf > 0 else None,
                "Surname Rank": rl if rl > 0 else None,
                "Source": source,
                "Match Type": "Strong" if (rf > 0 and rl > 0) else ("First Only" if rf > 0 else "Surname Only"),
                "Status": "Valid"
            })

    return found


# =========================================================
#             UNIVERSAL EXTRACTION
# =========================================================
def extract_names_multi(html: str, manual_sel: Optional[str] = None) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = []
    if manual_sel:
        selectors.append(manual_sel.strip())

    selectors += [
        "td.name", "td:first-child", "td:nth-child(1)",
        "h3", "h4", "h2",
        ".person .name", ".person-name", ".profile-name", ".result-title", ".result__title",
        "a", "strong"
    ]

    out: List[str] = []
    for sel in selectors:
        for el in soup.select(sel):
            t = el.get_text(" ", strip=True)
            c = clean_extracted_name(t)
            if c:
                out.append(c)
        if len(out) >= 500: break
    return list(dict.fromkeys(out))


# =========================================================
#             DRIVER MANAGEMENT
# =========================================================
def get_driver(headless: bool = True):
    if not HAS_SELENIUM: return None
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-allow-origins=*")
    options.add_argument("--disable-extensions")
    
    try:
        # Fallback to WebDriverManager
        if HAS_WEBDRIVER_MANAGER:
            return webdriver.Chrome(service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()), options=options)
        return webdriver.Chrome(options=options)
    except Exception:
        return None

def selenium_wait_document_ready(driver, timeout: int = 10):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass

# =========================================================
#             SMART WAIT LOGIC (FIXED)
# =========================================================
def smart_search_wait(driver, timeout: int, name_selector: Optional[str] = None):
    """
    Waits until results appear OR 'no results' appears OR DOM stabilizes.
    Allows for generic selector fallback.
    """
    start_time = time.time()
    last_source_len = 0
    stable_count = 0
    
    negative_triggers = ["no results", "not found", "0 results", "no matches", "search returned no", "try again"]
    if manual_no_results: negative_triggers.append(manual_no_results.lower())

    # If no manual selector, use generic ones to detect if results appeared
    check_selectors = [name_selector] if name_selector else ["td.name", "h3", "h4", ".result"]

    while (time.time() - start_time) < timeout:
        # 1. Check for Matches
        for sel in check_selectors:
            if not sel: continue
            try:
                if len(driver.find_elements(By.CSS_SELECTOR, sel)) > 0:
                    time.sleep(0.5) 
                    return True
            except: pass
        
        # 2. Check for "No Results"
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text.lower()[:10000] 
            if any(trig in body_text for trig in negative_triggers):
                return False 
        except: pass

        # 3. DOM Stabilization
        try:
            current_len = len(driver.page_source)
            if current_len == last_source_len:
                stable_count += 1
            else:
                stable_count = 0
            last_source_len = current_len
            
            # Stricter stability: Wait at least 3 seconds before assuming stable
            if stable_count > 4 and (time.time() - start_time) > 3.0:
                 return True
        except: pass

        time.sleep(0.5)
    return False

def selenium_wait_results(driver, timeout: int, name_selector: Optional[str] = None):
    smart_search_wait(driver, timeout, name_selector)

# =========================================================
#             PARALLEL WORKER (ACTIVE SEARCH)
# =========================================================
def worker_search_batch(surnames_chunk, url, selector, search_sel, headless):
    """
    Independent worker that opens one browser and processes a list of surnames
    """
    driver = get_driver(headless=headless)
    if not driver: return []
    
    results = []
    seen = set()
    
    try:
        for surname in surnames_chunk:
            html = None
            try:
                driver.get(url)
                
                # Input Selection
                inp = None
                search_selectors = [search_sel] if search_sel else ["input[type='search']", "input[name='q']", "input[name='search']", "input[aria-label='Search']"]
                
                for s in search_selectors:
                    if not s: continue
                    try:
                        inp = driver.find_element(By.CSS_SELECTOR, s)
                        if inp: break
                    except: pass
                
                if inp:
                    driver.execute_script("arguments[0].value = '';", inp)
                    inp.send_keys(surname)
                    inp.send_keys(Keys.RETURN)
                    
                    # Wait for results
                    smart_search_wait(driver, timeout=20, name_selector=selector)
                    html = driver.page_source
            except Exception: pass
            
            # Fallback
            if not html:
                try:
                    u = urlparse(url)
                    qs = parse_qs(u.query)
                    qs[manual_search_param or "q"] = [surname]
                    cand = u._replace(query=urlencode(qs, doseq=True)).geturl()
                    r = requests.get(cand, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                    if r.status_code == 200: html = r.text
                except: pass

            if html:
                raw_names = extract_names_multi(html, selector)
                matches = match_names(raw_names, f"Search: {surname}")
                for m in matches:
                    if m["Full Name"] not in seen:
                        seen.add(m["Full Name"])
                        results.append(m)
    finally:
        try: driver.quit()
        except: pass
        
    return results

# =========================================================
#             AI CLEANING AGENT
# =========================================================
def batch_clean_with_ai(matches, api_key):
    if not api_key: st.error("API Key required."); return matches
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
    names = [m["Full Name"] for m in matches]
    junk_names = []
    
    prog = st.progress(0)
    for i in range(0, len(names), 50):
        batch = names[i:i+50]
        try:
            prompt = f"Identify non-name junk strings (titles, headers). Return JSON list. Input: {json.dumps(batch)}"
            response = model.generate_content(prompt)
            junk_names.extend(json.loads(response.text.replace("```json", "").replace("```", "")))
        except: pass
        prog.progress(min((i+50)/len(names), 1.0))
        time.sleep(1) 

    for m in matches:
        if m["Full Name"] in junk_names:
            m["Status"] = "Junk (AI)"
            m["Brazil Score"] = -1
        else:
            m["Status"] = "Verified"
    return matches

# =========================================================
#             PAGINATION UTILS (CLASSIC)
# =========================================================
def extract_form_request_from_element(el, current_url: str) -> Optional[Dict[str, Any]]:
    if el is None or el.name not in ("button", "input"): return None
    form = el.find_parent("form")
    if not form: return None
    method = (form.get("method") or "GET").upper()
    action = form.get("action") or current_url
    url = urljoin(current_url, action)
    data: Dict[str, str] = {}
    for inp in form.find_all("input"):
        nm = inp.get("name")
        if nm: data[nm] = inp.get("value", "")
    btn_name = el.get("name")
    if btn_name: data[btn_name] = el.get("value", "")
    if method == "GET":
        u = urlparse(url)
        qs = parse_qs(u.query)
        for k, v in data.items(): qs[k] = [v]
        url = u._replace(query=urlencode(qs, doseq=True)).geturl()
        return {"method": "GET", "url": url, "data": None}
    return {"method": "POST", "url": url, "data": data}

def find_next_request_heuristic(html: str, current_url: str, manual_next: Optional[str] = None) -> Optional[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    base = soup.find("base", href=True)
    base_url = base["href"] if base else current_url
    if manual_next:
        el = soup.select_one(manual_next)
        if el:
            if el.name == "a" and el.get("href"): return {"method": "GET", "url": urljoin(base_url, el["href"]), "data": None}
            if el.name in ("button", "input"): 
                req = extract_form_request_from_element(el, base_url)
                if req: return req
    el = soup.select_one("a[rel='next'][href]")
    if el: return {"method": "GET", "url": urljoin(base_url, el["href"]), "data": None}
    
    next_texts = {"next", "next page", "older", ">", "›", "»", "more"}
    def looks_like_next(s: str) -> bool:
        s = (s or "").strip().lower()
        return (s in next_texts) or ("next" in s) or ("more" in s)
        
    for a in soup.select("a[href]"):
        if looks_like_next(a.get_text(" ", strip=True)) or looks_like_next(a.get("aria-label", "")):
            return {"method": "GET", "url": urljoin(base_url, a["href"]), "data": None}
            
    for btn in soup.find_all(["button", "input"]):
        txt = btn.get_text(" ", strip=True) if btn.name == "button" else btn.get("value", "")
        if looks_like_next(txt) or looks_like_next(btn.get("aria-label", "")):
            req = extract_form_request_from_element(btn, base_url)
            if req: return req
            
    u = urlparse(base_url)
    qs = parse_qs(u.query)
    for k in ["page", "p", "pg", "start", "offset"]:
        if k in qs:
            try:
                val = int(qs[k][0])
                qs[k] = [str(val + 1)]
                return {"method": "GET", "url": u._replace(query=urlencode(qs, doseq=True)).geturl(), "data": None}
            except: pass
    return None


# =========================================================
#             MAIN UI & EXECUTION
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", placeholder="https://directory.example.com")
max_cycles = c2.number_input("Total Surnames to Search", 1, 5000, 50)

st.write("---")
mode = st.radio(
    "Mode:",
    [
        "Classic Directory (Native/Fast)",
        "Infinite Scroller (Selenium)",
        "Active Search Injection (Brute Force Surnames)",
    ]
)

if st.button("🚀 Start Mission", type="primary"):
    if not start_url:
        st.error("URL required")
        st.stop()
        
    st.session_state.running = True
    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()
    all_matches = []
    
    # ---------------------------
    # CLASSIC MODE
    # ---------------------------
    if mode.startswith("Classic"):
        current_req = {"method": "GET", "url": start_url, "data": None}
        for page in range(1, int(max_cycles) + 1):
            status_log.update(label=f"Scanning Page {page}...", state="running")
            r = fetch_native(current_req["method"], current_req["url"], current_req.get("data"))
            if not r or getattr(r, "status_code", None) != 200: break
            
            raw_html = r.text
            names = extract_names_multi(raw_html, manual_name_selector)
            matches = match_names(names, f"Page {page}")
            all_matches.extend(matches)
            
            if all_matches:
                df = pd.DataFrame(all_matches).sort_values(by="Brazil Score", ascending=False)
                table_placeholder.dataframe(df, height=320)
                
            next_req = find_next_request_heuristic(raw_html, current_req["url"], manual_next_selector)
            if not next_req: break
            current_req = next_req
            time.sleep(search_delay)

    # ---------------------------
    # INFINITE SCROLLER MODE
    # ---------------------------
    elif mode.startswith("Infinite"):
        driver = get_driver(headless=run_headless)
        if driver:
            try:
                driver.get(start_url)
                selenium_wait_document_ready(driver)
                for i in range(int(max_cycles)):
                    status_log.update(label=f"Scroll {i+1}...", state="running")
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)
                    
                    html = driver.page_source
                    names = extract_names_multi(html, manual_name_selector)
                    matches = match_names(names, "Scroll")
                    all_matches.extend(matches)
                    
                    if all_matches:
                        df = pd.DataFrame(all_matches).drop_duplicates(subset=["Full Name"]).sort_values(by="Brazil Score", ascending=False)
                        table_placeholder.dataframe(df, height=320)
            finally:
                driver.quit()

    # ---------------------------
    # ACTIVE SEARCH MODE (PARALLEL UPDATED)
    # ---------------------------
    elif mode.startswith("Active"):
        if not sorted_surnames:
            status_log.error("IBGE Database empty.")
            st.stop()
        
        surnames_to_check = sorted_surnames[:int(max_cycles)]
        chunk_size = len(surnames_to_check) // num_browsers + 1
        chunks = [surnames_to_check[i:i + chunk_size] for i in range(0, len(surnames_to_check), chunk_size)]
        
        status_log.write(f"🚀 Launching {len(chunks)} browsers in parallel...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_browsers) as executor:
            futures = []
            for i, chunk in enumerate(chunks):
                if not chunk: continue
                status_log.write(f"Browser {i+1}: Checking {len(chunk)} surnames ({chunk[0]}...)")
                futures.append(
                    executor.submit(worker_search_batch, chunk, start_url, manual_name_selector, manual_search_selector, run_headless)
                )
            
            completed_count = 0
            for future in concurrent.futures.as_completed(futures):
                batch_results = future.result()
                all_matches.extend(batch_results)
                completed_count += 1
                status_log.write(f"✅ Browser {completed_count}/{len(chunks)} finished.")
                
                if all_matches:
                    df = pd.DataFrame(all_matches).sort_values(by="Brazil Score", ascending=False)
                    st.session_state.matches = df.to_dict('records')
                    table_placeholder.dataframe(df, height=300)

    status_log.update(label="Mission Complete!", state="complete")
    st.session_state.running = False


# =========================================================
#             EXPORT & CLEAN
# =========================================================
if st.session_state.matches:
    st.markdown("---")
    col1, col2 = st.columns([1, 4])
    
    with col1:
        st.subheader("🧹 Cleaning")
        if st.button("✨ AI Clean & Sort Results", type="primary"):
            if not api_key:
                st.error("Please provide an API Key in the sidebar.")
            else:
                with st.spinner("🤖 AI is reviewing every name..."):
                    cleaned = batch_clean_with_ai(st.session_state.matches, api_key)
                    cleaned.sort(key=lambda x: (x.get("Status") == "Junk (AI Flagged)", -x["Brazil Score"]))
                    st.session_state.matches = cleaned
                    st.success("Cleaning Complete!")
                    st.rerun()

    with col2:
        df = pd.DataFrame(st.session_state.matches)
        st.dataframe(
            df, 
            column_config={
                "Brazil Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
                "Status": st.column_config.TextColumn("Status"),
            }, 
            use_container_width=True
        )
        
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 CSV", df.to_csv(index=False).encode("utf-8"), "results.csv")
    with c2:
        b = io.BytesIO()
        with pd.ExcelWriter(b, engine="xlsxwriter") as w:
            df.to_excel(w, index=False)
        st.download_button("📥 Excel", b.getvalue(), "results.xlsx")
