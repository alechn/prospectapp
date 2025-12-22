import streamlit as st
import time
import re
import os
import json
import requests
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse, parse_qs, urlencode
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
    from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
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
st.caption("Probe → Detect → Decide → Extract (Universal, log-heavy)")

c1, c2, c3 = st.columns([3, 1, 1])
TARGET_URL = c1.text_input("Target URL", "https://web.mit.edu/directory/")
TERM = c2.text_input("Test Surname", "oliveira")
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
# Logging
# =========================================================
def log(status, msg: str):
    if status is not None:
        status.write(msg)

def vlog(status, msg: str):
    if DEBUG_VERBOSE:
        log(status, msg)


# =========================================================
# Simple candidate/name-ish filter (debug-focused)
# =========================================================
NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,6}$")
NO_RESULTS_PHRASES = [
    "no results", "0 results", "zero results", "no matches", "nothing found",
    "did not match", "try a different", "no records", "no entries"
]

def clean_candidate_text(t: str) -> Optional[str]:
    if not t:
        return None
    t = " ".join(str(t).split()).strip()
    if len(t) < 3 or len(t) > 120:
        return None
    lt = t.lower()
    junk = [
        "privacy", "accessibility", "cookie", "terms", "login", "sign up", "signup",
        "home", "about", "contact", "menu", "skip to", "search results", "results"
    ]
    if any(j in lt for j in junk):
        return None
    if NAME_REGEX.match(t):
        return t
    return None

def html_has_no_results_signal(text_or_html: str) -> bool:
    t = (text_or_html or "").lower()
    return any(p in t for p in NO_RESULTS_PHRASES)


# =========================================================
# Requests probe (universal-ish)
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
    for sel in ["h1", "h2", "h3", "h4", "strong", "a", "td", "li"]:
        for el in soup.select(sel):
            c = clean_candidate_text(el.get_text(" ", strip=True))
            if c:
                out.append(c)
        if len(out) >= 300:
            break
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def requests_probe_server_search(base_url: str, term: str, status) -> Optional[Dict[str, Any]]:
    base_sc, base_html = fetch_url(base_url)
    if base_sc != 200:
        vlog(status, f"⚠ Requests base fetch status={base_sc}")
        base_html = ""
    base_fp = hash(base_html) if base_html else 0

    u = urlparse(base_url)
    candidates = [base_url]

    # sibling /search attempt (universal heuristic)
    if u.netloc:
        root = u._replace(path="/", query="", fragment="").geturl().rstrip("/")
        candidates.extend([root + "/search", root + "/search/"])

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

            # Success only if term appears in raw HTML.
            # (If the site is JS-rendered, this will fail and we fall back to Selenium.)
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
# Selenium driver
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
# Universal DOM “intel”
# Key fixes from your logs:
# 1) Never hold a single element reference while the page/app navigates.
#    (stale element was killing your debug view)
# 2) Re-detect root after navigation OR if stale
# 3) Pick a root that actually has text/links (score by measured metrics)
# =========================================================
def safe_inner_text(driver, css: str) -> str:
    try:
        return driver.execute_script(
            "const el = document.querySelector(arguments[0]); return el ? (el.innerText || '') : '';",
            css
        ) or ""
    except Exception:
        return ""

def safe_metrics(driver, css: str) -> Dict[str, int]:
    """
    Metrics computed in-page so we don't get stale element errors.
    """
    try:
        return driver.execute_script(
            """
            const sel = arguments[0];
            const el = document.querySelector(sel);
            if (!el) return null;
            const txt = (el.innerText || "");
            const a = el.querySelectorAll("a").length;
            const li = el.querySelectorAll("li").length;
            const tr = el.querySelectorAll("tr").length;
            const tlen = Math.min(txt.trim().length, 200000);
            return {tlen, a, li, tr};
            """,
            css
        ) or {"tlen": 0, "a": 0, "li": 0, "tr": 0}
    except Exception:
        return {"tlen": 0, "a": 0, "li": 0, "tr": 0}

def score_root_metrics(m: Dict[str, int], css: str) -> float:
    hint = css.lower()
    hint_score = 0.0
    for h in ["result", "results", "search", "directory", "listing", "people", "person", "profile", "entry", "gsc", "gcse"]:
        if h in hint:
            hint_score += 2.0
    size_score = min(m.get("tlen", 0), 200000) / 800.0
    link_score = m.get("a", 0) * 0.15
    list_score = m.get("li", 0) * 0.35 + m.get("tr", 0) * 0.45
    return hint_score + size_score + link_score + list_score

def detect_results_root_css(driver, status) -> Optional[str]:
    """
    Return a CSS selector string (not a WebElement) so we can query it safely
    even after navigation/re-render.
    """
    if MANUAL_RESULTS_ROOT.strip():
        css = MANUAL_RESULTS_ROOT.strip()
        m = safe_metrics(driver, css)
        vlog(status, f"🎯 Manual results root '{css}' metrics={m}")
        # accept even if empty; user might be testing
        return css

    # Strong candidates first (universal + includes common search widgets)
    candidates = [
        "main",
        "#main",
        "[role='main']",
        "#results",
        "#search-results",
        ".results",
        ".search-results",
        ".result",
        ".search-result",
        ".directory",
        ".listing",
        ".list",
        ".people",
        ".profiles",
        ".entries",
        ".items",
        # Google CSE commonly uses these
        ".gsc-control-cse",
        ".gsc-results-wrapper-visible",
        ".gcse-searchresults",
        ".gsc-resultsbox-visible",
        ".gsc-webResult",
        "#gs_tti50",  # sometimes present, harmless
        # fallback-ish sections some sites use
        "section",
        "article",
    ]

    best_css = None
    best_score = -1e18
    checked = 0

    # Deduplicate while preserving order
    seen = set()
    ordered = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)

    for css in ordered:
        checked += 1
        m = safe_metrics(driver, css)
        s = score_root_metrics(m, css)

        vlog(status, f"🧪 root_candidate css={css} metrics={m} score={s:.2f}")

        # Prefer roots that have *some* content
        if m.get("tlen", 0) == 0 and m.get("a", 0) == 0 and m.get("li", 0) == 0 and m.get("tr", 0) == 0:
            continue

        if s > best_score:
            best_score = s
            best_css = css

    if best_css:
        vlog(status, f"🧠 Results root chosen: css={best_css} score={best_score:.2f}")
    else:
        vlog(status, f"⚠ No non-empty root found (checked {checked}).")
    return best_css

def extract_candidates_from_root_html(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []
    for sel in ["h1", "h2", "h3", "h4", "a", "strong", "td", "li"]:
        for el in soup.select(sel):
            c = clean_candidate_text(el.get_text(" ", strip=True))
            if c:
                out.append(c)
        if len(out) >= 250:
            break
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def root_inner_html(driver, css: str) -> str:
    try:
        return driver.execute_script(
            "const el = document.querySelector(arguments[0]); return el ? (el.innerHTML || '') : '';",
            css
        ) or ""
    except Exception:
        return ""

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

    # Enter first
    try:
        inp.send_keys(Keys.RETURN)
        vlog(status, "⌨️ Submitted with ENTER")
        return True
    except Exception:
        pass

    # Click submit
    if click_submit_if_possible(driver):
        vlog(status, "🖱️ Submitted by clicking submit button")
        return True

    # Form submit
    try:
        inp.submit()
        vlog(status, "📨 Submitted by form submit()")
        return True
    except Exception:
        return False

def wait_for_search_outcome_universal(
    driver,
    term: str,
    timeout: int,
    status,
    poll: float = 0.25
) -> Tuple[str, Dict[str, Any], List[str], Optional[str]]:
    """
    FIXED WAITER:
    - Re-detect results root CSS after navigation or if root is empty
    - Use in-page JS to read text/metrics so we don't get StaleElementReferenceException
    - Decide outcome by:
        A) "no results" phrases inside root text
        B) term seen in root text (case-insensitive)
        C) material change in extracted candidates after root starts having content
    """
    start = time.time()
    term_l = term.lower()

    # Root is a CSS selector string; safe to re-query anytime
    root_css = detect_results_root_css(driver, status)

    # baseline snapshots
    baseline_url = driver.current_url
    baseline_cands: List[str] = []
    baseline_set: set = set()
    baseline_metrics = {"tlen": 0, "a": 0, "li": 0, "tr": 0}

    def refresh_root_if_needed(reason: str):
        nonlocal root_css
        new_css = detect_results_root_css(driver, status)
        if new_css and new_css != root_css:
            vlog(status, f"🔁 Root updated ({reason}): {root_css} → {new_css}")
            root_css = new_css

    # initial baseline
    if root_css:
        baseline_metrics = safe_metrics(driver, root_css)
        html = root_inner_html(driver, root_css)
        baseline_cands = extract_candidates_from_root_html(html)
        baseline_set = set(baseline_cands)
        vlog(status, f"🧪 Baseline root css={root_css} metrics={baseline_metrics} cands={len(baseline_cands)}")
    else:
        vlog(status, "⚠ No results root at baseline; will keep trying to detect during wait loop.")

    last_sig = (baseline_metrics.get("tlen", 0), baseline_metrics.get("a", 0), baseline_metrics.get("li", 0), baseline_metrics.get("tr", 0))
    last_cset = set(baseline_set)

    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)
        cur_url = driver.current_url

        # If URL changed, the app likely navigated → re-detect root
        if cur_url != baseline_url:
            baseline_url = cur_url
            refresh_root_if_needed("url_changed")

        if not root_css:
            refresh_root_if_needed("root_missing")
            time.sleep(poll)
            continue

        m = safe_metrics(driver, root_css)
        sig = (m.get("tlen", 0), m.get("a", 0), m.get("li", 0), m.get("tr", 0))
        changed = sig != last_sig
        last_sig = sig

        txt = safe_inner_text(driver, root_css)
        txt_l = txt.lower()

        term_seen = term_l in txt_l
        nores = html_has_no_results_signal(txt_l)

        vlog(status, f"🧪 t={elapsed}s root={root_css} changed={changed} term_seen={term_seen} no_results={nores} metrics={m}")

        # If root seems empty for too long, try re-detecting a better root
        if elapsed > 1.0 and m.get("tlen", 0) == 0 and m.get("a", 0) == 0:
            refresh_root_if_needed("root_empty")
            time.sleep(poll)
            continue

        if nores:
            html = root_inner_html(driver, root_css)
            cands = extract_candidates_from_root_html(html)
            return "no_results", {"elapsed": elapsed, "root_css": root_css, "metrics": m}, cands, root_css

        if term_seen and (m.get("tlen", 0) > 0):
            html = root_inner_html(driver, root_css)
            cands = extract_candidates_from_root_html(html)
            return "results_term_seen", {"elapsed": elapsed, "root_css": root_css, "metrics": m}, cands, root_css

        if changed and (m.get("tlen", 0) > 0):
            html = root_inner_html(driver, root_css)
            cands = extract_candidates_from_root_html(html)
            cset = set(cands)
            new_items = list(cset - last_cset)
            last_cset = cset

            # material change threshold (tunable)
            if len(new_items) >= 3 or (len(cset) > 0 and len(baseline_set) == 0):
                return "results_changed", {
                    "elapsed": elapsed,
                    "root_css": root_css,
                    "metrics": m,
                    "new_items_preview": new_items[:10],
                }, cands, root_css

        time.sleep(poll)

    # timeout
    cands: List[str] = []
    m = {"tlen": 0, "a": 0, "li": 0, "tr": 0}
    if root_css:
        m = safe_metrics(driver, root_css)
        html = root_inner_html(driver, root_css)
        cands = extract_candidates_from_root_html(html)

    return "timeout", {"elapsed": round(time.time() - start, 1), "root_css": root_css, "metrics": m}, cands, root_css


# =========================================================
# RUNNER
# =========================================================
if RUN:
    status = st.status("Running debugger...", expanded=True)

    # 0) Requests probe
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
            st.dataframe({"Candidates (first 50)": req_hit["candidates"][:50]})
            status.update(label="Done", state="complete")
            st.stop()
        else:
            log(status, "ℹ️ Requests probe did not find a confident server-side search path.")

    # 1) Selenium path
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
        st.error("Could not start Selenium driver (driver mismatch / SessionNotCreatedException).")
        st.stop()

    try:
        driver.get(TARGET_URL)
        selenium_wait_ready(driver, timeout=10)
        time.sleep(0.5)

        inp = find_search_input(driver)
        if not inp:
            status.update(label="Done", state="complete")
            st.error("No search input found (try manual selector).")
            st.stop()

        vlog(status, f"🎯 Search input: tag={inp.tag_name} type={inp.get_attribute('type')} "
                    f"name={inp.get_attribute('name')} id={inp.get_attribute('id')} class={(inp.get_attribute('class') or '')[:80]}")

        ok = submit_query(driver, inp, TERM, status)
        if not ok:
            status.update(label="Done", state="complete")
            st.error("Failed to submit query (try manual submit selector).")
            st.stop()

        log(status, f"🧪 Waiting for outcome for '{TERM}' (timeout={TIMEOUT}s)")
        state, meta, candidates, root_css = wait_for_search_outcome_universal(
            driver=driver,
            term=TERM,
            timeout=int(TIMEOUT),
            status=status,
            poll=0.25
        )

        status.update(label="Done", state="complete")

        st.subheader("🧠 Result")
        st.write("Strategy used: `selenium_universal_fixed`")
        st.write(f"Outcome state: `{state}`")
        st.write(f"Current URL: {driver.current_url}")
        st.write(f"Results root CSS: `{root_css}`")
        st.write(f"Meta: `{json.dumps(meta, ensure_ascii=False)}`")
        st.write(f"Candidates extracted: {len(candidates)}")

        nameish = [c for c in candidates if NAME_REGEX.match(c)]
        st.write(f"Name-ish candidates: {len(nameish)}")

        # show a compact table
        max_rows = 60
        st.dataframe({
            "Candidates": candidates[:max_rows],
            "Name-ish": nameish[:max_rows] + [""] * max(0, max_rows - len(nameish[:max_rows]))
        })

        with st.expander("🔎 Debug: root text preview + metrics", expanded=False):
            if root_css:
                preview = safe_inner_text(driver, root_css)[:1200]
                st.write("Root metrics:", safe_metrics(driver, root_css))
                st.write("Root text preview:")
                st.code(preview or "(empty)")

    except WebDriverException as e:
        status.update(label="Done (webdriver error)", state="complete")
        st.error(f"WebDriver error: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
