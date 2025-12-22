import streamlit as st
import pandas as pd
import json
import time
import re
import requests
import io
import os
from unidecode import unidecode
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup

# --- SELENIUM SETUP ---
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

# =========================================================
#             PART 0: CONFIGURATION
# =========================================================
st.set_page_config(page_title="Universal Alumni Finder", layout="wide", page_icon="🕵️")
st.title("🕵️ Universal Brazilian Alumni Finder")
st.caption("Powered by Multi-Model AI • 3-in-1 Engine • Scoring System")
st.info(
    "Reminder: only scrape directories you have permission to process. "
    "Respect robots.txt, rate limits, and site terms."
)

if "running" not in st.session_state:
    st.session_state.running = False

# =========================================================
#             PART 1: SIDEBAR / SETTINGS
# =========================================================
st.sidebar.header("🧠 AI Brain")
ai_provider = st.sidebar.selectbox(
    "Choose your Model:",
    ["Google Gemini (Flash 2.0)", "OpenAI (GPT-4o)", "Anthropic (Claude 3.5)", "DeepSeek (V3)"]
)
api_key = st.sidebar.text_input(f"Enter {ai_provider.split()[0]} API Key", type="password")

st.sidebar.markdown("---")
search_delay = st.sidebar.slider("⏳ Search/Scroll Wait Time (Sec)", 1, 60, 10)
use_ai_cleaning = st.sidebar.checkbox("✨ Batch AI Cleaning", value=True, help="Clean final list with AI.")

with st.sidebar.expander("🛠️ Advanced / Debug"):
    manual_search_selector = st.sidebar.text_input(
        "Manual Search Box Selector", placeholder="e.g. input[name='q']"
    )
    manual_name_selector = st.sidebar.text_input(
        "Manual Name Selector", placeholder="e.g. table tbody tr td:first-child or h3"
    )
    manual_next_selector = st.sidebar.text_input(
        "Manual Next Selector", placeholder="e.g. a[rel='next'] or a.next"
    )
    show_debug_ai_payload = st.sidebar.checkbox("Show AI Debug Errors", value=True)

if st.sidebar.button("🛑 ABORT MISSION", type="primary"):
    st.session_state.running = False
    st.sidebar.warning("Mission Aborted.")
    st.stop()

# --- DATA ---
BLOCKLIST_SURNAMES = {
    "WANG", "LI", "ZHANG", "LIU", "CHEN", "YANG", "HUANG", "ZHAO", "WU", "ZHOU",
    "XU", "SUN", "MA", "ZHU", "HU", "GUO", "HE", "GAO", "LIN", "LUO",
    "LIANG", "SONG", "TANG", "ZHENG", "HAN", "FENG", "DONG", "YE", "YU", "WEI",
    "CAI", "YUAN", "PAN", "DU", "DAI", "JIN", "FAN", "SU", "MAN", "WONG",
    "CHAN", "CHANG", "LEE", "KIM", "PARK", "CHOI", "NG", "HO", "CHOW", "LAU",
    "SINGH", "PATEL", "KUMAR", "SHARMA", "GUPTA", "ALI", "KHAN", "TRAN", "NGUYEN",
    "RESULTS", "WEBSITE", "SEARCH", "MENU", "SKIP", "CONTENT", "FOOTER", "HEADER",
    "OVERVIEW", "PROJECTS", "PEOPLE", "PROFILE", "VIEW", "CONTACT", "SPOTLIGHT",
    "EDITION", "JEWELS", "COLAR", "PAINTER", "GUIDE", "LOG", "REVIEW", "PDF"
}

# =========================================================
#             PART 2: AI WRAPPER (FIXED LOGGING)
# =========================================================
def clean_json_response(text: str) -> str:
    if not text:
        return "{}"
    text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text

def safe_json_loads(text: str):
    try:
        return json.loads(clean_json_response(text))
    except Exception:
        return None

def call_ai_api(prompt: str, provider: str, key: str):
    """Returns either JSON text OR a sentinel error string '__HTTP_ERROR__ ...'."""
    if not key:
        return None
    headers = {"Content-Type": "application/json"}

    try:
        if "Gemini" in provider:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"},
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                return f"__HTTP_ERROR__ {resp.status_code}: {resp.text}"
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

        elif "OpenAI" in provider:
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "Return STRICT JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 1200,
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                return f"__HTTP_ERROR__ {resp.status_code}: {resp.text}"
            return resp.json()["choices"][0]["message"]["content"]

        elif "Anthropic" in provider:
            url = "https://api.anthropic.com/v1/messages"
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                return f"__HTTP_ERROR__ {resp.status_code}: {resp.text}"
            return resp.json()["content"][0]["text"]

        elif "DeepSeek" in provider:
            url = "https://api.deepseek.com/chat/completions"
            headers["Authorization"] = f"Bearer {key}"
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "Return STRICT JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 1200,
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code != 200:
                return f"__HTTP_ERROR__ {resp.status_code}: {resp.text}"
            return resp.json()["choices"][0]["message"]["content"]

    except Exception as e:
        return f"__HTTP_ERROR__ exception: {repr(e)}"

    return None

# =========================================================
#             PART 3: HELPERS & IBGE DB
# =========================================================
def normalize_token(s: str) -> str:
    if not s:
        return ""
    return "".join(ch for ch in unidecode(str(s).strip().upper()) if "A" <= ch <= "Z")

def clean_html_for_ai(html_text: str) -> str:
    """Make AI payload small & stable: strip heavy tags, keep head+tail."""
    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup(["script", "style", "svg", "noscript", "img", "iframe", "footer"]):
        element.decompose()
    text = str(soup)
    # Keep within ~80k chars; preserve pagination hints in footer
    if len(text) > 80000:
        return text[:60000] + "\n<!-- snip -->\n" + text[-20000:]
    return text

def clean_extracted_name(raw_text):
    if not isinstance(raw_text, str):
        return None
    upper = raw_text.upper()
    junk = [
        "RESULTS FOR", "SEARCH", "WEBSITE", "EDITION", "JEWELS", "SPOTLIGHT",
        "EXPERIENCE", "CALCULATION", "WAGE", "LIVING", "GOING", "FAST",
        "GUIDE", "LOG", "REVIEW"
    ]
    if any(j in upper for j in junk):
        return None
    if ":" in raw_text:
        raw_text = raw_text.split(":")[-1]
    clean = re.split(r"[|,\-–—»\(\)]", raw_text)[0]
    clean = " ".join(clean.split())
    if len(clean.split()) > 6 or len(clean) < 3:
        return None
    # Avoid pure uppercase nav items
    if clean.isupper() and len(clean.split()) <= 2 and clean in BLOCKLIST_SURNAMES:
        return None
    return clean.strip()

@st.cache_data(ttl=86400)
def fetch_ibge_data(limit_first, limit_surname):
    IBGE_FIRST = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"

    def _fetch(url, limit):
        data_map = {}
        page = 1
        while len(data_map) < limit:
            try:
                r = requests.get(url, params={"page": page}, timeout=10)
                if r.status_code != 200:
                    break
                items = r.json().get("items", [])
                if not items:
                    break
                for i in items:
                    n = normalize_token(i.get("nome"))
                    if n:
                        data_map[n] = i.get("rank", 0)
                page += 1
            except Exception:
                break
        return data_map

    return _fetch(IBGE_FIRST, limit_first), _fetch(IBGE_SURNAME, limit_surname)

st.sidebar.header("⚙️ Settings")
limit_first = st.sidebar.number_input("DB: First Names", 100, 20000, 3000, 100)
limit_surname = st.sidebar.number_input("DB: Surnames", 100, 20000, 3000, 100)

try:
    first_name_ranks, surname_ranks = fetch_ibge_data(limit_first, limit_surname)
    sorted_surnames = sorted(surname_ranks.keys(), key=lambda k: surname_ranks[k])
    st.sidebar.success(f"✅ DB Loaded: {len(first_name_ranks)} Firsts / {len(surname_ranks)} Surnames")
except Exception:
    st.error("IBGE Error")
    st.stop()

# =========================================================
#             PART 4: EXTRACTION LOGIC (AI OPTIONAL)
# =========================================================
def heuristic_extract_names(html_content: str, name_selector: str | None = None):
    soup = BeautifulSoup(html_content, "html.parser")
    out = []

    # Manual override
    if name_selector:
        for el in soup.select(name_selector):
            cand = clean_extracted_name(el.get_text(" ", strip=True))
            if cand:
                out.append(cand)
        return list(dict.fromkeys(out))

    # 1) Table with a Name header => take first td
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if any("name" in h for h in headers):
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if not tds:
                    continue
                cand = clean_extracted_name(tds[0].get_text(" ", strip=True))
                if cand:
                    out.append(cand)
            if out:
                return list(dict.fromkeys(out))

    # 2) Directory-ish patterns
    for sel in ["h3", "h4", "h2", "a"]:
        for el in soup.select(sel):
            cand = clean_extracted_name(el.get_text(" ", strip=True))
            if cand:
                out.append(cand)

    return list(dict.fromkeys(out))

def heuristic_find_next(html_content: str, current_url: str, manual_next: str | None = None):
    """Return absolute next URL if found."""
    soup = BeautifulSoup(html_content, "html.parser")
    base = soup.find("base", href=True)
    base_url = base["href"] if base else current_url

    # Manual override
    if manual_next:
        el = soup.select_one(manual_next)
        if el:
            href = el.get("href")
            if href:
                return urljoin(base_url, href)

    # rel=next
    rel_next = soup.select_one("a[rel='next'][href]")
    if rel_next:
        return urljoin(base_url, rel_next["href"])

    # Link text "Next"
    for a in soup.select("a[href]"):
        t = (a.get_text(" ", strip=True) or "").strip().lower()
        if t in {"next", "next page", ">", "›", "»"}:
            return urljoin(base_url, a["href"])

    # Pattern: "Page X of Y" and query param page=
    text = soup.get_text(" ", strip=True)
    m = re.search(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", text, flags=re.IGNORECASE)
    if m:
        cur, total = int(m.group(1)), int(m.group(2))
        if cur < total:
            # Try to increment common params
            u = urlparse(current_url)
            qs = parse_qs(u.query)
            for key in ["page", "p", "pg", "start"]:
                if key in qs:
                    try:
                        val = int(qs[key][0])
                        qs[key] = [str(val + 1)]
                        new_q = urlencode(qs, doseq=True)
                        return u._replace(query=new_q).geturl()
                    except Exception:
                        pass

    return None

def agent_learn_selectors(html_content: str, current_url: str, provider: str, key: str):
    """AI returns selectors ONLY (no big name list)."""
    if not html_content or len(html_content) < 800:
        return None

    prompt = f"""
You are a web scraping expert. Analyze the HTML from {current_url}.

Goal:
- Find a CSS selector that matches PERSON NAMES in the directory/list.
- Find a CSS selector for the NEXT PAGE clickable element (if pagination exists).

Rules:
- Return STRICT JSON only.
- Keep selectors simple and specific.

Return JSON in this exact shape:
{{
  "selectors": {{
    "name_element": "CSS_SELECTOR_FOR_NAME_ELEMENTS",
    "next_element": "CSS_SELECTOR_FOR_NEXT_PAGE_ELEMENT_OR_EMPTY_STRING"
  }}
}}

HTML:
{clean_html_for_ai(html_content)}
"""
    raw = call_ai_api(prompt, provider, key)
    if not raw:
        return None
    if raw.startswith("__HTTP_ERROR__"):
        return {"__error__": raw}

    data = safe_json_loads(raw)
    if not isinstance(data, dict):
        return None
    return data

def extract_with_selectors(html_content: str, current_url: str, selectors: dict):
    soup = BeautifulSoup(html_content, "html.parser")
    base = soup.find("base", href=True)
    base_url = base["href"] if base else current_url

    names = []
    sel_name = selectors.get("name_element") if selectors else None
    if sel_name:
        for el in soup.select(sel_name):
            cand = clean_extracted_name(el.get_text(" ", strip=True))
            if cand:
                names.append(cand)
    names = list(dict.fromkeys(names))

    nav_next = None
    sel_next = selectors.get("next_element") if selectors else None
    if sel_next:
        el = soup.select_one(sel_next)
        if el:
            href = el.get("href")
            if href:
                nav_next = urljoin(base_url, href)

    return names, nav_next

def match_names_detailed(names, source):
    found = []
    seen = set()
    for n in names:
        n = " ".join(str(n).split())
        if n in seen:
            continue
        seen.add(n)

        parts = n.strip().split()
        if len(parts) < 2:
            continue

        f, l = normalize_token(parts[0]), normalize_token(parts[-1])
        if l in BLOCKLIST_SURNAMES or f in BLOCKLIST_SURNAMES:
            continue

        rank_f = first_name_ranks.get(f, 0)
        rank_l = surname_ranks.get(l, 0)

        score = 0
        if rank_f > 0:
            score += 50
        if rank_l > 0:
            score += 50

        if score > 0:
            found.append({
                "Full Name": n,
                "Brazil Score": score,
                "Match Type": "Strong",
                "Source": source
            })
    return found

def ai_janitor_clean_names(raw_list, provider, key):
    if not raw_list or not key:
        return []

    clean_results = []
    chunk_size = 30
    prog = st.progress(0)

    for i in range(0, len(raw_list), chunk_size):
        batch = raw_list[i:i + chunk_size]
        prompt = f"""You are a Data Cleaner. Extract HUMAN NAMES only.
Fix spacing. Remove titles/junk. Return JSON exactly:
{{ "cleaned_names": ["Name 1", "Name 2"] }}

INPUT: {json.dumps(batch)}
"""
        resp_text = call_ai_api(prompt, provider, key)
        if resp_text and resp_text.startswith("__HTTP_ERROR__"):
            clean_results.extend(batch)
        else:
            data = safe_json_loads(resp_text or "")
            if isinstance(data, dict) and isinstance(data.get("cleaned_names"), list):
                clean_results.extend(data["cleaned_names"])
            else:
                clean_results.extend(batch)

        prog.progress(min((i + chunk_size) / max(1, len(raw_list)), 1.0))

    return list(dict.fromkeys([x for x in clean_results if isinstance(x, str) and x.strip()]))

# =========================================================
#             PART 5: FETCHERS
# =========================================================
def fetch_native(session: requests.Session, url: str):
    try:
        return session.get(url, timeout=25)
    except Exception:
        return None

def get_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    # Try default Selenium discovery first, then webdriver_manager fallback
    try:
        return webdriver.Chrome(options=options)
    except Exception:
        return webdriver.Chrome(
            service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
            options=options
        )

def fetch_selenium(driver, url, scroll_count=0):
    driver.get(url)
    time.sleep(2.5)
    if scroll_count > 0:
        for _ in range(scroll_count):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.4)
    return driver.page_source

# =========================================================
#             PART 6: UI + MAIN EXECUTION
# =========================================================
st.markdown("### 🤖 Auto-Pilot Control Center")
c1, c2 = st.columns([3, 1])
start_url = c1.text_input("Target URL", placeholder="https://directory.example.com")
max_pages = c2.number_input("Max Pages", 1, 500, 10)

st.write("---")
mode = st.radio(
    "Mode:",
    ["Classic Directory (Native/Fast)", "Infinite Scroller (Selenium)", "Active Search Injection (Brute Force Surnames)"]
)

run_headless = True
if "Search" in mode or "Infinite" in mode:
    if not HAS_SELENIUM:
        st.error("Selenium Missing")
        st.stop()
    run_headless = st.checkbox("Headless Mode", value=True)

if st.button("🚀 Start Mission", type="primary"):
    st.session_state.running = True

if st.session_state.running:
    if not start_url:
        st.error("Missing Target URL")
        st.stop()

    # AI only required for learning selectors / cleaning
    if not api_key:
        st.warning("No API key provided — running in heuristic mode only (no AI).")

    status_log = st.status("Initializing...", expanded=True)
    table_placeholder = st.empty()

    all_matches = []
    learned_selectors = None

    # Apply manual selector overrides (if present)
    manual_overrides = {}
    if manual_name_selector:
        manual_overrides["name_element"] = manual_name_selector
    if manual_next_selector:
        manual_overrides["next_element"] = manual_next_selector

    # ------------------------------------------------------------------
    # BRANCH A: CLASSIC & INFINITE SCROLL
    # ------------------------------------------------------------------
    if "Search Injection" not in mode:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://google.com",
        })

        driver = get_driver(headless=run_headless) if "Infinite" in mode else None
        if "Infinite" in mode and not driver:
            status_log.error("Driver Failed")
            st.stop()

        current_url = start_url
        visited_urls = set()
        visited_form_fps = set()

        for page in range(1, max_pages + 1):
            if not st.session_state.running:
                break

            if current_url in visited_urls:
                status_log.info("🏁 Revisited same URL; stopping.")
                break
            visited_urls.add(current_url)

            status_log.update(label=f"Scanning Page {page}...", state="running")
            raw_html = None

            try:
                if "Classic" in mode:
                    r = fetch_native(session, current_url)
                    if r and r.status_code == 200:
                        raw_html = r.text
                    else:
                        status_log.warning(f"HTTP status: {getattr(r, 'status_code', None)}")
                else:
                    raw_html = fetch_selenium(driver, current_url, scroll_count=3)
            except Exception as e:
                status_log.warning(f"Fetch failed: {repr(e)}")

            if not raw_html:
                status_log.info("No HTML fetched; stopping.")
                break

            # 1) Extract names without AI (works for Stanford-like directory tables)
            names = heuristic_extract_names(raw_html, manual_name_selector if manual_name_selector else None)
            if names:
                status_log.write(f"🧩 Heuristic extracted {len(names)} names.")

            # 2) Try selector-based extraction (fast template)
            next_url = None
            if learned_selectors:
                # merge manual overrides on top
                selectors = dict(learned_selectors)
                selectors.update(manual_overrides)
                n2, next2 = extract_with_selectors(raw_html, current_url, selectors)
                if len(n2) > len(names):
                    names = n2
                if next2:
                    next_url = next2

            # 3) Heuristic next (no AI needed)
            if not next_url:
                next_url = heuristic_find_next(raw_html, current_url, manual_next_selector if manual_next_selector else None)

            # 4) AI learn selectors only if:
            #    - we got few/no names, OR we can't paginate, AND we have an API key
            if api_key and (len(names) == 0 or not next_url) and (not learned_selectors):
                status_log.write(f"🧠 {ai_provider.split()[0]} analyzing page structure (selectors only)...")
                ai_data = agent_learn_selectors(raw_html, current_url, ai_provider, api_key)

                if isinstance(ai_data, dict) and "__error__" in ai_data:
                    if show_debug_ai_payload:
                        status_log.error(ai_data["__error__"])
                    else:
                        status_log.error("❌ AI failed to read page (hidden).")
                elif isinstance(ai_data, dict) and isinstance(ai_data.get("selectors"), dict):
                    learned_selectors = ai_data["selectors"]
                    # manual overrides still win
                    learned_selectors.update(manual_overrides)
                    status_log.success(f"🎓 Learned selectors: {learned_selectors}")

                    n3, next3 = extract_with_selectors(raw_html, current_url, learned_selectors)
                    if len(n3) > len(names):
                        names = n3
                    if not next_url and next3:
                        next_url = next3
                else:
                    status_log.error("❌ AI returned unusable data.")

            # MATCHING
            if names:
                matches = match_names_detailed(names, f"Page {page}")
                if matches:
                    all_matches.extend(matches)
                    table_placeholder.dataframe(pd.DataFrame(all_matches), height=300, use_container_width=True)
                    status_log.write(f"✅ Found {len(matches)} Brazilian-likely matches on this page.")
                else:
                    status_log.write("🤷 Names extracted, but none matched IBGE scoring.")
            else:
                status_log.write("🤷 No names extracted.")

            # NAVIGATION
            if next_url:
                status_log.write(f"➡️ Next: {next_url}")
                current_url = next_url
            else:
                status_log.info("🏁 No more pages detected.")
                break

            time.sleep(1.0)

        if driver:
            driver.quit()

    # ------------------------------------------------------------------
    # BRANCH B: SEARCH INJECTION
    # ------------------------------------------------------------------
    else:
        driver = get_driver(headless=run_headless)
        if not driver:
            st.stop()

        try:
            driver.get(start_url)
            time.sleep(4)
        except Exception:
            st.stop()

        # 1. Find Search Box
        sel_input = manual_search_selector.strip() if manual_search_selector else ""

        if not sel_input and api_key:
            ai_data = agent_learn_selectors(driver.page_source, start_url, ai_provider, api_key)
            # In this mode we want search input; keep simple heuristic fallback below
            # (We don't force AI here because it's unreliable across sites.)

        if not sel_input:
            for f in [
                "input[type='search']",
                "input[name='q']",
                "input[name='query']",
                "input[aria-label='Search']",
                "input[placeholder*='Search' i]",
            ]:
                if len(driver.find_elements(By.CSS_SELECTOR, f)) > 0:
                    sel_input = f
                    break

        if not sel_input:
            st.error("No Search Box found (provide Manual Search Box Selector).")
            driver.quit()
            st.stop()

        status_log.success(f"🎯 Target search input: {sel_input}")

        for i, surname in enumerate(sorted_surnames[:max_pages]):
            if not st.session_state.running:
                break

            status_log.update(label=f"🔎 Checking '{surname}' ({i+1}/{max_pages})", state="running")

            try:
                inp = driver.find_element(By.CSS_SELECTOR, sel_input)
                inp.click()
                inp.send_keys(Keys.CONTROL + "a")
                inp.send_keys(Keys.BACKSPACE)
                inp.send_keys(surname)
                inp.send_keys(Keys.RETURN)
                time.sleep(search_delay)

                soup = BeautifulSoup(driver.page_source, "html.parser")
                raw_names = heuristic_extract_names(str(soup), manual_name_selector if manual_name_selector else None)

                if raw_names:
                    matches = match_names_detailed(raw_names, f"Search: {surname}")
                    if matches:
                        all_matches.extend(matches)
                        table_placeholder.dataframe(pd.DataFrame(all_matches), height=300, use_container_width=True)
                        status_log.write(f"✅ Found {len(matches)} matches.")

                # go back
                try:
                    driver.execute_script("window.history.go(-1)")
                    time.sleep(2)
                except Exception:
                    driver.get(start_url)
                    time.sleep(2)

            except Exception:
                try:
                    driver.get(start_url)
                    time.sleep(2)
                except Exception:
                    pass

        driver.quit()

    # --- BATCH CLEANING ---
    if use_ai_cleaning and api_key and all_matches:
        status_log.write(f"🧹 AI Cleaning {len(all_matches)} items...")
        raw = list(dict.fromkeys([m["Full Name"] for m in all_matches]))
        clean = ai_janitor_clean_names(raw, ai_provider, api_key)
        if clean:
            all_matches = match_names_detailed(clean, "Batch Processed")
            table_placeholder.dataframe(pd.DataFrame(all_matches), height=500, use_container_width=True)

    status_log.update(label="Done!", state="complete")
    st.session_state.running = False

    if all_matches:
        df = pd.DataFrame(all_matches)
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("📥 CSV", df.to_csv(index=False).encode("utf-8"), "results.csv")
        with c2:
            try:
                b = io.BytesIO()
                with pd.ExcelWriter(b, engine="xlsxwriter") as w:
                    df.to_excel(w, index=False)
                st.download_button("📥 Excel", b.getvalue(), "results.xlsx")
            except Exception:
                pass
