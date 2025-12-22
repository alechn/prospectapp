import streamlit as st
import requests
import time
import re
from typing import List, Tuple, Optional
from urllib.parse import urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup

# ================================
# Selenium (optional)
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
st.set_page_config("Universal Active Search Debugger", layout="wide")
st.title("🧪 Universal Active Search Debugger")
st.caption("Probe → Detect → Decide → Extract")

# ================================
# Config
# ================================
TARGET_URL = st.text_input(
    "Target URL",
    "https://web.mit.edu/directory/"
)

SURNAME = st.text_input(
    "Test Surname",
    "OLIVEIRA"
)

TIMEOUT = st.slider("Timeout (seconds)", 5, 30, 15)

RUN = st.button("▶ Run Debugger")

# ================================
# Utilities
# ================================
NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\\-\\. ]{3,}$")

def clean_name(txt: str) -> Optional[str]:
    if not txt:
        return None
    txt = " ".join(txt.split())
    if not NAME_RE.match(txt):
        return None
    if any(x in txt.upper() for x in [
        "SEARCH", "RESULT", "DIRECTORY", "LOGIN", "ABOUT",
        "CONTACT", "HOME", "MENU", "MIT", "EDUCATION"
    ]):
        return None
    return txt

def extract_names_multi(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for el in soup.select("a, h2, h3, h4, strong"):
        t = clean_name(el.get_text(" ", strip=True))
        if t:
            out.append(t)
    return list(dict.fromkeys(out))

def log(status, msg):
    status.write(msg)

# ================================
# Detection Heuristics
# ================================
def detect_frontend(html: str) -> dict:
    h = html.lower()
    return {
        "vue": "<result-list" in h or "vue" in h,
        "react": "reactroot" in h or "__react" in h,
        "js_app": "<script" in h and ("search" in h),
    }

# ================================
# Strategy 1: Server-side URL search
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
                    log(status, f"✅ Server search worked via param '{p}'")
                    return names
        except Exception as e:
            log(status, f"⚠ Server search error: {e}")

    return []

# ================================
# Selenium Helpers
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
# Strategy 4: Selenium DOM extraction
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

    start = time.time()
    while time.time() - start < timeout:
        html = driver.page_source or ""
        names = extract_names_multi(html)
        if names:
            log(status, "✅ Selenium DOM extraction worked")
            driver.quit()
            return names
        time.sleep(0.5)

    driver.quit()
    return []

# ================================
# UNIVERSAL ACTIVE SEARCH ENGINE
# ================================
def universal_active_search(url: str, term: str, timeout: int, status) -> Tuple[str, List[str]]:

    # 1️⃣ Server-rendered HTML search
    names = try_server_search(url, term, status)
    if names:
        return "server_html_search", names

    # 2️⃣ Load base page
    log(status, "🌐 Fetching base page")
    try:
        r = requests.get(url, timeout=15)
        html = r.text
    except Exception:
        html = ""

    caps = detect_frontend(html)
    log(status, f"🧠 Frontend detected: {caps}")

    # 3️⃣ JS frontend → try known search endpoint
    if caps["vue"] or caps["react"]:
        mit_style = "search" in url.lower() or "mit.edu" in url.lower()
        if mit_style:
            log(status, "🧪 Trying MIT-style /search endpoint")
            try:
                r = requests.get(
                    "https://www.mit.edu/search/",
                    params={"q": term, "tab": "directory"},
                    timeout=15
                )
                names = extract_names_multi(r.text)
                if names:
                    return "frontend_server_endpoint", names
            except Exception:
                pass

    # 4️⃣ Selenium fallback
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
