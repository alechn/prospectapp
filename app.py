import streamlit as st
import time
import re
import os
import json
import requests
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup

# =========================
# Selenium (optional)
# =========================
HAS_SELENIUM = False
HAS_WDM = False
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.common.exceptions import WebDriverException
    HAS_SELENIUM = True
except Exception:
    HAS_SELENIUM = False

try:
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    HAS_WDM = True
except Exception:
    HAS_WDM = False


# =========================================================
# Streamlit UI
# =========================================================
st.set_page_config(page_title="Universal Active Search Debugger", layout="wide", page_icon="🧪")
st.title("🧪 Universal Active Search Debugger")
st.caption("Probe → Detect → Decide → Extract (universal, log-heavy, tab-aware)")

c1, c2, c3 = st.columns([3, 1, 1])
TARGET_URL = c1.text_input("Target URL", "https://web.mit.edu/directory/")
TERM = c2.text_input("Test term (surname)", "oliveira")
TIMEOUT = c3.slider("Timeout (seconds)", 5, 60, 20)

st.markdown("### Controls")
colA, colB, colC, colD = st.columns(4)
USE_SELENIUM = colA.checkbox("Enable Selenium", value=True, disabled=not HAS_SELENIUM)
HEADLESS = colB.checkbox("Headless", value=True)
TRY_REQUESTS_PARAMS = colC.checkbox("Try server-side URL params first", value=True)
DEBUG_VERBOSE = colD.checkbox("Verbose logging", value=True)

st.markdown("### Advanced (optional overrides)")
a1, a2, a3 = st.columns(3)
MANUAL_SEARCH_SELECTOR = a1.text_input("Manual search input CSS", "")
MANUAL_SUBMIT_SELECTOR = a2.text_input("Manual submit CSS", "")
MANUAL_RESULTS_ROOT = a3.text_input("Manual results root CSS", "")

st.markdown("### “Working logic” fallback")
b1, b2 = st.columns(2)
FALLBACK_SLEEP = b1.slider("Fallback sleep after submit (seconds)", 0, 30, 5)
TRY_TAB_CLICK = b2.checkbox("Try to click People/Directory tab/filter", value=True)

RUN = st.button("▶ Run Debugger", type="primary")


# =========================================================
# Logging
# =========================================================
def log(status, msg: str):
    if status is not None:
        status.write(msg)

def vlog(status, msg: str):
    if DEBUG_VERBOSE:
        log(status, msg)


# =========================================================
# Text helpers / extraction
# =========================================================
NAMEISH_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){1,5}$")
NO_RESULTS_PHRASES = [
    "no results", "0 results", "zero results", "no matches", "nothing found",
    "did not match", "try a different", "no records", "no entries"
]

def html_has_no_results_signal(text_or_html: str) -> bool:
    t = (text_or_html or "").lower()
    return any(p in t for p in NO_RESULTS_PHRASES)

def clean_candidate_text(t: str) -> Optional[str]:
    if not t:
        return None
    t = " ".join(str(t).split()).strip()
    if len(t) < 3 or len(t) > 120:
        return None

    lt = t.lower()
    junk = [
        "privacy", "accessibility", "cookie", "terms", "login", "sign up", "signup",
        "home", "about", "contact", "menu", "skip to", "search results"
    ]
    if any(j in lt for j in junk):
        return None
    return t

def extract_candidates_generic(html: str) -> List[str]:
    """
    Generic candidate extraction from HTML (debugger-grade).
    """
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []

    # Prefer result-title-ish patterns first
    for sel in [
        "[class*='result' i] h3",
        "[class*='result' i] h2",
        "h3", "h2", "h4",
        "strong",
        "a",
        "li",
        "td"
    ]:
        for el in soup.select(sel):
            txt = clean_candidate_text(el.get_text(" ", strip=True))
            if txt:
                out.append(txt)
        if len(out) >= 300:
            break

    # de-dupe preserving order
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def filter_nameish(cands: List[str]) -> List[str]:
    """
    A "person-ish" filter (still universal): must look like 2-6 word name.
    """
    out = []
    for c in cands:
        cc = c.strip()
        if NAMEISH_RE.match(cc):
            # kill obvious non-person titles
            low = cc.lower()
            if any(k in low for k in ["massachusetts institute of technology", "search", "results", "map", "events"]):
                continue
            out.append(cc)
    # de-dupe
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


# =========================================================
# Requests probe (optional)
# =========================================================
COMMON_QUERY_PARAMS = ["q", "query", "search", "s", "term", "keyword", "name"]

def fetch_url(url: str) -> Tuple[int, str]:
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code, r.text or ""
    except Exception:
        return 0, ""

def build_url_with_param(base_url: str, param: str, value: str) -> str:
    u = urlparse(base_url)
    qs = parse_qs(u.query)
    qs[param] = [value]
    return u._replace(query=urlencode(qs, doseq=True)).geturl()

def requests_probe_server_search(base_url: str, term: str, status) -> Optional[Dict[str, Any]]:
    base_sc, base_html = fetch_url(base_url)
    base_fp = hash(base_html) if base_html else 0

    u = urlparse(base_url)
    endpoints = [base_url]
    if u.netloc:
        root = u._replace(path="/", query="", fragment="").geturl().rstrip("/")
        endpoints.extend([root + "/search", root + "/search/"])

    tried = 0
    for endpoint in endpoints:
        for p in COMMON_QUERY_PARAMS:
            tried += 1
            test_url = build_url_with_param(endpoint, p, term)
            vlog(status, f"🔍 Trying server search: {test_url}")
            sc, html = fetch_url(test_url)
            if sc != 200 or not html:
                continue
            fp = hash(html)
            term_present = term.lower() in html.lower()
            changed = (fp != base_fp)
            vlog(status, f"🧪 server_probe sc={sc} changed={changed} term_present={term_present} fp={fp}")

            # Only count as success if term appears in HTML (non-JS pages)
            if term_present and changed:
                cands = extract_candidates_generic(html)
                return {
                    "strategy": "server_html_search",
                    "url_used": test_url,
                    "param": p,
                    "http_status": sc,
                    "candidates": cands,
                    "nameish": filter_nameish(cands),
                }

    vlog(status, f"ℹ️ Requests probe exhausted ({tried} attempts), no confident server-side search found.")
    return None


# =========================================================
# Selenium driver helpers
# =========================================================
def get_driver(headless: bool = True):
    if not HAS_SELENIUM:
        return None

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--remote-allow-origins=*")

    if os.path.exists("/usr/bin/chromedriver"):
        try:
            return webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=opts)
        except Exception:
            pass

    try:
        return webdriver.Chrome(options=opts)
    except Exception:
        pass

    if HAS_WDM:
        try:
            return webdriver.Chrome(
                service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
                options=opts
            )
        except Exception:
            return None
    return None

def selenium_wait_ready(driver, timeout=10):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass


# =========================================================
# Universal: find search input + submit
# =========================================================
def find_search_input(driver) -> Optional[Any]:
    if MANUAL_SEARCH_SELECTOR.strip():
        els = driver.find_elements(By.CSS_SELECTOR, MANUAL_SEARCH_SELECTOR.strip())
        if els:
            return els[0]

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

    # last resort: any visible input not obviously wrong type
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
    if MANUAL_SUBMIT_SELECTOR.strip():
        try:
            driver.find_element(By.CSS_SELECTOR, MANUAL_SUBMIT_SELECTOR.strip()).click()
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

def submit_query(driver, inp, term: str, status) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", inp)
    except Exception:
        pass
    try:
        inp.click()
    except Exception:
        pass

    # clear
    try:
        inp.send_keys(Keys.CONTROL + "a")
        inp.send_keys(Keys.BACKSPACE)
    except Exception:
        pass
    try:
        driver.execute_script("arguments[0].value='';", inp)
    except Exception:
        pass

    # type term
    try:
        inp.send_keys(term)
    except Exception:
        return False

    # Enter
    try:
        inp.send_keys(Keys.RETURN)
        vlog(status, "⌨️ Submitted with ENTER")
        return True
    except Exception:
        pass

    # click submit
    if click_submit_if_possible(driver):
        vlog(status, "🖱️ Submitted by clicking submit")
        return True

    # submit form
    try:
        inp.submit()
        vlog(status, "📨 Submitted by form submit()")
        return True
    except Exception:
        return False


# =========================================================
# KEY FIX: Use the “working logic” + tab switching
# =========================================================
TAB_PRIORITIES = [
    # strongest intent
    r"\bpeople\b",
    r"\bperson\b",
    r"\bdirectory\b",
    r"\bstaff\b",
    r"\bfaculty\b",
    r"\bstudent\b",
    r"\bprofiles?\b",
    # weaker but sometimes used
    r"\bcontacts?\b",
    r"\bmembers?\b",
]

def click_best_people_tab(driver, status) -> Optional[str]:
    """
    Universal heuristic:
    - look for clickable elements that look like tabs/filters (role=tab, buttons, links)
    - choose the one whose visible text matches "people/directory/person/..."
    """
    patterns = [re.compile(p, re.I) for p in TAB_PRIORITIES]

    # collect clickables
    elems = []
    try:
        elems.extend(driver.find_elements(By.CSS_SELECTOR, "[role='tab']"))
    except Exception:
        pass
    try:
        elems.extend(driver.find_elements(By.CSS_SELECTOR, "a, button"))
    except Exception:
        pass

    # score by text match priority
    best = None
    best_score = -1
    best_txt = None

    seen_ids = set()
    for el in elems:
        try:
            # dedupe by internal id if possible
            eid = el.id
            if eid in seen_ids:
                continue
            seen_ids.add(eid)

            if not el.is_displayed() or not el.is_enabled():
                continue
            txt = (el.text or "").strip()
            if not txt or len(txt) > 40:
                continue

            score = None
            for i, pat in enumerate(patterns):
                if pat.search(txt):
                    score = (len(patterns) - i)  # earlier pattern => higher score
                    break
            if score is None:
                continue

            # tiny boost if element is tab-like
            role = (el.get_attribute("role") or "").lower()
            cls = (el.get_attribute("class") or "").lower()
            if role == "tab" or "tab" in cls or "filter" in cls:
                score += 1

            if score > best_score:
                best_score = score
                best = el
                best_txt = txt
        except Exception:
            continue

    if best is None:
        vlog(status, "ℹ️ No People/Directory tab/filter detected to click.")
        return None

    try:
        vlog(status, f"🧭 Clicking best tab/filter: '{best_txt}' (score={best_score})")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
        best.click()
        return best_txt
    except Exception as e:
        vlog(status, f"⚠ Tab click failed: {e}")
        return None

def safe_page_text(driver, max_chars=200000) -> str:
    try:
        t = driver.execute_script("return document.body ? (document.body.innerText || '') : '';") or ""
        return t[:max_chars]
    except Exception:
        return ""

def wait_like_working_logic(driver, term: str, timeout: int, status) -> Dict[str, Any]:
    """
    This is the “working logic” expanded:
    - Loop: sleep/poll
    - Decide "done" when:
        - page has "no results" signal OR
        - term appears in body text OR
        - candidate set changes materially (page_source parse)
    """
    start = time.time()
    term_l = term.lower()

    base_html = driver.page_source or ""
    base_cands = extract_candidates_generic(base_html)
    base_fp = hash(base_html)
    base_set = set(base_cands)

    vlog(status, f"🧪 baseline fp={base_fp} candidates={len(base_cands)}")

    last_set = set(base_set)

    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)

        txt = safe_page_text(driver, max_chars=50000).lower()
        if html_has_no_results_signal(txt):
            vlog(status, f"🧪 t={elapsed}s → no-results detected in page text")
            html = driver.page_source or ""
            cands = extract_candidates_generic(html)
            return {"state": "no_results", "elapsed": elapsed, "candidates": cands}

        if term_l in txt:
            vlog(status, f"🧪 t={elapsed}s → term '{term}' appears in page text")
            html = driver.page_source or ""
            cands = extract_candidates_generic(html)
            return {"state": "term_seen", "elapsed": elapsed, "candidates": cands}

        html = driver.page_source or ""
        cands = extract_candidates_generic(html)
        cset = set(cands)

        changed = (cset != last_set)
        delta = len(cset - last_set)
        last_set = cset

        vlog(status, f"🧪 t={elapsed}s candidates={len(cands)} changed={changed} delta={delta}")

        # material change threshold
        if delta >= 5:
            return {"state": "results_changed", "elapsed": elapsed, "candidates": cands}

        time.sleep(0.4)

    html = driver.page_source or ""
    cands = extract_candidates_generic(html)
    return {"state": "timeout", "elapsed": round(time.time() - start, 1), "candidates": cands}


# =========================================================
# RUNNER
# =========================================================
if RUN:
    status = st.status("Running debugger...", expanded=True)

    # Phase 1: Requests probe
    if TRY_REQUESTS_PARAMS:
        log(status, "🌐 Phase 1: Requests probe (server-side search)")
        req_hit = requests_probe_server_search(TARGET_URL, TERM, status)
        if req_hit:
            log(status, f"✅ Requests probe succeeded via param '{req_hit['param']}'")
            st.subheader("🧠 Result")
            st.write(f"Strategy used: `{req_hit['strategy']}`")
            st.write(f"URL used: {req_hit['url_used']}")
            st.write(f"HTTP status: {req_hit['http_status']}")
            st.write(f"Candidates extracted: {len(req_hit['candidates'])}")
            st.write(f"Name-ish extracted: {len(req_hit['nameish'])}")
            st.dataframe({"Candidates (first 50)": req_hit["candidates"][:50]})
            st.dataframe({"Name-ish (first 50)": req_hit["nameish"][:50]})
            status.update(label="Done", state="complete")
            st.stop()
        else:
            log(status, "ℹ️ Requests probe did not find a confident server-side search path (likely JS-rendered).")

    # Phase 2: Selenium
    if not USE_SELENIUM:
        status.update(label="Done (no Selenium)", state="complete")
        st.error("Selenium disabled (or not installed). Enable it to continue.")
        st.stop()

    if not HAS_SELENIUM:
        status.update(label="Done (Selenium missing)", state="complete")
        st.error("Selenium is not installed in this environment.")
        st.stop()

    log(status, "🤖 Phase 2: Selenium (universal)")

    driver = get_driver(headless=HEADLESS)
    if not driver:
        status.update(label="Done (driver failed)", state="complete")
        st.error("Could not start Selenium driver (SessionNotCreatedException / driver mismatch).")
        st.stop()

    try:
        driver.get(TARGET_URL)
        selenium_wait_ready(driver, timeout=12)
        time.sleep(0.6)

        inp = find_search_input(driver)
        if not inp:
            status.update(label="Done", state="complete")
            st.error("No search input found. Try manual selector.")
            st.stop()

        vlog(status, f"🎯 Search input: tag={inp.tag_name} type={inp.get_attribute('type')} "
                    f"name={inp.get_attribute('name')} id={inp.get_attribute('id')} class={(inp.get_attribute('class') or '')[:80]}")

        ok = submit_query(driver, inp, TERM, status)
        if not ok:
            status.update(label="Done", state="complete")
            st.error("Failed to submit query. Try manual submit selector.")
            st.stop()

        # --- “Working logic” small initial sleep (what your older code did) ---
        if FALLBACK_SLEEP > 0:
            vlog(status, f"⏳ Working-logic initial sleep: {FALLBACK_SLEEP}s")
            time.sleep(float(FALLBACK_SLEEP))

        # --- Tab/Filter switching to People/Directory (universal heuristic) ---
        clicked_tab = None
        if TRY_TAB_CLICK:
            clicked_tab = click_best_people_tab(driver, status)
            if clicked_tab:
                # give the UI a moment, then wait again
                time.sleep(0.8)

        # Now wait using the working-style waiter (page_source + body text)
        log(status, f"🧪 Waiting for outcome for '{TERM}' (timeout={TIMEOUT}s)")
        res = wait_like_working_logic(driver, TERM, timeout=int(TIMEOUT), status=status)

        cands = res["candidates"]
        nameish = filter_nameish(cands)

        status.update(label="Done", state="complete")

        st.subheader("🧠 Result")
        st.write("Strategy used: `selenium_working_logic_tab_aware`")
        st.write(f"Clicked tab/filter: `{clicked_tab}`")
        st.write(f"Outcome state: `{res['state']}`")
        st.write(f"Elapsed: {res['elapsed']}s")
        st.write(f"Current URL: {driver.current_url}")
        st.write(f"Candidates extracted: {len(cands)}")
        st.write(f"Name-ish extracted: {len(nameish)}")

        st.markdown("#### Candidates (first 60)")
        st.dataframe({"Candidates": cands[:60]})

        st.markdown("#### Name-ish (first 60)")
        st.dataframe({"Name-ish": nameish[:60]})

        with st.expander("🔎 Debug: page text preview", expanded=False):
            preview = safe_page_text(driver, max_chars=2500)
            st.code(preview if preview else "(empty)")

    except WebDriverException as e:
        status.update(label="Done (webdriver error)", state="complete")
        st.error(f"WebDriver error: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
