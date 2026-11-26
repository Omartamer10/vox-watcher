"""Vox watcher for GitHub Actions: one check per run."""
import json
import os
import random
import re
import time
import urllib.parse
import urllib.request

from curl_cffi import requests
from bs4 import BeautifulSoup

KEYWORD = "doomsday"
TARGET_FORMATS = ["4DX", "IMAX"]
CINEMA = "city-centre-almaza"
BASE = "https://egy.voxcinemas.com"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["TELEGRAM_CHAT_IDS"].split(",") if c.strip()]
STATE_FILE = "state.json"
TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*(?:am|pm)", re.I)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    sent = 0
    for chat_id in CHAT_IDS:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=data)
                with urllib.request.urlopen(req, timeout=15) as r:
                    r.read()
                sent += 1
                break
            except Exception as e:
                print(f"Telegram send to a chat failed (try {attempt + 1}/3): {e}")
                time.sleep(3)
    if sent == 0:
        raise RuntimeError("Telegram message could not be sent to anyone")


def fetch(url):
    r = requests.get(url, impersonate="chrome", timeout=30)
    r.raise_for_status()
    return r.text


def absolute(href):
    return href if href.startswith("http") else BASE + href


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def format_label(a):
    li = a.find_parent("li")
    outer = li.find_parent("li") if li else None
    node = outer or li
    label = ""
    if node:
        text = node.get_text(" ", strip=True)
        m = TIME_RE.search(text)
        label = (text[: m.start()] if m else text).strip()
    if not label:
        prev = a.find_previous(["strong", "b"])
        label = prev.get_text(" ", strip=True) if prev else ""
    return label.upper()


def sections(html):
    soup = BeautifulSoup(html, "html.parser")
    for h2 in soup.find_all("h2"):
        title = h2.get_text(" ", strip=True)
        links = []
        for el in h2.find_all_next():
            if el.name == "h2":
                break
            if el.name == "a" and "/booking/" in (el.get("href") or ""):
                links.append((format_label(el), el["href"]))
        if links:
            yield title, links


def has_format(label, fmt):
    return re.search(rf"(?<![A-Z0-9]){re.escape(fmt.upper())}(?![A-Z0-9])", label) is not None


def formats_found(html, wanted):
    found = {}
    for title, links in sections(html):
        if KEYWORD.lower() not in title.lower():
            continue
        for label, href in links:
            for fmt in wanted:
                if fmt not in found and has_format(label, fmt):
                    found[fmt] = absolute(href)
    return found


def date_page_urls(html):
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        if re.search(r"showtimes\?c=" + re.escape(CINEMA) + r"&d=\d{8}", a["href"]):
            urls.append(absolute(a["href"]))
    return list(dict.fromkeys(urls))


def check_once(wanted):
    main_url = f"{BASE}/showtimes/{CINEMA}"
    main_html = fetch(main_url)
    hits = {}
    for fmt, link in formats_found(main_html, wanted).items():
        hits[fmt] = (main_url, link)
    for url in date_page_urls(main_html):
        if all(f in hits for f in wanted):
            break
        time.sleep(random.uniform(1, 3))
        html = fetch(url)
        for fmt, link in formats_found(html, wanted).items():
            hits.setdefault(fmt, (url, link))
    return hits


def main():
    state = load_state()
    alerted = state.setdefault("alerted", {})

    if not state.get("started"):
        send_telegram(
            f"GitHub watcher is live. Waiting for '{KEYWORD}' in: {', '.join(TARGET_FORMATS)}."
        )
        state["started"] = True
        save_state(state)

    pending = [f for f in TARGET_FORMATS if f not in alerted]
    if not pending:
        print("All formats already announced.")
        return

    try:
        hits = check_once(pending)
    except Exception as e:
        print("Vox check failed:", e)
        return

    for fmt, (page, link) in hits.items():
        send_telegram(
            f"{fmt} tickets for '{KEYWORD}' are LIVE at {CINEMA}!\n"
            f"Date page: {page}\nBook: {link}"
        )
        alerted[fmt] = {"page": page, "link": link}
        save_state(state)
        print("ALERT sent for", fmt)

    if not hits:
        print("Not listed yet. Still waiting for:", ", ".join(pending))


if __name__ == "__main__":
    main()
