import streamlit as st
import requests
import time
import re
from typing import List, Tuple, Optional, Dict
from urllib.parse import urlparse, urljoin, parse_qs, urlencode
from bs4 import BeautifulSoup

# ================================
# Selenium (fallback)
# ================================
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType

# ================================
# Streamlit UI
# ================================
st.set_page_config(page_title="Universal Active Search Debugger", layout="wide", page_icon="🧪")
st.title("🧪 Universal Active Search Debugger")
st.caption("Probe → Detect → Decide → Extract (Form-aware, JS-shell-aware, Query-aware)")

TARGET_URL = st.text_input("Target URL", "https://web.mit.edu/directory/")
SURNAME = st.text_input("Test Surname", "oliveira")
TIMEOUT = st.slider("Timeout (seconds)", 5, 30, 15)
RUN = st.button("▶ Run Debugger", type="primary")


# ================================
# Logging
# ================================
def log(status, msg: str):
    status.write(msg)


# ================================
# Name & result validation
# ================================
NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\. ]{3,}$")

BAD_EXACT = {
    "SEARCH RESULTS",
    "MASSACHUSETTS INSTITUTE OF TECHNOLOGY",
    "LIFELONG LEARNING",
    "INNOVATION",
    "CAMPUS LIFE",
    "NEWS",
    "ALUMNI",
    "GIVE",
    "VISIT",
    "MAP",
    "EVENTS",
    "PEOPLE",
    "JOBS",
    "PRIVACY",
    "ACCESSIBILITY",
    "SOCIAL MEDIA HUB",
}

def looks_like_person_name(txt: str) -> bool:
    if not txt:
        return False
    txt = " ".join(txt.split()).strip()
    if not NAME_RE.match(txt):
        return False

    up = txt.upper()
    if up in BAD_EXACT:
        return False

    # avoid generic headings
    if "RESULT" in up and len(txt.split()) <= 3:
        return False

    # (debugger-level) keep permissive but require 2-5 tokens
    words = txt.split()
    if not (2 <= len(words) <= 5):
        return False

    return True

def contains_query_in_text(html: str, term: str) -> bool:
    if not html:
        return False
    return term.lower() in html.lower()

def detect_js_shell(html: str) -> bool:
    """
    Detect pages where results are typically JS-rendered (CSE/Vue/React shells).
    If true, requests will often NOT contain actual results.
    """
    if not html:
        return True
    h = html.lower()

    # Google CSE / gsc signals
    if "cse.google.com" in h or "gcse" in h or "gsc-" in h:
        return True

    # Vue/React placeholders
    if "<result-list" in h or "data-vue" in h or "reactroot" in h or "__react" in h:
        return True

    return False

def results_seem_real(names: List[str], term: str) -> bool:
    """
    Only accept a result set if it isn't just headings/nav,
    and ideally contains the searched term somewhere.
    """
    if not names:
        return False

    # reject trivial “Search Results”-only style outputs
    if len(names) == 1 and names[0].strip().upper() in BAD_EXACT:
        return False

    # strong signal: any extracted name contains the surname token
    t = term.strip().lower()
    if any(t in n.lower() for n in names):
        return True

    # weak acceptance: at least 3 plausible person names
    if len(names) >= 3:
        return True

    return False


# ================================
# Extraction (Requests & Selenium)
# ================================
def extract_names_from_google_cse_dom(soup: BeautifulSoup) -> List[str]:
    """
    When Google CSE results are rendered, names/titles usually appear in:
      - .gs-title, .gs-title a
      - .gsc-webResult .gs-title
    We filter to plausible person names.
    """
    out: List[str] = []

    selectors = [
        ".gsc-webResult .gs-title",
        ".gsc-webResult .gs-title a",
        ".gsc-result .gs-title",
        ".gsc-result .gs-title a",
    ]
    for sel in selectors:
        for el in soup.select(sel):
            txt = el.get_text(" ", strip=True)
            if looks_like_person_name(txt):
                out.append(txt)

    return list(dict.fromkeys(out))

def extract_names_generic(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []

    # If CSE present, prioritize CSE extraction
    cse_names = extract_names_from_google_cse_dom(soup)
    if cse_names:
        return cse_names

    # Otherwise, generic extraction (scoped to main when possible)
    main = soup.select_one("main") or soup
    for el in main.select("h2, h3, h4, strong, a"):
        txt = el.get_text(" ", strip=True)
        if looks_like_person_name(txt):
            out.append(txt)

    return list(dict.fromkeys(out))


# ================================
# Discover search form on base page
# ================================
def discover_search_form(base_url: str, status) -> Optional[Dict[str, str]]:
    try:
        r = requests.get(base_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log(status, f"⚠ Base fetch status {r.status_code}")
            return None
        html = r.text
    except Exception as e:
        log(status, f"⚠ Base fetch error: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    if not forms:
        log(status, "ℹ️ No forms found on base page.")
        return None

    best = None
    best_score = -1
    for f in forms:
        action = (f.get("action") or "").strip()
        method = (f.get("method") or "GET").strip().upper()
        inputs = f.find_all("input")
        input_names = [i.get("name") for i in inputs if i.get("name")]

        score = 0
        if "search" in (action.lower() if action else ""):
            score += 3
        if any(n in ("q", "query", "search", "s") for n in input_names):
            score += 3
        if any((i.get("type") or "").lower() in ("search", "text", "query") for i in inputs):
            score += 1
        if "search" in (f.get("id") or "").lower() or "search" in " ".join((f.get("class") or [])).lower():
            score += 1
        if method == "GET":
            score += 1

        if score > best_score:
            best_score = score
            best = f

    if not best or best_score <= 0:
        log(status, "ℹ️ No search-like form found (heuristics).")
        return None

    action = (best.get("action") or "").strip() or base_url
    method = (best.get("method") or "GET").strip().upper()

    qparam = None
    for cand in ("q", "query", "search", "s"):
        if best.find("input", attrs={"name": cand}):
            qparam = cand
            break
    if not qparam:
        for i in best.find_all("input"):
            t = (i.get("type") or "").lower()
            if t in ("search", "text", "query") and i.get("name"):
                qparam = i.get("name")
                break

    if not qparam:
        log(status, "⚠ Found a form but couldn't identify a query input name.")
        return None

    action_url = urljoin(base_url, action)
    return {"action_url": action_url, "method": method, "query_param": qparam}


def build_search_url(action_url: str, query_param: str, term: str) -> str:
    u = urlparse(action_url)
    qs = parse_qs(u.query)
    qs[query_param] = [term]

    # Common pattern: a directory tab
    if "search" in u.path.lower() and "tab" not in qs:
        qs["tab"] = ["directory"]

    return u._replace(query=urlencode(qs, doseq=True)).geturl()


# ================================
# Selenium helpers
# ================================
def get_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
    return webdriver.Chrome(service=service, options=opts)

def selenium_wait_ready(driver, timeout=10):
    WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")

def selenium_find_search_input(driver):
    for sel in ["input[name='q']", "input[type='search']", "#es-search-form-input", "input[aria-label*='search' i]"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            try:
                if els[0].is_displayed() and els[0].is_enabled():
                    return els[0]
            except Exception:
                pass

    for el in driver.find_elements(By.TAG_NAME, "input"):
        try:
            if el.is_displayed() and el.is_enabled():
                return el
        except Exception:
            pass
    return None

def selenium_submit_search(driver, inp, term):
    inp.click()
    inp.send_keys(Keys.CONTROL + "a")
    inp.send_keys(Keys.BACKSPACE)
    inp.send_keys(term)
    inp.send_keys(Keys.RETURN)

def selenium_wait_for_cse_results(driver, term: str, timeout: int, status, poll: float = 0.25):
    """
    Query-aware waiting for Google CSE / JS results:
      - waits for result nodes to appear OR "no results"
      - avoids false positives from static page shell
    """
    start = time.time()
    target = term.strip().lower()

    log(status, f"🧪 Waiting for JS results for '{term}' (CSE-aware)")

    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)

        # "no results" text signals
        page_txt = (driver.page_source or "").lower()
        if any(x in page_txt for x in ["no results", "no match", "0 results", "did not match any"]):
            log(status, f"🧪 t={elapsed}s → no-results text detected")
            return "no_results", driver.page_source or ""

        # Google CSE result blocks
        result_blocks = driver.find_elements(By.CSS_SELECTOR, ".gsc-webResult, .gsc-result")
        if result_blocks:
            # confirm query is actually active in page text somewhere
            if target in page_txt:
                log(status, f"🧪 t={elapsed}s → CSE result blocks detected ({len(result_blocks)})")
                return "results", driver.page_source or ""
            # Sometimes query isn’t echoed; still accept if blocks are present and growing
            log(status, f"🧪 t={elapsed}s → result blocks present but query not echoed yet; waiting…")

        time.sleep(poll)

    log(status, "🧪 TIMEOUT waiting for JS results")
    return "timeout", driver.page_source or ""


# ================================
# Universal engine
# ================================
def universal_active_search(start_url: str, term: str, timeout: int, status) -> Tuple[str, List[str], str]:
    # 1) Discover search form from start_url
    log(status, "🔎 Discovering search form…")
    form = discover_search_form(start_url, status)

    # 2) If found, try requests GET on that search URL — but validate it's not a JS shell
    if form and form.get("method", "GET").upper() == "GET":
        search_url = build_search_url(form["action_url"], form["query_param"], term)
        log(status, f"✅ Using discovered form: action={form['action_url']} param={form['query_param']}")
        log(status, f"🌐 Fetching search URL (requests): {search_url}")

        try:
            r = requests.get(search_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            html = r.text if r.status_code == 200 else ""
        except Exception as e:
            log(status, f"⚠ Requests error: {e}")
            html = ""

        names = extract_names_generic(html)
        log(status, f"📦 Requests extracted candidates: {len(names)}")

        # Gate: if page is JS shell or results not real, do NOT accept
        if html and (detect_js_shell(html) or not results_seem_real(names, term)):
            log(status, "⚠ Requests page looks like JS shell / non-real results → forcing Selenium fallback")
        else:
            return "requests_form_search", names, search_url

        # If it was a JS shell, Selenium should go to the SEARCH URL directly
        selenium_target = search_url
    else:
        selenium_target = start_url

    # 3) Selenium fallback
    log(status, f"🤖 Selenium fallback starting at: {selenium_target}")
    driver = get_driver()
    try:
        driver.get(selenium_target)
        selenium_wait_ready(driver, timeout=10)

        inp = selenium_find_search_input(driver)
        if not inp:
            log(status, "❌ Selenium: no search input found")
            return "selenium_failed_no_input", [], driver.current_url

        selenium_submit_search(driver, inp, term)

        state, html = selenium_wait_for_cse_results(driver, term, timeout, status)
        names = extract_names_generic(html)
        log(status, f"📦 Selenium extracted candidates: {len(names)} (state={state})")

        return "selenium_dom", names, driver.current_url
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ================================
# RUN
# ================================
if RUN:
    status = st.status("Running universal active search...", expanded=True)

    strategy, names, used_url = universal_active_search(TARGET_URL, SURNAME, TIMEOUT, status)

    status.update(label="Done", state="complete")

    st.subheader("🧠 Result")
    st.write(f"**Strategy used:** `{strategy}`")
    st.write(f"**URL used:** {used_url}")
    st.write(f"**Names found:** {len(names)}")

    if names:
        st.dataframe({"Names": names})
