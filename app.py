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
st.caption("Probe → Detect → Decide → Extract (universal, log-heavy, people-block aware)")

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
b1, b2, b3 = st.columns(3)
FALLBACK_SLEEP = b1.slider("Fallback sleep after submit (seconds)", 0, 30, 15)
TRY_TAB_CLICK = b2.checkbox("Try to click People/Directory tab/filter", value=True)
AUTO_PEOPLE_BLOCK = b3.checkbox("Auto-select best People-like TEXT block", value=True)

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
# Regexes & signals
# =========================================================
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

# "Lastname, Firstname" OR "Firstname Lastname"
NAME_COMMA_RE = re.compile(
    r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,4},\s*"
    r"[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,4}$"
)
NAME_SPACE_RE = re.compile(
    r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+(?:da|de|do|dos|das|del|della|di|van|von|bin|ibn))?"
    r"(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){1,5}$",
    re.I
)

PEOPLE_KEYWORDS = [
    "people", "person", "directory", "staff", "faculty", "students", "student",
    "profiles", "contacts", "employees", "members"
]
NONPEOPLE_HEADINGS = ["websites", "locations", "news", "events", "maps", "jobs"]

NO_RESULTS_REGEXES = [
    re.compile(r"\bno\s+results\b", re.I),
    re.compile(r"\b0\s+results\b", re.I),
    re.compile(r"\bzero\s+results\b", re.I),
    re.compile(r"\bno\s+matches\b", re.I),
    re.compile(r"\bnothing\s+found\b", re.I),
    re.compile(r"\bdid\s+not\s+match\b", re.I),
    re.compile(r"\bno\s+records\b", re.I),
]

def text_has_no_results_signal(text: str) -> bool:
    t = text or ""
    if EMAIL_RE.search(t):
        return False
    return any(rx.search(t) for rx in NO_RESULTS_REGEXES)

def is_nameish(line: str) -> bool:
    if not line:
        return False
    s = line.strip()
    if len(s) < 3 or len(s) > 90:
        return False
    low = s.lower()
    if any(k in low for k in ["results for", "website results", "locations results", "people results"]):
        return False
    return bool(NAME_COMMA_RE.match(s) or NAME_SPACE_RE.match(s))

def safe_dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


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
                return {"strategy": "server_html_search", "url_used": test_url, "param": p, "http_status": sc}
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

def body_text(driver, max_chars=250000) -> str:
    try:
        t = driver.execute_script("return document.body ? (document.body.innerText || '') : '';") or ""
        return t[:max_chars]
    except Exception:
        return ""

def page_source(driver, max_chars=900000) -> str:
    try:
        s = driver.page_source or ""
        return s[:max_chars]
    except Exception:
        return ""


# =========================================================
# Universal search input + submit
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
# OPTIONAL: click People-like tab/filter (best effort)
# =========================================================
TAB_PATTERNS = [re.compile(rf"\b{re.escape(k)}\b", re.I) for k in PEOPLE_KEYWORDS]

def click_best_people_tab(driver, status) -> Optional[str]:
    if not TRY_TAB_CLICK:
        return None

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

    for el in elems:
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue
            txt = (el.text or "").strip()
            if not txt or len(txt) > 40:
                continue
            score = 0
            for i, pat in enumerate(TAB_PATTERNS):
                if pat.search(txt):
                    score = 10 - i
                    break
            if score <= 0:
                continue
            # small bump if it looks like tabs/filters
            role = (el.get_attribute("role") or "").lower()
            cls = (el.get_attribute("class") or "").lower()
            if role == "tab" or "tab" in cls or "filter" in cls:
                score += 1
            if score > best_score:
                best_score = score
                best = el
                best_txt = txt
        except Exception:
            continue

    if not best:
        return None

    try:
        vlog(status, f"🧭 Clicking best tab/filter: '{best_txt}' (score={best_score})")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
        best.click()
        return best_txt
    except Exception as e:
        vlog(status, f"⚠ Tab click failed: {e}")
        return best_txt


# =========================================================
# KEY FIX: choose best PEOPLE-LIKE TEXT BLOCK from innerText
# =========================================================
def split_into_blocks(txt: str) -> List[Dict[str, Any]]:
    """
    Splits the page innerText into blocks whenever we see a heading-like line.
    Universal-ish heuristic: short line, title-case-ish, or matches known headings.
    """
    lines = [l.strip() for l in (txt or "").splitlines()]
    lines = [l for l in lines if l]

    blocks: List[Dict[str, Any]] = []
    cur = {"title": "", "lines": []}

    def is_heading(line: str) -> bool:
        if len(line) <= 2 or len(line) > 50:
            return False
        low = line.lower()
        # headings like Websites/People/Locations/Directory
        if low in PEOPLE_KEYWORDS or low in NONPEOPLE_HEADINGS:
            return True
        # "People results for …" / "Results" etc.
        if "results" in low and len(line) < 70:
            return True
        # title-like: mostly letters/spaces and not too long
        if re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9'\-\. ]+", line) and (line[0].isupper() or low.startswith("people")):
            # avoid headings that are clearly full sentences
            if line.count(" ") <= 5:
                return True
        return False

    for line in lines:
        if is_heading(line) and cur["lines"]:
            blocks.append(cur)
            cur = {"title": line, "lines": []}
        else:
            if not cur["title"] and is_heading(line):
                cur["title"] = line
            else:
                cur["lines"].append(line)

    if cur["lines"] or cur["title"]:
        blocks.append(cur)

    return blocks

def score_people_block(block: Dict[str, Any]) -> Dict[str, Any]:
    title = (block.get("title") or "").strip()
    lines = block.get("lines") or []
    joined = "\n".join([title] + lines)

    emails = len(EMAIL_RE.findall(joined))
    nameish_count = sum(1 for l in lines if is_nameish(l))
    mailto_hint = 1 if "mailto:" in joined.lower() else 0

    title_low = title.lower()
    people_hint = 2 if any(k in title_low for k in PEOPLE_KEYWORDS) else 0
    nonpeople_penalty = 2 if any(k in title_low for k in NONPEOPLE_HEADINGS) else 0

    # This is the important part: reward emails heavily, and reward being a People-ish titled block.
    score = emails * 10 + nameish_count * 2 + mailto_hint * 3 + people_hint * 6 - nonpeople_penalty * 6

    return {
        "title": title,
        "emails": emails,
        "nameish": nameish_count,
        "score": score,
        "text": joined[:6000],
        "lines": lines,
    }

def pick_best_people_block(page_txt: str) -> Optional[Dict[str, Any]]:
    blocks = split_into_blocks(page_txt)
    scored = [score_people_block(b) for b in blocks]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[0] if scored else None


# =========================================================
# Extract "people records" from selected block
# =========================================================
def extract_people_records_from_lines(lines: List[str]) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []

    def find_email_near(i: int) -> str:
        # search next few lines for an email
        for j in range(i, min(i + 6, len(lines))):
            m = EMAIL_RE.search(lines[j])
            if m:
                return m.group(0)
        return ""

    for i, line in enumerate(lines):
        if is_nameish(line):
            email = find_email_near(i)
            # skip obvious heading-like pseudo names
            low = line.lower()
            if low in PEOPLE_KEYWORDS or low in NONPEOPLE_HEADINGS:
                continue
            records.append({"name": line.strip(), "email": email})

    # de-dupe by name+email
    seen = set()
    out = []
    for r in records:
        k = (r["name"], r["email"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


# =========================================================
# Waiter (simple, “dumb code” style): submit → sleep → pick best people block
# =========================================================
def wait_and_extract_people(driver, term: str, timeout: int, status) -> Dict[str, Any]:
    start = time.time()

    # quick poll loop to let JS load
    best_block = None
    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)
        txt = body_text(driver)

        # if term appears and page has grown, likely loaded
        term_seen = term.lower() in txt.lower()
        nores = text_has_no_results_signal(txt)

        best_block = pick_best_people_block(txt) if AUTO_PEOPLE_BLOCK else None
        best_score = best_block["score"] if best_block else None
        best_title = best_block["title"] if best_block else None

        vlog(status, f"🧪 t={elapsed}s term_seen={term_seen} no_results={nores} best_block_title={best_title!r} best_score={best_score}")

        # if we found a strong people-ish block, stop early
        if best_block and best_block["score"] >= 20 and best_block["emails"] >= 1:
            break

        time.sleep(0.6)

    # final extract
    txt = body_text(driver)
    best_block = pick_best_people_block(txt) if AUTO_PEOPLE_BLOCK else None

    if not best_block:
        return {
            "state": "no_block",
            "elapsed": round(time.time() - start, 1),
            "best_block": None,
            "people_records": [],
            "page_preview": txt[:3000]
        }

    people_records = extract_people_records_from_lines(best_block["lines"])
    return {
        "state": "ok",
        "elapsed": round(time.time() - start, 1),
        "best_block": {k: best_block[k] for k in ["title", "emails", "nameish", "score", "text"]},
        "people_records": people_records,
        "page_preview": txt[:3000]
    }


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
        st.error("Could not start Selenium driver (driver mismatch).")
        st.stop()

    try:
        driver.get(TARGET_URL)
        selenium_wait_ready(driver, timeout=12)
        time.sleep(0.7)

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

        if FALLBACK_SLEEP > 0:
            vlog(status, f"⏳ Working-logic initial sleep: {FALLBACK_SLEEP}s")
            time.sleep(float(FALLBACK_SLEEP))

        clicked = click_best_people_tab(driver, status)
        if clicked:
            time.sleep(0.8)

        log(status, f"🧪 Waiting & extracting best people-like block (timeout={TIMEOUT}s)")
        res = wait_and_extract_people(driver, TERM, timeout=int(TIMEOUT), status=status)

        status.update(label="Done", state="complete")

        st.subheader("🧠 Result")
        st.write("Strategy used: `selenium_best_people_text_block`")
        st.write(f"Current URL: {driver.current_url}")
        st.write(f"State: `{res['state']}`")
        st.write(f"Elapsed: {res['elapsed']}s")

        st.markdown("#### Best block chosen")
        st.json(res["best_block"] if res["best_block"] else {})

        st.markdown("#### People-like records (name + optional email)")
        st.write(f"Count: {len(res['people_records'])}")
        st.dataframe(res["people_records"][:100])

        with st.expander("🔎 Debug: best block text (first ~6000 chars)", expanded=False):
            if res["best_block"]:
                st.code(res["best_block"]["text"])
            else:
                st.code("(no block)")

        with st.expander("🔎 Debug: page preview", expanded=False):
            st.code(res["page_preview"])

    except WebDriverException as e:
        status.update(label="Done (webdriver error)", state="complete")
        st.error(f"WebDriver error: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
