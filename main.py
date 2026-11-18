import calendar
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse, urldefrag

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
STATE_PATH = os.path.join(ROOT, "sent_items.json")
UTC = timezone.utc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("opportunity-radar")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "OpportunityRadar/1.0 (student opportunity monitor; respectful low-frequency checks)"
})

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def clean_url(url):
    url = urldefrag((url or "").strip())[0]
    return url.rstrip("/")

def strip_html(value):
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)

def matches_keywords(text, keywords):
    haystack = (text or "").casefold()
    return any(k.casefold() in haystack for k in keywords)

def parse_date(value):
    if not value:
        return None
    try:
        dt = date_parser.parse(value, fuzzy=True, dayfirst=False)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None

MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
DATE_PATTERNS = [
    rf"\b(?:deadline|apply by|applications? close|closing date|last date|"
    rf"registration closes|submission deadline|due date)\b[^.\n]{{0,45}}?"
    rf"(\d{{1,2}}(?:st|nd|rd|th)?[\s\-/,]+{MONTH}[\s,/-]+\d{{4}})",
    rf"\b(?:deadline|apply by|applications? close|closing date|last date|"
    rf"registration closes|submission deadline|due date)\b[^.\n]{{0,45}}?"
    rf"({MONTH}[\s\-/,]+\d{{1,2}}(?:st|nd|rd|th)?[\s,/-]+\d{{4}})",
    r"\b(?:deadline|apply by|applications? close|closing date|last date|"
    r"registration closes|submission deadline|due date)\b[^.\n]{0,45}?"
    r"(\d{4}-\d{2}-\d{2})",
]

def extract_deadline(text):
    text = strip_html(text)
    for pattern in DATE_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parsed = parse_date(match.group(1))
            if parsed:
                return parsed
    return None

def telegram_send(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram secrets are missing; notification skipped.")
        return False

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    response = SESSION.post(
        endpoint,
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return True

def telegram_text(item):
    title = html.escape(item["title"][:250])
    desc = html.escape(item.get("description", "")[:500])
    source = html.escape(item.get("source", "Source"))
    url = html.escape(item["url"], quote=True)
    deadline = item.get("deadline")
    deadline_line = f"\n<b>Deadline:</b> {html.escape(deadline)}" if deadline else ""
    return (
        f"🆕 <b>New opportunity</b>\n\n"
        f"<b>{title}</b>\n"
        f"{desc}\n\n"
        f"<b>Source:</b> {source}{deadline_line}\n"
        f'🔗 <a href="{url}">Open opportunity</a>'
    )

def fetch_rss(source):
    response = SESSION.get(source["url"], timeout=30)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    results = []
    for entry in feed.entries:
        link = clean_url(entry.get("link", ""))
        title = strip_html(entry.get("title", "")).strip()
        summary = strip_html(entry.get("summary", entry.get("description", ""))).strip()
        if not link or not title:
            continue
        published = entry.get("published") or entry.get("updated")
        results.append({
            "title": title,
            "description": summary,
            "url": link,
            "source": source["name"],
            "published": published,
            "deadline": extract_deadline(f"{title} {summary}"),
        })
    return results

def fetch_html(source):
    response = SESSION.get(source["url"], timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    seen = set()

    # Generic anchor discovery. For a site with unusual markup, configure a CSS
    # selector in config.json (for example, article a or .event-card a).
    selector = source.get("selector", "a[href]")
    for anchor in soup.select(selector):
        title = strip_html(anchor.get_text(" ", strip=True))
        href = anchor.get("href")
        if not href or not title or len(title) < 8:
            continue
        link = clean_url(urljoin(source["url"], href))
        parsed = urlparse(link)
        if parsed.scheme not in ("http", "https"):
            continue
        if link == clean_url(source["url"]) or link in seen:
            continue
        seen.add(link)

        parent = anchor.find_parent(["article", "li", "div", "section"])
        context = strip_html(parent.get_text(" ", strip=True)) if parent else title
        # Avoid giant page-wide descriptions.
        description = context if 25 <= len(context) <= 1200 else title
        results.append({
            "title": title[:300],
            "description": description[:700],
            "url": link,
            "source": source["name"],
            "published": None,
            "deadline": extract_deadline(context),
        })
    return results

def fetch_source(source):
    try:
        if source["type"] == "rss":
            items = fetch_rss(source)
        elif source["type"] == "html":
            items = fetch_html(source)
        else:
            log.warning("Unknown source type %s: %s", source.get("type"), source.get("name"))
            return []
        log.info("%s: collected %d candidate links", source["name"], len(items))
        return items
    except requests.RequestException as exc:
        log.warning("Could not fetch %s: %s", source["name"], exc)
    except Exception:
        log.exception("Unexpected error while processing %s", source["name"])
    return []

def eligible(item, keywords, config):
    text = f'{item["title"]} {item.get("description", "")}'
    if not matches_keywords(text, keywords):
        return False

    # Optional exclusion terms reduce irrelevant matches.
    exclusions = config.get("exclude_keywords", [])
    if exclusions and matches_keywords(text, exclusions):
        return False
    return True

def process_deadline_reminders(state, days_before=7):
    today = datetime.now(UTC).date()
    sent = 0
    for url, record in list(state.items()):
        deadline_text = record.get("deadline")
        if not deadline_text or record.get("reminder_sent"):
            continue
        try:
            deadline = date_parser.parse(deadline_text).date()
        except (ValueError, TypeError):
            continue
        days_left = (deadline - today).days
        if days_left == days_before:
            title = html.escape(record.get("title", "Opportunity"))
            safe_url = html.escape(url, quote=True)
            message = (
                f"⏰ <b>Deadline reminder — 7 days left</b>\n\n"
                f"<b>{title}</b>\n"
                f"Deadline: <b>{deadline.isoformat()}</b>\n"
                f'🔗 <a href="{safe_url}">Open opportunity</a>'
            )
            if telegram_send(message):
                record["reminder_sent"] = True
                sent += 1
    return sent

def main():
    config = load_json(CONFIG_PATH, {})
    state = load_json(STATE_PATH, {})
    keywords = config.get("keywords", [])
    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]

    if not keywords:
        raise RuntimeError("No keywords configured in config.json")

    # Send deadline reminders first, including reminders for older opportunities.
    reminder_count = process_deadline_reminders(state, config.get("reminder_days_before", 7))
    log.info("Deadline reminders sent: %d", reminder_count)

    candidates = []
    for source in sources:
        candidates.extend(fetch_source(source))
        time.sleep(config.get("delay_between_sources_seconds", 2))

    new_count = 0
    for item in candidates:
        url = clean_url(item.get("url"))
        if not url or url in state or not eligible(item, keywords, config):
            continue

        item["url"] = url
        if telegram_send(telegram_text(item)):
            state[url] = {
                "title": item["title"],
                "deadline": item.get("deadline"),
                "first_seen": datetime.now(UTC).isoformat(),
                "reminder_sent": False,
            }
            new_count += 1
            log.info("Sent: %s", item["title"])

    save_state(state)
    log.info("Finished. New alerts: %d", new_count)

if __name__ == "__main__":
    main()
