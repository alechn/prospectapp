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
st.caption("Probe → Detect → Decide → Extract (Form-aware, Query-aware)")

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
# Extraction (still intentionally simple for debugging)
# ================================
NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\. ]{3,}$")
BAD_PHRASES = {
    "MASSACHUSETTS INSTITUTE OF TECHNOLOGY",
    "INNOVATION", "CAMPUS LIFE", "NEWS", "ALUMNI", "GIVE",
    "VISIT", "MAP", "EVENTS", "PEOPLE", "JOBS", "PRIVACY",
    "ACCESSIBILITY", "SOCIAL MEDIA HUB", "LIFELONG LEARNING"
}

def looks_like_person_name(txt: str) -> bool:
    if not txt:
        return False
    txt = " ".join(txt.split()).strip()
    if not NAME_RE.match(txt):
        return False
    up = txt.upper()
    if up in BAD_PHRASES:
        return False
    # keep permissive for now; we'll tighten later
    words = txt.split()
    return 2 <= len(words) <= 5

def extract_names_multi(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []
    # Prefer mailto anchors if present (often “people-ish”)
    for el in soup.select("a[href^='mailto:']"):
        t = el.get_text(" ", strip=True)
        if looks_like_person_name(t):
            out.append(t)

    # General fallbacks inside main content
    main = soup.select_one("main") or soup
    for el in main.select("h2, h3, h4, strong, a"):
        t = el.get_text(" ", strip=True)
        if looks_like_person_name(t):
            out.append(t)

    # de-dupe preserve order
    return list(dict.fromkeys(out))

# ================================
# Universal: discover search form on the start page
# ================================
def discover_search_form(base_url: str, status) -> Optional[Dict[str, str]]:
    """
    Returns dict with:
      - action_url: absolute URL
      - method: GET/POST (we use GET only in this debugger)
      - query_param: input name to use (e.g., q)
      - extra_params: optional fixed params (like tab=directory)
    """
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

    # Heuristic: choose a form that looks like a search form
    best = None
    best_score = -1

    for f in forms:
        action = (f.get("action") or "").strip()
        method = (f.get("method") or "GET").strip().upper()

        # find candidate inputs
        inputs = f.find_all("input")
        input_names = [i.get("name") for i in inputs if i.get("name")]

        score = 0
        if "search" in (action.lower() if action else ""):
            score += 3
        if any(n in ("q", "query", "search") for n in input_names):
            score += 3
        if any((i.get("type") or "").lower() in ("search", "query", "text") for i in inputs):
            score += 1
        if "search" in (f.get("id") or "").lower() or "search" in " ".join((f.get("class") or [])).lower():
            score += 1

        if score > best_score:
            best_score = score
            best = f

    if not best or best_score <= 0:
        log(status, "ℹ️ No search-like form found (heuristics).")
        return None

    action = (best.get("action") or "").strip() or base_url
    method = (best.get("method") or "GET").strip().upper()

    # Pick best query param name
    qparam = None
    for cand in ("q", "query", "search", "s"):
        if best.find("input", attrs={"name": cand}):
            qparam = cand
            break
    if not qparam:
        # fallback: first text-ish input
        for i in best.find_all("input"):
            t = (i.get("type") or "").lower()
            if t in ("search", "text", "query") and i.get("name"):
                qparam = i.get("name")
                break

    if not qparam:
        log(status, "⚠ Found a form but couldn't identify a query input name.")
        return None

    action_url = urljoin(base_url, action)

    # MIT-specific extra param is “tab=directory”, but we can also infer from page
    extra_params = {}
    # If there is any input/select named "tab" (rare), include its value
    tab_inp = best.find(["input", "select"], attrs={"name": "tab"})
    if tab_inp:
        v = tab_inp.get("value")
        if v:
            extra_params["tab"] = v

    return {
        "action_url": action_url,
        "method": method,
        "query_param": qparam,
        "extra_params": urlencode(extra_params, doseq=True) if extra_params else ""
    }

# ================================
# Build search URL using discovered form
# ================================
def build_search_url(form_info: Dict[str, str], term: str) -> str:
    action_url = form_info["action_url"]
    qparam = form_info["query_param"]

    u = urlparse(action_url)
    qs = parse_qs(u.query)
    qs[qparam] = [term]

    # Add MIT directory tab if action is /search and tab missing
    # (universal-ish rule: if page supports tabs, "directory" is common)
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
    # prioritize common names
    for sel in ["input[name='q']", "input[type='search']", "input[aria-label*='search' i]"]:
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

def selenium_wait_for_results(driver, surname: str, timeout: int, status, poll: float = 0.3):
    def fp(html: str) -> int:
        return hash("".join((html or "").split()))

    start = time.time()
    base = driver.page_source or ""
    base_fp = fp(base)
    target = surname.upper()

    log(status, f"🧪 Waiting for results for '{surname}' (query-aware)")

    while time.time() - start < timeout:
        html = driver.page_source or ""
        now_fp = fp(html)
        elapsed = round(time.time() - start, 1)

        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).upper()

        if any(x in text for x in ["NO RESULTS", "NO MATCH", "0 RESULTS"]):
            log(status, f"🧪 t={elapsed}s → no results signal")
            return "no_results", html

        # Only accept as "updated" if the page changed AND term appears somewhere
        if now_fp != base_fp and target in text:
            log(status, f"🧪 t={elapsed}s → results updated (term seen)")
            return "results", html

        log(status, f"🧪 t={elapsed}s → waiting…")
        time.sleep(poll)

    log(status, "🧪 TIMEOUT waiting for results")
    return "timeout", driver.page_source or ""

# ================================
# UNIVERSAL ENGINE
# ================================
def universal_active_search(url: str, term: str, timeout: int, status) -> Tuple[str, List[str], str]:
    # 0) Discover form on base page
    log(status, "🔎 Discovering search form…")
    form_info = discover_search_form(url, status)

    # 1) If we found a form: build a real search URL and request it
    if form_info and form_info.get("method", "GET").upper() == "GET":
        search_url = build_search_url(form_info, term)
        log(status, f"✅ Using discovered form: action={form_info['action_url']} param={form_info['query_param']}")
        log(status, f"🌐 Fetching search URL: {search_url}")

        try:
            r = requests.get(search_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                names = extract_names_multi(r.text)
                log(status, f"📦 Requests extracted candidates: {len(names)}")
                return "requests_form_search", names, search_url
            else:
                log(status, f"⚠ Search fetch status {r.status_code}")
        except Exception as e:
            log(status, f"⚠ Search fetch error: {e}")

    # 2) Fallback: naive query injection (only if form not found)
    log(status, "↩️ No usable form-based search; trying naive query injection…")
    for p in ["q", "query", "search", "s"]:
        u = urlparse(url)
        qs = parse_qs(u.query)
        qs[p] = [term]
        test_url = u._replace(query=urlencode(qs, doseq=True)).geturl()
        log(status, f"🔍 Trying naive server search: {test_url}")
        try:
            r = requests.get(test_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                names = extract_names_multi(r.text)
                log(status, f"📦 Requests extracted candidates: {len(names)}")
                # IMPORTANT: don't “accept” unless the term appears somewhere in text
                if term.lower() in (r.text or "").lower() and names:
                    return "requests_naive_search", names, test_url
        except Exception as e:
            log(status, f"⚠ Naive search error: {e}")

    # 3) Selenium fallback (query-aware waiting)
    log(status, "🤖 Falling back to Selenium…")
    driver = get_driver()
    try:
        driver.get(url)
        selenium_wait_ready(driver, timeout=10)
        inp = selenium_find_search_input(driver)
        if not inp:
            return "selenium_failed_no_input", [], url

        selenium_submit_search(driver, inp, term)
        state, html = selenium_wait_for_results(driver, term, timeout, status)
        names = extract_names_multi(html)
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
