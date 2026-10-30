import os
import json
import time
import tempfile
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# -------------------- تنظیمات --------------------

BASE_URL = "https://api.marketapp.org/v1"

COLLECTIONS_URL = f"{BASE_URL}/collections/gifts/"
GIFTS_URL = f"{BASE_URL}/rent/gifts/"

LIMIT = 100
REQUEST_TIMEOUT = 45

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

COLLECTIONS_FILE = DATA_DIR / "collections.json"
GIFTS_FILE = DATA_DIR / "gifts.json"

TOKEN = os.getenv("MARKETAPP_API_TOKEN", "").strip()


# -------------------- بررسی توکن --------------------

if not TOKEN:
    raise SystemExit(
        "خطا: متغیر محیطی MARKETAPP_API_TOKEN تنظیم نشده است."
    )


# -------------------- ساخت Session --------------------

session = requests.Session()

session.headers.update({
    "Authorization": TOKEN,
    "Accept": "application/json",
    "User-Agent": "Marketapp-Gift-Catalog/1.0"
})

retry = Retry(
    total=5,
    connect=5,
    read=5,
    backoff_factor=2,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)

adapter = HTTPAdapter(max_retries=retry)

session.mount("https://", adapter)
session.mount("http://", adapter)


# -------------------- توابع کمکی --------------------

def atomic_save_json(path, data):
    """
    ذخیره امن فایل JSON؛
    در صورت خطا فایل قبلی خراب یا پاک نمی‌شود.
    """

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=path.stem + "_",
        suffix=".tmp"
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(temp_path, path)

    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def request_json(url, params=None):
    response = session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError:
        raise RuntimeError(
            f"پاسخ API معتبر نیست: {url}"
        )


def extract_records(payload, possible_keys):
    """
    استخراج لیست اطلاعات از ساختارهای رایج API.
    """

    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        raise RuntimeError(
            "ساختار پاسخ API قابل شناسایی نیست."
        )

    for key in possible_keys:
        value = payload.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            try:
                return extract_records(value, possible_keys)
            except RuntimeError:
                pass

    # پاسخ ممکن است مستقیماً یک کالکشن یا گیفت باشد.
    if payload.get("address") and payload.get("name"):
        return [payload]

    if payload.get("nft_address"):
        return [payload]

    raise RuntimeError(
        "لیست اطلاعات در پاسخ API پیدا نشد. "
        f"کلیدهای دریافتی: {list(payload.keys())}"
    )


def get_next_cursor(payload, current_cursor=None):
    """
    پیدا کردن cursor صفحه بعد.
    """

    if not isinstance(payload, dict):
        return None

    possible_values = [
        payload.get("next_cursor"),
        payload.get("next"),
        payload.get("next_page"),
    ]

    pagination = payload.get("pagination")

    if isinstance(pagination, dict):
        possible_values.extend([
            pagination.get("next_cursor"),
            pagination.get("next"),
        ])

    page_info = payload.get("page_info")

    if isinstance(page_info, dict):
        possible_values.extend([
            page_info.get("next_cursor"),
            page_info.get("end_cursor"),
        ])

    # بعضی APIها cursor صفحه بعد را مستقیماً با نام cursor می‌دهند.
    possible_values.append(payload.get("cursor"))

    for value in possible_values:
        if value is None or value is False or value == "":
            continue

        if isinstance(value, (str, int)):
            value = str(value)

            if value != str(current_cursor or ""):
                return value

    return None


def fetch_paginated(url, params, record_keys, label):
    """
    دریافت تمام صفحات یک endpoint.
    """

    all_records = []
    seen_cursors = set()
    cursor = None
    page = 0

    while True:
        page += 1

        current_params = dict(params)

        if cursor:
            current_params["cursor"] = cursor

        print(f"{label} | صفحه {page}")

        payload = request_json(
            url,
            params=current_params
        )

        records = extract_records(
            payload,
            record_keys
        )

        if not records:
            break

        all_records.extend(records)

        next_cursor = get_next_cursor(
            payload,
            current_cursor=cursor
        )

        if not next_cursor:
            # اگر صفحه کامل باشد اما cursor نداشته باشیم،
            # احتمال ناقص‌ماندن اطلاعات را اعلام می‌کنیم.
            if len(records) >= int(current_params.get("limit", LIMIT)):
                raise RuntimeError(
                    f"{label}: صفحه {page} کامل است، "
                    "اما cursor صفحه بعد در پاسخ پیدا نشد. "
                    "برای جلوگیری از ذخیره اطلاعات ناقص، عملیات متوقف شد."
                )

            break

        if next_cursor in seen_cursors:
            raise RuntimeError(
                f"{label}: cursor تکراری دریافت شد."
            )

        seen_cursors.add(next_cursor)
        cursor = next_cursor

        time.sleep(0.15)

    return all_records


# -------------------- دریافت کالکشن‌ها --------------------

def fetch_collections():
    print("\n========== دریافت کالکشن‌ها ==========")

    payload = request_json(
        COLLECTIONS_URL,
        params=None
    )

    print("\nساختار پاسخ کالکشن‌ها:")
    print(json.dumps(
        payload,
        ensure_ascii=False,
        indent=2
    )[:15000])

    collections = extract_records(
        payload,
        [
            "items",
            "results",
            "collections",
            "data",
        ]
    )

    cleaned = []
    seen_addresses = set()

    for item in collections:
        if not isinstance(item, dict):
            continue

        address = item.get("address")
        name = item.get("name")

        if not address or not name:
            continue

        address = str(address).strip()
        name = str(name).strip()

        if not address or address in seen_addresses:
            continue

        seen_addresses.add(address)

        cleaned.append({
            "name": name,
            "address": address,
            "extra_data": item.get("extra_data") or {}
        })

    if not cleaned:
        raise RuntimeError(
            "هیچ کالکشن معتبری پیدا نشد. "
            "ساختار JSON چاپ‌شده را بررسی کن."
        )

    cleaned.sort(
        key=lambda item: item["name"].lower()
    )

    print(f"\nتعداد کالکشن‌ها: {len(cleaned)}")

    return cleaned


# -------------------- دریافت گیفت‌های هر کالکشن --------------------

def fetch_collection_gifts(collection):
    name = collection["name"]
    address = collection["address"]

    print(f"\n========== {name} ==========")

    gifts = fetch_paginated(
        url=GIFTS_URL,
        params={
            "limit": LIMIT,
            "collection_address": address
        },
        record_keys=[
            "items",
            "results",
            "gifts",
            "data",
        ],
        label=name
    )

    result = []
    seen = set()

    for gift in gifts:
        if not isinstance(gift, dict):
            continue

        nft_address = gift.get("nft_address")

        if not nft_address:
            continue

        nft_address = str(nft_address)

        if nft_address in seen:
            continue

        seen.add(nft_address)

        # اطلاعات کالکشن از منبع واقعی کالکشن اضافه می‌شود.
        gift["collection_name"] = name
        gift["collection_address"] = address

        result.append(gift)

    print(f"گیفت‌های دریافت‌شده: {len(result)}")

    return result


# -------------------- اجرای اصلی --------------------

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    collections = fetch_collections()

    all_gifts = []
    seen_gifts = set()

    for index, collection in enumerate(collections, start=1):
        print(
            f"\nکالکشن {index} از {len(collections)}"
        )

        collection_gifts = fetch_collection_gifts(
            collection
        )

        collection["gift_count"] = len(collection_gifts)

        for gift in collection_gifts:
            address = gift["nft_address"]

            if address in seen_gifts:
                continue

            seen_gifts.add(address)
            all_gifts.append(gift)

    if not all_gifts:
        raise RuntimeError(
            "هیچ گیفتی دریافت نشد؛ فایل‌های قبلی حفظ شدند."
        )

    all_gifts.sort(
        key=lambda item: (
            item.get("collection_name", "").lower(),
            item.get("nft_name", "").lower()
        )
    )

    collections_output = {
        "total": len(collections),
        "collections": collections
    }

    gifts_output = {
        "total": len(all_gifts),
        "updated_at": int(time.time()),
        "gifts": all_gifts
    }

    # ابتدا هر دو فایل موقتاً آماده می‌شوند و سپس ذخیره می‌شوند.
    atomic_save_json(
        COLLECTIONS_FILE,
        collections_output
    )

    atomic_save_json(
        GIFTS_FILE,
        gifts_output
    )

    print("\n===================================")
    print("به‌روزرسانی با موفقیت انجام شد.")
    print(f"تعداد کالکشن‌ها: {len(collections)}")
    print(f"تعداد کل گیفت‌ها: {len(all_gifts)}")
    print(f"فایل کالکشن‌ها: {COLLECTIONS_FILE}")
    print(f"فایل گیفت‌ها: {GIFTS_FILE}")
    print("===================================")


if __name__ == "__main__":
    main()
