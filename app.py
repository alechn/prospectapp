import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import os
import subprocess
import tempfile
import shutil
from typing import Optional, Dict, Any, List, Tuple
from unidecode import unidecode
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup

# --- Optional curl_cffi for better TLS fingerprint (free) ---
try:
    from curl_cffi import requests as crequests  # type: ignore
    HAS_CURL = True
except Exception:
    HAS_CURL = False

# --- Selenium (free) ---
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    HAS_SELENIUM = True
except Exception:
    HAS_SELENIUM = False


# =========================================================
#             PART 0: CONFIGURATION & SESSION
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Free Engines • Universal Pagination • Optional AI • IBGE File→API Fallback")
st.info("Reminder: only scrape directories you have permission to process. Respect robots.txt, rate limits, and site terms.")

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
st.sidebar.header("🧠 AI Brain (optional)")
ai_provider = st.sidebar.selectbox(
    "Choose your Model:",
    ["Google Gemini (Flash 2.0)", "OpenAI (GPT-4o)", "Anthropic (Claude 3.5)", "DeepSeek (V3)"]
)
api_key = st.sidebar.text_input(f"Enter {ai_provider.split()[0]} API Key", type="password")

st.sidebar.markdown("---")
st.sidebar.header("🛰️ Networking")
search_delay = st.sidebar.slider("⏳ Wait Time (Sec)", 0, 30, 5)
use_browserlike_tls = st.sidebar.checkbox("Use browser-like requests (curl_cffi)", value=False)
if use_browserlike_tls and not HAS_CURL:
    st.sidebar.warning("curl_cffi not installed; falling back to requests.")
    use_browserlike_tls = False

st.sidebar.markdown("---")
st.sidebar.header("🔎 Active Search Engine")
active_search_engine = st.sidebar.selectbox(
    "Engine:",
    [
        "Auto (Requests Form → Requests URL params → Selenium Chromium)",
        "Requests: Form submit (no Selenium)",
        "Requests: URL params (no Selenium)",
        "Selenium: Chromium (force; error if can't start)",
    ]
)

st.sidebar.markdown("---")
run_headless = st.sidebar.checkbox("Run Selenium headless", value=True)
selenium_wait = st.sidebar.slider("Selenium wait timeout", 3, 45, 15)

with st.sidebar.expander("🛠️ Advanced / Debug"):
    manual_name_selector = st.text_input("Manual Name Selector", placeholder="e.g. h3, table td.name, a.gs-title")
    manual_next_selector = st.text_input("Manual Next Selector", placeholder="e.g. a[rel='next'], input[value*='Next']")
    manual_search_selector = st.text_input("Manual Search Box Selector", placeholder="e.g. input[name='q']")
    manual_search_button = st.text_input("Manual Search Button Selector", placeholder="e.g. button[type='submit']")
    manual_search_param = st.text_input("Manual Search Param (URL mode)", placeholder="e.g. q or query")
    debug_show_candidates = st.checkbox("Debug: show extracted candidates", value=False)

st.sidebar.markdown("---")
allow_surname_only = st.sidebar.checkbox(
    "Allow single-token surname matches (low confidence)",
    value=True,
    help="If a result is just 'Santos', count it as a weak match."
)

if st.sidebar.button("🧪 Selenium diagnostics"):
    st.sidebar.write("HAS_SELENIUM:", HAS_SELENIUM)
    st.sidebar.write("Exists /usr/bin/chromedriver:", os.path.exists("/usr/bin/chromedriver"))
    st.sidebar.write("Exists /usr/bin/chromium:", os.path.exists("/usr/bin/chromium"))
    try:
        out = subprocess.check_output(["/usr/bin/chromedriver", "--version"]).decode()
        st.sidebar.code(out)
    except Exception as e:
        st.sidebar.write("chromedriver --version failed:", repr(e))
    try:
        out = subprocess.check_output(["/usr/bin/chromium", "--version"]).decode()
        st.sidebar.code(out)
    except Exception as e:
        st.sidebar.write("chromium --version failed:", repr(e))

if st.sidebar.button("🛑 ABORT MISSION", type="primary"):
    st.session_state.running = False
    st.sidebar.warning("Mission Aborted.")
    st.stop()

if st.sidebar.button("🧹 Clear results"):
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
    "LIANG","SONG","TANG","ZHENG","HAN","FENG","DONG","YE","YU","WEI",
    "CAI","YUAN","PAN","DU","DAI","JIN","FAN","SU","MAN","WONG",
    "CHAN","CHANG","LEE","KIM","PARK","CHOI","NG","HO","CHOW","LAU",
    "SINGH","PATEL","KUMAR","SHARMA","GUPTA","ALI","KHAN","TRAN","NGUYEN",
    "RESULTS","WEBSITE","SEARCH","MENU","SKIP","CONTENT","FOOTER","HEADER",
    "OVERVIEW","PROJECTS","PEOPLE","PROFILE","VIEW","CONTACT","SPOTLIGHT",
    "EDITION","JEWELS","COLAR","PAINTER","GUIDE","LOG","REVIEW","PDF",
    "CALCULATION","EXPERIENCE","WAGE","LIVING","GOING","FAST"
}

# =========================================================
#             HELPERS
# =========================================================
def normalize_token(s: str) -> str:
    if not s:
        return ""
    s = unidecode(str(s).strip().upper())
    return "".join(ch for ch in s if "A" <= ch <= "Z")

NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,5}$")

def clean_extracted_name(raw_text):
    if not isinstance(raw_text, str):
        return None
    raw_text = " ".join(raw_text.split()).strip()
    if not raw_text:
        return None

    upper = raw_text.upper()
    junk_phrases = [
        "RESULTS FOR", "SEARCH", "WEBSITE", "EDITION", "SPOTLIGHT",
        "EXPERIENCE IN", "CALCULATION FOR", "LIVING WAGE", "GOING FAST",
        "GUIDE TO", "LOG OF", "REVIEW OF", "MENU", "SKIP TO",
        "CONTENT", "FOOTER", "HEADER", "OVERVIEW", "PROJECTS", "PEOPLE",
        "PROFILE", "VIEW", "CONTACT"
    ]
    if any(phrase in upper for phrase in junk_phrases):
        return None

    # convert "LAST, FIRST" -> "FIRST LAST"
    if "," in raw_text:
        parts = [p.strip() for p in raw_text.split(",") if p.strip()]
        if len(parts) >= 2:
            raw_text = f"{parts[1]} {parts[0]}"

    if ":" in raw_text:
        raw_text = raw_text.split(":")[-1].strip()

    clean = re.split(r"[|–—»\(\)]", raw_text)[0].strip()
    clean = " ".join(clean.split()).strip()

    if len(clean) < 3 or len(clean.split()) > 6:
        return None

    if not NAME_REGEX.match(clean):
        return None

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
#             IBGE: FILE -> API FALLBACK (load once; slice by Top-N)
# =========================================================
IBGE_CACHE_FILE = "data/ibge_rank_cache.json"

st.sidebar.markdown("---")
st.sidebar.header("⚙️ IBGE Matching Scope (Precision)")
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
            r = requests.get(url, params={"page": page}, timeout=30)
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                break
            for it in items:
                n = normalize_token(it.get("nome"))
                if n:
                    out[n] = int(it.get("rank", 0) or 0)
            page += 1
            time.sleep(0.08)
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
        with open(IBGE_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        first_full = {str(k): int(v) for k, v in (payload.get("first_name_ranks", {}) or {}).items()}
        surname_full = {str(k): int(v) for k, v in (payload.get("surname_ranks", {}) or {}).items()}
        meta = payload.get("meta", {"source": "local_json"})
        return first_full, surname_full, meta, "file"

    if not allow_api_fallback:
        raise FileNotFoundError(f"Missing {IBGE_CACHE_FILE} and API fallback disabled.")

    first_full, surname_full, meta = fetch_ibge_full_from_api()
    if save_if_fetched:
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
def rank_score(rank: int, top_n: int, max_points: int = 50) -> int:
    if rank <= 0 or top_n <= 0:
        return 0
    return max(1, int(max_points * (top_n - rank + 1) / top_n))

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
                score = rank_score(rl, int(limit_surname), 50)
                found.append({
                    "Full Name": n,
                    "Brazil Score": score,
                    "First Rank": None,
                    "Surname Rank": rl,
                    "Source": source,
                    "Match Type": "Surname Only (weak)"
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

        score = 0
        if rf > 0:
            score += rank_score(rf, int(limit_first), 50)
        if rl > 0:
            score += rank_score(rl, int(limit_surname), 50)

        if score > 0:
            found.append({
                "Full Name": n,
                "Brazil Score": score,
                "First Rank": rf if rf > 0 else None,
                "Surname Rank": rl if rl > 0 else None,
                "Source": source,
                "Match Type": "Strong" if (rf > 0 and rl > 0) else ("First Only" if rf > 0 else "Surname Only")
            })

    return found


# =========================================================
#             UNIVERSAL EXTRACTION (multi-selector)
# =========================================================
def extract_names_multi(html: str, manual_sel: Optional[str] = None) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")

    def fallback_from_text(text: str) -> List[str]:
        out: List[str] = []
        for raw in text.splitlines():
            raw = " ".join(raw.split()).strip()
            if len(raw) < 5 or len(raw.split()) > 6:
                continue
            c = clean_extracted_name(raw)
            if c:
                out.append(c)
        return out

    selectors = []
    if manual_sel:
        selectors.append(manual_sel.strip())

    selectors += [
        "td.name", "td:first-child", "td:nth-child(1)",
        "h3", "h4", "h2",
        ".person .name", ".person-name", ".profile-name", ".result-title", ".result__title",
        ".gs-title", "a.gs-title", ".gsc-result", # Google Search Selectors
        "a"
    ]

    out: List[str] = []
    for sel in selectors:
        for el in soup.select(sel):
            t = el.get_text(" ", strip=True)
            c = clean_extracted_name(t)
            if c:
                out.append(c)

        if len(out) >= 80 and sel in ("td:first-child", "h3", "h4", "td:nth-child(1)"):
            break

    if not out:
        text_blob = soup.get_text("\n", strip=True)
        out = fallback_from_text(text_blob)

    return list(dict.fromkeys(out))


# =========================================================
#             PAGINATION (classic): LINK + FORM + HEURISTICS
# =========================================================
def extract_form_request_from_element(el, current_url: str) -> Optional[Dict[str, Any]]:
    if el is None or el.name not in ("button", "input"):
        return None
    form = el.find_parent("form")
    if not form:
        return None

    method = (form.get("method") or "GET").upper()
    action = form.get("action") or current_url
    url = urljoin(current_url, action)

    data: Dict[str, str] = {}
    for inp in form.find_all("input"):
        nm = inp.get("name")
        if nm:
            data[nm] = inp.get("value", "")

    btn_name = el.get("name")
    if btn_name:
        data[btn_name] = el.get("value", "")

    if method == "GET":
        u = urlparse(url)
        qs = parse_qs(u.query)
        for k, v in data.items():
            qs[k] = [v]
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
            if el.name == "a" and el.get("href"):
                return {"method": "GET", "url": urljoin(base_url, el["href"]), "data": None}
            if el.name in ("button", "input"):
                req = extract_form_request_from_element(el, base_url)
                if req:
                    return req

    el = soup.select_one("a[rel='next'][href]")
    if el:
        return {"method": "GET", "url": urljoin(base_url, el["href"]), "data": None}

    next_texts = {"next", "next page", "older", ">", "›", "»", "more"}
    for a in soup.select("a[href]"):
        t = (a.get_text(" ", strip=True) or "").strip().lower()
        aria = (a.get("aria-label") or "").strip().lower()
        if t in next_texts or aria in next_texts or "next" in aria:
            return {"method": "GET", "url": urljoin(base_url, a["href"]), "data": None}

    def looks_like_next(s: str) -> bool:
        s = (s or "").strip().lower()
        return (s in next_texts) or ("next" in s) or ("more" in s)

    for btn in soup.find_all(["button", "input"]):
        if btn.name == "button":
            if looks_like_next(btn.get_text(" ", strip=True)) or looks_like_next(btn.get("aria-label", "")):
                req = extract_form_request_from_element(btn, base_url)
                if req:
                    return req
        else:
            t = btn.get("value", "") or btn.get("aria-label", "") or ""
            if looks_like_next(t):
                req = extract_form_request_from_element(btn, base_url)
                if req:
                    return req

    u = urlparse(base_url)
    qs = parse_qs(u.query)
    for k in ["page", "p", "pg", "start", "offset"]:
        if k in qs:
            try:
                val = int(qs[k][0])
                qs[k] = [str(val + 1)]
                return {"method": "GET", "url": u._replace(query=urlencode(qs, doseq=True)).geturl(), "data": None}
            except Exception:
                pass

    return None


# =========================================================
#             SELENIUM DRIVER (FIXED FOR CLOUD)
# =========================================================
def get_driver(headless: bool = True, fail_loud: bool = False):
    """
    Streamlit Cloud-friendly Selenium setup.
    Creates a unique user-data-dir for every session to prevent 'SessionNotCreatedException' / 'DevToolsActivePort' crashes.
    """
    if not HAS_SELENIUM:
        return None

    options = Options()
    if headless:
        options.add_argument("--headless")

    # Critical Cloud Flags
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--remote-allow-origins=*")
    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1920,1080")
    
    # Port fix for DevToolsActivePort issues
    options.add_argument("--remote-debugging-port=9222")

    # --- FIX: UNIQUE USER PROFILE ---
    # Create a fresh temporary directory for this specific driver instance
    user_data_dir = tempfile.mkdtemp()
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument(f"--data-path={user_data_dir}/data")
    options.add_argument(f"--disk-cache-dir={user_data_dir}/cache")

    # Explicit Binary Location (Matches your diagnostics)
    if os.path.exists("/usr/bin/chromium"):
        options.binary_location = "/usr/bin/chromium"
    elif os.path.exists("/usr/bin/chromium-browser"):
        options.binary_location = "/usr/bin/chromium-browser"

    # Prefer System Driver (Matches your diagnostics)
    service = None
    if os.path.exists("/usr/bin/chromedriver"):
        service = Service("/usr/bin/chromedriver")
    else:
        # Fallback to webdriver_manager if system driver is missing
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            from webdriver_manager.core.os_manager import ChromeType
            service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
        except Exception:
            pass

    try:
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        # Clean up the temp dir if startup fails
        try:
            shutil.rmtree(user_data_dir)
        except:
            pass
            
        if fail_loud:
            st.error(f"Selenium failed to start: {repr(e)}")
            raise e
        return None

# =========================================================
#             SELENIUM WAIT (FIXED)
# =========================================================
def selenium_wait_document_ready(driver, timeout: int = 10):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass

def selenium_wait_results(driver, timeout: int, name_selector: Optional[str] = None):
    selenium_wait_document_ready(driver, min(8, timeout))

    if name_selector:
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, name_selector)) > 0
            )
            return
        except Exception:
            pass

    common = [
        "main a", "article a", "table tbody tr", "ul li a", 
        ".search-results *", "#search-results *", ".gsc-result", ".gs-title"
    ]
    for sel in common:
        try:
            WebDriverWait(driver, max(3, timeout // 2)).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, sel)) > 0
            )
            return
        except Exception:
            continue


# =========================================================
#             ACTIVE SEARCH
# =========================================================
def selenium_find_search_input(driver) -> Optional[str]:
    if manual_search_selector and len(driver.find_elements(By.CSS_SELECTOR, manual_search_selector)) > 0:
        return manual_search_selector

    candidates = [
        "input[type='search']", "input[name='q']", "input[name='query']", 
        "input[name='search']", "input[aria-label='Search']"
    ]
    for c in candidates:
        if len(driver.find_elements(By.CSS_SELECTOR, c)) > 0:
            return c
    return None

def selenium_submit_search(driver, sel_input: str, query: str) -> bool:
    try:
        inp = driver.find_element(By.CSS_SELECTOR, sel_input)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
        inp.click()

        try:
            inp.send_keys(Keys.CONTROL + "a")
            inp.send_keys(Keys.BACKSPACE)
        except Exception:
            pass
        driver.execute_script("arguments[0].value = '';", inp)

        driver.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            inp, query
        )

        try:
            inp.send_keys(Keys.RETURN)
        except Exception:
            pass

        if manual_search_button:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, manual_search_button)
                btn.click()
            except Exception:
                pass

        return True
    except Exception:
        return False


# =========================================================
#             EXECUTION
# =========================================================
if st.session_state.running:
    if not start_url:
        st.error("Missing Target URL")
        st.stop()

    manual_name_sel = manual_name_selector.strip() if manual_name_selector else None
    manual_next_sel = manual_next_selector.strip() if manual_next_selector else None

    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()
    all_seen = set()

    if st.session_state.matches:
        table_placeholder.dataframe(pd.DataFrame(st.session_state.matches), use_container_width=True, height=260)

    # ---------------------------
    # CLASSIC DIRECTORY MODE
    # ---------------------------
    if mode.startswith("Classic"):
        current_req = {"method": "GET", "url": start_url, "data": None}

        for page in range(1, int(max_pages) + 1):
            fp = request_fingerprint(current_req["method"], current_req["url"], current_req.get("data"))
            if fp in st.session_state.visited_fps:
                status_log.info("🏁 Pagination loop detected; stopping.")
                break
            st.session_state.visited_fps.add(fp)

            status_log.update(label=f"Scanning Page {page}...", state="running")
            r = fetch_native(current_req["method"], current_req["url"], current_req.get("data"))
            if not r or getattr(r, "status_code", None) != 200:
                break

            names = extract_names_multi(r.text, manual_name_sel)
            matches = match_names(names, f"Page {page}")
            if matches:
                for m in matches:
                    if m["Full Name"] not in all_seen:
                        all_seen.add(m["Full Name"])
                        st.session_state.matches.append(m)
                st.session_state.matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                table_placeholder.dataframe(pd.DataFrame(st.session_state.matches), height=320, use_container_width=True)
                status_log.write(f"✅ Added {len(matches)} matches.")

            next_req = find_next_request_heuristic(r.text, current_req["url"], manual_next_sel)
            if not next_req:
                break
            current_req = {
                "method": next_req.get("method", "GET").upper(),
                "url": next_req["url"],
                "data": next_req.get("data"),
            }
            time.sleep(search_delay)

    # ---------------------------
    # INFINITE SCROLLER MODE
    # ---------------------------
    elif mode.startswith("Infinite"):
        driver = get_driver(headless=run_headless)
        if driver:
            try:
                driver.get(start_url)
                for k in range(int(max_pages)):
                    status_log.update(label=f"Scroll batch {k+1}/{int(max_pages)}...", state="running")
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(max(1, search_delay))
                    selenium_wait_results(driver, timeout=int(selenium_wait), name_selector=manual_name_sel)
                    
                    names = extract_names_multi(driver.page_source, manual_name_sel)
                    matches = match_names(names, f"Scroll batch {k+1}")
                    if matches:
                        for m in matches:
                            if m["Full Name"] not in all_seen:
                                all_seen.add(m["Full Name"])
                                st.session_state.matches.append(m)
                        st.session_state.matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                        table_placeholder.dataframe(pd.DataFrame(st.session_state.matches), height=320, use_container_width=True)
                        status_log.write(f"✅ Added {len(matches)} matches.")
            finally:
                driver.quit()
        else:
            st.error("Selenium unavailable.")
            st.stop()

    # ---------------------------
    # ACTIVE SEARCH INJECTION MODE
    # ---------------------------
    else:
        status_log.write("🔎 Active Search Injection (Fixed): Force Wait Enabled")

        driver = get_driver(headless=run_headless)
        if not driver:
            status_log.warning("Selenium could not start here. Will use requests-only fallback.")

        forced_param = manual_search_param.strip() if manual_search_param.strip() else None
        param_candidates = [forced_param] if forced_param else ["q", "query", "search", "name", "keyword", "term"]
        wait_for_results = max(3, min(20, int(selenium_wait)))

        def requests_urlparam_search(term: str) -> Optional[str]:
            for p in param_candidates:
                if not p:
                    continue
                u = urlparse(start_url)
                qs = parse_qs(u.query)
                qs[p] = [term]
                cand = u._replace(query=urlencode(qs, doseq=True)).geturl()
                rr = fetch_native("GET", cand, None)
                if rr and getattr(rr, "status_code", None) == 200 and rr.text and len(rr.text) > 400:
                    return rr.text
            return None

        def selenium_wait_for_names(driver) -> Tuple[str, List[str]]:
            html_local = driver.page_source
            names_local = extract_names_multi(html_local, manual_name_sel)
            deadline = time.time() + wait_for_results

            while not names_local and time.time() < deadline:
                status_log.write(f"⏳ Waiting for results... ({int(deadline - time.time())}s)")
                time.sleep(1.5)
                html_local = driver.page_source
                names_local = extract_names_multi(html_local, manual_name_sel)

            return html_local, names_local

        for i, surname in enumerate(sorted_surnames[: int(max_pages)]):
            status_log.update(label=f"🔎 Searching '{surname}' ({i+1}/{int(max_pages)})", state="running")
            html = None
            names: List[str] = []

            if driver:
                try:
                    if surname == sorted_surnames[0] or "search" not in driver.current_url:
                        driver.get(start_url)
                        selenium_wait_document_ready(driver, timeout=int(selenium_wait))

                    sel_input = selenium_find_search_input(driver)
                    if not sel_input:
                        status_log.warning("No search input found by Selenium; using requests fallback.")
                    else:
                        ok = selenium_submit_search(driver, sel_input, surname)
                        if ok:
                            # CRITICAL FIX: Force wait for JS rendering
                            sleep_time = max(4.0, float(search_delay))
                            status_log.write(f"⏳ Loading results... ({sleep_time}s)")
                            time.sleep(sleep_time) 
                            
                            html, names = selenium_wait_for_names(driver)
                except Exception as e:
                    status_log.warning(f"Selenium search failed: {repr(e)}")
                    html = None

            if not names and not html:
                html = requests_urlparam_search(surname)
                names = extract_names_multi(html, manual_name_sel) if html else []

            if debug_show_candidates:
                st.write(f"HTML len: {len(html) if html else 0}")
                st.write(f"Candidates: {names[:10]}")

            if not names:
                status_log.write("🤷 No names found (check page load time or selector).")
                time.sleep(1)
                continue

            matches = match_names(names, f"Search: {surname}")
            if matches:
                for m in matches:
                    if m["Full Name"] not in all_seen:
                        all_seen.add(m["Full Name"])
                        st.session_state.matches.append(m)
                st.session_state.matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                table_placeholder.dataframe(pd.DataFrame(st.session_state.matches), height=320, use_container_width=True)
                status_log.write(f"✅ Added {len(matches)} matches.")
            else:
                status_log.write("🤷 Names found, but filtered by IBGE.")

        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    status_log.update(label="Scanning Complete", state="complete")
    st.session_state.running = False

    if st.session_state.matches:
        df = pd.DataFrame(st.session_state.matches)
        st.markdown("---")
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("📥 CSV", df.to_csv(index=False).encode("utf-8"), "results.csv")
        with c2:
            try:
                b = io.BytesIO()
                with pd.ExcelWriter(b, engine="xlsxwriter") as w:
                    df.to_excel(w, index=False)
                st.download_button("📥 Excel", b.getvalue(), "results.xlsx")
            except:
                pass
