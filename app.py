import streamlit as st
import requests
import time
from typing import List, Tuple, Optional, Dict
from urllib.parse import urlparse, urljoin, parse_qs, urlencode
from bs4 import BeautifulSoup

# ================================
# Selenium
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
st.caption("Probe → Detect → Decide → Extract (Form-aware, JS-shell-aware, CSE-tab-aware)")

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
# JS shell detection
# ================================
def is_js_shell(html: str) -> bool:
    if not html:
        return True
    h = html.lower()
    if "cse.google.com" in h or "gcse" in h or "gsc-" in h:
        return True
    if "<result-list" in h or "__react" in h or "reactroot" in h:
        return True
    return False

# ================================
# Discover search form
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
        log(status, "⚠ Found a form but couldn't identify a query input name.")
        return None

    action_url = urljoin(base_url, action)
    return {"action_url": action_url, "method": method, "query_param": qparam}

def build_search_url(action_url: str, query_param: str, term: str) -> str:
    u = urlparse(action_url)
    qs = parse_qs(u.query)
    qs[query_param] = [term]
    if "search" in u.path.lower() and "tab" not in qs:
        qs["tab"] = ["directory"]  # MIT pattern
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
    for sel in ["#es-search-form-input", "input[name='q']", "input[type='search']"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            try:
                if els[0].is_displayed() and els[0].is_enabled():
                    return els[0]
            except Exception:
                pass
    # fallback any input
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

# ================================
# CSE wait + tab logic
# ================================
def cse_tab_labels(driver) -> List[str]:
    out = []
    for el in driver.find_elements(By.CSS_SELECTOR, ".gsc-tabsArea .gsc-tabHeader"):
        try:
            t = (el.text or "").strip()
            if t:
                out.append(t)
        except Exception:
            pass
    return out

def cse_active_tab_label(driver) -> str:
    for el in driver.find_elements(By.CSS_SELECTOR, ".gsc-tabsArea .gsc-tabHeader.gsc-tabhActive"):
        try:
            t = (el.text or "").strip()
            if t:
                return t
        except Exception:
            pass
    return ""

def cse_click_tab(driver, want_keywords=("directory", "people")) -> bool:
    tabs = driver.find_elements(By.CSS_SELECTOR, ".gsc-tabsArea .gsc-tabHeader")
    for el in tabs:
        try:
            t = (el.text or "").strip().lower()
            if any(k in t for k in want_keywords):
                el.click()
                return True
        except Exception:
            pass
    return False

def selenium_wait_for_cse_dom(driver, term: str, timeout: int, status, poll: float = 0.25):
    """
    Wait for CSE container, then results OR explicit no-results element.
    """
    start = time.time()
    log(status, f"🧪 Waiting for CSE to load for '{term}'")

    # wait for container
    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)
        cse_controls = driver.find_elements(By.CSS_SELECTOR, ".gsc-control-cse, .gcse-searchresults, .gsc-results-wrapper-visible")
        if cse_controls:
            log(status, f"🧪 t={elapsed}s → CSE container detected ({len(cse_controls)})")
            return True
        time.sleep(poll)

    log(status, "🧪 TIMEOUT: CSE container never appeared")
    return False

def selenium_wait_for_results_or_noresults(driver, timeout: int, status, poll: float = 0.25) -> Tuple[str, str]:
    start = time.time()
    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)

        results = driver.find_elements(By.CSS_SELECTOR, ".gsc-result, .gsc-webResult")
        nores = driver.find_elements(By.CSS_SELECTOR, ".gs-no-results-result, .gsc-no-results-result")

        # light debug heartbeat
        titles = driver.find_elements(By.CSS_SELECTOR, ".gsc-result .gs-title, .gsc-webResult .gs-title")
        peek = []
        for e in titles[:3]:
            try:
                if e.text.strip():
                    peek.append(e.text.strip())
            except Exception:
                pass

        log(status, f"🧪 t={elapsed}s → results={len(results)} nores={len(nores)} peek={peek}")

        if results:
            return "results", driver.page_source or ""
        if nores:
            return "no_results", driver.page_source or ""

        time.sleep(poll)

    return "timeout", driver.page_source or ""

# ================================
# Extraction: ONLY accept titles containing surname (debug-proof)
# ================================
def extract_cse_titles(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []
    for el in soup.select(".gsc-result .gs-title, .gsc-webResult .gs-title, .gsc-result .gs-title a, .gsc-webResult .gs-title a"):
        t = (el.get_text(" ", strip=True) or "").strip()
        if t:
            out.append(t)
    return list(dict.fromkeys(out))

def titles_containing_term(titles: List[str], term: str) -> List[str]:
    t = term.strip().lower()
    return [x for x in titles if t in x.lower()]

# ================================
# Universal engine
# ================================
def universal_active_search(start_url: str, term: str, timeout: int, status) -> Tuple[str, List[str], str]:
    log(status, "🔎 Discovering search form…")
    form = discover_search_form(start_url, status)

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

        req_titles = extract_cse_titles(html)
        log(status, f"📦 Requests CSE titles: {len(req_titles)}")

        if not html or is_js_shell(html):
            log(status, "⚠ Requests looks like JS shell → forcing Selenium")
            selenium_target = search_url
        else:
            # If somehow not shell, still only show matching titles for debug sanity
            return "requests_form_search", titles_containing_term(req_titles, term), search_url
    else:
        selenium_target = start_url

    # Selenium flow
    log(status, f"🤖 Selenium fallback starting at: {selenium_target}")
    driver = get_driver()
    try:
        driver.get(selenium_target)
        selenium_wait_ready(driver, timeout=10)

        inp = selenium_find_search_input(driver)
        if inp:
            selenium_submit_search(driver, inp, term)
        else:
            log(status, "ℹ️ No search input found; continuing (URL may already include query)")

        # Wait for CSE container
        ok = selenium_wait_for_cse_dom(driver, term, timeout, status)
        if not ok:
            return "selenium_timeout_no_cse", [], driver.current_url

        # Log tabs
        labels = cse_tab_labels(driver)
        active = cse_active_tab_label(driver)
        log(status, f"🧭 CSE tabs: {labels} | active='{active}'")

        # Try to click Directory/People tab if present AND not active
        if labels:
            if not any(k in (active or "").lower() for k in ("directory", "people")):
                clicked = cse_click_tab(driver, want_keywords=("directory", "people"))
                log(status, f"🧭 Clicked directory/people tab: {clicked}")
                # small settle
                time.sleep(0.3)

        # Wait for results/no-results AFTER tab selection
        state, html = selenium_wait_for_results_or_noresults(driver, timeout, status)

        titles = extract_cse_titles(html)
        matching = titles_containing_term(titles, term)

        log(status, f"📦 Selenium CSE titles: {len(titles)} | titles containing '{term}': {len(matching)} | state={state}")

        # IMPORTANT: only return matching items (so we stop lying with unrelated titles)
        return "selenium_dom", (matching[:25]), driver.current_url
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

    strategy, items, used_url = universal_active_search(TARGET_URL, SURNAME, TIMEOUT, status)

    status.update(label="Done", state="complete")

    st.subheader("🧠 Result")
    st.write(f"**Strategy used:** `{strategy}`")
    st.write(f"**URL used:** {used_url}")
    st.write(f"**Matching items shown:** {len(items)}")

    if items:
        st.dataframe({"Matches (must contain surname)": items})
    else:
        st.warning("No matching items. (This is good signal: it means we’re not accepting junk like 'Ascending Republic'.)")
