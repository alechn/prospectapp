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
st.caption("Probe → Detect → Decide → Extract (universal, log-heavy, people-container-aware)")

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
AUTO_PEOPLE_SCOPE = b3.checkbox("Auto-scope to most people-like container", value=True)

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
# Extraction helpers
# =========================================================
NAMEISH_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){1,6}$")
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

# IMPORTANT: regex-based no-results detection (no substring traps like "9330 results" -> "0 results")
NO_RESULTS_REGEXES = [
    re.compile(r"\bno\s+results\b", re.I),
    re.compile(r"\b0\s+results\b", re.I),
    re.compile(r"\bzero\s+results\b", re.I),
    re.compile(r"\bno\s+matches\b", re.I),
    re.compile(r"\bnothing\s+found\b", re.I),
    re.compile(r"\bdid\s+not\s+match\b", re.I),
    re.compile(r"\bno\s+records\b", re.I),
    re.compile(r"\bno\s+entries\b", re.I),
]

def text_has_no_results_signal(text_or_html: str) -> bool:
    t = text_or_html or ""
    # Guard: if there are obvious emails, it’s probably NOT a no-results page
    if EMAIL_RE.search(t):
        return False
    return any(rx.search(t) for rx in NO_RESULTS_REGEXES)

def clean_candidate_text(t: str) -> Optional[str]:
    if not t:
        return None
    t = " ".join(str(t).split()).strip()
    if len(t) < 3 or len(t) > 140:
        return None
    lt = t.lower()
    junk = [
        "privacy", "accessibility", "cookie", "terms", "login", "sign up",
        "skip to", "search results", "jobs", "events", "map"
    ]
    if any(j in lt for j in junk):
        return None
    return t

def extract_candidates_generic(html: str) -> List[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[str] = []
    selectors = [
        "[class*='result' i] h3",
        "[class*='result' i] h2",
        "[class*='profile' i] h3",
        "[class*='person' i] h3",
        "h3", "h2", "h4",
        "strong",
        "a",
        "li",
        "td",
    ]
    for sel in selectors:
        for el in soup.select(sel):
            txt = clean_candidate_text(el.get_text(" ", strip=True))
            if txt:
                out.append(txt)
        if len(out) >= 400:
            break
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def filter_nameish(cands: List[str]) -> List[str]:
    out = []
    for c in cands:
        cc = c.strip()
        if not NAMEISH_RE.match(cc):
            continue
        low = cc.lower()
        if any(k in low for k in ["results for", "websites results", "locations results", "people results"]):
            continue
        out.append(cc)
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def extract_people_like_records_from_text(block_text: str) -> List[Dict[str, str]]:
    lines = [l.strip() for l in (block_text or "").splitlines() if l.strip()]
    records: List[Dict[str, str]] = []
    for i, line in enumerate(lines):
        if NAMEISH_RE.match(line) and not any(x in line.lower() for x in ["results for", "website", "locations", "people results"]):
            email = ""
            for j in range(i, min(i + 6, len(lines))):
                m = EMAIL_RE.search(lines[j])
                if m:
                    email = m.group(0)
                    break
            records.append({"name": line, "email": email})
    seen = set()
    uniq = []
    for r in records:
        key = (r["name"], r["email"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


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
                cands = extract_candidates_generic(html)
                return {
                    "strategy": "server_html_search",
                    "url_used": test_url,
                    "param": p,
                    "http_status": sc,
                    "candidates": cands,
                    "nameish": filter_nameish(cands),
                }

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

def safe_body_inner_text(driver, max_chars=200000) -> str:
    try:
        t = driver.execute_script("return document.body ? (document.body.innerText || '') : '';") or ""
        return t[:max_chars]
    except Exception:
        return ""

def safe_element_text(driver, el, max_chars=200000) -> str:
    try:
        t = el.text or ""
        return t[:max_chars]
    except Exception:
        return ""

def safe_element_html(driver, el, max_chars=500000) -> str:
    try:
        html = el.get_attribute("innerHTML") or ""
        return html[:max_chars]
    except Exception:
        return ""


# =========================================================
# Universal: find search input + submit
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
# Tab clicking (best effort; may not provide panel id)
# =========================================================
TAB_PRIORITIES = [
    r"\bpeople\b",
    r"\bperson\b",
    r"\bdirectory\b",
    r"\bstaff\b",
    r"\bfaculty\b",
    r"\bstudent\b",
    r"\bprofiles?\b",
    r"\bcontacts?\b",
]

def click_best_people_tab(driver, status) -> Dict[str, Optional[str]]:
    patterns = [re.compile(p, re.I) for p in TAB_PRIORITIES]
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
    best_panel = None

    for el in elems:
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue
            txt = (el.text or "").strip()
            if not txt or len(txt) > 40:
                continue

            score = None
            for i, pat in enumerate(patterns):
                if pat.search(txt):
                    score = (len(patterns) - i)
                    break
            if score is None:
                continue

            role = (el.get_attribute("role") or "").lower()
            cls = (el.get_attribute("class") or "").lower()
            if role == "tab" or "tab" in cls or "filter" in cls:
                score += 1

            aria_controls = (el.get_attribute("aria-controls") or "").strip() or None
            data_target = (el.get_attribute("data-target") or "").strip() or None
            href = (el.get_attribute("href") or "").strip()
            href_hash = None
            if href and "#" in href:
                href_hash = href.split("#", 1)[-1].strip() or None

            panel = aria_controls or data_target or href_hash

            if score > best_score:
                best_score = score
                best = el
                best_txt = txt
                best_panel = panel
        except Exception:
            continue

    if best is None:
        vlog(status, "ℹ️ No People/Directory tab/filter detected.")
        return {"clicked_text": None, "panel_id": None}

    try:
        vlog(status, f"🧭 Clicking best tab/filter: '{best_txt}' (score={best_score}) panel_id={best_panel}")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best)
        best.click()
        return {"clicked_text": best_txt, "panel_id": best_panel}
    except Exception as e:
        vlog(status, f"⚠ Tab click failed: {e}")
        return {"clicked_text": best_txt, "panel_id": best_panel}


# =========================================================
# UNIVERSAL PEOPLE-SCOPE: find the "most people-like" container
# =========================================================
def find_best_people_container(driver, status) -> Optional[Dict[str, Any]]:
    """
    Universal heuristic:
    - score many DOM containers by:
      - mailto links count
      - email regex count
      - number of name-ish lines
    - pick max score container
    Returns a dict with a JS path and metrics (we re-find it each poll by JS path).
    """
    js = r"""
    const EMAIL_RE = /[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}/ig;

    function isBad(el){
      if(!el) return true;
      const tag = (el.tagName||"").toLowerCase();
      if(["script","style","svg","noscript","header","footer","nav"].includes(tag)) return true;
      const cls = (el.className||"").toString().toLowerCase();
      const id  = (el.id||"").toString().toLowerCase();
      if(/nav|menu|footer|header|site-nav/.test(cls+ " " + id)) return true;
      return false;
    }

    function jsPath(el){
      // stable-ish path: tag:nth-of-type(...) chain, stops at body
      if(!el) return null;
      const parts = [];
      while(el && el !== document.body){
        let tag = el.tagName.toLowerCase();
        let parent = el.parentElement;
        if(!parent) break;
        let siblings = Array.from(parent.children).filter(x => x.tagName === el.tagName);
        let idx = siblings.indexOf(el) + 1;
        parts.push(tag + ":nth-of-type(" + idx + ")");
        el = parent;
      }
      parts.push("body");
      return parts.reverse().join(" > ");
    }

    const roots = Array.from(document.querySelectorAll("main, section, article, div")).slice(0, 800);
    let best = null;

    for(const el of roots){
      if(isBad(el)) continue;
      const txt = (el.innerText || "").trim();
      if(txt.length < 300) continue;
      if(txt.length > 30000) continue;

      const mailtos = el.querySelectorAll("a[href^='mailto:']").length;
      const emails = (txt.match(EMAIL_RE) || []).length;

      const lines = txt.split("\n").map(x => x.trim()).filter(Boolean).slice(0, 400);
      let nameish = 0;
      for(const line of lines){
        // crude universal name-ish: "Word, Word" OR "Word Word"
        if(line.length < 3 || line.length > 80) continue;
        if(/[|]{1,}/.test(line)) continue;
        if(/results for|website results|locations results|people results/i.test(line)) continue;
        if(/^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+,\s*[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+/.test(line)) nameish++;
        else if(/^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){1,4}$/.test(line)) nameish++;
      }

      // score: emails are strongest signal for "people directory"
      let score = mailtos*10 + emails*6 + nameish*1;

      // penalties if it looks like generic web search section
      if(/websites results/i.test(txt)) score -= 10;
      if(/locations results/i.test(txt)) score -= 5;

      if(!best || score > best.score){
        best = { score, mailtos, emails, nameish, path: jsPath(el), preview: txt.slice(0, 900) };
      }
    }
    return best;
    """
    try:
        best = driver.execute_script(js)
        if best and best.get("path"):
            vlog(status, f"🧲 Auto people-scope selected score={best.get('score')} mailtos={best.get('mailtos')} emails={best.get('emails')} nameish={best.get('nameish')}")
            vlog(status, f"🧲 people-scope path: {best.get('path')}")
            return best
    except Exception as e:
        vlog(status, f"⚠ people-scope JS failed: {e}")
    return None

def get_container_by_js_path(driver, js_path: str):
    js = r"""
    const path = arguments[0];
    try { return document.querySelector(path); } catch(e){ return null; }
    """
    try:
        return driver.execute_script(js, js_path)
    except Exception:
        return None


# =========================================================
# Wait for outcome (scoped to: manual root > panel_id > people-container > body)
# =========================================================
def wait_scoped_outcome(driver, term: str, timeout: int, status, panel_id: Optional[str], people_scope: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    start = time.time()
    term_l = term.lower()

    def get_scope_text_and_html() -> Tuple[str, str, str]:
        # returns (scope_kind, text, html)
        if MANUAL_RESULTS_ROOT.strip():
            try:
                el = driver.find_element(By.CSS_SELECTOR, MANUAL_RESULTS_ROOT.strip())
                txt = (el.text or "")[:200000]
                html = (el.get_attribute("innerHTML") or "")[:500000]
                return ("manual_root", txt, html)
            except Exception:
                pass

        if panel_id:
            try:
                el = driver.find_element(By.ID, panel_id)
                txt = (el.text or "")[:200000]
                html = (el.get_attribute("innerHTML") or "")[:500000]
                return ("panel_id", txt, html)
            except Exception:
                pass

        if people_scope and people_scope.get("path"):
            el = get_container_by_js_path(driver, people_scope["path"])
            if el is not None:
                txt = safe_element_text(driver, el, 200000)
                html = safe_element_html(driver, el, 500000)
                if txt or html:
                    return ("people_scope", txt, html)

        txt = safe_body_inner_text(driver, max_chars=200000)
        html = driver.page_source or ""
        return ("body", txt, html)

    scope_kind, base_txt, base_html = get_scope_text_and_html()
    base_fp = hash(base_html or "")
    base_cands = extract_candidates_generic(base_html)
    vlog(status, f"🧪 baseline scope={scope_kind} panel_id={panel_id} fp={base_fp} candidates={len(base_cands)}")

    last_cset = set(base_cands)

    while time.time() - start < timeout:
        elapsed = round(time.time() - start, 1)
        scope_kind, txt, html = get_scope_text_and_html()

        # no-results: only if signal exists AND we have near-zero people signals
        if text_has_no_results_signal(txt) or text_has_no_results_signal(html):
            cands = extract_candidates_generic(html)
            people_records = extract_people_like_records_from_text(txt)
            if len(people_records) == 0 and len(cands) < 5:
                vlog(status, f"🧪 t={elapsed}s → no-results detected (scope={scope_kind})")
                return {"state": "no_results", "elapsed": elapsed, "candidates": cands, "scope_kind": scope_kind}

        # term seen in scoped text is a good sign (but not sufficient alone)
        if term_l in (txt or "").lower():
            vlog(status, f"🧪 t={elapsed}s → term '{term}' appears in scoped text (scope={scope_kind})")

        cands = extract_candidates_generic(html)
        cset = set(cands)
        changed = (cset != last_cset)
        delta = len(cset - last_cset)
        last_cset = cset

        # If we have emails or multiple people-like lines, call it "results"
        people_records = extract_people_like_records_from_text(txt)
        emails_found = 1 if any(r.get("email") for r in people_records) else 0

        vlog(status, f"🧪 t={elapsed}s scope={scope_kind} candidates={len(cands)} people_records={len(people_records)} emails={emails_found} changed={changed} delta={delta}")

        if len(people_records) >= 3 or emails_found:
            return {"state": "people_records", "elapsed": elapsed, "candidates": cands, "scope_kind": scope_kind, "people_records": people_records}

        if delta >= 5:
            return {"state": "results_changed", "elapsed": elapsed, "candidates": cands, "scope_kind": scope_kind}

        time.sleep(0.4)

    scope_kind, txt, html = get_scope_text_and_html()
    cands = extract_candidates_generic(html)
    return {"state": "timeout", "elapsed": round(time.time() - start, 1), "candidates": cands, "scope_kind": scope_kind, "scoped_text_preview": (txt or "")[:2500]}


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
            st.write(f"Candidates extracted: {len(req_hit['candidates'])}")
            st.write(f"Name-ish extracted: {len(req_hit['nameish'])}")
            st.dataframe({"Candidates (first 50)": req_hit["candidates"][:50]})
            st.dataframe({"Name-ish (first 50)": req_hit["nameish"][:50]})
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
        st.error("Could not start Selenium driver (SessionNotCreatedException / driver mismatch).")
        st.stop()

    clicked_tab_text = None
    panel_id = None
    people_scope = None

    try:
        driver.get(TARGET_URL)
        selenium_wait_ready(driver, timeout=12)
        time.sleep(0.6)

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

        if TRY_TAB_CLICK:
            tab_res = click_best_people_tab(driver, status)
            clicked_tab_text = tab_res.get("clicked_text")
            panel_id = tab_res.get("panel_id")
            if clicked_tab_text:
                time.sleep(0.8)

        # If there is no panel_id, auto-scope to the most people-like container
        if AUTO_PEOPLE_SCOPE and not panel_id and not MANUAL_RESULTS_ROOT.strip():
            people_scope = find_best_people_container(driver, status)
            if people_scope:
                with st.expander("🧲 Auto people-scope preview (why this was chosen)", expanded=False):
                    st.write(people_scope)

        log(status, f"🧪 Waiting for outcome for '{TERM}' (timeout={TIMEOUT}s) panel_id={panel_id} auto_people_scope={bool(people_scope)}")
        res = wait_scoped_outcome(driver, TERM, timeout=int(TIMEOUT), status=status, panel_id=panel_id, people_scope=people_scope)

        cands = res.get("candidates", [])
        nameish = filter_nameish(cands)

        # Build scoped text preview for debug
        scoped_text_preview = res.get("scoped_text_preview", "")
        scope_kind = res.get("scope_kind", "unknown")
        people_records = res.get("people_records", [])

        # If we didn’t compute people_records inside waiter, compute now from best available scope
        if not people_records:
            try:
                if MANUAL_RESULTS_ROOT.strip():
                    el = driver.find_element(By.CSS_SELECTOR, MANUAL_RESULTS_ROOT.strip())
                    t = (el.text or "")
                elif panel_id:
                    el = driver.find_element(By.ID, panel_id)
                    t = (el.text or "")
                elif people_scope and people_scope.get("path"):
                    el = get_container_by_js_path(driver, people_scope["path"])
                    t = (el.text or "") if el else safe_body_inner_text(driver)
                else:
                    t = safe_body_inner_text(driver)
                people_records = extract_people_like_records_from_text(t)
                if not scoped_text_preview:
                    scoped_text_preview = (t or "")[:2500]
            except Exception:
                pass

        status.update(label="Done", state="complete")

        st.subheader("🧠 Result")
        st.write("Strategy used: `selenium_universal_people_container_scope`")
        st.write(f"Clicked tab/filter: `{clicked_tab_text}`")
        st.write(f"Locked panel_id: `{panel_id}`")
        st.write(f"Scope used: `{scope_kind}`")
        st.write(f"Outcome state: `{res['state']}`")
        st.write(f"Elapsed: {res['elapsed']}s")
        st.write(f"Current URL: {driver.current_url}")

        st.markdown("#### Scoped extraction counts")
        st.write(f"Candidates (scoped): {len(cands)}")
        st.write(f"Name-ish (scoped): {len(nameish)}")
        st.write(f"People-like records (name + optional email): {len(people_records)}")

        st.markdown("#### People-like records (first 50)")
        st.dataframe(people_records[:50])

        st.markdown("#### Candidates (first 60)")
        st.dataframe({"Candidates": cands[:60]})

        st.markdown("#### Name-ish (first 60)")
        st.dataframe({"Name-ish": nameish[:60]})

        with st.expander("🔎 Debug: scoped text preview", expanded=False):
            st.code(scoped_text_preview if scoped_text_preview else "(empty)")

    except WebDriverException as e:
        status.update(label="Done (webdriver error)", state="complete")
        st.error(f"WebDriver error: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
