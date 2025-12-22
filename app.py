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
st.caption("Probe → Detect → Decide → Extract (universal, log-heavy, tabpanel-locked)")

c1, c2, c3 = st.columns([3, 1, 1])
TARGET_URL = c1.text_input("Target URL", "https://web.mit.edu/directory/")
TERM = c2.text_input("Test term", "oliveira")
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
FALLBACK_SLEEP = b1.slider("Fallback sleep after submit (seconds)", 0, 30, 15)
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
# Extraction helpers
# =========================================================
NAMEISH_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){1,6}$")
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

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
    if len(t) < 3 or len(t) > 140:
        return None
    lt = t.lower()
    # light junk filter (universal)
    junk = [
        "privacy", "accessibility", "cookie", "terms", "login", "sign up",
        "skip to", "search results", "jobs", "events", "map"
    ]
    if any(j in lt for j in junk):
        return None
    return t

def extract_candidates_generic(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []

    # Prefer common "result title" / name spots first
    selectors = [
        "[class*='result' i] h3",
        "[class*='result' i] h2",
        "[class*='profile' i] h3",
        "[class*='person' i] h3",
        "h3", "h2", "h4",
        "strong",
        "a",
        "li",
        "td",
    ]

    for sel in selectors:
        for el in soup.select(sel):
            txt = clean_candidate_text(el.get_text(" ", strip=True))
            if txt:
                out.append(txt)
        if len(out) >= 400:
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
    out = []
    for c in cands:
        cc = c.strip()
        if not NAMEISH_RE.match(cc):
            continue
        low = cc.lower()
        # common non-person headings
        if any(k in low for k in ["massachusetts institute", "search", "results for", "websites results", "locations results"]):
            continue
        out.append(cc)

    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def extract_people_like_records_from_text(block_text: str) -> List[Dict[str, str]]:
    """
    Universal “people-ish” parsing from text:
    - picks lines that look like names
    - tries to attach an email nearby
    """
    lines = [l.strip() for l in (block_text or "").splitlines() if l.strip()]
    records: List[Dict[str, str]] = []

    # Build a rolling window to associate emails
    for i, line in enumerate(lines):
        if NAMEISH_RE.match(line) and not any(x in line.lower() for x in ["results for", "website", "locations", "people results"]):
            # search for email in same/next few lines
            email = ""
            for j in range(i, min(i + 6, len(lines))):
                m = EMAIL_RE.search(lines[j])
                if m:
                    email = m.group(0)
                    break
            records.append({"name": line, "email": email})

    # de-dupe by (name,email)
    seen = set()
    uniq = []
    for r in records:
        key = (r["name"], r["email"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
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

def safe_body_inner_text(driver, max_chars=200000) -> str:
    try:
        t = driver.execute_script("return document.body ? (document.body.innerText || '') : '';") or ""
        return t[:max_chars]
    except Exception:
        return ""

def safe_element_text_by_id(driver, element_id: str, max_chars=200000) -> str:
    try:
        el = driver.find_element("id", element_id)
        t = el.text or ""
        return t[:max_chars]
    except Exception:
        return ""

def safe_element_html_by_id(driver, element_id: str, max_chars=500000) -> str:
    try:
        el = driver.find_element("id", element_id)
        html = el.get_attribute("innerHTML") or ""
        return html[:max_chars]
    except Exception:
        return ""


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
        vlog(status, "⌨️ Submitted with ENTER")
        return True
    except Exception:
        pass

    if click_submit_if_possible(driver):
        vlog(status, "🖱️ Submitted by clicking submit")
        return True

    try:
        inp.submit()
        vlog(status, "📨 Submitted by form submit()")
        return True
    except Exception:
        return False


# =========================================================
# Tab clicking (tabpanel locked) — THE IMPORTANT PART
# =========================================================
TAB_PRIORITIES = [
    r"\bpeople\b",
    r"\bperson\b",
    r"\bdirectory\b",
    r"\bstaff\b",
    r"\bfaculty\b",
    r"\bstudent\b",
    r"\bprofiles?\b",
    r"\bcontacts?\b",
]

def click_best_people_tab(driver, status) -> Dict[str, Optional[str]]:
    """
    Universal heuristic:
    - find tab-like clickables
    - click best match (People/Directory)
    - capture aria-controls / data-target / href#id as "tabpanel id"
    """
    patterns = [re.compile(p, re.I) for p in TAB_PRIORITIES]

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
    best_panel = None

    seen_ids = set()
    for el in elems:
        try:
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
                    score = (len(patterns) - i)
                    break
            if score is None:
                continue

            role = (el.get_attribute("role") or "").lower()
            cls = (el.get_attribute("class") or "").lower()
            if role == "tab" or "tab" in cls or "filter" in cls:
                score += 1

            # try to determine panel target
            aria_controls = (el.get_attribute("aria-controls") or "").strip() or None
            data_target = (el.get_attribute("data-target") or "").strip() or None
            href = (el.get_attribute("href") or "").strip()
            href_hash = None
            if href and "#" in href:
                href_hash = href.split("#", 1)[-1].strip() or None

            panel = aria_controls or data_target or href_hash

            if score > best_score:
                best_score = score
                best = el
                best_txt = txt
                best_panel = panel
        except Exception:
            continue

    if best is None:
        vlog(status, "ℹ️ No People/Directory tab/filter detected.")
        return {"clicked_text": None, "panel_id": None}

    try:
        vlog(status, f"🧭 Clicking best tab/filter: '{best_txt}' (score={best_score}) panel_id={best_panel}")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
        best.click()
        return {"clicked_text": best_txt, "panel_id": best_panel}
    except Exception as e:
        vlog(status, f"⚠ Tab click failed: {e}")
        return {"clicked_text": best_txt, "panel_id": best_panel}


# =========================================================
# “Working logic” waiter BUT SCOPED TO TABPANEL (if we have one)
# =========================================================
def wait_scoped_outcome(driver, term: str, timeout: int, status, panel_id: Optional[str]) -> Dict[str, Any]:
    start = time.time()
    term_l = term.lower()

    def get_scope_text_and_html() -> Tuple[str, str]:
        if MANUAL_RESULTS_ROOT.strip():
            # if user provides manual root CSS, use that first
            try:
                el = driver.find_element(By.CSS_SELECTOR, MANUAL_RESULTS_ROOT.strip())
                txt = (el.text or "")[:200000]
                html = (el.get_attribute("innerHTML") or "")[:500000]
                return txt, html
            except Exception:
                pass

        if panel_id:
            txt = safe_element_text_by_id(driver, panel_id, max_chars=200000)
            html = safe_element_html_by_id(driver, panel_id, max_chars=500000)
            if txt or html:
                return txt, html

        # fallback: full body
        txt = safe_body_inner_text(driver, max_chars=200000)
        html = driver.page_source or ""
        return txt, html

    base_txt, base_html = get_scope_text_and_html()
    base_fp = hash(base_html or "")
    base_cands = extract_candidates_generic(base_html)
    vlog(status, f"🧪 baseline scope panel_id={panel_id} fp={base_fp} candidates={len(base_cands)}")

    last_cset = set(base_cands)

    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)
        txt, html = get_scope_text_and_html()

        if html_has_no_results_signal(txt) or html_has_no_results_signal(html):
            vlog(status, f"🧪 t={elapsed}s → no-results detected (scoped)")
            cands = extract_candidates_generic(html)
            return {"state": "no_results", "elapsed": elapsed, "candidates": cands, "scope_panel": panel_id}

        if term_l in (txt or "").lower():
            vlog(status, f"🧪 t={elapsed}s → term '{term}' appears in scoped text")
            cands = extract_candidates_generic(html)
            return {"state": "term_seen", "elapsed": elapsed, "candidates": cands, "scope_panel": panel_id}

        cands = extract_candidates_generic(html)
        cset = set(cands)

        changed = (cset != last_cset)
        delta = len(cset - last_cset)
        last_cset = cset

        vlog(status, f"🧪 t={elapsed}s scoped_candidates={len(cands)} changed={changed} delta={delta}")

        if delta >= 5:
            return {"state": "results_changed", "elapsed": elapsed, "candidates": cands, "scope_panel": panel_id}

        time.sleep(0.4)

    # timeout
    txt, html = get_scope_text_and_html()
    cands = extract_candidates_generic(html)
    return {"state": "timeout", "elapsed": round(time.time() - start, 1), "candidates": cands, "scope_panel": panel_id, "scoped_text_preview": (txt or "")[:2500]}


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

    clicked_tab_text = None
    panel_id = None

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

        # working logic sleep (from the code that "worked")
        if FALLBACK_SLEEP > 0:
            vlog(status, f"⏳ Working-logic initial sleep: {FALLBACK_SLEEP}s")
            time.sleep(float(FALLBACK_SLEEP))

        # click People/Directory and LOCK to its panel
        if TRY_TAB_CLICK:
            tab_res = click_best_people_tab(driver, status)
            clicked_tab_text = tab_res.get("clicked_text")
            panel_id = tab_res.get("panel_id")
            if clicked_tab_text:
                time.sleep(0.8)

        log(status, f"🧪 Waiting for outcome for '{TERM}' (timeout={TIMEOUT}s) scoped_panel={panel_id}")
        res = wait_scoped_outcome(driver, TERM, timeout=int(TIMEOUT), status=status, panel_id=panel_id)

        cands = res["candidates"]
        nameish = filter_nameish(cands)

        # additionally: parse people-like records from *scoped text* if we have it
        scoped_text = ""
        if panel_id:
            scoped_text = safe_element_text_by_id(driver, panel_id, max_chars=200000)
        else:
            scoped_text = safe_body_inner_text(driver, max_chars=200000)

        people_records = extract_people_like_records_from_text(scoped_text)

        status.update(label="Done", state="complete")

        st.subheader("🧠 Result")
        st.write("Strategy used: `selenium_tabpanel_scoped_working_logic`")
        st.write(f"Clicked tab/filter: `{clicked_tab_text}`")
        st.write(f"Locked panel_id: `{panel_id}`")
        st.write(f"Outcome state: `{res['state']}`")
        st.write(f"Elapsed: {res['elapsed']}s")
        st.write(f"Current URL: {driver.current_url}")

        st.markdown("#### Scoped extraction counts")
        st.write(f"Candidates (scoped): {len(cands)}")
        st.write(f"Name-ish (scoped): {len(nameish)}")
        st.write(f"People-like records (name + optional email): {len(people_records)}")

        st.markdown("#### People-like records (first 50)")
        st.dataframe(people_records[:50])

        st.markdown("#### Candidates (first 60)")
        st.dataframe({"Candidates": cands[:60]})

        st.markdown("#### Name-ish (first 60)")
        st.dataframe({"Name-ish": nameish[:60]})

        with st.expander("🔎 Debug: scoped text preview", expanded=False):
            preview = (scoped_text or "")[:2500]
            st.code(preview if preview else "(empty)")

    except WebDriverException as e:
        status.update(label="Done (webdriver error)", state="complete")
        st.error(f"WebDriver error: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
