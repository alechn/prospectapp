# engine.py
import json, time, re, os, threading
from typing import Optional, Dict, Any, List, Tuple, Union

import requests
from unidecode import unidecode
from bs4 import BeautifulSoup

# Selenium (required for Active Search mode)
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

# webdriver_manager optional
try:
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.core.os_manager import ChromeType
    HAS_WDM = True
except Exception:
    HAS_WDM = False


# -------------------------
# Globals / Config defaults
# -------------------------
BLOCKLIST_SURNAMES = {
    "WANG","LI","ZHANG","LIU","CHEN","YANG","HUANG","ZHAO","WU","ZHOU",
    "XU","SUN","MA","ZHU","HU","GUO","HE","GAO","LIN","LUO",
    "KIM","PARK","LEE","CHOI","NG","SINGH","PATEL","KHAN","TRAN",
    "RESULTS","WEBSITE","SEARCH","MENU","SKIP","CONTENT","FOOTER","HEADER",
    "OVERVIEW","PROJECTS","PEOPLE","PROFILE","VIEW","CONTACT","SPOTLIGHT",
    "PDF","LOGIN","SIGNUP","HOME","ABOUT","CAREERS","NEWS","EVENTS"
}

NAME_REGEX = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){0,6}$")
NAME_COMMA_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\. ]{2,},\s*[A-Za-zÀ-ÖØ-öø-ÿ'\-\. ]{2,}$")
NAME_SPACE_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'\-\.]+){1,6}$")
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

def normalize_token(s: str) -> str:
    if not s:
        return ""
    s = unidecode(str(s).strip().upper())
    return "".join(ch for ch in s if "A" <= ch <= "Z")

def clean_extracted_name(raw_text: Any, block_mit_word: bool = False) -> Optional[str]:
    if not isinstance(raw_text, str):
        return None
    raw_text = " ".join(raw_text.split()).strip()
    if not raw_text:
        return None

    upper = raw_text.upper()
    junk_phrases = [
        "RESULTS FOR","SEARCH","WEBSITE","EDITION","SPOTLIGHT","EXPERIENCE",
        "MENU","SKIP TO","CONTENT","FOOTER","HEADER","OVERVIEW","PROJECTS",
        "PEOPLE","PROFILE","VIEW","CONTACT","READ MORE","LEARN MORE",
        "UNIVERSITY","INSTITUTE","SCHOOL","DEPARTMENT","COLLEGE","PROGRAM",
        "INITIATIVE","LABORATORY","CENTER FOR","CENTRE FOR","ALUMNI",
        "DIRECTORY","MBA","PHD","MSC","CLASS OF","EDUCATION","INNOVATION",
        "CAMPUS LIFE","LIFELONG LEARNING","GIVE","HOME","VISIT","MAP","EVENTS",
        "JOBS","PRIVACY","ACCESSIBILITY","SOCIAL MEDIA","TERMS OF USE",
        "COPYRIGHT","BRASIL","BRAZIL","USA","UNITED STATES",
        "JANUARY","FEBRUARY","MARCH","APRIL","MAY","JUNE","JULY","AUGUST",
        "SEPTEMBER","OCTOBER","NOVEMBER","DECEMBER"
    ]
    if any(p in upper for p in junk_phrases):
        return None
    if block_mit_word and re.search(r"\bMIT\b", upper):
        return None

    if "," in raw_text:
        parts = [p.strip() for p in raw_text.split(",") if p.strip()]
        if len(parts) >= 2:
            raw_text = f"{parts[1]} {parts[0]}"

    if ":" in raw_text:
        raw_text = raw_text.split(":")[-1].strip()

    clean = re.split(r"[|–—»\(\)]|\s-\s", raw_text)[0].strip()
    clean = " ".join(clean.split()).strip()

    if len(clean) < 3 or len(clean.split()) > 7:
        return None
    if any(x in clean for x in ["@", ".com", ".org", ".edu", ".net", "http", "www"]):
        return None
    if not NAME_REGEX.match(clean):
        return None
    return clean

def calculate_score(rank, limit, weight=50):
    if not rank or rank > limit:
        return 0
    return weight * (1 - (rank / limit))


# -------------------------
# IBGE loading (no Streamlit)
# -------------------------
IBGE_CACHE_FILE = "data/ibge_rank_cache.json"

def fetch_ibge_full_from_api() -> Tuple[Dict[str,int], Dict[str,int], Dict[str,Any]]:
    IBGE_FIRST = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/nome"
    IBGE_SURNAME = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/sobrenome"

    def _fetch_all(url: str) -> Dict[str,int]:
        out: Dict[str,int] = {}
        page = 1
        while True:
            r = requests.get(url, params={"page": page}, timeout=30)
            if r.status_code != 200:
                break
            items = (r.json() or {}).get("items", [])
            if not items:
                break
            for it in items:
                n = normalize_token(it.get("nome"))
                if n:
                    out[n] = int(it.get("rank", 0) or 0)
            page += 1
            if len(out) > 20000:
                break
            time.sleep(0.08)
        return out

    first_full = _fetch_all(IBGE_FIRST)
    surname_full = _fetch_all(IBGE_SURNAME)
    meta = {"saved_at_unix": int(time.time()), "source": "IBGE API v3"}
    return first_full, surname_full, meta

def load_ibge_full_best_effort(allow_api_fallback: bool = True, save_if_fetched: bool = True):
    if os.path.exists(IBGE_CACHE_FILE):
        with open(IBGE_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        first_full = {str(k): int(v) for k, v in (payload.get("first_name_ranks", {}) or {}).items()}
        surname_full = {str(k): int(v) for k, v in (payload.get("surname_ranks", {}) or {}).items()}
        meta = payload.get("meta", {"source": "local_json"})
        return first_full, surname_full, meta, "file"

    if not allow_api_fallback:
        raise FileNotFoundError(f"Missing {IBGE_CACHE_FILE} and API fallback disabled.")

    first_full, surname_full, meta = fetch_ibge_full_from_api()
    if save_if_fetched and first_full:
        os.makedirs(os.path.dirname(IBGE_CACHE_FILE), exist_ok=True)
        with open(IBGE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "first_name_ranks": first_full, "surname_ranks": surname_full}, f, ensure_ascii=False)
    return first_full, surname_full, meta, "api"

def slice_ibge_by_rank(first_full: Dict[str,int], surname_full: Dict[str,int], n_first: int, n_surname: int):
    first = {k: v for k, v in first_full.items() if v > 0 and v <= n_first}
    surname = {k: v for k, v in surname_full.items() if v > 0 and v <= n_surname}
    sorted_surnames = sorted(surname.keys(), key=lambda k: surname[k])
    return first, surname, sorted_surnames


# -------------------------
# Matching (same contract as your Streamlit version)
# -------------------------
def match_names(
    items: List[Union[str, Dict[str, Any]]],
    source: str,
    *,
    first_name_ranks: Dict[str,int],
    surname_ranks: Dict[str,int],
    limit_first: int,
    limit_surname: int,
    allow_surname_only: bool = True,
    block_mit_word: bool = False,
) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    seen = set()

    for item in items:
        raw_name = None
        meta_email = None
        meta_desc = None
        meta_url = None

        if isinstance(item, dict):
            raw_name = item.get("name") or item.get("Full Name") or item.get("full_name")
            meta_email = item.get("email")
            meta_desc = item.get("description") or item.get("desc")
            meta_url = item.get("url")
        else:
            raw_name = item

        n = clean_extracted_name(raw_name, block_mit_word=block_mit_word)
        if not n and isinstance(item, dict) and raw_name:
            raw_name2 = " ".join(str(raw_name).split()).strip()
            toks = raw_name2.split()
            if 2 <= len(toks) <= 7:
                n = raw_name2
        if not n:
            continue

        dedup_key = (n, (meta_email or "").strip().lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        parts = n.split()

        if len(parts) == 1:
            if not allow_surname_only:
                continue
            tok = normalize_token(parts[0])
            if not tok or tok in BLOCKLIST_SURNAMES:
                continue
            rl = surname_ranks.get(tok, 0)
            if rl > 0:
                score = calculate_score(rl, int(limit_surname), 50)
                found.append({
                    "Full Name": n,
                    "Email": meta_email,
                    "Description": meta_desc,
                    "URL": meta_url,
                    "Brazil Score": round(score, 1),
                    "First Rank": None,
                    "Surname Rank": rl,
                    "Source": source,
                    "Match Type": "Surname Only (Weak)",
                    "Status": "Valid"
                })
            continue

        f = normalize_token(parts[0])
        l = normalize_token(parts[-1])
        if not f or not l:
            continue
        if f in BLOCKLIST_SURNAMES or l in BLOCKLIST_SURNAMES:
            continue

        rf = first_name_ranks.get(f, 0)
        rl = surname_ranks.get(l, 0)

        score_f = calculate_score(rf, int(limit_first), 50)
        score_l = calculate_score(rl, int(limit_surname), 50)
        total_score = round(score_f + score_l, 1)

        if total_score > 5:
            found.append({
                "Full Name": n,
                "Email": meta_email,
                "Description": meta_desc,
                "URL": meta_url,
                "Brazil Score": total_score,
                "First Rank": rf if rf > 0 else None,
                "Surname Rank": rl if rl > 0 else None,
                "Source": source,
                "Match Type": "Strong" if (rf > 0 and rl > 0) else ("First Only" if rf > 0 else "Surname Only"),
                "Status": "Valid"
            })

    return found


# -------------------------
# Selenium driver + active search helpers
# -------------------------
def get_driver(headless: bool = True, enable_light_chrome: bool = True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-allow-origins=*")
    options.add_argument("--disable-extensions")

    try:
        options.page_load_strategy = "eager"
    except Exception:
        pass

    if enable_light_chrome:
        options.add_argument("--blink-settings=imagesEnabled=false")
        options.add_argument("--disable-features=TranslateUI")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-notifications")
        options.add_argument("--mute-audio")
        prefs = {
            "profile.managed_default_content_settings.images": 2,
            "profile.managed_default_content_settings.stylesheets": 2,
            "profile.managed_default_content_settings.fonts": 2,
        }
        try:
            options.add_experimental_option("prefs", prefs)
        except Exception:
            pass

    service = Service("/usr/bin/chromedriver") if os.path.exists("/usr/bin/chromedriver") else None

    try:
        if service:
            return webdriver.Chrome(service=service, options=options)
        return webdriver.Chrome(options=options)
    except Exception as e_native:
        if HAS_WDM:
            return webdriver.Chrome(
                service=Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()),
                options=options
            )
        raise RuntimeError(f"Driver Init Failed: {e_native}")

def selenium_wait_document_ready(driver, timeout: int = 10):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
        )
    except Exception:
        pass

def find_search_input(driver, manual_search_selector: str = ""):
    ms = (manual_search_selector or "").strip()
    if ms:
        els = driver.find_elements(By.CSS_SELECTOR, ms)
        for e in els:
            if e.is_displayed() and e.is_enabled():
                return e

    selectors = [
        "input[type='search']","input[name='q']","input[name='query']","input[name='search']","input[name='s']",
        "input[aria-label*='search' i]","input[placeholder*='search' i]",
        "input[placeholder*='name' i]","input[placeholder*='last' i]",
    ]
    for sel in selectors:
        for e in driver.find_elements(By.CSS_SELECTOR, sel):
            if e.is_displayed() and e.is_enabled():
                return e

    for e in driver.find_elements(By.TAG_NAME, "input"):
        t = (e.get_attribute("type") or "").lower()
        if t in ("hidden","submit","button","checkbox","radio","file","password"):
            continue
        if e.is_displayed() and e.is_enabled():
            return e
    return None

def click_submit_if_possible(driver, manual_search_button: str = "") -> bool:
    msb = (manual_search_button or "").strip()
    if msb:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, msb)
            if btn.is_displayed() and btn.is_enabled():
                btn.click()
                return True
        except Exception:
            return False

    for sel in ["button[type='submit']","input[type='submit']","button[aria-label*='search' i]","button[class*='search' i]"]:
        for b in driver.find_elements(By.CSS_SELECTOR, sel):
            if b.is_displayed() and b.is_enabled():
                b.click()
                return True
    return False

def submit_query(driver, inp, term: str, manual_search_button: str = "") -> bool:
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
        return True
    except Exception:
        pass

    if click_submit_if_possible(driver, manual_search_button=manual_search_button):
        return True

    try:
        inp.submit()
        return True
    except Exception:
        return False


# -------------------------
# People container heuristics + record extraction
# (copied from your version, unchanged logic)
# -------------------------
def page_has_no_results_signal(html: str) -> bool:
    if not html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    for s in [".no-results",".noresult",".no-result","#no-results",".empty-state",".empty",".nothing-found","[data-empty='true']"]:
        if soup.select_one(s):
            return True
    text = soup.get_text(" ", strip=True).lower()
    phrases = ["no results","0 results","zero results","no matches","no match","nothing found","did not match any","we couldn't find",
               "try a different search","no records found","no entries found","no people found","no profiles found","your search returned no results"]
    return any(p in text for p in phrases)

def _text_signature(txt: str) -> str:
    txt = (txt or "").strip()
    if len(txt) > 4000:
        txt = txt[:4000]
    return str(hash(txt))

def _score_people_block(text: str) -> Dict[str, Any]:
    t = (text or "")
    tlow = t.lower()
    emails = len(re.findall(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", t, re.I))
    mailtos = len(re.findall(r"mailto:", t, re.I))
    nameish = 0
    for line in [ln.strip() for ln in t.splitlines() if ln.strip()]:
        if NAME_COMMA_RE.match(line) or NAME_SPACE_RE.match(line):
            if NAME_COMMA_RE.match(line):
                nameish += 1
                continue
            if clean_extracted_name(line):
                nameish += 1
    people_hint = 1 if ("people results" in tlow or re.search(r"\bpeople\b", tlow)) else 0
    score = (emails * 12) + (mailtos * 18) + (nameish * 8) + (people_hint * 10)
    return {"score": score,"emails": emails,"mailtos": mailtos,"nameish": nameish,"people_hint": people_hint,"title": "","text": t}

def best_people_container_html(driver) -> Tuple[Optional[str], Dict[str, Any]]:
    css_candidates = ["main", "section", "article", "div", "ul", "ol", "table"]
    best = {"score": -1, "text": "", "emails": 0, "mailtos": 0, "nameish": 0, "people_hint": 0, "title": ""}
    best_html = None

    for tag in css_candidates:
        els = driver.find_elements(By.CSS_SELECTOR, tag)
        for el in els[:80]:
            txt = el.text or ""
            if len(txt.strip()) < 120:
                continue
            metrics = _score_people_block(txt)
            if metrics["emails"] == 0 and metrics["mailtos"] == 0 and metrics["nameish"] < 2:
                continue
            if metrics["score"] > best["score"]:
                best = metrics
                best_html = el.get_attribute("outerHTML")
    return best_html, best

def click_best_people_tab_if_any(driver, try_people_tab_click: bool = True) -> Optional[str]:
    if not try_people_tab_click:
        return None
    targets = [("people",10),("directory",7),("staff",6),("faculty",6),("students",5),("profiles",5),("employees",5)]
    best_el = None
    best_score = -1
    best_label = None

    for css in ["[role='tab']", "a", "button", "[role='button']"]:
        els = driver.find_elements(By.CSS_SELECTOR, css)
        for el in els[:250]:
            label = (el.text or "").strip() or (el.get_attribute("aria-label") or "").strip()
            if not label:
                continue
            low = label.lower()
            score = 0
            for word, wscore in targets:
                if word in low:
                    score = max(score, wscore)
            if score > best_score:
                if not el.is_displayed():
                    continue
                best_el, best_score, best_label = el, score, label

    if best_el and best_score >= 8:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best_el)
            best_el.click()
            return best_label
        except Exception:
            return None
    return None


def _norm_space(s: str) -> str:
    return " ".join((s or "").split()).strip()

def _strip_name_tokens(text: str, name: str) -> str:
    t = text or ""
    n = (name or "").strip()
    if not t or not n:
        return t
    t = " ".join(t.split())
    variants = {n}
    parts = [p for p in re.split(r"\s+", n) if p]
    if len(parts) >= 2:
        variants.add(f"{parts[-1]} {parts[0]}")
        variants.add(f"{parts[0]} {parts[-1]}")
        variants.add(f"{parts[-1]}, {parts[0]}")
    for v in sorted(variants, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(v)}\b", " ", t, flags=re.I)
    for tok in parts:
        if len(tok) >= 2:
            t = re.sub(rf"\b{re.escape(tok)}\b", " ", t, flags=re.I)
    t = re.sub(r"\s*\|\s*", " | ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"^[\|\-–—,:;]+", "", t).strip()
    t = re.sub(r"[\|\-–—,:;]+$", "", t).strip()
    return t

def _pick_description_from_text(text: str, name: str = "", email: str = "") -> str:
    t = " ".join((text or "").split()).strip()
    if not t:
        return ""
    if email:
        t = re.sub(rf"\b{re.escape(email)}\b", " ", t, flags=re.I)
    if name:
        t = _strip_name_tokens(t, name)
    t = " ".join(t.split()).strip()
    if not t:
        return ""
    if len(t) > 180:
        t = t[:180].rsplit(" ", 1)[0].strip() + "…"
    return t

def extract_people_like_records(container_html: str) -> List[Dict[str, Any]]:
    if not container_html:
        return []
    soup = BeautifulSoup(container_html, "html.parser")

    item_selectors = ["tr","li","article","[role='listitem']",".card",".result",".person",".profile",".directory-item","div"]
    blocks = []
    for sel in item_selectors:
        blocks.extend(soup.select(sel))
        if len(blocks) >= 250:
            break

    records: List[Dict[str, Any]] = []
    seen = set()

    def add_record(name: str, email: str = "", desc: str = "", url: str = ""):
        name = _norm_space(name)
        email = _norm_space(email)
        desc = _norm_space(desc)
        url = _norm_space(url)
        if not name:
            return
        c = clean_extracted_name(name)
        if not c:
            toks = name.split()
            if not (2 <= len(toks) <= 7):
                return
            c = name
        key = (c.lower(), (email or "").lower())
        if key in seen:
            return
        seen.add(key)
        records.append({"name": c, "email": email or None, "description": desc or None, "url": url or None})

    for blk in blocks[:250]:
        txt = blk.get_text("\n", strip=True)
        if not txt or len(txt) < 8:
            continue

        emails: List[str] = []
        for a in blk.select("a[href^='mailto:']"):
            href = a.get("href") or ""
            em = href.replace("mailto:", "").split("?")[0].strip()
            if em:
                emails.append(em)
        if not emails:
            m = EMAIL_RE.search(txt)
            if m:
                emails = [m.group(0)]

        url = ""
        for a in blk.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href or href.lower().startswith("mailto:"):
                continue
            url = href
            break

        name_candidates: List[str] = []
        for sel in ["h1","h2","h3","h4","strong","a"]:
            for el in blk.select(sel)[:8]:
                t = el.get_text(" ", strip=True)
                if t:
                    name_candidates.append(t)

        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        for ln in lines[:12]:
            if NAME_COMMA_RE.match(ln):
                parts = [p.strip() for p in ln.split(",") if p.strip()]
                if len(parts) >= 2:
                    name_candidates.append(f"{parts[1]} {parts[0]}".strip())
            elif NAME_SPACE_RE.match(ln):
                name_candidates.append(ln)

        chosen_name = ""
        for cand in name_candidates:
            c = clean_extracted_name(cand)
            if c:
                chosen_name = c
                break
        if not chosen_name:
            for cand in name_candidates:
                toks = cand.split()
                if 2 <= len(toks) <= 7:
                    chosen_name = _norm_space(cand)
                    break
        if not chosen_name:
            continue

        primary_email = emails[0] if emails else ""
        desc = _pick_description_from_text(txt, name=chosen_name, email=primary_email)

        if emails:
            for em in list(dict.fromkeys(emails))[:3]:
                add_record(chosen_name, email=em, desc=desc, url=url)
        else:
            add_record(chosen_name, email="", desc=desc, url=url)

    return records


def selenium_wait_for_people_results(driver, term: str, timeout: int, poll_s: float = 0.35):
    start = time.time()
    base_html, base_dbg = best_people_container_html(driver)
    base_text = BeautifulSoup(base_html, "html.parser").get_text(" ", strip=True) if base_html else ""
    base_sig = _text_signature(base_text)

    debug = {"baseline": {"sig": base_sig, "metrics": base_dbg}, "ticks": []}
    NO_RESULTS_GRACE_S = 1.25

    while (time.time() - start) < timeout:
        selenium_wait_document_ready(driver, timeout=3)
        page_html = driver.page_source or ""
        cont_html, cont_dbg = best_people_container_html(driver)
        cont_text = BeautifulSoup(cont_html, "html.parser").get_text(" ", strip=True) if cont_html else ""

        sig = _text_signature(cont_text)
        elapsed = round(time.time() - start, 2)

        # stable evidence
        people_names = []  # only used as evidence
        if cont_html:
            # reuse the old evidence helper idea but without implementing full name-extractor again
            people_names = [1] if (NAME_SPACE_RE.search(cont_text) or NAME_COMMA_RE.search(cont_text)) else []

        page_has_email = bool(EMAIL_RE.search(page_html or ""))
        cont_has_email = bool(EMAIL_RE.search(cont_text or ""))

        term_seen = (term.lower() in page_html.lower()) or (term.lower() in cont_text.lower())

        debug["ticks"].append({"t": elapsed,"sig": sig,"term_seen": bool(term_seen),"metrics": cont_dbg,
                               "people_names": len(people_names),"page_has_email": page_has_email,"cont_has_email": cont_has_email})

        if people_names or cont_has_email or page_has_email:
            return "results", cont_html, debug

        if elapsed >= NO_RESULTS_GRACE_S and term_seen:
            if page_has_no_results_signal(page_html):
                if cont_dbg.get("nameish", 0) < 2 and cont_dbg.get("emails", 0) == 0 and cont_dbg.get("mailtos", 0) == 0:
                    return "no_results", None, debug

        if sig != base_sig and cont_dbg.get("score", -1) >= 20:
            pass

        time.sleep(poll_s)

    return "timeout", None, debug


# -------------------------
# CLI-friendly active search runner
# -------------------------
def run_active_search(
    start_url: str,
    surnames: List[str],
    *,
    selenium_wait_s: int = 15,
    post_submit_sleep: float = 0.4,
    try_people_tab_click: bool = True,
    enable_light_chrome: bool = True,
    headless: bool = True,
    manual_search_selector: str = "",
    manual_search_button: str = "",
) -> List[Tuple[str, List[Dict[str,Any]]]]:
    """
    Returns list of (surname, people_records)
    """
    driver = get_driver(headless=headless, enable_light_chrome=enable_light_chrome)
    out: List[Tuple[str, List[Dict[str,Any]]]] = []

    try:
        driver.get(start_url)
        selenium_wait_document_ready(driver, timeout=min(12, selenium_wait_s))
        time.sleep(0.7)

        for surname in surnames:
            inp = find_search_input(driver, manual_search_selector=manual_search_selector)
            if not inp:
                out.append((surname, []))
                continue

            ok = submit_query(driver, inp, surname, manual_search_button=manual_search_button)
            if not ok:
                out.append((surname, []))
                continue

            if post_submit_sleep > 0:
                time.sleep(float(post_submit_sleep))

            click_best_people_tab_if_any(driver, try_people_tab_click=try_people_tab_click)

            state, people_container_html, _ = selenium_wait_for_people_results(
                driver=driver, term=surname, timeout=int(selenium_wait_s), poll_s=0.35
            )

            if state == "timeout":
                people_container_html, _ = best_people_container_html(driver)

            people_records = extract_people_like_records(people_container_html or "")
            out.append((surname, people_records))

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return out
