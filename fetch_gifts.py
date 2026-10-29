"""
این اسکریپت توسط GitHub Actions اجرا می‌شه (توکن از Secret میاد) و
گیفت‌های قابل اجاره رو توی data/gifts.json ذخیره می‌کنه.
اجرای محلی:  MARKETAPP_API_TOKEN=xxx python fetch_gifts.py
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://api.marketapp.org/v1/rent/gifts/"
TOKEN = os.getenv("MARKETAPP_API_TOKEN", "").strip()
OUT = Path(__file__).parent / "data" / "gifts.json"
MAX_PAGES = 30

IMAGE_KEYS = ("image_url", "image", "photo_url", "thumbnail_url", "preview_url", "sticker_url")
PRICE_KEYS = ("rent_price", "price_per_day", "price", "rent_price_ton", "min_price")
DURATION_KEYS = ("rent_duration", "duration", "max_duration", "rent_days")


def attributes_of(item):
    attrs = item.get("attributes") or []
    if isinstance(attrs, dict):
        return [{"trait_type": k, "value": v} for k, v in attrs.items()]
    return [a for a in attrs if isinstance(a, dict)] if isinstance(attrs, list) else []


def first_value(item, keys):
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def fragment_slug(item):
    blob = json.dumps(item, ensure_ascii=False)
    match = re.search(r"t\.me/nft/([A-Za-z0-9]+-\d+)", blob)
    if match:
        return match.group(1).lower()
    name = str(item.get("nft_name") or item.get("name") or item.get("full_name") or "")
    match = re.match(r"^(.*?)\s*#\s*(\d+)\s*$", name)
    if not match:
        return ""
    base = re.sub(r"[^A-Za-z0-9]", "", match.group(1))
    return f"{base}-{match.group(2)}".lower() if base else ""


def image_candidates(item):
    urls = []
    for key in IMAGE_KEYS:
        value = str(item.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            urls.append(value)
    slug = fragment_slug(item)
    if slug:
        base = f"https://nft.fragment.com/gift/{slug}"
        urls += [f"{base}.webp", f"{base}.medium.jpg", f"{base}.small.jpg"]
    return list(dict.fromkeys(urls))


def normalize(item):
    attrs = {a.get("trait_type"): a.get("value") for a in attributes_of(item)}
    return {
        "id": item.get("nft_address") or item.get("id"),
        "name": str(item.get("nft_name") or item.get("name") or item.get("full_name") or "Gift"),
        "images": image_candidates(item),
        "price": first_value(item, PRICE_KEYS),
        "duration": first_value(item, DURATION_KEYS),
        "attributes": {k: v for k, v in attrs.items() if k and v},
    }


def get_page(session, cursor):
    params = {"limit": 100, "sort_by": "recently_touch", "include_sellable": "false"}
    if cursor:
        params["cursor"] = cursor
    for attempt in range(1, 5):
        try:
            r = session.get(API_URL, params=params, timeout=(15, 75))
            if r.status_code in (401, 403):
                sys.exit("Marketapp denied access: check MARKETAPP_API_TOKEN secret.")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 4:
                sys.exit(f"Marketapp request failed: {exc}")
            time.sleep(attempt * 2)


def main():
    if not TOKEN:
        sys.exit("MARKETAPP_API_TOKEN is not set.")

    items, seen, cursor = [], set(), None
    with requests.Session() as session:
        session.headers.update({"Authorization": TOKEN, "Accept": "application/json"})
        for _ in range(MAX_PAGES):
            data = get_page(session, cursor)
            page = data.get("items", []) if isinstance(data, dict) else []
            for raw in page:
                if not isinstance(raw, dict):
                    continue
                gift = normalize(raw)
                key = gift["id"] or gift["name"]
                if key not in seen:
                    seen.add(key)
                    items.append(gift)
            nxt = data.get("cursor") or data.get("next_cursor") if isinstance(data, dict) else None
            if not nxt or not page or nxt == cursor:
                break
            cursor = nxt

    if not items:
        sys.exit("No items returned; keeping the previous data file.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"updated_at": datetime.now(timezone.utc).isoformat(), "count": len(items), "items": items},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Saved {len(items)} gifts")


if __name__ == "__main__":
    main()
