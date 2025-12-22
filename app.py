import time
import re
from typing import List, Optional, Tuple
from bs4 import BeautifulSoup
from unidecode import unidecode

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait


# =========================================================
# CONFIG
# =========================================================
URL = "https://web.mit.edu/directory/"
SURNAMES = ["SILVA", "SOUZA", "OLIVEIRA"]
TIMEOUT = 20
POLL = 0.3
HEADLESS = False   # IMPORTANT: keep visible for now


# =========================================================
# HELPERS
# =========================================================
NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\. ]{3,}$")

def clean_name(txt: str) -> Optional[str]:
    if not txt:
        return None
    txt = " ".join(txt.split())
    if not NAME_RE.match(txt):
        return None
    if any(x in txt.upper() for x in [
        "SEARCH","RESULT","DIRECTORY","LOGIN","ABOUT",
        "CONTACT","HOME","PEOPLE","STAFF","FACULTY","STUDENTS"
    ]):
        return None
    return txt

def extract_names(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for el in soup.select("a[href^='mailto:'], h2, h3, h4, strong, a"):
        t = clean_name(el.get_text(" ", strip=True))
        if t:
            out.append(t)
    return list(dict.fromkeys(out))

def page_has_no_results(html: str, names: List[str]) -> bool:
    # CRITICAL RULE: if names exist, cannot be no-results
    if names:
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
# SELENIUM
# =========================================================
def get_driver():
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)

def wait_ready(driver, t=10):
    try:
        WebDriverWait(driver, t).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass

def find_search_input(driver):
    for el in driver.find_elements(By.TAG_NAME, "input"):
        try:
            if el.is_displayed() and el.is_enabled():
                return el
        except Exception:
            pass
    return None

def submit_search(inp, term):
    inp.click()
    inp.send_keys(Keys.CONTROL + "a")
    inp.send_keys(Keys.BACKSPACE)
    inp.send_keys(term)
    inp.send_keys(Keys.RETURN)


# =========================================================
# WAIT + LOG
# =========================================================
def wait_for_outcome(driver, surname: str) -> Tuple[str, str, List[str]]:
    start = time.time()

    baseline_html = driver.page_source or ""
    baseline_fp = hash(baseline_html)
    baseline_len = len(baseline_html)

    print(f"\n=== {surname} ===")
    print(f"baseline fp={baseline_fp} len={baseline_len}")

    dom_changed = False

    while time.time() - start < TIMEOUT:
        html = driver.page_source or ""
        fp = hash(html)
        length = len(html)
        elapsed = round(time.time() - start, 1)

        names = extract_names(html)
        no_results = page_has_no_results(html, names)

        if fp != baseline_fp:
            dom_changed = True

        print(
            f"t={elapsed}s "
            f"dom_changed={dom_changed} "
            f"len={length} "
            f"names={len(names)} "
            f"no_results={no_results}"
        )

        if dom_changed:
            if no_results:
                return "no_results", html, []
            if names:
                return "names", html, names

        time.sleep(POLL)

    return "timeout", driver.page_source or "", extract_names(driver.page_source or "")


# =========================================================
# MAIN
# =========================================================
driver = get_driver()
driver.get(URL)
wait_ready(driver)

for surname in SURNAMES:
    inp = find_search_input(driver)
    if not inp:
        print("❌ No search input found")
        break

    submit_search(inp, surname)
    outcome, html, names = wait_for_outcome(driver, surname)
    print(f"➡ OUTCOME: {outcome}, names={len(names)}")

driver.quit()
