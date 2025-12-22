import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import os
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
st.caption("Universal Scraper • IBGE Scoring • AI Cleaning • Auto-Driver Fix")

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
st.sidebar.header("🛰️ Networking")
search_delay = st.sidebar.slider("⏳ Wait Time (Sec)", 0, 20, 3)
use_browserlike_tls = st.sidebar.checkbox("Use browser-like requests (curl_cffi)", value=False)
if use_browserlike_tls and not HAS_CURL:
    st.sidebar.warning("curl_cffi not installed; falling back to requests.")
    use_browserlike_tls = False

st.sidebar.markdown("---")
st.sidebar.header("🧪 Selenium")
run_headless = st.sidebar.checkbox("Run Selenium headless", value=True)
selenium_wait = st.sidebar.slider("Selenium wait timeout", 5, 60, 15)

st.sidebar.markdown("---")
st.sidebar.header("🧪 Active Search (Universal Debugger Logic)")
working_logic_sleep = st.sidebar.slider(
    "Initial sleep after submit (sec)",
    0, 30, 15,
    help="This is the key 'dumb but universal' delay from the debugger to let JS results render."
)
try_tab_click = st.sidebar.checkbox(
    "Try clicking People/Directory tab",
    value=True,
    help="Best-effort universal click for People-like tabs/filters."
)

with st.sidebar.expander("🛠️ Advanced / Debug"):
    manual_name_selector = st.text_input("Manual Name Selector", placeholder="e.g. h3, table td.name")
    manual_next_selector = st.text_input("Manual Next Selector", placeholder="e.g. a[rel='next'], input[value*='Next']")
    manual_search_selector = st.text_input("Manual Search Box Selector", placeholder="e.g. input[name='q']")
    manual_search_button = st.text_input("Manual Search Button Selector", placeholder="e.g. button[type='submit']")
    manual_search_param = st.text_input("Manual Search Param (URL mode)", placeholder="e.g. q or query")
    debug_show_candidates = st.checkbox("Debug: show extracted candidates", value=False)

st.sidebar.markdown("---")
allow_surname_only = st.sidebar.checkbox(
    "Allow single-token surname matches (Weak)",
    value=True,
    help="If a result is just 'Santos', count it as a weak match."
)

st.sidebar.markdown("---")
# IMPORTANT: you had a MIT-specific filter in cleaner. Make it a toggle so the app is universal.
block_word_mit = st.sidebar.checkbox(
    "Filter out strings containing 'MIT' (NOT universal)",
    value=False,
    help="Turn OFF for universal usage. If ON, any string containing the token MIT is discarded."
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
    # Generic Web Words
    "RESULTS","WEBSITE","SEARCH","MENU","SKIP","CONTENT","FOOTER","HEADER",
    "OVERVIEW","PROJECTS","PEOPLE","PROFILE","VIEW","CONTACT","SPOTLIGHT",
    "PDF","LOGIN","SIGNUP","HOME","ABOUT","CAREERS","NEWS","EVENTS"
}

NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,6}$")

# These two are used by the debugger-style extraction (helps Tiago/Matheus cases)
NAME_COMMA_RE = re.compile(
    r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,4},\s*"
    r"[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,4}$"
)
NAME_SPACE_RE = re.compile(
    r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+(?:da|de|do|dos|das|del|della|di|van|von|bin|ibn))?"
    r"(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){1,5}$",
    re.I
)

def normalize_token(s: str) -> str:
    if not s: return ""
    s = unidecode(str(s).strip().upper())
    return "".join(ch for ch in s if "A" <= ch <= "Z")

def clean_extracted_name(raw_text):
    """
    Cleaner with expanded Blacklist for nav items (Education, Campus, etc.)
    """
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
        "EDUCATION", "INNOVATION", "CAMPUS LIFE", "LIFELONG LEARNING",
        "GIVE", "HOME", "VISIT", "MAP", "EVENTS", "JOBS", "PRIVACY",
        "ACCESSIBILITY", "SOCIAL MEDIA", "TERMS OF USE", "COPYRIGHT",
        "BRASIL", "BRAZIL", "PERU", "ARGENTINA", "CHILE", "USA", "UNITED STATES",
        "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
        "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"
    ]
    if any(phrase in upper for phrase in junk_phrases):
        return None

    # OPTIONAL MIT filter (toggle)
    if block_word_mit and re.search(r"\bMIT\b", upper):
        return None

    # Handle "Last, First"
    if "," in raw_text:
        parts = [p.strip() for p in raw_text.split(",") if p.strip()]
        if len(parts) >= 2:
            raw_text = f"{parts[1]} {parts[0]}"

    if ":" in raw_text:
        raw_text = raw_text.split(":")[-1].strip()

    clean = re.split(r"[|–—»\(\)]|\s-\s", raw_text)[0].strip()
    clean = " ".join(clean.split()).strip()

    if len(clean) < 3 or len(clean.split()) > 7:
        return None

    if any(x in clean for x in ["@", ".com", ".org", ".edu", ".net", "http", "www"]):
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
#             IBGE: FULL FILE -> API FALLBACK
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
            except:
                break
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

        if len(out) >= 500:
            break

    return list(dict.fromkeys(out))

# =========================================================
#             PAGINATION (Classic Mode)
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
                if req: return req

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
                if req: return req
        else:
            t = btn.get("value", "") or btn.get("aria-label", "") or ""
            if looks_like_next(t):
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
            except Exception:
                pass

    return None

# =========================================================
#             DRIVER MANAGEMENT (FIXED: WDM + Fallback)
# =========================================================
def get_driver(headless: bool = True):
    if not HAS_SELENIUM:
        return None

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-allow-origins=*")
    options.add_argument("--disable-extensions")

    service = None
    if os.path.exists("/usr/bin/chromedriver"):
        service = Service("/usr/bin/chromedriver")

    try:
        if service:
            return webdriver.Chrome(service=service, options=options)
        return webdriver.Chrome(options=options)
    except Exception as e_native:
        if HAS_WEBDRIVER_MANAGER:
            try:
                return webdriver.Chrome(
                    service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
                    options=options
                )
            except Exception as e_wdm:
                st.error(f"Driver Init Failed. Native: {e_native} | WDM: {e_wdm}")
                return None
        else:
            st.error(f"Selenium Driver Error (and webdriver_manager not found): {e_native}")
            return None

def selenium_wait_document_ready(driver, timeout: int = 10):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass

def selenium_wait_results(driver, timeout: int, name_selector: Optional[str] = None):
    selenium_wait_document_ready(driver, min(5, timeout))
    time.sleep(1.5)

    if name_selector:
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, name_selector)) > 0
            )
            return
        except Exception:
            pass

    common = [".search-results", "#search-results", "table tr", "ul li", ".result", ".person", ".profile"]
    for s in common:
        if len(driver.find_elements(By.CSS_SELECTOR, s)) > 0:
            return
    time.sleep(1.5)

# =========================================================
#     ACTIVE SEARCH (DEBUGGER-STYLE: innerText → best people block)
# =========================================================
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

PEOPLE_KEYWORDS = [
    "people", "person", "directory", "staff", "faculty", "students", "student",
    "profiles", "contacts", "employees", "members"
]
NONPEOPLE_HEADINGS = ["websites", "locations", "news", "events", "maps", "jobs"]

NO_RESULTS_REGEXES = [
    re.compile(r"\bno\s+results\b", re.I),
    re.compile(r"\b0\s+results\b", re.I),
    re.compile(r"\bzero\s+results\b", re.I),
    re.compile(r"\bno\s+matches\b", re.I),
    re.compile(r"\bnothing\s+found\b", re.I),
    re.compile(r"\bdid\s+not\s+match\b", re.I),
    re.compile(r"\bno\s+records\b", re.I),
]

def body_text(driver, max_chars=250000) -> str:
    try:
        t = driver.execute_script("return document.body ? (document.body.innerText || '') : '';") or ""
        return t[:max_chars]
    except Exception:
        return ""

def text_has_no_results_signal(text: str) -> bool:
    t = text or ""
    if EMAIL_RE.search(t):
        return False
    return any(rx.search(t) for rx in NO_RESULTS_REGEXES)

def is_nameish(line: str) -> bool:
    if not line:
        return False
    s = line.strip()
    if len(s) < 3 or len(s) > 90:
        return False
    low = s.lower()
    if any(k in low for k in ["results for", "website results", "locations results", "people results"]):
        return False
    return bool(NAME_COMMA_RE.match(s) or NAME_SPACE_RE.match(s))

def safe_dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

def find_search_input(driver) -> Optional[Any]:
    if manual_search_selector and manual_search_selector.strip():
        els = driver.find_elements(By.CSS_SELECTOR, manual_search_selector.strip())
        for e in els:
            try:
                if e.is_displayed() and e.is_enabled():
                    return e
            except Exception:
                continue

    selectors = [
        "input[type='search']",
        "input[name='q']",
        "input[name='query']",
        "input[name='search']",
        "input[name='s']",
        "input[aria-label*='search' i]",
        "input[placeholder*='search' i]",
        "input[placeholder*='name' i]",
        "input[placeholder*='last' i]",
    ]
    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            els = []
        for e in els:
            try:
                if e.is_displayed() and e.is_enabled():
                    return e
            except Exception:
                continue

    for e in driver.find_elements(By.TAG_NAME, "input"):
        try:
            t = (e.get_attribute("type") or "").lower()
            if t in ("hidden", "submit", "button", "checkbox", "radio", "file", "password"):
                continue
            if e.is_displayed() and e.is_enabled():
                return e
        except Exception:
            continue
    return None

def click_submit_if_possible(driver) -> bool:
    if manual_search_button and manual_search_button.strip():
        try:
            driver.find_element(By.CSS_SELECTOR, manual_search_button.strip()).click()
            return True
        except Exception:
            return False

    for sel in [
        "button[type='submit']",
        "input[type='submit']",
        "button[aria-label*='search' i]",
        "button[class*='search' i]",
    ]:
        try:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            btns = []
        for b in btns:
            try:
                if b.is_displayed() and b.is_enabled():
                    b.click()
                    return True
            except Exception:
                continue
    return False

def submit_query(driver, inp, term: str) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
    except Exception:
        pass

    try:
        inp.click()
    except Exception:
        pass

    try:
        inp.send_keys(Keys.CONTROL + "a")
        inp.send_keys(Keys.BACKSPACE)
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].value='';", inp)
        driver.execute_script("arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", inp)
    except Exception:
        pass

    try:
        inp.send_keys(term)
    except Exception:
        return False

    try:
        inp.send_keys(Keys.RETURN)
        return True
    except Exception:
        pass

    if click_submit_if_possible(driver):
        return True

    try:
        inp.submit()
        return True
    except Exception:
        return False

def click_best_people_tab(driver) -> Optional[str]:
    if not try_tab_click:
        return None

    elems = []
    try:
        elems.extend(driver.find_elements(By.CSS_SELECTOR, "[role='tab']"))
    except Exception:
        pass
    try:
        elems.extend(driver.find_elements(By.CSS_SELECTOR, "a, button"))
    except Exception:
        pass

    best = None
    best_score = -1
    best_txt = None

    for el in elems[:400]:
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue
            txt = (el.text or "").strip()
            if not txt or len(txt) > 50:
                continue
            low = txt.lower()
            score = 0
            if any(k in low for k in ["people", "directory"]):
                score = 10
            elif any(k in low for k in ["staff", "faculty", "students", "profiles", "employees"]):
                score = 7
            if score <= 0:
                continue

            role = (el.get_attribute("role") or "").lower()
            cls = (el.get_attribute("class") or "").lower()
            if role == "tab" or "tab" in cls or "filter" in cls:
                score += 1

            if score > best_score:
                best = el
                best_score = score
                best_txt = txt
        except Exception:
            continue

    if not best or best_score < 8:
        return None

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
        best.click()
        return best_txt
    except Exception:
        return best_txt

def split_into_blocks(txt: str) -> List[Dict[str, Any]]:
    lines = [l.strip() for l in (txt or "").splitlines()]
    lines = [l for l in lines if l]

    blocks: List[Dict[str, Any]] = []
    cur = {"title": "", "lines": []}

    def is_heading(line: str) -> bool:
        if len(line) <= 2 or len(line) > 50:
            return False
        low = line.lower()
        if low in PEOPLE_KEYWORDS or low in NONPEOPLE_HEADINGS:
            return True
        if "results" in low and len(line) < 70:
            return True
        if re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9'\-\. ]+", line) and (line[0].isupper() or low.startswith("people")):
            if line.count(" ") <= 5:
                return True
        return False

    for line in lines:
        if is_heading(line) and cur["lines"]:
            blocks.append(cur)
            cur = {"title": line, "lines": []}
        else:
            if not cur["title"] and is_heading(line):
                cur["title"] = line
            else:
                cur["lines"].append(line)

    if cur["lines"] or cur["title"]:
        blocks.append(cur)

    return blocks

def score_people_block(block: Dict[str, Any]) -> Dict[str, Any]:
    title = (block.get("title") or "").strip()
    lines = block.get("lines") or []
    joined = "\n".join([title] + lines)

    emails = len(EMAIL_RE.findall(joined))
    nameish_count = sum(1 for l in lines if is_nameish(l))
    mailto_hint = 1 if "mailto:" in joined.lower() else 0

    title_low = title.lower()
    people_hint = 2 if any(k in title_low for k in PEOPLE_KEYWORDS) else 0
    nonpeople_penalty = 2 if any(k in title_low for k in NONPEOPLE_HEADINGS) else 0

    score = emails * 10 + nameish_count * 2 + mailto_hint * 3 + people_hint * 6 - nonpeople_penalty * 6

    return {
        "title": title,
        "emails": emails,
        "nameish": nameish_count,
        "score": score,
        "lines": lines,
        "text": joined[:6000],
    }

def pick_best_people_block(page_txt: str) -> Optional[Dict[str, Any]]:
    blocks = split_into_blocks(page_txt)
    scored = [score_people_block(b) for b in blocks]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[0] if scored else None

def extract_people_records_from_lines(lines: List[str]) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []

    def find_email_near(i: int) -> str:
        for j in range(i, min(i + 6, len(lines))):
            m = EMAIL_RE.search(lines[j])
            if m:
                return m.group(0)
        return ""

    for i, line in enumerate(lines):
        if is_nameish(line):
            email = find_email_near(i)
            low = line.lower()
            if low in PEOPLE_KEYWORDS or low in NONPEOPLE_HEADINGS:
                continue
            records.append({"name": line.strip(), "email": email})

    seen = set()
    out = []
    for r in records:
        k = (r["name"], r["email"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out

def wait_and_extract_people(driver, term: str, timeout: int, poll_s: float = 0.6) -> Dict[str, Any]:
    start = time.time()
    best_block = None

    while time.time() - start < timeout:
        txt = body_text(driver)

        if text_has_no_results_signal(txt):
            return {
                "state": "no_results",
                "elapsed": round(time.time() - start, 1),
                "best_block": None,
                "people_records": [],
                "page_preview": txt[:3000],
            }

        best_block = pick_best_people_block(txt)
        if best_block and best_block["score"] >= 20 and best_block["emails"] >= 1:
            break

        time.sleep(poll_s)

    txt = body_text(driver)
    best_block = pick_best_people_block(txt)

    if not best_block:
        return {
            "state": "no_block",
            "elapsed": round(time.time() - start, 1),
            "best_block": None,
            "people_records": [],
            "page_preview": txt[:3000],
        }

    people_records = extract_people_records_from_lines(best_block["lines"])
    return {
        "state": "ok",
        "elapsed": round(time.time() - start, 1),
        "best_block": {k: best_block[k] for k in ["title", "emails", "nameish", "score", "text"]},
        "people_records": people_records,
        "page_preview": txt[:3000],
    }

# =========================================================
#             AI CLEANING AGENT
# =========================================================
def batch_clean_with_ai(matches, api_key):
    if not api_key:
        st.error("API Key required.")
        return matches

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')

    names = [m["Full Name"] for m in matches]
    junk_names = []

    batch_size = 50
    progress_bar = st.progress(0)

    for i in range(0, len(names), batch_size):
        batch = names[i:i+batch_size]
        prompt = f"""
        Identify junk strings (titles, places, nav items) in this list.
        Return JSON list of JUNK items only.
        List: {json.dumps(batch)}
        """
        try:
            response = model.generate_content(prompt)
            text = response.text.replace("```json", "").replace("```", "")
            found_junk = json.loads(text)
            junk_names.extend(found_junk)
        except Exception:
            pass

        progress_bar.progress(min((i + batch_size) / len(names), 1.0))
        time.sleep(1)

    for m in matches:
        if m["Full Name"] in junk_names:
            m["Status"] = "Junk (AI Flagged)"
            m["Brazil Score"] = -1
        else:
            m["Status"] = "Verified"

    return matches

# =========================================================
#             MAIN UI
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", placeholder="https://directory.example.com")
max_pages = c2.number_input("Max Pages / Search Cycles", 1, 10000, 500)

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
    st.session_state.running = True

# =========================================================
#             EXECUTION
# =========================================================
if st.session_state.running:
    if not start_url:
        st.error("Missing Target URL")
        st.stop()

    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()

    all_matches: List[Dict[str, Any]] = []
    all_seen = set()

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
                status_log.warning("Fetch failed.")
                break

            raw_html = r.text
            names = extract_names_multi(raw_html, manual_name_selector.strip() if manual_name_selector else None)
            matches = match_names(names, f"Page {page}")

            for m in matches:
                if m["Full Name"] not in all_seen:
                    all_seen.add(m["Full Name"])
                    all_matches.append(m)

            all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
            st.session_state.matches = all_matches
            table_placeholder.dataframe(pd.DataFrame(all_matches), height=320, use_container_width=True)
            status_log.write(f"✅ Added {len(matches)} matches.")

            next_req = find_next_request_heuristic(
                raw_html, current_req["url"], manual_next_selector.strip() if manual_next_selector else None
            )
            if not next_req:
                status_log.info("🏁 No more pages detected.")
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
        if not HAS_SELENIUM:
            st.error("Selenium not installed.")
            st.stop()

        driver = get_driver(headless=run_headless)
        if not driver:
            st.error("Selenium could not start.")
            st.stop()

        try:
            driver.get(start_url)
            selenium_wait_document_ready(driver, timeout=int(selenium_wait))
            for k in range(int(max_pages)):
                status_log.update(label=f"Scroll batch {k+1}/{int(max_pages)}...", state="running")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(max(1, search_delay))
                selenium_wait_results(driver, timeout=int(selenium_wait), name_selector=(manual_name_selector.strip() if manual_name_selector else None))

                html = driver.page_source
                names = extract_names_multi(html, manual_name_selector.strip() if manual_name_selector else None)
                matches = match_names(names, f"Scroll batch {k+1}")

                for m in matches:
                    if m["Full Name"] not in all_seen:
                        all_seen.add(m["Full Name"])
                        all_matches.append(m)

                all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                st.session_state.matches = all_matches
                if matches:
                    table_placeholder.dataframe(pd.DataFrame(all_matches), height=320, use_container_width=True)
                    status_log.write(f"✅ Added {len(matches)} matches.")
        finally:
            driver.quit()

    # ---------------------------
    # ACTIVE SEARCH INJECTION MODE (DEBUGGER LOGIC)
    # ---------------------------
    else:
        status_log.write("🔎 Active Search Injection (Debugger-style best people block)")

        driver = get_driver(headless=run_headless)
        if not driver:
            status_log.error("Selenium could not start; Active Search Injection needs Selenium.")
            st.stop()

        try:
            driver.get(start_url)
            selenium_wait_document_ready(driver, timeout=min(12, int(selenium_wait)))
            time.sleep(0.7)

            for i, surname in enumerate(sorted_surnames[: int(max_pages)]):
                status_log.update(label=f"🔎 Searching '{surname}' ({i+1}/{int(max_pages)})", state="running")

                inp = find_search_input(driver)
                if not inp:
                    driver.get(start_url)
                    selenium_wait_document_ready(driver, timeout=min(12, int(selenium_wait)))
                    time.sleep(0.7)
                    inp = find_search_input(driver)

                if not inp:
                    status_log.error("❌ No search input found. Use Manual Search Box Selector.")
                    break

                ok = submit_query(driver, inp, surname)
                if not ok:
                    driver.get(start_url)
                    selenium_wait_document_ready(driver, timeout=min(12, int(selenium_wait)))
                    time.sleep(0.7)
                    inp = find_search_input(driver)
                    ok = submit_query(driver, inp, surname) if inp else False

                if not ok:
                    status_log.write(f"❌ Failed submit for '{surname}'. Skipping.")
                    time.sleep(search_delay)
                    continue

                if working_logic_sleep > 0:
                    time.sleep(float(working_logic_sleep))

                clicked = click_best_people_tab(driver)
                if clicked:
                    status_log.write(f"🧭 Clicked tab/filter: {clicked}")
                    time.sleep(0.8)

                res = wait_and_extract_people(driver, surname, timeout=int(selenium_wait))

                if res["state"] == "no_results":
                    status_log.write(f"🚫 '{surname}': no results detected.")
                    time.sleep(search_delay)
                    continue

                if debug_show_candidates:
                    st.write({"surname": surname, "best_block": res.get("best_block", {})})

                people_names = [r["name"] for r in (res.get("people_records") or [])]
                people_names = safe_dedupe(people_names)

                if not people_names:
                    html = driver.page_source
                    people_names = extract_names_multi(html, manual_name_selector.strip() if manual_name_selector else None)

                matches = match_names(people_names, f"Search: {surname}")

                for m in matches:
                    if m["Full Name"] not in all_seen:
                        all_seen.add(m["Full Name"])
                        all_matches.append(m)

                all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                st.session_state.matches = all_matches

                if matches:
                    table_placeholder.dataframe(pd.DataFrame(all_matches), height=320, use_container_width=True)
                    status_log.write(f"✅ '{surname}': added {len(matches)} matches (people_lines={len(people_names)}).")

                time.sleep(search_delay)

        finally:
            try:
                driver.quit()
            except Exception:
                pass

    status_log.update(label="Scanning Complete", state="complete")
    st.session_state.running = False

# =========================================================
#             POST-PROCESSING UI
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

        cols_config = {
            "Brazil Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
            "Status": st.column_config.TextColumn("Status"),
        }

        st.dataframe(df, column_config=cols_config, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 CSV", df.to_csv(index=False).encode("utf-8"), "results.csv")
    with c2:
        b = io.BytesIO()
        with pd.ExcelWriter(b, engine="xlsxwriter") as w:
            df.to_excel(w, index=False)
        st.download_button("📥 Excel", b.getvalue(), "results.xlsx")
