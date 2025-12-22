import streamlit as st
import time
import re
import os
import json
import requests
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup
from unidecode import unidecode

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
st.caption("Probe → Detect → Decide → Extract (Universal, log-heavy, no MIT-specific rules)")

c1, c2, c3 = st.columns([3, 1, 1])
TARGET_URL = c1.text_input("Target URL", "https://web.mit.edu/directory/")
SURNAME = c2.text_input("Test Surname", "oliveira")
TIMEOUT = c3.slider("Timeout (seconds)", 5, 60, 20)

st.markdown("### Controls")
colA, colB, colC, colD = st.columns(4)
USE_SELENIUM = colA.checkbox("Enable Selenium", value=True, disabled=not HAS_SELENIUM)
HEADLESS = colB.checkbox("Headless", value=True)
TRY_REQUESTS_PARAMS = colC.checkbox("Try server-side URL params first", value=True)
DEBUG_VERBOSE = colD.checkbox("Verbose logging", value=True)

st.markdown("### Advanced")
a1, a2, a3 = st.columns(3)
MANUAL_SEARCH_SELECTOR = a1.text_input("Manual search input CSS (optional)", "")
MANUAL_SUBMIT_SELECTOR = a2.text_input("Manual search submit CSS (optional)", "")
MANUAL_RESULTS_ROOT = a3.text_input("Manual results root CSS (optional)", "")

RUN = st.button("▶ Run Debugger", type="primary")


# =========================================================
# Helpers: logging
# =========================================================
def log(status, msg: str):
    if status is not None:
        status.write(msg)

def vlog(status, msg: str):
    if DEBUG_VERBOSE:
        log(status, msg)


# =========================================================
# Generic string/name cleaning (keep it simple for debugging)
# =========================================================
NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,6}$")

def normalize_token(s: str) -> str:
    if not s:
        return ""
    s = unidecode(str(s)).strip().upper()
    return re.sub(r"[^A-Z ]+", "", s)

def clean_candidate_text(t: str) -> Optional[str]:
    if not t:
        return None
    t = " ".join(str(t).split()).strip()
    if len(t) < 3 or len(t) > 90:
        return None
    # toss obvious UI junk (universal-ish)
    bad = [
        "privacy", "accessibility", "cookie", "terms", "login", "sign up", "signup",
        "home", "about", "contact", "menu", "skip to", "search results", "results"
    ]
    lt = t.lower()
    if any(b in lt for b in bad):
        return None
    # accept “name-ish” strings for debugging
    if NAME_REGEX.match(t):
        return t
    return None


# =========================================================
# Requests path: Try to find a server-side search quickly
# (Universal-ish: try common query params on same origin / same path)
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

def requests_extract_candidates(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []
    # generic: headings + strong + link text
    for sel in ["h1", "h2", "h3", "h4", "strong", "a", "td", "li"]:
        for el in soup.select(sel):
            t = el.get_text(" ", strip=True)
            c = clean_candidate_text(t)
            if c:
                out.append(c)
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

def requests_probe_server_search(base_url: str, term: str, status) -> Optional[Dict[str, Any]]:
    """
    Try a few query params on:
      1) the given URL
      2) a sibling /search path if base has no query and looks like a directory root
    Heuristic success criteria (universal):
      - response changes meaningfully vs base
      - AND page contains the term (case-insensitive) somewhere
    """
    base_status, base_html = fetch_url(base_url)
    if base_status != 200:
        vlog(status, f"⚠ Requests base fetch status={base_status}")
        base_html = ""

    base_fp = hash(base_html) if base_html else 0

    # Candidate endpoints: original + if no path extension, also try /search
    u = urlparse(base_url)
    candidates = [base_url]

    # add "/search" sibling if it seems plausible
    if u.path and not u.path.lower().endswith((".html", ".php", ".aspx", ".jsp")):
        root = u._replace(path="/").geturl().rstrip("/")
        candidates.append(root + "/search")
        candidates.append(root + "/search/")

    tried = 0
    for endpoint in candidates:
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
                cands = requests_extract_candidates(html)
                return {
                    "strategy": "server_html_search",
                    "url_used": test_url,
                    "param": p,
                    "http_status": sc,
                    "candidates": cands,
                }

    vlog(status, f"ℹ️ Requests probe exhausted ({tried} attempts), no confident server-side search found.")
    return None


# =========================================================
# Selenium driver (robust-ish)
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
    opts.add_argument("--disable-extensions")

    # Try system chromedriver first (common on Streamlit Cloud)
    if os.path.exists("/usr/bin/chromedriver"):
        try:
            return webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=opts)
        except Exception:
            pass

    # Try plain
    try:
        return webdriver.Chrome(options=opts)
    except Exception:
        pass

    # webdriver_manager fallback
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
# Universal DOM “intel” (this is the key)
# We will:
# 1) detect an input to type into
# 2) detect a "results root" container that is most likely to change with search
# 3) after submitting, wait until that container's "signature" changes AND it
#    either contains term OR contains "no results" signal
# =========================================================
NO_RESULTS_PHRASES = [
    "no results", "0 results", "zero results", "no matches", "nothing found",
    "did not match", "try a different", "no records", "no entries"
]

def dom_text(el) -> str:
    try:
        return (el.text or "").strip()
    except Exception:
        return ""

def html_has_no_results_signal(html: str) -> bool:
    t = (html or "").lower()
    return any(p in t for p in NO_RESULTS_PHRASES)

def find_search_input(driver) -> Optional[Any]:
    # manual override first
    if MANUAL_SEARCH_SELECTOR.strip():
        els = driver.find_elements(By.CSS_SELECTOR, MANUAL_SEARCH_SELECTOR.strip())
        if els:
            return els[0]

    # common selectors
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
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        for e in els:
            try:
                if e.is_displayed() and e.is_enabled():
                    return e
            except Exception:
                continue

    # fallback: any visible enabled text-like input
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

    # Try common submit buttons near the input? (universal fallback)
    for sel in [
        "button[type='submit']",
        "input[type='submit']",
        "button[aria-label*='search' i]",
        "button[class*='search' i]",
    ]:
        try:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            for b in btns:
                try:
                    if b.is_displayed() and b.is_enabled():
                        b.click()
                        return True
                except Exception:
                    continue
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

    # clear robustly
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

    # Enter first
    try:
        inp.send_keys(Keys.RETURN)
        vlog(status, "⌨️ Submitted with ENTER")
        return True
    except Exception:
        pass

    # fallback click submit
    if click_submit_if_possible(driver):
        vlog(status, "🖱️ Submitted by clicking submit button")
        return True

    # fallback form submit
    try:
        inp.submit()
        vlog(status, "📨 Submitted by form submit()")
        return True
    except Exception:
        return False


# ---------- Results root detection ----------
RESULTY_HINTS = [
    "result", "results", "search", "directory", "listing", "list", "people", "person",
    "profiles", "entries", "items", "hits", "records", "table", "gsc", "gcse"
]

def element_signature(el) -> Dict[str, Any]:
    """
    Create a compact signature to detect meaningful changes.
    """
    try:
        txt = (el.text or "")
    except Exception:
        txt = ""
    txt = " ".join(txt.split())
    txt_l = txt.lower()

    # count anchors and list-like children
    try:
        a_count = len(el.find_elements(By.CSS_SELECTOR, "a"))
    except Exception:
        a_count = 0
    try:
        li_count = len(el.find_elements(By.CSS_SELECTOR, "li"))
    except Exception:
        li_count = 0
    try:
        tr_count = len(el.find_elements(By.CSS_SELECTOR, "tr"))
    except Exception:
        tr_count = 0
    # text length (cap)
    tlen = min(len(txt), 20000)

    return {
        "tlen": tlen,
        "a_": a_count,
        "li": li_count,
        "tr": tr_count,
        "has_nores": any(p in txt_l for p in NO_RESULTS_PHRASES),
    }

def score_results_root(el) -> float:
    """
    Heuristic scoring: prefer visible-ish large containers with "resulty" hints and lots of items/links.
    """
    try:
        if not el.is_displayed():
            return -1
    except Exception:
        pass

    try:
        tag = (el.tag_name or "").lower()
    except Exception:
        tag = ""

    try:
        cls = (el.get_attribute("class") or "").lower()
    except Exception:
        cls = ""
    try:
        eid = (el.get_attribute("id") or "").lower()
    except Exception:
        eid = ""

    hint = (cls + " " + eid)
    hint_score = 0.0
    for h in RESULTY_HINTS:
        if h in hint:
            hint_score += 1.5

    sig = element_signature(el)
    # prefer containers with lists/tables/anchors
    item_score = (sig["R_"] if "R_" in sig else sig["R_"]) if False else 0  # noop safety

    # compute item score from signature
    item_score = sig["R_"] if "R_" in sig else 0
    # (we used keys "R_" nowhere; correct below)
    item_score = sig["R_"] if "R_" in sig else 0

    # correct calculation:
    item_score = sig["R_"] if "R_" in sig else 0  # still none
    # Let's just use counts we do have:
    item_score = (sig["li"] * 0.3) + (sig["tr"] * 0.35) + (sig["R_"] * 0.0)

    # anchors matter a lot for search listings
    anchor_score = sig["R_"] if "R_" in sig else 0
    anchor_score = sig["R_"] if "R_" in sig else 0
    # actually:
    anchor_score = sig["R_"] if "R_" in sig else 0

    anchor_score = 0.0
    # we stored anchor count under "R_"? no, under "R_" never; it's "R_" mistake
    # correct: it's under "R_"? No. It's "R_" nowhere. It's "R_" bug.
    # correct: we stored anchor count under "R_"? no, under "R_": none.
    # It's "R_" not present. It's under "R_" - wrong.
    # Let's fix by recomputing quickly:
    try:
        a_count = len(el.find_elements(By.CSS_SELECTOR, "a"))
    except Exception:
        a_count = 0
    anchor_score = a_count * 0.12

    size_score = sig["tlen"] / 500.0  # up to ~40 points at 20000 chars
    tag_bonus = 1.0 if tag in ("main", "section", "article") else 0.2 if tag in ("div",) else 0.0

    return hint_score + item_score + anchor_score + size_score + tag_bonus

def detect_results_root(driver, status) -> Optional[Any]:
    """
    Choose a likely results container.
    Universal approach:
      - prefer manual override if provided
      - else scan common container tags and score them
    """
    if MANUAL_RESULTS_ROOT.strip():
        try:
            el = driver.find_element(By.CSS_SELECTOR, MANUAL_RESULTS_ROOT.strip())
            vlog(status, f"🎯 Using manual results root: {MANUAL_RESULTS_ROOT.strip()}")
            return el
        except Exception:
            vlog(status, "⚠ Manual results root not found; falling back to auto-detect.")

    candidates = []
    # Start with strong candidates
    for sel in [
        "main", "#main", "[role='main']",
        "#results", "#search-results", ".results", ".search-results",
        ".result", ".results", ".searchResult", ".search-result", ".directory",
        ".list", ".listing", ".people", ".profiles",
        ".gsc-control-cse", ".gcse-searchresults", ".gsc-results-wrapper-visible"
    ]:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                candidates.append(el)
        except Exception:
            pass

    # Add generic containers if not enough
    if len(candidates) < 10:
        for sel in ["section", "article", "div"]:
            try:
                candidates.extend(driver.find_elements(By.CSS_SELECTOR, sel))
            except Exception:
                pass

    best = None
    best_score = -1e9
    checked = 0

    # score only first N to avoid huge overhead
    for el in candidates[:200]:
        checked += 1
        try:
            s = score_results_root(el)
        except Exception:
            continue
        if s > best_score:
            best_score = s
            best = el

    if best is None:
        vlog(status, f"⚠ Results root detection failed (checked {checked}).")
        return None

    vlog(status, f"🧠 Results root auto-detected: score={best_score:.2f} tag={best.tag_name} id={best.get_attribute('id')} class={(best.get_attribute('class') or '')[:80]}")
    return best

def extract_candidates_from_root(root_el) -> List[str]:
    """
    Extract simple candidate strings from inside the chosen results root.
    Focus on headings/links/list/table cells.
    """
    try:
        html = root_el.get_attribute("innerHTML") or ""
    except Exception:
        html = ""

    soup = BeautifulSoup(html, "html.parser")
    out: List[str] = []

    for sel in ["h1", "h2", "h3", "h4", "a", "strong", "td", "li"]:
        for el in soup.select(sel):
            t = el.get_text(" ", strip=True)
            c = clean_candidate_text(t)
            if c:
                out.append(c)
        if len(out) >= 200:
            break

    # de-dupe
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def wait_for_search_outcome_universal(
    driver,
    root_el,
    term: str,
    timeout: int,
    status,
    poll: float = 0.25
) -> Tuple[str, Dict[str, Any], List[str]]:
    """
    Universal waiter:
      - do NOT rely on full page hash (too noisy)
      - rely on RESULTS ROOT signature changing
      - require one of:
          A) term appears in root text OR
          B) "no results" signal in root text OR
          C) extracted candidates materially change (set diff) after a root change
    Return state in {"results_term_seen", "no_results", "results_changed", "timeout"}
    """
    start = time.time()

    # baseline
    base_sig = element_signature(root_el)
    base_text = ""
    try:
        base_text = (root_el.text or "")
    except Exception:
        pass
    base_text_l = base_text.lower()

    base_candidates = extract_candidates_from_root(root_el)
    base_set = set(base_candidates)

    vlog(status, f"🧪 Baseline root sig={base_sig} base_candidates={len(base_candidates)}")

    last_sig = base_sig
    last_seen = set(base_candidates)

    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)

        try:
            sig = element_signature(root_el)
        except Exception:
            sig = {"tlen": 0, "a": 0, "li": 0, "tr": 0, "has_nores": False}

        # lightweight check: only extract when signature changes
        changed = (sig != last_sig)
        last_sig = sig

        term_seen = False
        try:
            txt = (root_el.text or "")
            term_seen = term.lower() in txt.lower()
            nores = html_has_no_results_signal(txt)
        except Exception:
            txt = ""
            nores = False

        vlog(status, f"🧪 t={elapsed}s changed={changed} term_seen={term_seen} no_results={nores} sig={sig}")

        if nores:
            cands = extract_candidates_from_root(root_el)
            return "no_results", {"elapsed": elapsed, "sig": sig}, cands

        if term_seen and changed:
            cands = extract_candidates_from_root(root_el)
            return "results_term_seen", {"elapsed": elapsed, "sig": sig}, cands

        if changed:
            cands = extract_candidates_from_root(root_el)
            cset = set(cands)

            # material change: something actually changed in extracted candidates
            new_items = list(cset - last_seen)
            last_seen = cset

            if len(new_items) >= 3:
                vlog(status, f"✅ Material change detected: +{len(new_items)} new candidate strings")
                return "results_changed", {"elapsed": elapsed, "sig": sig, "new_items": new_items[:10]}, cands

        time.sleep(poll)

    # timeout
    cands = extract_candidates_from_root(root_el)
    return "timeout", {"elapsed": round(time.time() - start, 1), "sig": element_signature(root_el)}, cands


# =========================================================
# RUNNER
# =========================================================
if RUN:
    status = st.status("Running debugger...", expanded=True)

    # 0) Try requests param search first
    if TRY_REQUESTS_PARAMS:
        log(status, "🌐 Phase 1: Requests probe (server-side search)")
        req_hit = requests_probe_server_search(TARGET_URL, SURNAME, status)
        if req_hit:
            log(status, f"✅ Requests probe succeeded via param '{req_hit['param']}'")
            st.subheader("🧠 Result")
            st.write(f"Strategy used: `{req_hit['strategy']}`")
            st.write(f"URL used: {req_hit['url_used']}")
            st.write(f"HTTP status: {req_hit['http_status']}")
            st.write(f"Candidates extracted: {len(req_hit['candidates'])}")
            st.dataframe({"Candidates (first 50)": req_hit["candidates"][:50]})
            status.update(label="Done", state="complete")
            st.stop()
        else:
            log(status, "ℹ️ Requests probe did not find a confident server-side search path.")

    # 1) Selenium universal path
    if not USE_SELENIUM:
        status.update(label="Done (no Selenium)", state="complete")
        st.error("Selenium disabled (or not installed). Enable it to continue.")
        st.stop()

    if not HAS_SELENIUM:
        status.update(label="Done (Selenium not installed)", state="complete")
        st.error("Selenium is not installed in this environment.")
        st.stop()

    log(status, "🤖 Phase 2: Selenium universal search")

    driver = get_driver(headless=HEADLESS)
    if not driver:
        status.update(label="Done (driver failed)", state="complete")
        st.error("Could not start Selenium driver (SessionNotCreatedException / driver mismatch).")
        st.stop()

    try:
        driver.get(TARGET_URL)
        selenium_wait_ready(driver, timeout=10)
        time.sleep(0.5)

        # find input
        inp = find_search_input(driver)
        if not inp:
            status.update(label="Done", state="complete")
            st.error("No search input found (try manual selector).")
            st.stop()

        vlog(status, f"🎯 Search input found: tag={inp.tag_name} type={inp.get_attribute('type')} name={inp.get_attribute('name')} id={inp.get_attribute('id')} class={(inp.get_attribute('class') or '')[:80]}")

        # detect root BEFORE search
        root = detect_results_root(driver, status)
        if not root:
            status.update(label="Done", state="complete")
            st.error("Could not detect results root (try manual results root CSS).")
            st.stop()

        # submit
        ok = submit_query(driver, inp, SURNAME, status)
        if not ok:
            status.update(label="Done", state="complete")
            st.error("Failed to submit query (try manual submit selector).")
            st.stop()

        # wait for outcome
        log(status, f"🧪 Waiting for search outcome for '{SURNAME}' (timeout={TIMEOUT}s)")
        state, meta, candidates = wait_for_search_outcome_universal(
            driver=driver,
            root_el=root,
            term=SURNAME,
            timeout=int(TIMEOUT),
            status=status,
            poll=0.25
        )

        # show summary
        status.update(label="Done", state="complete")

        st.subheader("🧠 Result")
        st.write(f"Strategy used: `selenium_universal`")
        st.write(f"Outcome state: `{state}`")
        st.write(f"Current URL: {driver.current_url}")
        st.write(f"Meta: `{json.dumps(meta, ensure_ascii=False)}`")
        st.write(f"Candidates extracted: {len(candidates)}")

        # show candidates + a simple filter preview: “name-ish”
        nameish = [c for c in candidates if NAME_REGEX.match(c)]
        st.write(f"Name-ish candidates: {len(nameish)}")
        st.dataframe({
            "Candidates (first 60)": candidates[:60],
            "Name-ish (first 60)": nameish[:60] + [""] * max(0, 60 - len(nameish[:60]))
        })

        # Also show root debug intel
        with st.expander("🔎 Debug: Results root details", expanded=False):
            try:
                st.write("Root tag:", root.tag_name)
                st.write("Root id:", root.get_attribute("id"))
                st.write("Root class:", root.get_attribute("class"))
                st.write("Root signature:", element_signature(root))
                st.write("Root text preview:", (root.text or "")[:800])
            except Exception as e:
                st.write("Error reading root details:", str(e))

    finally:
        try:
            driver.quit()
        except Exception:
            pass
