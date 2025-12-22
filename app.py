import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import os
from typing import Optional, Dict, Any, List, Tuple
from unidecode import unidecode
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup
import google.generativeai as genai

# =========================================================
# DEBUG FLAG
# =========================================================
DEBUG_ACTIVE_SEARCH = True

def debug_log(status_log, msg):
    if DEBUG_ACTIVE_SEARCH and status_log:
        status_log.write(f"🧪 DEBUG {msg}")

# =========================================================
# Optional curl_cffi
# =========================================================
try:
    from curl_cffi import requests as crequests
    HAS_CURL = True
except Exception:
    HAS_CURL = False

# =========================================================
# Selenium
# =========================================================
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
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
# Streamlit config
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Universal Scraper • IBGE Scoring • AI Cleaning • Auto-Driver Fix")

if "running" not in st.session_state:
    st.session_state.running = False
if "matches" not in st.session_state:
    st.session_state.matches = []

# =========================================================
# Sidebar
# =========================================================
st.sidebar.header("🧠 AI Brain (Cleaning)")
api_key = st.sidebar.text_input("Enter API Key", type="password")

st.sidebar.markdown("---")
st.sidebar.header("🧪 Selenium")
run_headless = st.sidebar.checkbox("Run Selenium headless", True)
selenium_wait = st.sidebar.slider("Selenium wait timeout", 5, 60, 20)
search_delay = st.sidebar.slider("Delay between surnames", 0, 5, 1)

# =========================================================
# Utilities
# =========================================================
NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\. ]{3,}$")

def normalize_token(s: str) -> str:
    return unidecode(s.upper()).strip()

def clean_extracted_name(txt: str) -> Optional[str]:
    if not txt:
        return None
    txt = " ".join(txt.split())
    if not NAME_REGEX.match(txt):
        return None
    if any(x in txt.upper() for x in [
        "SEARCH","RESULT","DIRECTORY","LOGIN","ABOUT","CONTACT",
        "HOME","PEOPLE","STAFF","FACULTY","STUDENTS"
    ]):
        return None
    return txt

def extract_names_multi(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for el in soup.select("a[href^='mailto:'], h2, h3, h4, strong, a"):
        t = clean_extracted_name(el.get_text(" ", strip=True))
        if t:
            out.append(t)
    return list(dict.fromkeys(out))

def page_has_no_results_signal(html: str) -> bool:
    text = (html or "").lower()
    return any(p in text for p in [
        "no results", "0 results", "nothing found",
        "no matches", "try another search"
    ])

# =========================================================
# Selenium helpers
# =========================================================
def get_driver():
    opts = Options()
    if run_headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

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
        return webdriver.Chrome(
            service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
            options=opts
        )
    return None

def selenium_wait_document_ready(driver, timeout=10):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass

def selenium_find_search_input(driver):
    for el in driver.find_elements(By.TAG_NAME, "input"):
        try:
            if el.is_displayed() and el.is_enabled():
                return el
        except Exception:
            pass
    return None

def selenium_submit_search(driver, inp, term):
    try:
        inp.click()
        inp.send_keys(Keys.CONTROL + "a")
        inp.send_keys(Keys.BACKSPACE)
        inp.send_keys(term)
        inp.send_keys(Keys.RETURN)
        return True
    except Exception:
        return False

# =========================================================
# 🔥 FIXED + INSTRUMENTED WAITER
# =========================================================
def selenium_wait_for_search_outcome(
    driver,
    timeout: int,
    status_log,
    surname: str,
    poll_s: float = 0.3
) -> Tuple[str, str, List[str]]:

    start = time.time()

    try:
        baseline_html = driver.page_source or ""
    except Exception:
        baseline_html = ""
    baseline_fp = hash(baseline_html)
    baseline_len = len(baseline_html)

    debug_log(status_log, f"[{surname}] baseline fp={baseline_fp} len={baseline_len}")

    dom_changed = False

    while (time.time() - start) < timeout:
        try:
            html = driver.page_source or ""
        except Exception:
            html = ""

        fp = hash(html)
        length = len(html)
        elapsed = round(time.time() - start, 1)

        names = extract_names_multi(html)
        no_results = page_has_no_results_signal(html)

        if fp != baseline_fp:
            dom_changed = True

        debug_log(
            status_log,
            f"[{surname}] t={elapsed}s dom_changed={dom_changed} "
            f"len={length} names={len(names)} no_results={no_results}"
        )

        if dom_changed:
            if no_results:
                return "no_results", html, []
            if names:
                return "names", html, names

        time.sleep(poll_s)

    debug_log(status_log, f"[{surname}] TIMEOUT")

    try:
        html = driver.page_source or ""
    except Exception:
        html = ""
    names = extract_names_multi(html) if html else []

    return "timeout", html, names

# =========================================================
# UI
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", "https://web.mit.edu/directory/")
max_pages = c2.number_input("Max Search Cycles", 1, 5000, 500)

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
# EXECUTION
# =========================================================
if st.session_state.running:
    status_log = st.status("Running...", expanded=True)
    table_placeholder = st.empty()

    surnames = ["SILVA", "SOUZA", "OLIVEIRA", "SANTOS", "PEREIRA"]

    if mode.startswith("Active"):
        driver = get_driver()
        driver.get(start_url)
        selenium_wait_document_ready(driver)

        for i, surname in enumerate(surnames[: int(max_pages)], 1):
            status_log.update(label=f"🔎 Searching '{surname}' ({i}/{max_pages})", state="running")

            inp = selenium_find_search_input(driver)
            if not inp:
                status_log.error("No search input found.")
                break

            selenium_submit_search(driver, inp, surname)

            outcome, html, names = selenium_wait_for_search_outcome(
                driver,
                timeout=int(selenium_wait),
                status_log=status_log,
                surname=surname
            )

            status_log.write(f"➡ Outcome: {outcome}, names={len(names)}")

            time.sleep(search_delay)

        driver.quit()

    status_log.update(label="Done", state="complete")
    st.session_state.running = False
