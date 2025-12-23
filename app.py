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

# Threading for parallel Chrome
import threading
from queue import Queue, Empty


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

# New: speed + parallel
st.sidebar.markdown("---")
st.sidebar.header("⚡ Speed / Parallel")
enable_light_chrome = st.sidebar.checkbox(
    "Lightweight Chrome (disable images/fonts)",
    value=True,
    help="Often speeds up scraping significantly with little downside."
)
parallel_workers = st.sidebar.slider(
    "Parallel Chrome workers (Active Search only)",
    1, 4, 2,
    help="Each worker runs its own Chrome instance. 2 is usually safe; 3-4 can crash on small machines."
)
# urgent fix: avoid constant hard resets
max_consecutive_submit_failures = st.sidebar.slider(
    "Max consecutive submit failures before hard reset",
    1, 10, 3
)
# keep time logic stable: do NOT change selenium_wait behavior; add small optional initial sleep, default matches your current
post_submit_sleep = st.sidebar.slider(
    "Post-submit settle sleep (seconds)",
    0.0, 5.0, 0.4, 0.1,
    help="Small fixed pause after submitting before tab clicking / waiting (kept to avoid breaking working behavior)."
)
try_people_tab_click = st.sidebar.checkbox(
    "Try to click People/Directory tab/filter",
    value=True
)

with st.sidebar.expander("🛠️ Advanced / Debug"):
    manual_name_selector = st.text_input("Manual Name Selector", placeholder="e.g. h3, table td.name")
    manual_next_selector = st.text_input("Manual Next Selector", placeholder="e.g. a[rel='next'], input[value*='Next']")
    manual_search_selector = st.text_input("Manual Search Box Selector", placeholder="e.g. input[name='q']")
    manual_search_button = st.text_input("Manual Search Button Selector", placeholder="e.g. button[type='submit']")
    manual_search_param = st.text_input("Manual Search Param (URL mode)", placeholder="e.g. q or query")
    debug_show_candidates = st.checkbox("Debug: show extracted candidates", value=False)
    # urgent fix: remove non-universal MIT filter by default
    block_mit_word = st.checkbox(
        "Cleaner: block strings containing standalone 'MIT'",
        value=False,
        help="This is NOT universal; keep OFF unless you specifically want to exclude MIT."
    )

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
#             BLOCKLIST + NAME CLEANING
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

# NEW: allow comma-form + particles (fix Tiago/Matheus issue)
NAME_COMMA_RE = re.compile(
    r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\. ]{2,},\s*[A-Za-zÀ-ÖØ-öø-ÿ'\-\. ]{2,}$"
)
NAME_SPACE_RE = re.compile(
    r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){1,6}$"
)

def normalize_token(s: str) -> str:
    if not s: return ""
    s = unidecode(str(s).strip().upper())
    return "".join(ch for ch in s if "A" <= ch <= "Z")

def clean_extracted_name(raw_text):
    if not isinstance(raw_text, str): 
        return None

    raw_text = " ".join(raw_text.split()).strip()
    if not raw_text:
        return None

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

    # Optional, user-controlled (universal default OFF)
    if block_mit_word and re.search(r"\bMIT\b", upper):
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
#             UNIVERSAL EXTRACTION (fallback)
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
#             DRIVER MANAGEMENT (Fixed + Lightweight)
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

    # Speed win: faster load strategy (doesn't change your wait logic)
    try:
        options.page_load_strategy = "eager"
    except Exception:
        pass

    # Speed win: disable heavy assets
    if enable_light_chrome:
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--disable-features=TranslateUI")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-notifications")
        options.add_argument("--mute-audio")
        prefs = {
            "profile.managed_default_content_settings.images": 2,
            "profile.managed_default_content_settings.stylesheets": 2,
            "profile.managed_default_content_settings.fonts": 2,
        }
        try:
            options.add_experimental_option("prefs", prefs)
        except Exception:
            pass

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
            lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
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
#             ACTIVE SEARCH: WORKING “DEBUGGER” SUBMIT LOGIC (URGENT FIX)
# =========================================================
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

def find_search_input(driver):
    """
    Returns the WebElement, not a selector string.
    This avoids 'selector found but element not interactable' issues.
    """
    # Manual override first
    ms = (manual_search_selector or "").strip()
    if ms:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, ms)
            for e in els:
                if e.is_displayed() and e.is_enabled():
                    return e
        except Exception:
            pass

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

    # Last resort: first visible enabled input (not hidden/submit/etc)
    try:
        for e in driver.find_elements(By.TAG_NAME, "input"):
            try:
                t = (e.get_attribute("type") or "").lower()
                if t in ("hidden", "submit", "button", "checkbox", "radio", "file", "password"):
                    continue
                if e.is_displayed() and e.is_enabled():
                    return e
            except Exception:
                continue
    except Exception:
        pass

    return None

def click_submit_if_possible(driver) -> bool:
    msb = (manual_search_button or "").strip()
    if msb:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, msb)
            if btn.is_displayed() and btn.is_enabled():
                btn.click()
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
    """
    Keep the debugger's robust submit order:
    ENTER -> click submit -> inp.submit()
    """
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
    except Exception:
        pass
    try:
        inp.click()
    except Exception:
        pass

    # Clear
    try:
        inp.send_keys(Keys.CONTROL + "a")
        inp.send_keys(Keys.BACKSPACE)
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].value='';", inp)
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


# =========================================================
#             PEOPLE-CONTAINER “WORKING LOGIC” (as you have)
# =========================================================
def page_has_no_results_signal(html: str) -> bool:
    if not html:
        return False

    soup = BeautifulSoup(html, "html.parser")
    no_sel = [
        ".no-results", ".noresult", ".no-result", "#no-results",
        ".empty-state", ".empty", ".nothing-found",
        "[data-empty='true']",
    ]
    for s in no_sel:
        if soup.select_one(s):
            return True

    text = soup.get_text(" ", strip=True).lower()
    phrases = [
        "no results",
        "0 results",
        "zero results",
        "no matches",
        "no match",
        "nothing found",
        "did not match any",
        "we couldn't find",
        "try a different search",
        "no records found",
        "no entries found",
        "no people found",
        "no profiles found",
        "your search returned no results",
    ]
    return any(p in text for p in phrases)

def _text_signature(txt: str) -> str:
    txt = (txt or "").strip()
    if len(txt) > 4000:
        txt = txt[:4000]
    return str(hash(txt))

def _score_people_block(text: str) -> Dict[str, Any]:
    t = (text or "")
    tlow = t.lower()

    emails = len(re.findall(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", t, re.I))
    mailtos = len(re.findall(r"mailto:", t, re.I))

    nameish = 0
    for line in [ln.strip() for ln in t.splitlines() if ln.strip()]:
        if NAME_COMMA_RE.match(line) or NAME_SPACE_RE.match(line):
            # We allow comma-form lines even if cleaner rejects due to generic junk filters.
            if NAME_COMMA_RE.match(line):
                nameish += 1
                continue
            if clean_extracted_name(line):
                nameish += 1

    people_hint = 1 if ("people results" in tlow or re.search(r"\bpeople\b", tlow)) else 0
    score = (emails * 12) + (mailtos * 18) + (nameish * 8) + (people_hint * 10)

    return {
        "score": score,
        "emails": emails,
        "mailtos": mailtos,
        "nameish": nameish,
        "people_hint": people_hint,
        "title": "",
        "text": t
    }

def _best_people_container_html(driver) -> Tuple[Optional[str], Dict[str, Any]]:
    css_candidates = ["main", "section", "article", "div", "ul", "ol", "table"]

    best = {"score": -1, "text": "", "emails": 0, "mailtos": 0, "nameish": 0, "people_hint": 0, "title": ""}
    best_html = None

    for tag in css_candidates:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, tag)
        except Exception:
            continue

        for el in els[:80]:
            try:
                txt = el.text or ""
            except Exception:
                continue

            if len(txt.strip()) < 120:
                continue

            metrics = _score_people_block(txt)
            if metrics["emails"] == 0 and metrics["mailtos"] == 0 and metrics["nameish"] < 2:
                continue

            if metrics["score"] > best["score"]:
                best = metrics
                try:
                    best_html = el.get_attribute("outerHTML")
                except Exception:
                    best_html = None

    return best_html, best

def _click_best_people_tab_if_any(driver) -> Optional[str]:
    if not try_people_tab_click:
        return None

    targets = [
        ("people", 10),
        ("directory", 7),
        ("staff", 6),
        ("faculty", 6),
        ("students", 5),
        ("profiles", 5),
        ("employees", 5),
    ]

    best_el = None
    best_score = -1
    best_label = None

    for css in ["[role='tab']", "a", "button", "[role='button']"]:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, css)
        except Exception:
            continue
        for el in els[:250]:
            try:
                label = (el.text or "").strip()
                if not label:
                    label = (el.get_attribute("aria-label") or "").strip()
            except Exception:
                continue
            if not label:
                continue
            low = label.lower()
            score = 0
            for word, wscore in targets:
                if word in low:
                    score = max(score, wscore)

            if score > best_score:
                try:
                    if not el.is_displayed():
                        continue
                except Exception:
                    continue
                best_el = el
                best_score = score
                best_label = label

    if best_el and best_score >= 8:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best_el)
            best_el.click()
            return best_label
        except Exception:
            return None
    return None

def _extract_people_like_names(container_html: str) -> List[str]:
    if not container_html:
        return []

    soup = BeautifulSoup(container_html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    out: List[str] = []

    for ln in lines:
        if NAME_COMMA_RE.match(ln):
            parts = [p.strip() for p in ln.split(",") if p.strip()]
            if len(parts) >= 2:
                candidate = f"{parts[1]} {parts[0]}".strip()
                candidate = " ".join(candidate.split())
                c = clean_extracted_name(candidate)
                if c:
                    out.append(c)
                else:
                    toks = candidate.split()
                    if 2 <= len(toks) <= 7:
                        out.append(candidate)
            continue

        if NAME_SPACE_RE.match(ln):
            c = clean_extracted_name(ln)
            if c:
                out.append(c)

    for a in soup.select("a[href^='mailto:']"):
        t = a.get_text(" ", strip=True)
        if t:
            c = clean_extracted_name(t)
            if c:
                out.append(c)

    seen = set()
    dedup = []
    for x in out:
        if x not in seen:
            seen.add(x)
            dedup.append(x)
    return dedup

def selenium_wait_for_people_results(
    driver,
    term: str,
    timeout: int,
    poll_s: float = 0.35
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """
    Safer waiter:
    - avoids early false 'no_results' while JS is still hydrating
    - only accepts 'no_results' after a short grace period AND no evidence of results
    """
    start = time.time()

    base_html, base_dbg = _best_people_container_html(driver)
    base_text = ""
    if base_html:
        base_text = BeautifulSoup(base_html, "html.parser").get_text(" ", strip=True)
    base_sig = _text_signature(base_text)

    debug = {
        "baseline": {"sig": base_sig, "metrics": base_dbg},
        "ticks": []
    }

    # --- NEW: grace period before believing "no results" ---
    NO_RESULTS_GRACE_S = 1.25  # small, keeps your time behavior effectively the same

    while (time.time() - start) < timeout:
        selenium_wait_document_ready(driver, timeout=3)

        try:
            page_html = driver.page_source or ""
        except Exception:
            page_html = ""

        cont_html, cont_dbg = _best_people_container_html(driver)
        cont_text = ""
        if cont_html:
            cont_text = BeautifulSoup(cont_html, "html.parser").get_text(" ", strip=True)

        sig = _text_signature(cont_text)
        elapsed = round(time.time() - start, 2)

        # Evidence of results
        people_names = _extract_people_like_names(cont_html or "")
        page_has_email = bool(EMAIL_RE.search(page_html or ""))
        cont_has_email = bool(EMAIL_RE.search(cont_text or ""))

        term_seen = (term.lower() in (page_html or "").lower()) or (term.lower() in (cont_text or "").lower())

        debug["ticks"].append({
            "t": elapsed,
            "sig": sig,
            "term_seen": bool(term_seen),
            "metrics": cont_dbg,
            "people_names": len(people_names),
            "page_has_email": page_has_email,
            "cont_has_email": cont_has_email,
        })

        # ✅ If we have any real results evidence, return results immediately
        if people_names or cont_has_email or page_has_email:
            return "results", cont_html, debug

        # ✅ Only after grace period: consider no-results, and only if term is actually present
        if elapsed >= NO_RESULTS_GRACE_S and term_seen:
            if page_has_no_results_signal(page_html):
                # extra guard: don't trust no-results if container "looks like" people results
                if cont_dbg.get("nameish", 0) < 2 and cont_dbg.get("emails", 0) == 0 and cont_dbg.get("mailtos", 0) == 0:
                    return "no_results", None, debug

        # Regular “changed container” heuristic (kept)
        if sig != base_sig and cont_dbg.get("score", -1) >= 20:
            # if it changed but still no evidence, keep waiting
            pass

        time.sleep(poll_s)

    return "timeout", None, debug


# =========================================================
#             AI CLEANING AGENT (unchanged)
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
#             PARALLEL WORKER (threads, per-worker Chrome)
# =========================================================
def _active_search_worker_thread(
    worker_id: int,
    start_url: str,
    surnames: List[str],
    out_q: Queue,
    stop_flag: threading.Event,
):
    driver = None
    try:
        driver = get_driver(headless=True)
        if not driver:
            out_q.put(("log", worker_id, "❌ driver failed"))
            return

        driver.get(start_url)
        selenium_wait_document_ready(driver, timeout=min(12, int(selenium_wait)))
        time.sleep(0.7)

        consecutive_fails = 0

        for surname in surnames:
            if stop_flag.is_set():
                return

            # Find input fresh each time (fast) without navigating
            inp = find_search_input(driver)
            if not inp:
                consecutive_fails += 1
                out_q.put(("log", worker_id, f"⚠️ no input for {surname} (fails={consecutive_fails})"))
                if consecutive_fails >= int(max_consecutive_submit_failures):
                    out_q.put(("log", worker_id, "🧯 hard reset (reload start_url)"))
                    driver.get(start_url)
                    selenium_wait_document_ready(driver, timeout=min(12, int(selenium_wait)))
                    time.sleep(0.7)
                    consecutive_fails = 0
                continue

            ok = submit_query(driver, inp, surname)
            if not ok:
                consecutive_fails += 1
                out_q.put(("log", worker_id, f"❌ submit failed for {surname} (fails={consecutive_fails})"))
                if consecutive_fails >= int(max_consecutive_submit_failures):
                    out_q.put(("log", worker_id, "🧯 hard reset (reload start_url)"))
                    driver.get(start_url)
                    selenium_wait_document_ready(driver, timeout=min(12, int(selenium_wait)))
                    time.sleep(0.7)
                    consecutive_fails = 0
                continue

            consecutive_fails = 0

            # Keep the time behavior stable (your request)
            if post_submit_sleep > 0:
                time.sleep(float(post_submit_sleep))

            clicked = _click_best_people_tab_if_any(driver)
            if clicked:
                out_q.put(("log", worker_id, f"🧭 clicked: {clicked}"))

            state, people_container_html, dbg = selenium_wait_for_people_results(
                driver=driver,
                term=surname,
                timeout=int(selenium_wait),
                poll_s=0.35
            )

            if state == "no_results":
                out_q.put(("result", worker_id, surname, [], "no_results"))
                continue

            if state == "timeout":
                out_q.put(("log", worker_id, f"⏱️ timeout for {surname} (using best container anyway)"))
                people_container_html, _ = _best_people_container_html(driver)

            people_names = _extract_people_like_names(people_container_html or "")
            out_q.put(("result", worker_id, surname, people_names, state))

    except Exception as e:
        out_q.put(("log", worker_id, f"💥 worker crash: {e}"))
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass


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
    # ACTIVE SEARCH INJECTION MODE (Parallel + urgent fixes)
    # ---------------------------
    else:
        if not HAS_SELENIUM:
            st.error("Selenium not installed; Active Search Injection needs Selenium.")
            st.stop()

        status_log.write("🔎 Active Search Injection (Universal Selenium People-Container logic)")

        surnames_to_run = sorted_surnames[: int(max_pages)]
        workers = int(parallel_workers) if int(parallel_workers) > 0 else 1
        workers = max(1, min(4, workers))

        if workers > 1:
            status_log.info(f"⚡ Running parallel workers: {workers} (each uses its own Chrome)")

        chunks = [surnames_to_run[i::workers] for i in range(workers)]

        out_q: Queue = Queue()
        stop_flag = threading.Event()
        threads: List[threading.Thread] = []

        try:
            # Start threads
            for wid, chunk in enumerate(chunks):
                t = threading.Thread(
                    target=_active_search_worker_thread,
                    args=(wid, start_url, chunk, out_q, stop_flag),
                    daemon=True
                )
                t.start()
                threads.append(t)

            done_threads = 0
            alive = [True] * len(threads)
            started = time.time()

            # Main UI loop: consume results
            while done_threads < len(threads) and st.session_state.running:
                # Drain messages quickly
                try:
                    msg = out_q.get(timeout=0.4)
                except Empty:
                    msg = None

                if msg:
                    kind = msg[0]

                    if kind == "log":
                        _, wid, text = msg
                        status_log.write(f"[W{wid}] {text}")

                    elif kind == "result":
                        _, wid, surname, people_names, state = msg

                        if debug_show_candidates and people_names:
                            st.write(f"[W{wid}] {surname} candidates (first 30):", people_names[:30])

                        # If none, do nothing (avoid falling back to full-page scrape here; it’s slower and can pull junk)
                        if people_names:
                            matches = match_names(people_names, f"Search: {surname}")
                        else:
                            matches = []

                        if matches:
                            for m in matches:
                                if m["Full Name"] not in all_seen:
                                    all_seen.add(m["Full Name"])
                                    all_matches.append(m)

                            all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                            st.session_state.matches = all_matches
                            table_placeholder.dataframe(pd.DataFrame(all_matches), height=320, use_container_width=True)
                            status_log.write(f"✅ '{surname}': +{len(matches)} matches (candidates={len(people_names)})")
                        else:
                            if state == "no_results":
                                status_log.write(f"🚫 '{surname}': no results")
                            else:
                                status_log.write(f"🤷 '{surname}': 0 matches (candidates={len(people_names)})")

                # Update thread liveness
                for i, t in enumerate(threads):
                    if alive[i] and (not t.is_alive()):
                        alive[i] = False
                        done_threads += 1

                # A tiny sleep prevents UI churn
                time.sleep(0.05)

                # Stop button behavior
                if not st.session_state.running:
                    break

        finally:
            # Stop workers
            stop_flag.set()
            for t in threads:
                try:
                    t.join(timeout=1.0)
                except Exception:
                    pass

        # Respect your existing delay semantics between cycles? (Parallel mode ignores per-surname delay by design)
        # We still keep your global search_delay for Classic/Infinite, and rely on selenium_wait + post_submit_sleep here.

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
