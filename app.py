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
# Selenium
# =========================================================
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType

# =========================================================
# Streamlit config
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Active Search Injection — Isolated Debug Mode")

if "running" not in st.session_state:
    st.session_state.running = False

# =========================================================
# Sidebar
# =========================================================
st.sidebar.header("🧪 Selenium")
run_headless = st.sidebar.checkbox("Run Selenium headless", True)
selenium_wait = st.sidebar.slider("Selenium wait timeout", 5, 60, 20)
search_delay = st.sidebar.slider("Delay between surnames", 0, 5, 1)

# =========================================================
# Utilities
# =========================================================
NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\. ]{3,}$")

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

# =========================================================
# ✅ FIXED NO-RESULTS LOGIC
# =========================================================
def page_has_no_results_signal(html: str, extracted_names: List[str]) -> bool:
    """
    Conservative rule:
    If ANY names exist, this is NOT a no-results page.
    """
    if extracted_names:
        return False

    if not html:
        return False

    soup = BeautifulSoup(html, "html.parser")

    for sel in [
        ".no-results",
        ".noresult",
        ".no-result",
        "#no-results",
        ".empty-state",
        ".nothing-found",
    ]:
        if soup.select_one(sel):
            return True

    text = soup.get_text(" ", strip=True).lower()
    phrases = [
        "no results found",
        "no records found",
        "your search returned no results",
        "did not match any results",
    ]

    return any(p in text for p in phrases)

# =========================================================
# Selenium helpers (STREAMLIT CLOUD SAFE)
# =========================================================
def get_driver():
    options = Options()
    if run_headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-allow-origins=*")

    service = Service(
        ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()
    )

    return webdriver.Chrome(service=service, options=options)

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

    baseline_html = driver.page_source or ""
    baseline_fp = hash(baseline_html)

    debug_log(status_log, f"[{surname}] baseline fp={baseline_fp} len={len(baseline_html)}")

    dom_changed = False

    while (time.time() - start) < timeout:
        html = driver.page_source or ""
        fp = hash(html)
        elapsed = round(time.time() - start, 1)

        names = extract_names_multi(html)
        no_results = page_has_no_results_signal(html, names)

        if fp != baseline_fp:
            dom_changed = True

        debug_log(
            status_log,
            f"[{surname}] t={elapsed}s dom_changed={dom_changed} "
            f"len={len(html)} names={len(names)} no_results={no_results}"
        )

        if dom_changed:
            if no_results:
                return "no_results", html, []
            if names:
                return "names", html, names

        time.sleep(poll_s)

    debug_log(status_log, f"[{surname}] TIMEOUT")
    return "timeout", html, extract_names_multi(html)

# =========================================================
# UI
# =========================================================
st.markdown("### 🤖 Active Search Injection — Debug Mode")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", "https://web.mit.edu/directory/")
max_pages = c2.number_input("Max Search Cycles", 1, 5000, 500)

if st.button("🚀 Start Mission", type="primary"):
    st.session_state.running = True

# =========================================================
# EXECUTION
# =========================================================
if st.session_state.running:
    status_log = st.status("Running...", expanded=True)

    surnames = ["SILVA", "SOUZA", "OLIVEIRA", "SANTOS", "PEREIRA"]

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
