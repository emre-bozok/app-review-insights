"""Bounded store collectors. No network activity at import time."""
import hashlib
import re


def text_id(platform, text, date, rating):
    return platform + ":" + hashlib.sha256(f"{text}|{date}|{rating}".encode()).hexdigest()[:24]


def collect_android(package, limit=100, country="tr", language="tr"):
    from google_play_scraper import Sort, reviews
    if limit < 1 or limit > 1000:
        raise ValueError("Android limit must be between 1 and 1000.")
    rows, token = [], None
    seen = set()
    while len(rows) < limit:
        batch, next_token = reviews(package, lang=language, country=country,
                                   sort=Sort.NEWEST, count=min(100, limit-len(rows)),
                                   continuation_token=token)
        for record in batch:
            text = record.get("content") or ""
            if not text.strip():
                continue
            date = record["at"].isoformat() if record.get("at") else ""
            rid = "android:" + str(record.get("reviewId") or text_id("review", text, date, record.get("score")))
            if rid in seen:
                continue
            seen.add(rid)
            rows.append({"review_id": rid, "text": text, "rating": record.get("score"),
                         "date": date, "platform": "Android"})
        if not batch or next_token is None or next_token == token:
            break
        token = next_token
    return rows[:limit]


def collect_ios(app_id, pages=1, country="tr", session=None):
    import requests
    if not str(app_id).isdigit() or not re.fullmatch(r"[a-z]{2}", country):
        raise ValueError("Use a numeric App Store ID and a lowercase two-letter country.")
    if not 1 <= pages <= 10:
        raise ValueError("iOS pages must be between 1 and 10.")
    session = session or requests.Session()
    rows, seen = [], set()
    for page in range(1, pages + 1):
        url = f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/sortBy=mostRecent/id={app_id}/json"
        response = session.get(url, timeout=30)
        response.raise_for_status()
        entries = response.json().get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        found = False
        for entry in entries:
            if "im:rating" not in entry:
                continue
            text = entry.get("content", {}).get("label", "")
            if not text.strip():
                continue
            found = True
            date = entry.get("updated", {}).get("label", "")
            rating = int(entry["im:rating"]["label"])
            rid = "ios:" + str(entry.get("id", {}).get("label") or text_id("review", text, date, rating))
            if rid in seen:
                continue
            seen.add(rid)
            rows.append({"review_id": rid, "text": text, "rating": rating,
                         "date": date, "platform": "iOS"})
        if not found:
            break
    return rows
