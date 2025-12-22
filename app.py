"""
Universal Brazilian Alumni Finder (Streamlit Cloud friendly)

GOALS
✅ Universal-ish directory scraper: works on many "simple" HTML directories
✅ Classic mode: GET/POST pagination (links + forms + heuristic URL increment)
✅ Active Search mode: multiple free engines:
   - Requests Form Submit (cloud-safe)
   - Requests URL-Param Search (cloud-safe)
   - Selenium Chromium (best-effort; FREE, but may fail on Streamlit Cloud depending on image)
✅ Optional AI:
   - Learn CSS selectors once (name selector + next selector)
   - Non-destructive verification column

IBGE
✅ Loads full IBGE ranks from data/ibge_rank_cache.json if present (recommended)
✅ Else fetches from IBGE API (optional), can save cache file
✅ Sidebar lets you match only Top-N (precision) without re-downloading ranks every run

FILES TO ADD TO REPO (Streamlit Cloud)
1) packages.txt  (repo root)
   chromium
   chromium-driver

2) requirements.txt (suggested)
   streamlit
   pandas
   requests
   beautifulsoup4
   unidecode
   xlsxwriter
   selenium
   webdriver-manager
   curl_cffi

NOTE:
- Some sites are JS-rendered; without Selenium you may only see skeleton HTML.
- Selenium on Streamlit Cloud is best-effort; the code auto-falls back to requests engines.
"""

import os
import json
import time
import re
import io
from typing import Optional, Dict, Any, List, Tuple

import requests
import streamlit as st
import pandas as pd
from unidecode import unidecode
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup

# Optional: curl_cffi for more browser-like TLS fingerprint (free)
try:
    from curl_cffi import requests as crequests  # type: ignore
    HAS_CURL = True
except Exception:
    HAS_CURL = False

# Optional: Selenium (FREE, but may fail on Streamlit Cloud depending on environment)
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
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
st.caption("Free Engines • Heuristics + Form Pagination • Optional AI • IBGE File→API Fallback")
st.info(
    "Reminder: only scrape directories you have permission to process. "
    "Respect robots.txt, rate limits, and site terms."
)

if "running" not in st.session_state:
    st.session_state.running = False
if "matches" not in st.session_state:
    st.session_state.matches = []
if "visited_fps" not in st.session_state:
    st.session_state.visited_fps = set()
if "learned_selectors" not in st.session_state:
    st.session_state.learned_selectors = {}
if "pages_scanned" not in st.session_state:
    st.session_state.pages_scanned = 0


# =========================================================
#             PART 1: SIDEBAR / SETTINGS
# =========================================================
st.sidebar.header("🧠 AI Brain (optional)")
ai_provider = st.sidebar.selectbox(
    "Choose your Model:",
    ["Google Gemini (Flash 2.0)", "OpenAI (GPT-4o)", "Anthropic (Claude 3.5)", "DeepSeek (V3)"]
)
api_key = st.sidebar.text_input(f"Enter {ai_provider.split()[0]} API Key", type="password")

st.sidebar.markdown("---")
st.sidebar.header("🛰️ Networking")
search_delay = st.sidebar.slider("⏳ Wait Time (Sec)", 0, 30, 2)
use_browserlike_tls = st.sidebar.checkbox(
    "Use browser-like requests (curl_cffi)", value=False,
    help="Optional. Helps on some sites. Requires curl_cffi."
)
if use_browserlike_tls and not HAS_CURL:
    st.sidebar.warning("curl_cffi not installed; falling back to requests.")

st.sidebar.markdown("---")
st.sidebar.header("💾 Selector Memory (optional)")
uploaded_selectors = st.sidebar.file_uploader("Load Selectors (JSON)", type="json")
if uploaded_selectors:
    try:
        st.session_state.learned_selectors = json.load(uploaded_selectors)
        st.sidebar.success("Selectors Loaded!")
    except Exception as e:
        st.sidebar.error(f"Failed to load selectors JSON: {e}")

if st.session_state.learned_selectors:
    st.sidebar.download_button(
        "💾 Save Current Selectors",
        data=json.dumps(st.session_state.learned_selectors, indent=2),
        file_name="site_selectors.json",
        mime="application/json"
    )

use_ai_verification = st.sidebar.checkbox(
    "✨ AI Verify (Non-Destructive)", value=False,
    help="Adds AI_Observation column, does not delete rows."
)

st.sidebar.markdown("---")
st.sidebar.header("🔎 Active Search Engine (free)")
active_search_engine = st.sidebar.selectbox(
    "Engine:",
    [
        "Auto (Requests Form → Requests URL params → Selenium Chromium)",
        "Requests: Form submit (no Selenium)",
        "Requests: URL params (no Selenium)",
        "Selenium: Chromium (best-effort on Streamlit Cloud)",
    ]
)

with st.sidebar.expander("🛠️ Advanced / Debug"):
    manual_name_selector = st.sidebar.text_input(
        "Manual Name Selector",
        placeholder="e.g. table tbody tr td:first-child or h3"
    )
    manual_next_selector = st.sidebar.text_input(
        "Manual Next Selector",
        placeholder="e.g. a[rel='next'], a.next, input[value*='Next']"
    )
    manual_search_param = st.sidebar.text_input(
        "Manual Search Param (URL mode)",
        placeholder="e.g. q or query or search",
        help="If you know the query parameter name for URL-based search."
    )
    show_debug_ai_payload = st.sidebar.checkbox("Show AI Debug Errors", value=True)
    run_headless = st.sidebar.checkbox("Selenium Headless", value=True)

if st.sidebar.button("🧹 Clear session results"):
    st.session_state.matches = []
    st.session_state.visited_fps = set()
    st.session_state.pages_scanned = 0
    st.sidebar.success("Cleared results & pagination fingerprints.")

if st.sidebar.button("🛑 ABORT MISSION", type="primary"):
    st.session_state.running = False
    st.sidebar.warning("Mission Aborted.")
    st.stop()


# =========================================================
#             PART 2: NAME CLEANING / BLOCKLIST
# =========================================================
BLOCKLIST_SURNAMES = {
    "WANG", "LI", "ZHANG", "LIU", "CHEN", "YANG", "HUANG", "ZHAO", "WU", "ZHOU",
    "XU", "SUN", "MA", "ZHU", "HU", "GUO", "HE", "GAO", "LIN", "LUO",
    "LIANG", "SONG", "TANG", "ZHENG", "HAN", "FENG", "DONG", "YE", "YU", "WEI",
    "CAI", "YUAN", "PAN", "DU", "DAI", "JIN", "FAN", "SU", "MAN", "WONG",
    "CHAN", "CHANG", "LEE", "KIM", "PARK", "CHOI", "NG", "HO", "CHOW", "LAU",
    "SINGH", "PATEL", "KUMAR", "SHARMA", "GUPTA", "ALI", "KHAN", "TRAN", "NGUYEN",
    "RESULTS", "WEBSITE", "SEARCH", "MENU", "SKIP", "CONTENT", "FOOTER", "HEADER",
    "OVERVIEW", "PROJECTS", "PEOPLE", "PROFILE", "VIEW", "CONTACT", "SPOTLIGHT",
    "EDITION", "JEWELS", "COLAR", "PAINTER", "GUIDE", "LOG", "REVIEW", "PDF",
}

def normalize_token(s: str) -> str:
    if not s:
        return ""
    s = unidecode(str(s).strip().upper())
    return "".join(ch for ch in s if "A" <= ch <= "Z")

def clean_extracted_name(raw_text):
    if not isinstance(raw_text, str):
        return None
    upper = raw_text.upper()
    junk = ["RESULTS FOR", "SEARCH", "WEBSITE", "EDITION", "JEWELS", "SPOTLIGHT", "GUIDE", "LOG", "REVIEW"]
    if any(j in upper for j in junk):
        return None
    if ":" in raw_text:
        raw_text = raw_text.split(":")[-1]
    clean = re.split(r"[|,\-–—»\(\)]", raw_text)[0]
    clean = " ".join(clean.split())
    if len(clean.split()) > 6 or len(clean) < 3:
        return None
    if clean.isupper() and len(clean.split()) <= 2 and clean in BLOCKLIST_SURNAMES:
        return None
    return clean.strip()


# =========================================================
#             PART 3: IBGE LOADER (FILE -> API FALLBACK)
# =========================================================
IBGE_CACHE_FILE = "data/ibge_rank_cache.json"

st.sidebar.markdown("---")
st.sidebar.header("⚙️ IBGE Matching Scope (Precision)")
limit_first = st.sidebar.number_input("Use Top N First Names", 1, 20000, 3000, 1)
limit_surname = st.sidebar.number_input("Use Top N Surnames", 1, 20000, 3000, 1)
allow_api = st.sidebar.checkbox("If JSON missing, fetch from IBGE API", value=True)
save_local = st.sidebar.checkbox("If fetched, save JSON locally", value=True)

@st.cache_data(ttl=60 * 60 * 24 * 30)  # 30 days
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
        first_full = payload.get("first_name_ranks", {})
        surname_full = payload.get("surname_ranks", {})
        meta = payload.get("meta", {"source": "local_json"})
        if isinstance(first_full, dict) and isinstance(surname_full, dict):
            first_full = {str(k): int(v) for k, v in first_full.items()}
            surname_full = {str(k): int(v) for k, v in surname_full.items()}
            return first_full, surname_full, meta, "file"

    if not allow_api_fallback:
        raise FileNotFoundError(f"IBGE cache file not found at '{IBGE_CACHE_FILE}' and API fallback disabled.")

    first_full, surname_full, meta = fetch_ibge_full_from_api()

    if save_if_fetched:
        try:
            os.makedirs(os.path.dirname(IBGE_CACHE_FILE), exist_ok=True)
            payload = {"meta": meta, "first_name_ranks": first_full, "surname_ranks": surname_full}
            with open(IBGE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
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
    try:
        ibge_first_full, ibge_surname_full, ibge_meta, ibge_mode = load_ibge_full_best_effort(
            allow_api_fallback=allow_api,
            save_if_fetched=save_local
        )
        first_name_ranks, surname_ranks, sorted_surnames = slice_ibge_by_rank(
            ibge_first_full, ibge_surname_full, int(limit_first), int(limit_surname)
        )
        s.update(label=f"IBGE ready ({ibge_mode}) ✅", state="complete")
        st.sidebar.success(
            f"✅ Using Top {int(limit_first)}/{int(limit_surname)} "
            f"→ {len(first_name_ranks)} first / {len(surname_ranks)} surname"
        )
    except Exception as e:
        s.update(label="IBGE failed ❌", state="error")
        st.error(f"IBGE load failed: {e}")
        st.stop()

if st.sidebar.button("🔄 Refresh IBGE API cache"):
    fetch_ibge_full_from_api.clear()
    st.sidebar.success("Cleared cached IBGE API download.")


# =========================================================
#             PART 4: AI WRAPPER (OPTIONAL)
# =========================================================
def clean_json_response(text: str) -> str:
    if not text:
        return "{}"
    text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text

def safe_json_loads(text: str):
    try:
        return json.loads(clean_json_response(text))
    except Exception:
        return None

def clean_html_for_ai(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup(["script", "style", "svg", "noscript", "img", "iframe"]):
        element.decompose()
    text = str(soup)
    if len(text) > 90000:
        return text[:65000] + "\n<!-- snip -->\n" + text[-20000:]
    return text

def call_ai_api(prompt: str, provider: str, key: str) -> Optional[str]:
    if not key:
        return None
    headers = {"Content-Type": "application/json"}
    try:
        if "OpenAI" in provider:
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "Return STRICT JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 1200,
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                return f"__HTTP_ERROR__ {resp.status_code}: {resp.text}"
            return resp.json()["choices"][0]["message"]["content"]

        if "Gemini" in provider:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"},
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                return f"__HTTP_ERROR__ {resp.status_code}: {resp.text}"
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

        if "Anthropic" in provider:
            url = "https://api.anthropic.com/v1/messages"
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
            payload = {"model": "claude-3-5-sonnet-20241022", "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]}
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                return f"__HTTP_ERROR__ {resp.status_code}: {resp.text}"
            return resp.json()["content"][0]["text"]

        if "DeepSeek" in provider:
            url = "https://api.deepseek.com/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "Return STRICT JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 1200,
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                return f"__HTTP_ERROR__ {resp.status_code}: {resp.text}"
            return resp.json()["choices"][0]["message"]["content"]

    except Exception as e:
        return f"__HTTP_ERROR__ exception: {repr(e)}"
    return None

def agent_learn_selectors(html_content: str, current_url: str, provider: str, key: str) -> Optional[Dict[str, str]]:
    if not html_content or len(html_content) < 800:
        return None

    prompt = f"""
You are a web scraping expert. Analyze HTML from {current_url}.

Return STRICT JSON in this shape:
{{
  "selectors": {{
    "name_element": "CSS selector that selects person names",
    "next_element": "CSS selector for a Next-page clickable element (link OR button OR input). If unsure, empty string."
  }}
}}

Output JSON only.

HTML:
{clean_html_for_ai(html_content)}
""".strip()

    raw = call_ai_api(prompt, provider, key)
    if not raw:
        return None
    if raw.startswith("__HTTP_ERROR__"):
        return {"__error__": raw}

    data = safe_json_loads(raw)
    if not isinstance(data, dict):
        return None
    sel = data.get("selectors")
    if not isinstance(sel, dict):
        return None

    return {
        "name_element": str(sel.get("name_element") or "").strip(),
        "next_element": str(sel.get("next_element") or "").strip(),
    }


# =========================================================
#             PART 5: REQUESTS FETCHER
# =========================================================
def fetch_native(method: str, url: str, data: Optional[dict], tls_impersonation: bool = False):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
        "Referer": url,
    }
    try:
        if tls_impersonation and HAS_CURL:
            if method.upper() == "POST":
                return crequests.post(url, headers=headers, data=data or {}, impersonate="chrome110", timeout=25)
            return crequests.get(url, headers=headers, impersonate="chrome110", timeout=25)

        if method.upper() == "POST":
            return requests.post(url, headers=headers, data=data or {}, timeout=25)
        return requests.get(url, headers=headers, timeout=25)

    except Exception:
        return None


# =========================================================
#             PART 6: SELENIUM DRIVER (BEST-EFFORT, FREE)
# =========================================================
def get_driver(headless: bool = True):
    """
    Streamlit Cloud-friendly Selenium setup (free).
    Priority:
      1) system chromedriver (installed via packages.txt: chromium-driver)
      2) webdriver_manager with Chromium
      3) selenium manager fallback
    """
    if not HAS_SELENIUM:
        return None

    options = Options()
    if headless:
        # new headless mode recommended
        options.add_argument("--headless=new")

    # Critical flags for Streamlit Cloud containers
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--remote-allow-origins=*")

    # 1) Use system chromedriver if present (typical for Streamlit Cloud)
    system_driver = "/usr/bin/chromedriver"
    if os.path.exists(system_driver):
        try:
            return webdriver.Chrome(service=Service(system_driver), options=options)
        except Exception:
            pass

    # 2) webdriver_manager pinned to Chromium
    try:
        return webdriver.Chrome(
            service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
            options=options
        )
    except Exception:
        pass

    # 3) Selenium manager fallback
    try:
        return webdriver.Chrome(options=options)
    except Exception:
        return None


# =========================================================
#             PART 7: UNIVERSAL PAGINATION HELPERS
# =========================================================
def request_fingerprint(method: str, url: str, data: Optional[dict]) -> str:
    return f"{method.upper()}|{url}|{json.dumps(data or {}, sort_keys=True, ensure_ascii=False)}"

def extract_form_request_from_element(el, current_url: str) -> Optional[Dict[str, Any]]:
    if el is None:
        return None
    if el.name not in ("button", "input"):
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
        if not nm:
            continue
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

def find_next_request_by_selector(html: str, current_url: str, next_selector: Optional[str]) -> Optional[Dict[str, Any]]:
    if not next_selector:
        return None
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(next_selector)
    if not el:
        return None

    base = soup.find("base", href=True)
    base_url = base["href"] if base else current_url

    if el.name == "a" and el.get("href"):
        return {"method": "GET", "url": urljoin(base_url, el["href"]), "data": None}

    if el.name in ("button", "input"):
        return extract_form_request_from_element(el, current_url=base_url)

    return None

def find_next_request_heuristic(html: str, current_url: str, manual_next: Optional[str] = None) -> Optional[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    base = soup.find("base", href=True)
    base_url = base["href"] if base else current_url

    # 1) manual selector
    if manual_next:
        req = find_next_request_by_selector(html, base_url, manual_next)
        if req:
            return req

    # 2) rel next link
    el = soup.select_one("a[rel='next'][href]")
    if el and el.get("href"):
        return {"method": "GET", "url": urljoin(base_url, el["href"]), "data": None}

    # 3) anchors by text / aria-label
    next_texts = {"next", "next page", "older", ">", "›", "»", "more"}
    for a in soup.select("a[href]"):
        t = (a.get_text(" ", strip=True) or "").strip().lower()
        aria = (a.get("aria-label") or "").strip().lower()
        if t in next_texts or aria in next_texts or "next" in aria:
            return {"method": "GET", "url": urljoin(base_url, a["href"]), "data": None}

    def looks_like_next(s: str) -> bool:
        s = (s or "").strip().lower()
        if s in next_texts:
            return True
        if "next" in s or "more" in s:
            return True
        return False

    # 4) buttons/inputs by label/value
    for btn in soup.find_all(["button", "input"]):
        if btn.name == "button":
            if looks_like_next(btn.get_text(" ", strip=True)) or looks_like_next(btn.get("aria-label", "")):
                req = extract_form_request_from_element(btn, current_url=base_url)
                if req:
                    return req
        elif btn.name == "input":
            t = btn.get("value", "") or btn.get("aria-label", "") or ""
            if looks_like_next(t):
                req = extract_form_request_from_element(btn, current_url=base_url)
                if req:
                    return req

    # 5) query increment if page info exists
    text = soup.get_text(" ", strip=True)
    m = re.search(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", text, flags=re.IGNORECASE)
    if m:
        cur, total = int(m.group(1)), int(m.group(2))
        if cur < total:
            u = urlparse(base_url)
            qs = parse_qs(u.query)
            for k in ["page", "p", "pg", "paging", "start", "offset"]:
                if k in qs:
                    try:
                        val = int(qs[k][0])
                        qs[k] = [str(val + 1)]
                        return {"method": "GET", "url": u._replace(query=urlencode(qs, doseq=True)).geturl(), "data": None}
                    except Exception:
                        pass

    # if URL already has a page-like param
    u = urlparse(base_url)
    qs = parse_qs(u.query)
    for k in ["page", "p", "pg"]:
        if k in qs:
            try:
                val = int(qs[k][0])
                qs[k] = [str(val + 1)]
                return {"method": "GET", "url": u._replace(query=urlencode(qs, doseq=True)).geturl(), "data": None}
            except Exception:
                pass

    return None


# =========================================================
#             PART 8: EXTRACTION
# =========================================================
def heuristic_extract_names(html_content: str, name_selector: Optional[str] = None) -> List[str]:
    soup = BeautifulSoup(html_content, "html.parser")
    out: List[str] = []

    if name_selector:
        for el in soup.select(name_selector):
            cand = clean_extracted_name(el.get_text(" ", strip=True))
            if cand:
                out.append(cand)
        return list(dict.fromkeys(out))

    # If a table has "Name" header, treat first column as name
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if any("name" in h for h in headers):
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if not tds:
                    continue
                cand = clean_extracted_name(tds[0].get_text(" ", strip=True))
                if cand:
                    out.append(cand)
            if out:
                return list(dict.fromkeys(out))

    # Otherwise scan headings/links
    for sel in ["h3", "h4", "h2", "a"]:
        for el in soup.select(sel):
            cand = clean_extracted_name(el.get_text(" ", strip=True))
            if cand:
                out.append(cand)

    return list(dict.fromkeys(out))

def extract_with_selectors(html: str, current_url: str, selectors: Dict[str, str]) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    names: List[str] = []

    name_sel = (selectors.get("name_element") or "").strip()
    if name_sel:
        for el in soup.select(name_sel):
            cand = clean_extracted_name(el.get_text(" ", strip=True))
            if cand:
                names.append(cand)
    names = list(dict.fromkeys(names))

    next_sel = (selectors.get("next_element") or "").strip()
    next_req = find_next_request_by_selector(html, current_url, next_sel) if next_sel else None
    return names, next_req


# =========================================================
#             PART 9: MATCHING
# =========================================================
def rank_score(rank: int, top_n: int, max_points: int = 50) -> int:
    if rank <= 0 or top_n <= 0:
        return 0
    return max(1, int(max_points * (top_n - rank + 1) / top_n))

def match_names_detailed(names: List[str], source: str, top_first: int, top_surname: int) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    seen = set()

    for n in names:
        n = " ".join(str(n).split())
        if n in seen:
            continue
        seen.add(n)

        parts = n.strip().split()
        if len(parts) < 2:
            continue

        f = normalize_token(parts[0])
        l = normalize_token(parts[-1])
        if not f or not l:
            continue
        if f in BLOCKLIST_SURNAMES or l in BLOCKLIST_SURNAMES:
            continue

        rank_f = first_name_ranks.get(f, 0)
        rank_l = surname_ranks.get(l, 0)

        score = 0
        if rank_f > 0:
            score += rank_score(rank_f, top_first, 50)
        if rank_l > 0:
            score += rank_score(rank_l, top_surname, 50)

        if score > 0:
            found.append({
                "Full Name": n,
                "Brazil Score": score,
                "First Rank": rank_f if rank_f > 0 else None,
                "Surname Rank": rank_l if rank_l > 0 else None,
                "Source": source,
                "AI_Observation": "Not Run",
            })

    return found

def batch_verify_names_nondestructive(df: pd.DataFrame, provider: str, key: str) -> pd.DataFrame:
    if df.empty or not key:
        return df

    names = df["Full Name"].astype(str).unique().tolist()
    observations: Dict[str, str] = {}
    chunk_size = 20
    prog = st.progress(0)

    for i in range(0, len(names), chunk_size):
        chunk = names[i:i + chunk_size]
        prompt = f"""
For each string, decide if it looks like a HUMAN PERSON NAME (vs junk/navigation/company).
Return STRICT JSON: keys are the input strings, values are short labels like:
"Valid person name", "Looks like junk/navigation", "Looks like organization", "Ambiguous".

INPUT: {json.dumps(chunk)}
""".strip()

        resp = call_ai_api(prompt, provider, key)
        if resp and not resp.startswith("__HTTP_ERROR__"):
            data = safe_json_loads(resp)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, str):
                        observations[k] = v

        prog.progress(min((i + chunk_size) / max(1, len(names)), 1.0))

    df["AI_Observation"] = df["Full Name"].map(observations).fillna(df.get("AI_Observation", "Pending"))
    return df


# =========================================================
#             PART 10: ACTIVE SEARCH (MULTI FREE ENGINES)
# =========================================================
def detect_search_form(html: str, base_url: str) -> Optional[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    if not forms:
        return None

    def score_form(f) -> int:
        score = 0
        action = (f.get("action") or "").lower()
        method = (f.get("method") or "get").lower()

        if any(k in action for k in ("search", "directory", "people", "staff", "student")):
            score += 3
        if method in ("get", "post"):
            score += 1

        inputs = f.find_all("input")
        textish = []
        for inp in inputs:
            t = (inp.get("type") or "").lower()
            if t in ("text", "search") or (t == "" and inp.get("name")):
                textish.append(inp)
        if len(textish) >= 1:
            score += 3
        if len(textish) == 1:
            score += 2

        for inp in textish:
            nm = (inp.get("name") or "").lower()
            if nm in ("q", "query", "search", "name", "keyword", "term"):
                score += 2

        return score

    best = sorted(forms, key=score_form, reverse=True)[0]
    if score_form(best) <= 2:
        return None

    action = best.get("action") or base_url
    action_url = urljoin(base_url, action)
    method = (best.get("method") or "GET").upper()

    input_candidates = []
    for inp in best.find_all("input"):
        t = (inp.get("type") or "").lower()
        nm = inp.get("name")
        if not nm:
            continue
        if t in ("text", "search") or t == "":
            input_candidates.append(inp)

    if not input_candidates:
        return None

    preferred = ["q", "query", "search", "name", "keyword", "term"]
    input_name = None
    for p in preferred:
        for inp in input_candidates:
            if (inp.get("name") or "").lower() == p:
                input_name = inp.get("name")
                break
        if input_name:
            break
    if not input_name:
        input_name = input_candidates[0].get("name")

    hidden = {}
    for inp in best.find_all("input"):
        nm = inp.get("name")
        if not nm:
            continue
        t = (inp.get("type") or "").lower()
        if t in ("hidden", "submit"):
            hidden[nm] = inp.get("value", "")

    return {"method": method, "action_url": action_url, "input_name": input_name, "hidden": hidden}

def build_url_param_search(url: str, term: str, forced_param: Optional[str] = None) -> List[str]:
    param_names = [forced_param] if forced_param else ["q", "query", "search", "name", "keyword", "term"]
    out = []
    for p in param_names:
        if not p:
            continue
        u = urlparse(url)
        qs = parse_qs(u.query)
        qs[p] = [term]
        out.append(u._replace(query=urlencode(qs, doseq=True)).geturl())
    return out

def try_requests_form_search(start_url: str, term: str, cached_form: Optional[Dict[str, Any]]) -> Optional[requests.Response]:
    form = cached_form
    if not form:
        r0 = fetch_native("GET", start_url, None, tls_impersonation=use_browserlike_tls)
        if not r0 or getattr(r0, "status_code", None) != 200:
            return None
        form = detect_search_form(r0.text, start_url)
        if not form:
            return None

    method = form["method"]
    action_url = form["action_url"]
    data = dict(form.get("hidden") or {})
    data[form["input_name"]] = term

    if method == "GET":
        u = urlparse(action_url)
        qs = parse_qs(u.query)
        for k, v in data.items():
            qs[k] = [v]
        url2 = u._replace(query=urlencode(qs, doseq=True)).geturl()
        return fetch_native("GET", url2, None, tls_impersonation=use_browserlike_tls)

    return fetch_native("POST", action_url, data, tls_impersonation=use_browserlike_tls)

def try_requests_urlparam_search(start_url: str, term: str, forced_param: Optional[str] = None) -> Optional[requests.Response]:
    for candidate in build_url_param_search(start_url, term, forced_param=forced_param):
        r = fetch_native("GET", candidate, None, tls_impersonation=use_browserlike_tls)
        if r and getattr(r, "status_code", None) == 200 and r.text and len(r.text) > 300:
            return r
    return None

def selenium_search(driver, start_url: str, term: str) -> Optional[str]:
    try:
        driver.get(start_url)
        time.sleep(2)

        # Try common search input patterns
        candidates = [
            "input[type='search']",
            "input[name='q']",
            "input[name='query']",
            "input[aria-label='Search']",
            "input[placeholder*='search' i]",
            "input[placeholder*='name' i]",
        ]
        sel = None
        for c in candidates:
            if len(driver.find_elements(By.CSS_SELECTOR, c)) > 0:
                sel = c
                break
        if not sel:
            return None

        inp = driver.find_element(By.CSS_SELECTOR, sel)
        inp.click()
        inp.send_keys(Keys.CONTROL + "a")
        inp.send_keys(Keys.BACKSPACE)
        inp.send_keys(term)
        inp.send_keys(Keys.RETURN)
        time.sleep(max(1, search_delay))

        return driver.page_source
    except Exception:
        return None


# =========================================================
#             PART 11: MAIN UI + EXECUTION
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", placeholder="https://directory.example.com")
max_pages = c2.number_input("Max Pages", 1, 500, 10)

st.write("---")
mode = st.radio(
    "Mode:",
    [
        "Classic Directory (Native/Fast)",
        "Infinite Scroller (Selenium - best-effort)",
        "Active Search Injection (Brute Force Surnames)",
    ]
)

if mode.startswith("Infinite") and not HAS_SELENIUM:
    st.warning("Selenium not installed. Infinite Scroller will not work here (Classic / Active Search still work).")

if st.button("🚀 Start Mission", type="primary"):
    st.session_state.running = True

if st.session_state.matches:
    st.dataframe(pd.DataFrame(st.session_state.matches), use_container_width=True, height=240)

if st.session_state.running:
    if not start_url:
        st.error("Missing Target URL")
        st.stop()

    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()

    manual_overrides: Dict[str, str] = {}
    if manual_name_selector.strip():
        manual_overrides["name_element"] = manual_name_selector.strip()
    if manual_next_selector.strip():
        manual_overrides["next_element"] = manual_next_selector.strip()

    # ------------------------------------------------------------------
    # BRANCH A: CLASSIC DIRECTORY (UNIVERSAL PAGINATION)
    # ------------------------------------------------------------------
    if mode.startswith("Classic"):
        current_req = {"method": "GET", "url": start_url, "data": None}

        for page in range(1, int(max_pages) + 1):
            if not st.session_state.running:
                break

            fp = request_fingerprint(current_req["method"], current_req["url"], current_req.get("data"))
            if fp in st.session_state.visited_fps:
                status_log.info("🏁 Pagination loop detected; stopping.")
                break
            st.session_state.visited_fps.add(fp)

            status_log.update(label=f"Scanning Page {page}...", state="running")

            r = fetch_native(current_req["method"], current_req["url"], current_req.get("data"), tls_impersonation=use_browserlike_tls)
            if not r or getattr(r, "status_code", None) != 200:
                status_log.warning(f"Fetch failed. HTTP={getattr(r, 'status_code', None)}")
                break

            raw_html = r.text
            st.session_state.pages_scanned += 1

            names: List[str] = []
            next_req: Optional[Dict[str, Any]] = None

            selectors = dict(st.session_state.learned_selectors) if isinstance(st.session_state.learned_selectors, dict) else {}
            selectors.update(manual_overrides)

            # 1) selectors
            if selectors.get("name_element"):
                n2, nx2 = extract_with_selectors(raw_html, current_req["url"], selectors)
                if n2:
                    names = n2
                    status_log.write(f"⚡ Selector extracted {len(names)} names.")
                if nx2:
                    next_req = nx2

            # 2) heuristic extraction
            if not names:
                names = heuristic_extract_names(raw_html, manual_name_selector.strip() if manual_name_selector else None)
                if names:
                    status_log.write(f"🧩 Heuristic extracted {len(names)} names.")

            # 3) heuristic pagination
            if not next_req:
                next_req = find_next_request_heuristic(
                    raw_html,
                    current_req["url"],
                    manual_next_selector.strip() if manual_next_selector else None
                )

            # 4) optional AI learn (one-time)
            if api_key and not st.session_state.learned_selectors and (len(names) == 0 or not next_req):
                status_log.write("🧠 AI learning selectors (one-time)...")
                learned = agent_learn_selectors(raw_html, current_req["url"], ai_provider, api_key)
                if isinstance(learned, dict) and learned.get("__error__"):
                    if show_debug_ai_payload:
                        status_log.error(str(learned["__error__"]))
                    else:
                        status_log.error("❌ AI failed to read page.")
                elif isinstance(learned, dict):
                    learned.update(manual_overrides)
                    st.session_state.learned_selectors = learned
                    status_log.success(f"🎓 Learned selectors: {learned}")

                    n3, nx3 = extract_with_selectors(raw_html, current_req["url"], learned)
                    if len(n3) > len(names):
                        names = n3
                    if nx3:
                        next_req = nx3
                    if not next_req:
                        next_req = find_next_request_heuristic(raw_html, current_req["url"])

            if names:
                matches = match_names_detailed(names, f"Page {page}", int(limit_first), int(limit_surname))
                if matches:
                    st.session_state.matches.extend(matches)
                    table_placeholder.dataframe(pd.DataFrame(st.session_state.matches), height=320, use_container_width=True)
                    status_log.write(f"✅ Added {len(matches)} matches.")
                else:
                    status_log.write("🤷 Names extracted, but none matched current IBGE Top-N filters.")
            else:
                status_log.write("🤷 No names extracted.")

            if next_req and next_req.get("url"):
                current_req = {
                    "method": next_req.get("method", "GET").upper(),
                    "url": next_req["url"],
                    "data": next_req.get("data"),
                }
                status_log.write(f"➡️ Next ({current_req['method']}): {current_req['url']}")
            else:
                status_log.info("🏁 No more pages detected.")
                break

            time.sleep(search_delay)

    # ------------------------------------------------------------------
    # BRANCH B: INFINITE SCROLLER (SELENIUM best-effort)
    # ------------------------------------------------------------------
    elif mode.startswith("Infinite"):
        status_log.write("🧭 Infinite Scroller: Selenium best-effort (may fail on Streamlit Cloud)")
        if not HAS_SELENIUM:
            st.error("Selenium not installed. Use Classic or Active Search modes.")
            st.session_state.running = False
            st.stop()

        driver = get_driver(headless=run_headless)
        if not driver:
            st.error("Selenium could not start here. Use Classic mode or Active Search requests engines.")
            st.session_state.running = False
            st.stop()

        try:
            driver.get(start_url)
            time.sleep(3)
            for k in range(int(max_pages)):
                if not st.session_state.running:
                    break
                status_log.update(label=f"Scrolling batch {k+1}/{int(max_pages)}...", state="running")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(max(1, search_delay))

                html = driver.page_source
                names = heuristic_extract_names(html, manual_name_selector.strip() if manual_name_selector else None)
                if names:
                    matches = match_names_detailed(names, f"Scroll batch {k+1}", int(limit_first), int(limit_surname))
                    if matches:
                        st.session_state.matches.extend(matches)
                        table_placeholder.dataframe(pd.DataFrame(st.session_state.matches), height=320, use_container_width=True)
                        status_log.write(f"✅ Added {len(matches)} matches.")
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # BRANCH C: ACTIVE SEARCH INJECTION (MULTI FREE ENGINES)
    # ------------------------------------------------------------------
    else:
        status_log.write("🔎 Active Search Injection: multiple free engines")
        cached_form = None

        # Detect form once if we are going to use it
        if active_search_engine in (
            "Auto (Requests Form → Requests URL params → Selenium Chromium)",
            "Requests: Form submit (no Selenium)",
        ):
            r0 = fetch_native("GET", start_url, None, tls_impersonation=use_browserlike_tls)
            if r0 and getattr(r0, "status_code", None) == 200 and r0.text:
                cached_form = detect_search_form(r0.text, start_url)

        if cached_form:
            status_log.success(f"🔍 Search form found: {cached_form['method']} {cached_form['action_url']} input={cached_form['input_name']}")
        else:
            status_log.write("ℹ️ No obvious form cached (will use URL params and/or Selenium).")

        driver = None
        if active_search_engine in (
            "Auto (Requests Form → Requests URL params → Selenium Chromium)",
            "Selenium: Chromium (best-effort on Streamlit Cloud)",
        ):
            if HAS_SELENIUM:
                driver = get_driver(headless=run_headless)
                if not driver:
                    status_log.warning("Selenium could not start here. Will use requests engines only.")

        def extract_and_add(html: str, source: str) -> int:
            raw_names = heuristic_extract_names(html, manual_name_selector.strip() if manual_name_selector else None)
            if not raw_names:
                return 0
            matches = match_names_detailed(raw_names, source, int(limit_first), int(limit_surname))
            if matches:
                st.session_state.matches.extend(matches)
                table_placeholder.dataframe(pd.DataFrame(st.session_state.matches), height=320, use_container_width=True)
            return len(matches)

        def run_one_term(term: str) -> Optional[str]:
            forced_param = manual_search_param.strip() if manual_search_param.strip() else None

            if active_search_engine == "Requests: Form submit (no Selenium)":
                r = try_requests_form_search(start_url, term, cached_form)
                return r.text if r and getattr(r, "status_code", None) == 200 else None

            if active_search_engine == "Requests: URL params (no Selenium)":
                r = try_requests_urlparam_search(start_url, term, forced_param=forced_param)
                return r.text if r and getattr(r, "status_code", None) == 200 else None

            if active_search_engine == "Selenium: Chromium (best-effort on Streamlit Cloud)":
                if not driver:
                    return None
                return selenium_search(driver, start_url, term)

            # AUTO pipeline
            # 1) Requests form
            if cached_form:
                r = try_requests_form_search(start_url, term, cached_form)
                if r and getattr(r, "status_code", None) == 200 and r.text:
                    return r.text

            # 2) Requests URL params
            r = try_requests_urlparam_search(start_url, term, forced_param=forced_param)
            if r and getattr(r, "status_code", None) == 200 and r.text:
                return r.text

            # 3) Selenium
            if driver:
                html = selenium_search(driver, start_url, term)
                if html:
                    return html

            return None

        try:
            for i, surname in enumerate(sorted_surnames[: int(max_pages)]):
                if not st.session_state.running:
                    break

                status_log.update(label=f"🔎 Searching '{surname}' ({i+1}/{int(max_pages)})", state="running")
                html = run_one_term(surname)
                if not html:
                    status_log.warning(f"Search failed for '{surname}'.")
                    time.sleep(search_delay)
                    continue

                added = extract_and_add(html, f"Search: {surname} (p1)")
                if added:
                    status_log.write(f"✅ Added {added} matches from page 1.")

                # Optional: paginate within each search results page
                inner_pages = 3
                cur_url = start_url
                for p in range(2, inner_pages + 1):
                    next_req = find_next_request_heuristic(html, cur_url, manual_next_selector.strip() if manual_next_selector else None)
                    if not next_req or not next_req.get("url"):
                        break
                    r_next = fetch_native(next_req.get("method", "GET"), next_req["url"], next_req.get("data"), tls_impersonation=use_browserlike_tls)
                    if not r_next or getattr(r_next, "status_code", None) != 200:
                        break
                    html = r_next.text
                    cur_url = next_req["url"]
                    added2 = extract_and_add(html, f"Search: {surname} (p{p})")
                    if added2:
                        status_log.write(f"✅ Added {added2} matches from page {p}.")

                time.sleep(search_delay)

        finally:
            try:
                if driver:
                    driver.quit()
            except Exception:
                pass

    status_log.update(label="Scanning Complete", state="complete")
    st.session_state.running = False


# =========================================================
#             PART 12: VERIFY & EXPORT
# =========================================================
if st.session_state.matches:
    st.markdown("---")
    st.subheader("📤 Verify & Export")
    df = pd.DataFrame(st.session_state.matches)

    col_v, col_d1, col_d2 = st.columns([2, 1, 1])

    if use_ai_verification and api_key:
        if col_v.button("🤖 Run AI Verification (Add Observations)"):
            with st.spinner("Verifying..."):
                df = batch_verify_names_nondestructive(df, ai_provider, api_key)
                st.session_state.matches = df.to_dict("records")
                st.rerun()

    with col_d1:
        st.download_button("📥 CSV", df.to_csv(index=False).encode("utf-8"), "results.csv")

    with col_d2:
        try:
            b = io.BytesIO()
            with pd.ExcelWriter(b, engine="xlsxwriter") as w:
                df.to_excel(w, index=False)
            st.download_button("📥 Excel", b.getvalue(), "results.xlsx")
        except Exception:
            pass
