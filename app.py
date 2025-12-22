import streamlit as st
import pandas as pd
import json
import time
import re
import os
import io
from typing import Optional, Dict, Any, List, Tuple, Set
from urllib.parse import urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup
from unidecode import unidecode

import requests

# --- Selenium ---
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

# --- webdriver_manager fallback ---
try:
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    HAS_WDM = True
except Exception:
    HAS_WDM = False


# =========================================================
# UI
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Universal Scraper • IBGE Scoring • Auto Results/No-Results Wait (No Site-Specific Settings)")

if "running" not in st.session_state:
    st.session_state.running = False
if "matches" not in st.session_state:
    st.session_state.matches = []
if "seen_names" not in st.session_state:
    st.session_state.seen_names = set()

# Sidebar minimal controls only (no site-specific selectors)
st.sidebar.header("⚙️ Run controls")
run_headless = st.sidebar.checkbox("Run Selenium headless", value=True)
selenium_wait = st.sidebar.slider("Selenium outcome timeout (seconds)", 5, 60, 20)
between_surnames_pause = st.sidebar.slider("Pause between surnames (seconds)", 0, 10, 1)
max_cycles_default = 25

st.sidebar.markdown("---")
allow_surname_only = st.sidebar.checkbox(
    "Allow single-token surname matches (weaker)",
    value=True,
    help="If a result is just 'Souza', count it as a weak match."
)

if st.sidebar.button("🛑 STOP", type="primary"):
    st.session_state.running = False
    st.stop()

if st.sidebar.button("🧹 Clear Results"):
    st.session_state.matches = []
    st.session_state.seen_names = set()
    st.success("Cleared.")


# =========================================================
# IBGE (simple local cache; universal fallback = empty if missing)
# =========================================================
IBGE_CACHE_FILE = "data/ibge_rank_cache.json"

def normalize_token(s: str) -> str:
    if not s:
        return ""
    s = unidecode(str(s).strip().upper())
    return "".join(ch for ch in s if "A" <= ch <= "Z")

def load_ibge_best_effort() -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    Loads cached ranks if present. If not present, returns empty dicts.
    (Keeps app universal & shareable without needing IBGE API access.)
    """
    if os.path.exists(IBGE_CACHE_FILE):
        try:
            with open(IBGE_CACHE_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
            first_full = {str(k): int(v) for k, v in (payload.get("first_name_ranks", {}) or {}).items()}
            surname_full = {str(k): int(v) for k, v in (payload.get("surname_ranks", {}) or {}).items()}
            return first_full, surname_full
        except Exception:
            pass
    return {}, {}

first_name_ranks_full, surname_ranks_full = load_ibge_best_effort()

# Limit scope to top N if present (otherwise matching still “works” but scores won't be meaningful)
limit_first = st.sidebar.number_input("IBGE Top N First Names", 1, 20000, 3000, 1)
limit_surname = st.sidebar.number_input("IBGE Top N Surnames", 1, 20000, 3000, 1)

first_name_ranks = {k: v for k, v in first_name_ranks_full.items() if 0 < v <= int(limit_first)}
surname_ranks = {k: v for k, v in surname_ranks_full.items() if 0 < v <= int(limit_surname)}

sorted_surnames = sorted(surname_ranks.keys(), key=lambda k: surname_ranks[k]) if surname_ranks else []


# =========================================================
# Name extraction & filtering (universal)
# =========================================================
BLOCKLIST_TOKENS = {
    "RESULTS","WEBSITE","SEARCH","MENU","SKIP","CONTENT","FOOTER","HEADER",
    "OVERVIEW","PROJECTS","PEOPLE","PROFILE","VIEW","CONTACT","SPOTLIGHT",
    "PDF","LOGIN","SIGNUP","HOME","ABOUT","CAREERS","NEWS","EVENTS",
    "DIRECTORY","ALUMNI","FACULTY","STAFF","STUDENTS","STUDENT"
}

NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,6}$")

def clean_extracted_name(raw_text: str) -> Optional[str]:
    if not isinstance(raw_text, str):
        return None

    raw_text = " ".join(raw_text.split()).strip()
    if not raw_text:
        return None

    upper = raw_text.upper()

    # Hard reject obvious junk / UI
    junk_phrases = [
        "SEARCH", "SKIP TO", "RESULTS FOR", "NO RESULTS", "TRY AGAIN",
        "SIGN IN", "LOG IN", "CREATE ACCOUNT", "PRIVACY", "TERMS",
        "COPYRIGHT", "ACCESSIBILITY", "COOKIE", "SITE MAP",
        "HOME", "ABOUT", "CONTACT", "PEOPLE", "DIRECTORY"
    ]
    if any(p in upper for p in junk_phrases):
        return None

    # Remove common delimiters
    raw_text = re.split(r"[|–—»\(\)]|\s-\s", raw_text)[0].strip()

    # Transform "LAST, FIRST"
    if "," in raw_text:
        parts = [p.strip() for p in raw_text.split(",") if p.strip()]
        if len(parts) >= 2:
            raw_text = f"{parts[1]} {parts[0]}"

    raw_text = " ".join(raw_text.split()).strip()

    # Reject urls/emails
    if any(x in raw_text.lower() for x in ["http://", "https://", "www.", "@", ".com", ".org", ".edu", ".net"]):
        return None

    # Basic length sanity
    if len(raw_text) < 3:
        return None
    if len(raw_text.split()) > 7:
        return None

    if not NAME_REGEX.match(raw_text):
        return None

    # Token blocklist (e.g., a single token "RESULTS")
    toks = [normalize_token(t) for t in raw_text.split()]
    if len(toks) == 1 and toks[0] in BLOCKLIST_TOKENS:
        return None

    return raw_text

def extract_names_universal(html: str) -> List[str]:
    """
    Universal extraction strategy:
    - prioritize elements commonly used for person entries
    - include mailto anchors (often names are displayed there)
    - fall back to headings/list items in results areas
    """
    soup = BeautifulSoup(html or "", "html.parser")
    selectors = [
        # very common in directories
        "a[href^='mailto:']",
        "[role='listitem'] a",
        "table tr td a",
        "table tr td",
        "ul li a",
        "ul li",
        ".results a", ".results .name", ".results h3", ".results h4",
        ".result a", ".result .name", ".result h3", ".result h4",
        ".person a", ".person .name", ".person-name",
        ".profile a", ".profile-name",
        "h2", "h3", "h4",
        "strong",
        "a",
    ]

    out: List[str] = []
    for sel in selectors:
        for el in soup.select(sel):
            t = el.get_text(" ", strip=True)
            c = clean_extracted_name(t)
            if c:
                out.append(c)
        if len(out) >= 400:
            break

    # de-dupe preserve order
    return list(dict.fromkeys(out))

def page_has_no_results(html: str) -> bool:
    if not html:
        return False
    soup = BeautifulSoup(html, "html.parser")

    # empty-state selectors found across many UIs
    for s in [
        ".no-results", ".noresult", ".no-result", "#no-results",
        ".empty-state", ".empty", ".nothing-found",
        "[data-empty='true']", "[aria-live='polite']"
    ]:
        if soup.select_one(s):
            txt = soup.select_one(s).get_text(" ", strip=True).lower()
            if any(p in txt for p in ["no", "0", "nothing", "not found", "matches"]):
                return True

    text = soup.get_text(" ", strip=True).lower()

    phrases = [
        "no results", "0 results", "zero results",
        "no matches", "no match",
        "nothing found", "no records found", "no entries found",
        "did not match any", "try a different search", "try another search",
        "we couldn't find", "we could not find",
    ]
    return any(p in text for p in phrases)

def calculate_score(rank: Optional[int], limit: int, weight: float = 50.0) -> float:
    if not rank or rank <= 0 or rank > limit:
        return 0.0
    return float(weight) * (1.0 - (rank / float(limit)))

def match_names(names: List[str], source: str) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    seen_local: Set[str] = set()

    for raw in names:
        n = clean_extracted_name(raw)
        if not n:
            continue
        if n in seen_local:
            continue
        seen_local.add(n)

        parts = n.split()
        if len(parts) == 1:
            if not allow_surname_only:
                continue
            tok = normalize_token(parts[0])
            rl = surname_ranks.get(tok, 0)
            if rl > 0:
                score = calculate_score(rl, int(limit_surname), 50.0)
                found.append({
                    "Full Name": n,
                    "Brazil Score": round(score, 1),
                    "First Rank": None,
                    "Surname Rank": rl,
                    "Source": source,
                    "Match Type": "Surname Only (Weak)",
                })
            continue

        f = normalize_token(parts[0])
        l = normalize_token(parts[-1])

        rf = first_name_ranks.get(f, 0)
        rl = surname_ranks.get(l, 0)

        score_f = calculate_score(rf, int(limit_first), 50.0)
        score_l = calculate_score(rl, int(limit_surname), 50.0)
        total = round(score_f + score_l, 1)

        # If IBGE data missing, these will be zero; still allow showing candidates? No: keep your original behavior
        if total > 5:
            found.append({
                "Full Name": n,
                "Brazil Score": total,
                "First Rank": rf if rf > 0 else None,
                "Surname Rank": rl if rl > 0 else None,
                "Source": source,
                "Match Type": "Strong" if (rf > 0 and rl > 0) else ("First Only" if rf > 0 else "Surname Only"),
            })

    return found


# =========================================================
# Selenium helpers (universal)
# =========================================================
def get_driver(headless: bool = True):
    if not HAS_SELENIUM:
        return None

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-allow-origins=*")
    options.add_argument("--disable-extensions")

    # Prefer system chromedriver if present
    if os.path.exists("/usr/bin/chromedriver"):
        try:
            return webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)
        except Exception:
            pass

    # Try native
    try:
        return webdriver.Chrome(options=options)
    except Exception:
        pass

    # webdriver_manager fallback
    if HAS_WDM:
        try:
            return webdriver.Chrome(
                service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
                options=options
            )
        except Exception:
            return None

    return None

def selenium_wait_document_ready(driver, timeout: int = 10):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass

def find_search_input_element(driver) -> Optional[Any]:
    """
    Universal search input detection:
    - input[type=search]
    - input with name/id/placeholder containing query/search/q
    - visible only
    """
    candidates = []

    try:
        candidates.extend(driver.find_elements(By.CSS_SELECTOR, "input[type='search']"))
    except Exception:
        pass

    css = [
        "input[name*='q' i]", "input[id*='q' i]",
        "input[name*='query' i]", "input[id*='query' i]",
        "input[name*='search' i]", "input[id*='search' i]",
        "input[placeholder*='search' i]",
        "input[aria-label*='search' i]",
        "input[type='text']",
    ]
    for sel in css:
        try:
            candidates.extend(driver.find_elements(By.CSS_SELECTOR, sel))
        except Exception:
            pass

    # pick first visible, enabled, reasonable size
    for el in candidates:
        try:
            if not el.is_displayed():
                continue
            if not el.is_enabled():
                continue
            sz = el.size or {}
            if (sz.get("width", 0) or 0) < 60:
                continue
            return el
        except Exception:
            continue

    return None

def submit_search(driver, input_el, term: str) -> bool:
    """
    Universal submit: click, clear, type, press ENTER.
    Also tries to trigger change event for apps that need it.
    """
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", input_el)
        input_el.click()
        time.sleep(0.05)

        # Clear
        try:
            input_el.send_keys(Keys.CONTROL + "a")
            input_el.send_keys(Keys.BACKSPACE)
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].value=''; arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", input_el)
        except Exception:
            pass

        # Type
        input_el.send_keys(term)
        time.sleep(0.05)

        # Some apps only react on input event
        try:
            driver.execute_script("arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", input_el)
            driver.execute_script("arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", input_el)
        except Exception:
            pass

        # Submit
        input_el.send_keys(Keys.RETURN)
        return True
    except Exception:
        return False

def wait_for_outcome_universal(driver, timeout_s: int = 20, poll_s: float = 0.25) -> Tuple[str, str, List[str]]:
    """
    Waits until:
      - DOM changes after search AND names appear, or
      - DOM changes after search AND no-results appears, or
      - timeout
    """
    start = time.time()
    selenium_wait_document_ready(driver, min(5, timeout_s))

    try:
        baseline = driver.page_source or ""
    except Exception:
        baseline = ""
    baseline_fp = hash(baseline)

    dom_changed = False
    last_fp = baseline_fp

    # settle
    time.sleep(0.15)

    while (time.time() - start) < timeout_s:
        try:
            html = driver.page_source or ""
        except Exception:
            html = ""

        fp = hash(html)
        if fp != last_fp:
            last_fp = fp
        if fp != baseline_fp:
            dom_changed = True

        if dom_changed:
            # first check no-results
            if page_has_no_results(html):
                return "no_results", html, []

            # then names
            names = extract_names_universal(html)
            if names:
                return "names", html, names

        time.sleep(poll_s)

    # timeout
    try:
        html = driver.page_source or ""
    except Exception:
        html = ""
    names = extract_names_universal(html) if html else []
    return "timeout", html, names


# =========================================================
# Requests fallback for URL-param search (universal-ish)
# =========================================================
def try_requests_param_search(base_url: str, term: str) -> Optional[str]:
    """
    Universal attempt:
    Tries common query params: q, query, search, term, keyword
    """
    params = ["q", "query", "search", "term", "keyword"]
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}

    u = urlparse(base_url)
    base_qs = parse_qs(u.query)

    for p in params:
        qs = dict(base_qs)
        qs[p] = [term]
        url = u._replace(query=urlencode(qs, doseq=True)).geturl()
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 200 and r.text:
                return r.text
        except Exception:
            continue
    return None


# =========================================================
# Main controls
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")

col1, col2 = st.columns([3, 1])
start_url = col1.text_input("Target URL", placeholder="https://web.mit.edu/directory/")
max_cycles = col2.number_input("Max Search Cycles", 1, 5000, max_cycles_default, 1)

mode = st.radio(
    "Mode:",
    [
        "Classic Directory (Requests)",
        "Infinite Scroller (Selenium)",
        "Active Search Injection (Selenium → Requests fallback)",
    ]
)

if st.button("🚀 Start Mission", type="primary"):
    st.session_state.running = True

# =========================================================
# Run
# =========================================================
table_placeholder = st.empty()
status = st.status("Idle", expanded=True)

def push_matches(new_matches: List[Dict[str, Any]]):
    for m in new_matches:
        nm = m["Full Name"]
        if nm not in st.session_state.seen_names:
            st.session_state.seen_names.add(nm)
            st.session_state.matches.append(m)
    st.session_state.matches.sort(key=lambda x: x.get("Brazil Score", 0), reverse=True)

if st.session_state.running:
    if not start_url:
        st.error("Please provide a Target URL.")
        st.session_state.running = False
        st.stop()

    # Show IBGE state
    if not surname_ranks:
        status.warning("IBGE cache not found/empty: Active Search Injection will still run, but scores may not populate.")

    # -------------------------
    # Classic (Requests)
    # -------------------------
    if mode.startswith("Classic"):
        status.update(label="Classic Directory (Requests) running...", state="running")
        try:
            r = requests.get(start_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
            if r.status_code != 200:
                st.error(f"Request failed: {r.status_code}")
            else:
                names = extract_names_universal(r.text)
                matches = match_names(names, "Classic")
                push_matches(matches)
        except Exception as e:
            st.error(f"Error: {e}")

    # -------------------------
    # Infinite scroll (Selenium)
    # -------------------------
    elif mode.startswith("Infinite"):
        if not HAS_SELENIUM:
            st.error("Selenium not installed in this environment.")
            st.session_state.running = False
            st.stop()

        driver = get_driver(headless=run_headless)
        if not driver:
            st.error("Could not start Selenium driver.")
            st.session_state.running = False
            st.stop()

        try:
            driver.get(start_url)
            selenium_wait_document_ready(driver, timeout=min(10, int(selenium_wait)))
            for i in range(int(max_cycles)):
                if not st.session_state.running:
                    break

                status.update(label=f"Infinite scroll cycle {i+1}/{int(max_cycles)}...", state="running")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.0)

                html = driver.page_source or ""
                names = extract_names_universal(html)
                matches = match_names(names, f"Scroll {i+1}")
                push_matches(matches)

                if st.session_state.matches:
                    table_placeholder.dataframe(pd.DataFrame(st.session_state.matches), use_container_width=True, height=360)
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    # -------------------------
    # Active Search Injection
    # -------------------------
    else:
        status.update(label="Active Search Injection running...", state="running")

        # Choose surname source:
        # - If IBGE available: use top ranked surnames
        # - Else: use a small universal demo list
        if sorted_surnames:
            search_terms = sorted_surnames[: int(max_cycles)]
        else:
            # fallback sample list so the mode still runs without IBGE cache
            search_terms = ["SILVA", "SOUZA", "OLIVEIRA", "SANTOS", "PEREIRA"][: int(max_cycles)]

        driver = None
        if HAS_SELENIUM:
            driver = get_driver(headless=run_headless)

        for idx, surname in enumerate(search_terms, start=1):
            if not st.session_state.running:
                break

            status.update(label=f"🔎 Searching '{surname}' ({idx}/{len(search_terms)})", state="running")

            # Try Selenium first
            html = None
            names: List[str] = []
            outcome = "timeout"

            if driver:
                try:
                    driver.get(start_url)
                    selenium_wait_document_ready(driver, timeout=min(10, int(selenium_wait)))

                    input_el = find_search_input_element(driver)
                    if input_el:
                        # try multiple casings (some sites behave differently)
                        variants = [surname, surname.title(), surname.lower()]
                        variants = list(dict.fromkeys(variants))

                        for term in variants:
                            ok = submit_search(driver, input_el, term)
                            if not ok:
                                continue

                            outcome, html, names = wait_for_outcome_universal(driver, timeout_s=int(selenium_wait))
                            if outcome in ("names", "no_results"):
                                break

                    else:
                        # no input found; fall back to requests param search
                        html = None

                except Exception:
                    html = None

            # Requests fallback if Selenium failed
            if not html:
                html = try_requests_param_search(start_url, surname)
                if html:
                    if page_has_no_results(html):
                        outcome = "no_results"
                        names = []
                    else:
                        names = extract_names_universal(html)
                        outcome = "names" if names else "timeout"

            # Decide action based on outcome
            if outcome == "no_results":
                status.write(f"🚫 '{surname}': no results detected.")
            elif outcome == "timeout":
                status.write(f"⏱️ '{surname}': timed out waiting for results (parsed what was available: {len(names)} candidates).")
            else:
                status.write(f"✅ '{surname}': results detected ({len(names)} candidates).")

            # Match + update table
            if html:
                matches = match_names(names, f"Search: {surname}")
                push_matches(matches)
                if st.session_state.matches:
                    table_placeholder.dataframe(pd.DataFrame(st.session_state.matches), use_container_width=True, height=360)

            time.sleep(float(between_surnames_pause))

        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    status.update(label="Done ✅", state="complete")
    st.session_state.running = False


# =========================================================
# Output
# =========================================================
if st.session_state.matches:
    st.markdown("---")
    st.subheader("Results")
    df = pd.DataFrame(st.session_state.matches)
    st.dataframe(df, use_container_width=True, height=420)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 Download CSV", df.to_csv(index=False).encode("utf-8"), "results.csv")
    with c2:
        b = io.BytesIO()
        with pd.ExcelWriter(b, engine="xlsxwriter") as w:
            df.to_excel(w, index=False)
        st.download_button("📥 Download Excel", b.getvalue(), "results.xlsx")
else:
    st.info("No matches yet.")
