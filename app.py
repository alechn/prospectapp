import streamlit as st
import requests
import time
import re
from typing import List, Tuple, Optional
from urllib.parse import urlparse, parse_qs, urlencode
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
st.set_page_config(
    page_title="Universal Active Search Debugger",
    layout="wide",
    page_icon="🧪"
)

st.title("🧪 Universal Active Search Debugger")
st.caption("Probe → Detect → Decide → Extract (Query-aware)")

TARGET_URL = st.text_input(
    "Target URL",
    "https://web.mit.edu/directory/"
)

SURNAME = st.text_input(
    "Test Surname",
    "OLIVEIRA"
)

TIMEOUT = st.slider("Timeout (seconds)", 5, 30, 15)

RUN = st.button("▶ Run Debugger", type="primary")

# ================================
# Logging
# ================================
def log(status, msg):
    status.write(msg)

# ================================
# Name heuristics (kept simple for now)
# ================================
NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\\-\\. ]{3,}$")

BAD_TOKENS = {
    "MIT","HOME","NEWS","EVENTS","MAP","SEARCH","ALUMNI",
    "PEOPLE","JOBS","PRIVACY","ACCESSIBILITY","INNOVATION",
    "CAMPUS","LIFE","GIVE","VISIT","MEDIA","SOCIAL"
}

def looks_like_person_name(txt: str) -> bool:
    if not txt:
        return False
    txt = " ".join(txt.split())
    if not NAME_RE.match(txt):
        return False
    words = txt.split()
    if not (2 <= len(words) <= 4):
        return False
    if any(w.upper() in BAD_TOKENS for w in words):
        return False
    return all(w[0].isupper() for w in words if w.isalpha())

def extract_names_multi(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for el in soup.select("a, h2, h3, h4, strong"):
        txt = el.get_text(" ", strip=True)
        if looks_like_person_name(txt):
            out.append(txt)
    return list(dict.fromkeys(out))

# ================================
# Frontend detection
# ================================
def detect_frontend(html: str) -> dict:
    h = html.lower()
    return {
        "vue": "<result-list" in h or "vue" in h,
        "react": "__react" in h or "reactroot" in h,
        "js_app": "<script" in h and "search" in h,
    }

# ================================
# Strategy 1: Server-side search
# ================================
def try_server_search(url: str, term: str, status) -> List[str]:
    params = ["q", "query", "search", "s"]

    for p in params:
        u = urlparse(url)
        qs = parse_qs(u.query)
        qs[p] = [term]
        test_url = u._replace(query=urlencode(qs, doseq=True)).geturl()

        log(status, f"🔍 Trying server search: {test_url}")

        try:
            r = requests.get(test_url, timeout=15)
            if r.status_code == 200:
                names = extract_names_multi(r.text)
                if names:
                    log(status, f"✅ Server search worked via '{p}'")
                    return names
        except Exception as e:
            log(status, f"⚠ Server search error: {e}")

    return []

# ================================
# Selenium helpers
# ================================
def get_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")

    service = Service(
        ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()
    )
    return webdriver.Chrome(service=service, options=opts)

def selenium_wait_ready(driver, timeout=10):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )

def selenium_find_search_input(driver):
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
# 🔑 QUERY-AWARE WAIT (THIS FIXES YOUR ISSUE)
# ================================
def selenium_wait_for_results(
    driver,
    surname: str,
    timeout: int,
    status,
    poll: float = 0.3
):
    def fingerprint(html: str) -> int:
        return hash("".join(html.split()))

    start = time.time()
    baseline_html = driver.page_source or ""
    baseline_fp = fingerprint(baseline_html)

    log(status, f"🧪 Waiting for results for '{surname}'")

    while time.time() - start < timeout:
        html = driver.page_source or ""
        fp = fingerprint(html)
        elapsed = round(time.time() - start, 1)

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True).upper()

        if any(x in text for x in ["NO RESULTS", "NO MATCH", "0 RESULTS"]):
            log(status, f"🧪 t={elapsed}s → no results signal")
            return "no_results", html

        if surname.upper() in text and fp != baseline_fp:
            log(status, f"🧪 t={elapsed}s → results updated for surname")
            return "results", html

        log(status, f"🧪 t={elapsed}s → waiting…")
        time.sleep(poll)

    log(status, "🧪 TIMEOUT waiting for results")
    return "timeout", driver.page_source

# ================================
# Strategy 4: Selenium
# ================================
def try_selenium_dom(url: str, term: str, status, timeout: int) -> List[str]:
    log(status, "🤖 Launching Selenium fallback")

    driver = get_driver()
    driver.get(url)
    selenium_wait_ready(driver)

    inp = selenium_find_search_input(driver)
    if not inp:
        log(status, "❌ No search input found")
        driver.quit()
        return []

    selenium_submit_search(driver, inp, term)

    state, html = selenium_wait_for_results(
        driver,
        term,
        timeout,
        status
    )

    driver.quit()

    if state in ("results", "no_results"):
        return extract_names_multi(html)

    return []

# ================================
# UNIVERSAL ENGINE
# ================================
def universal_active_search(
    url: str,
    term: str,
    timeout: int,
    status
) -> Tuple[str, List[str]]:

    names = try_server_search(url, term, status)
    if names:
        return "server_html_search", names

    log(status, "🌐 Fetching base page")
    try:
        r = requests.get(url, timeout=15)
        html = r.text
    except Exception:
        html = ""

    caps = detect_frontend(html)
    log(status, f"🧠 Frontend detected: {caps}")

    names = try_selenium_dom(url, term, status, timeout)
    if names:
        return "selenium_dom", names

    return "no_results", []

# ================================
# RUN
# ================================
if RUN:
    status = st.status("Running universal active search...", expanded=True)

    strategy, names = universal_active_search(
        TARGET_URL,
        SURNAME,
        TIMEOUT,
        status
    )

    status.update(label="Done", state="complete")

    st.subheader("🧠 Result")
    st.write(f"**Strategy used:** `{strategy}`")
    st.write(f"**Names found:** {len(names)}")

    if names:
        st.dataframe({"Names": names})
