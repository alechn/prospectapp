import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import os
import subprocess
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
if "learned_selectors" not in st.session_state:
    st.session_state.learned_selectors = {}  # per-site selectors cache


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
search_delay = st.sidebar.slider("⏳ Wait Time (Sec)", 0, 30, 2)
use_browserlike_tls = st.sidebar.checkbox("Use browser-like requests (curl_cffi)", value=False)
if use_browserlike_tls and not HAS_CURL:
    st.sidebar.warning("curl_cffi not installed; falling back to requests.")
    use_browserlike_tls = False

st.sidebar.markdown("---")
st.sidebar.header("🧪 Selenium")
run_headless = st.sidebar.checkbox("Run Selenium headless", value=True)
selenium_wait = st.sidebar.slider("Selenium wait timeout", 3, 30, 10)

with st.sidebar.expander("🛠️ Advanced / Debug"):
    manual_name_selector = st.text_input("Manual Name Selector", placeholder="e.g. h3, table td.name")
    manual_next_selector = st.text_input("Manual Next Selector", placeholder="e.g. a[rel='next'], input[value*='Next']")
    manual_search_selector = st.text_input("Manual Search Box Selector", placeholder="e.g. input[name='q']")
    manual_search_button = st.text_input("Manual Search Button Selector", placeholder="e.g. button[type='submit']")
    manual_search_param = st.text_input("Manual Search Param (URL mode)", placeholder="e.g. q or query")

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

NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){1,5}$")

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

    # quick name-ish test
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
allow_unranked_names = st.sidebar.checkbox(
    "Allow non-IBGE names (low confidence)",
    value=False,
    help="Keep names that are missing IBGE ranks; they'll be marked Unranked and given a minimal score."
)

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

def load_ibge_full_best_effort(allow_api_fallback: bool, save_if_fetched: bool):
    if os.path.exists(IBGE_CACHE_FILE):
        try:
            with open(IBGE_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data["first_name_ranks"], data["surname_ranks"], data.get("meta", {}), "cache"
        except Exception:
            pass

    if allow_api_fallback:
        try:
            first_full, surname_full, meta = fetch_ibge_full_from_api()
            if save_if_fetched:
                try:
                    os.makedirs(os.path.dirname(IBGE_CACHE_FILE), exist_ok=True)
                    with open(IBGE_CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump({"meta": meta, "first_name_ranks": first_full, "surname_ranks": surname_full}, f, ensure_ascii=False)
                except Exception:
                    pass
            return first_full, surname_full, meta, "api"
        except Exception:
            pass

    return {}, {}, {}, "none"

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
    scope_label = "Allowing unranked spillover" if allow_unranked_names else "IBGE-ranked only"
    st.sidebar.success(
        f"✅ Using Top {int(limit_first)}/{int(limit_surname)} → {len(first_name_ranks)} first / {len(surname_ranks)} surname ({scope_label})"
    )


# =========================================================
#             MATCHING
# =========================================================
def rank_score(rank: int, top_n: int, max_points: int = 50) -> int:
    if rank <= 0 or top_n <= 0:
        return 0
    return max(1, int(max_points * (top_n - rank + 1) / top_n))

def match_names(names: List[str], source: str, allow_unranked: bool) -> Tuple[List[Dict[str, Any]], List[str]]:
    found: List[Dict[str, Any]] = []
    dropped_unranked: List[str] = []
    seen = set()
    for n in names:
        n = clean_extracted_name(n)
        if not n or n in seen:
            continue
        seen.add(n)

        parts = n.split()
        if len(parts) < 2:
            continue

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

        if score == 0 and not allow_unranked:
            dropped_unranked.append(n)
            continue

        first_rank_field: Optional[Any] = rf if rf > 0 else ("Unranked" if allow_unranked else None)
        surname_rank_field: Optional[Any] = rl if rl > 0 else ("Unranked" if allow_unranked else None)
        found.append({
            "Full Name": n,
            "Brazil Score": score if score > 0 else 1,
            "First Rank": first_rank_field,
            "Surname Rank": surname_rank_field,
            "Source": source
        })
    return found, dropped_unranked


# =========================================================
#             UNIVERSAL EXTRACTION (multi-selector)
# =========================================================
def extract_names_multi(html: str, manual_sel: Optional[str] = None) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")

    selectors = []
    if manual_sel:
        selectors.append(manual_sel.strip())

    # common directory patterns
    selectors += [
        "td.name", "td:nth-child(1)", "td:first-child",
        "li", "h3", "h4", "h2",
        ".person", ".profile", ".result", ".results",
        "a", "span", "div"
    ]

    out: List[str] = []
    for sel in selectors:
        for el in soup.select(sel):
            t = el.get_text(" ", strip=True)
            c = clean_extracted_name(t)
            if c:
                out.append(c)

        # stop early if we already have enough high-confidence names
        if len(out) >= 60 and sel in ("td:first-child", "h3", "h4", "td:nth-child(1)"):
            break

    # de-dupe preserving order
    dedup = list(dict.fromkeys(out))
    return dedup


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

    # rel=next
    el = soup.select_one("a[rel='next'][href]")
    if el:
        return {"method": "GET", "url": urljoin(base_url, el["href"]), "data": None}

    # link text
    for txt in ("next", "próxima", "seguinte", ">", "»"):
        el = soup.find("a", string=re.compile(txt, re.I))
        if el and el.get("href"):
            return {"method": "GET", "url": urljoin(base_url, el["href"]), "data": None}

    # button/input
    for txt in ("next", "próxima", "seguinte", "submit", "continuar", ">"):
        el = soup.find(lambda tag: tag.name in ("button", "input") and txt in (tag.get("value", "") + tag.get_text(" ")).lower())
        if el:
            req = extract_form_request_from_element(el, base_url)
            if req:
                return req

    return None


# =========================================================
#             SELENIUM HELPERS
# =========================================================
def get_driver(headless: bool = True):
    try:
        opts = Options()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1280,2000")
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=opts)
        return driver
    except Exception:
        return None

def selenium_find_search_input(driver):
    try:
        inputs = driver.find_elements(By.CSS_SELECTOR, "input, textarea")
        for inp in inputs:
            placeholder = (inp.get_attribute("placeholder") or "").lower()
            name = (inp.get_attribute("name") or "").lower()
            itype = (inp.get_attribute("type") or "").lower()
            if itype in ("search", "text", "", None) and any(
                kw in placeholder or kw in name for kw in ["search", "buscar", "nome", "name", "keyword", "term", "query"]
            ):
                return inp
        return None
    except Exception:
        return None

def selenium_submit_search(driver, elem, query: str):
    try:
        elem.clear()
        elem.send_keys(query)
        elem.send_keys(Keys.ENTER)
        try:
            btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            btn.click()
        except Exception:
            pass

        if manual_search_selector:
            try:
                elem2 = driver.find_element(By.CSS_SELECTOR, manual_search_selector)
                elem2.clear()
                elem2.send_keys(query)
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

def selenium_wait_for_results(driver, before_url: str, before_len: int, timeout: int) -> None:
    # Wait for either URL change or DOM length change (cheap & effective)
    def changed(d):
        try:
            if d.current_url != before_url:
                return True
            html = d.page_source or ""
            return abs(len(html) - before_len) > 300
        except Exception:
            return False
    WebDriverWait(driver, timeout).until(changed)


# =========================================================
#             MAIN UI
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", placeholder="https://directory.example.com")
max_pages = c2.number_input("Max Pages / Search Cycles", 1, 500, 10)

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

    # Show previous matches if any
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
                status_log.warning(f"Fetch failed. HTTP={getattr(r, 'status_code', None)}")
                break

            raw_html = r.text
            names = extract_names_multi(raw_html, manual_name_selector.strip() if manual_name_selector else None)

            status_log.write(f"🧩 Extracted {len(names)} candidates.")
            matches, dropped = match_names(names, f"Page {page}", allow_unranked_names)
            if matches:
                # dedupe by name
                for m in matches:
                    if m["Full Name"] not in all_seen:
                        all_seen.add(m["Full Name"])
                        all_matches.append(m)
                all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                st.session_state.matches = all_matches
                table_placeholder.dataframe(pd.DataFrame(all_matches), height=320, use_container_width=True)
                status_log.write(f"✅ Added {len(matches)} matches.")
            else:
                if dropped:
                    sample = ", ".join(dropped[:5])
                    status_log.write(
                        f"🤷 IBGE ranks filtered out {len(dropped)} names. Enable 'Allow non-IBGE names' to keep them (e.g., {sample})."
                    )
                else:
                    status_log.write("🤷 No matches. Adjust IBGE scope or enable 'Allow non-IBGE names'.")

            next_req = find_next_request_heuristic(
                raw_html,
                current_req["url"],
                manual_next_selector.strip() if manual_next_selector else None
            )
            if not next_req:
                status_log.info("🏁 No more pages detected.")
                break

            current_req = {
                "method": next_req.get("method", "GET").upper(),
                "url": next_req["url"],
                "data": next_req.get("data"),
            }
            status_log.write(f"➡️ Next: {current_req['method']} {current_req['url']}")
            time.sleep(search_delay)

    # ---------------------------
    # INFINITE SCROLLER MODE
    # ---------------------------
    elif mode.startswith("Infinite"):
        if not HAS_SELENIUM:
            st.error("Selenium not installed.")
            st.session_state.running = False
            st.stop()

        driver = get_driver(headless=run_headless)
        if not driver:
            st.error("Selenium could not start in this environment.")
            st.session_state.running = False
            st.stop()

        try:
            driver.get(start_url)
            time.sleep(3)
            for k in range(int(max_pages)):
                status_log.update(label=f"Scroll batch {k+1}/{int(max_pages)}...", state="running")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(max(1, search_delay))

                html = driver.page_source
                names = extract_names_multi(html, manual_name_selector.strip() if manual_name_selector else None)
                matches, dropped = match_names(names, f"Scroll batch {k+1}", allow_unranked_names)

                for m in matches:
                    if m["Full Name"] not in all_seen:
                        all_seen.add(m["Full Name"])
                        all_matches.append(m)
                all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                st.session_state.matches = all_matches

                if matches:
                    table_placeholder.dataframe(pd.DataFrame(all_matches), height=320, use_container_width=True)
                    status_log.write(f"✅ Added {len(matches)} matches.")
                elif dropped:
                    sample = ", ".join(dropped[:5])
                    status_log.write(
                        f"🤷 IBGE ranks filtered out {len(dropped)} names. Enable 'Allow non-IBGE names' to keep them (e.g., {sample})."
                    )
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    # ---------------------------
    # ACTIVE SEARCH INJECTION MODE (FIXED)
    # ---------------------------
    else:
        status_log.write("🔎 Active Search Injection (fixed): requests + selenium")

        # Start selenium first (since you WANT it)
        driver = get_driver(headless=run_headless)
        if not driver:
            status_log.warning("Selenium could not start here. Will use requests-only fallback.")

        # Requests fallback: try detect form on landing page
        landing_html = None
        r0 = fetch_native("GET", start_url, None)
        if r0 and getattr(r0, "status_code", None) == 200:
            landing_html = r0.text

        # For url-param fallback
        forced_param = manual_search_param.strip() if manual_search_param.strip() else None
        param_candidates = [forced_param] if forced_param else ["q", "query", "search", "name", "keyword", "term"]

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

        # Go
        for i, surname in enumerate(sorted_surnames[: int(max_pages)]):
            status_log.update(label=f"🔎 Searching '{surname}' ({i+1}/{int(max_pages)})", state="running")

            html = None

            # Selenium path
            if driver:
                try:
                    driver.get(start_url)
                    time.sleep(2)

                    # cookie click best-effort
                    try:
                        driver.execute_script(
                            "document.querySelectorAll('button,a').forEach(b=>{if(/accept|agree|cookie/i.test(b.innerText))b.click()})"
                        )
                    except Exception:
                        pass

                    sel_input = selenium_find_search_input(driver)
                    if not sel_input:
                        status_log.warning("No search input found by Selenium; using requests fallback.")
                    else:
                        before_url = driver.current_url
                        before_len = len(driver.page_source or "")

                        ok = selenium_submit_search(driver, sel_input, surname)
                        if ok:
                            try:
                                selenium_wait_for_results(driver, before_url, before_len, timeout=int(selenium_wait))
                            except Exception:
                                # even if wait fails, still try scrape
                                pass
                            html = driver.page_source
                except Exception as e:
                    status_log.warning(f"Selenium search failed: {repr(e)}")
                    html = None

            # Requests fallback
            if not html:
                html = requests_urlparam_search(surname)

            if not html:
                status_log.write("🤷 No HTML results page.")
                time.sleep(search_delay)
                continue

            # Extract + match
            names = extract_names_multi(html, manual_name_selector.strip() if manual_name_selector else None)
            if not names:
                status_log.write("🤷 No names extracted from results page.")
                time.sleep(search_delay)
                continue

            matches, dropped = match_names(names, f"Search: {surname}", allow_unranked_names)
            if matches:
                for m in matches:
                    if m["Full Name"] not in all_seen:
                        all_seen.add(m["Full Name"])
                        all_matches.append(m)
                all_matches.sort(key=lambda x: x["Brazil Score"], reverse=True)
                st.session_state.matches = all_matches
                table_placeholder.dataframe(pd.DataFrame(all_matches), height=320, use_container_width=True)
                status_log.write(f"✅ Added {len(matches)} matches.")
            else:
                if dropped:
                    sample = ", ".join(dropped[:5])
                    status_log.write(
                        f"🤷 Names found, but IBGE Top-N removed {len(dropped)} of them. Example discards: {sample}. Enable 'Allow non-IBGE names' to keep them."
                    )
                else:
                    status_log.write("🤷 Names found, but none matched current IBGE Top-N filters. Try enabling 'Allow non-IBGE names'.")

            time.sleep(search_delay)

        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    status_log.update(label="Scanning Complete", state="complete")
    st.session_state.running = False

    # exports
    if st.session_state.matches:
        df = pd.DataFrame(st.session_state.matches)
        st.markdown("---")
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("📥 CSV", df.to_csv(index=False).encode("utf-8"), "results.csv")
        with c2:
            b = io.BytesIO()
            with pd.ExcelWriter(b, engine="xlsxwriter") as w:
                df.to_excel(w, index=False)
            st.download_button("📥 Excel", b.getvalue(), "results.xlsx")
