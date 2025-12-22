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
st.caption("Probe → Detect → Decide → Extract (Form-aware, JS-shell-aware, CSE-DOM-wait)")

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
# Small helpers
# ================================
def is_js_shell(html: str) -> bool:
    if not html:
        return True
    h = html.lower()
    # Google CSE / gsc signals
    if "cse.google.com" in h or "gcse" in h or "gsc-" in h:
        return True
    # Vue/React placeholders
    if "<result-list" in h or "__react" in h or "reactroot" in h:
        return True
    return False


# ================================
# Extraction
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
    # must have 2–5 tokens for “name-ish”
    parts = txt.split()
    if not (2 <= len(parts) <= 5):
        return False
    # avoid headings
    if "RESULT" in up and len(parts) <= 3:
        return False
    return True

def extract_cse_titles(html: str) -> List[str]:
    """
    Extract Google CSE titles (what CSE considers “results”).
    This is NOT person-specific yet; it proves the CSE rendered.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []
    for el in soup.select(".gsc-result .gs-title, .gsc-webResult .gs-title, .gsc-result .gs-title a, .gsc-webResult .gs-title a"):
        t = el.get_text(" ", strip=True)
        if t and t.strip():
            out.append(t.strip())
    return list(dict.fromkeys(out))

def extract_personish_names(html: str) -> List[str]:
    """
    Very conservative “person-ish” names for debugging.
    - prefers mailto anchors (often actual people directory records)
    - then falls back to CSE titles that look like names
    """
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []

    for el in soup.select("a[href^='mailto:']"):
        t = el.get_text(" ", strip=True)
        if looks_like_person_name(t):
            out.append(t)

    if out:
        return list(dict.fromkeys(out))

    # fallback: CSE titles that look like names
    for t in extract_cse_titles(html):
        if looks_like_person_name(t):
            out.append(t)

    return list(dict.fromkeys(out))


# ================================
# Discover search form (universal)
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
    # MIT uses tab=directory; universal-ish for tabbed search pages
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
    for sel in ["#es-search-form-input", "input[name='q']", "input[type='search']", "input[aria-label*='search' i]"]:
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

def selenium_wait_for_cse_dom(driver, term: str, timeout: int, status, poll: float = 0.25):
    """
    Correct waiting for Google CSE:
      1) wait until CSE control exists (.gsc-control-cse)
      2) then wait until either:
         - .gsc-result exists (real rendered results), OR
         - .gs-no-results-result exists (explicit no results block)
    IMPORTANT: We do NOT use page text “no results” because it can exist in shell templates.
    """
    start = time.time()
    t_lower = term.strip().lower()

    log(status, f"🧪 Waiting for CSE to load for '{term}'")

    # Step 1: wait for CSE container to exist
    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)
        cse_controls = driver.find_elements(By.CSS_SELECTOR, ".gsc-control-cse, .gcse-searchresults, .gsc-results-wrapper-visible")
        if cse_controls:
            log(status, f"🧪 t={elapsed}s → CSE container detected ({len(cse_controls)})")
            break
        time.sleep(poll)
    else:
        log(status, "🧪 TIMEOUT: CSE container never appeared")
        return "timeout_no_cse", driver.page_source or ""

    # Step 2: wait for results or explicit no-results element
    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)

        results = driver.find_elements(By.CSS_SELECTOR, ".gsc-result, .gsc-webResult")
        nores = driver.find_elements(By.CSS_SELECTOR, ".gs-no-results-result, .gsc-no-results-result")

        # log counts + a tiny peek at titles for visibility
        if int(elapsed * 10) % int(max(poll, 0.25) * 10) == 0:
            titles = driver.find_elements(By.CSS_SELECTOR, ".gsc-result .gs-title, .gsc-webResult .gs-title")
            peek = []
            for e in titles[:3]:
                try:
                    peek.append(e.text.strip())
                except Exception:
                    pass
            log(status, f"🧪 t={elapsed}s → results={len(results)} nores={len(nores)} peek={peek}")

        if results:
            # confirm the query is actually applied somewhere (CSE echoes it in multiple places often)
            page_html = driver.page_source or ""
            if t_lower in page_html.lower():
                log(status, f"🧪 t={elapsed}s → results present and query seen in DOM")
                return "results", page_html
            # still accept results because CSE sometimes doesn’t echo
            log(status, f"🧪 t={elapsed}s → results present (query not echoed); accepting")
            return "results", driver.page_source or ""

        if nores:
            log(status, f"🧪 t={elapsed}s → explicit no-results component detected")
            return "no_results", driver.page_source or ""

        time.sleep(poll)

    log(status, "🧪 TIMEOUT waiting for results/no-results")
    return "timeout", driver.page_source or ""


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

        # Requests will usually be shell for CSE pages
        titles = extract_cse_titles(html)
        log(status, f"📦 Requests CSE titles: {len(titles)}")

        if not html or is_js_shell(html):
            log(status, "⚠ Requests looks like JS shell → forcing Selenium")
            selenium_target = search_url
        else:
            # If somehow non-shell, extract personish
            names = extract_personish_names(html)
            return "requests_form_search", names, search_url
    else:
        selenium_target = start_url

    # Selenium fallback
    log(status, f"🤖 Selenium fallback starting at: {selenium_target}")
    driver = get_driver()
    try:
        driver.get(selenium_target)
        selenium_wait_ready(driver, timeout=10)

        inp = selenium_find_search_input(driver)
        if inp:
            selenium_submit_search(driver, inp, term)
        else:
            log(status, "ℹ️ No search input found; continuing (maybe page already has query in URL)")

        state, html = selenium_wait_for_cse_dom(driver, term, timeout, status)

        # Debug: show CSE titles and also “person-ish” names
        titles = extract_cse_titles(html)
        names = extract_personish_names(html)

        # Extra debug: how many titles contain the searched surname?
        s = term.strip().lower()
        matching_titles = [t for t in titles if s in t.lower()]

        log(status, f"📦 Selenium CSE titles: {len(titles)} | titles containing '{term}': {len(matching_titles)}")
        log(status, f"📦 Selenium person-ish names: {len(names)} (state={state})")

        # For now, return person-ish names (could be 0; that’s okay for proving wait logic)
        return "selenium_dom", (names or matching_titles or titles[:10]), driver.current_url
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
    st.write(f"**Items shown (debug):** {len(names)}")

    if names:
        st.dataframe({"Items": names})
