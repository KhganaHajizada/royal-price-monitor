import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://royal.az"
SNAPSHOT_PATH = Path("state/royal_snapshot.json")
USER_AGENT = "Mozilla/5.0 (compatible; RoyalPriceMonitor/1.0; +https://github.com/KhganaHajizada/royal-price-monitor)"
TELEGRAM_MAX = 3500


def http_json(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "az,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_products():
    """Fetch all products from Shopify's public JSON catalogue endpoint."""
    endpoints = [
        f"{BASE_URL}/products.json",
        f"{BASE_URL}/collections/all/products.json",
    ]

    last_error = None
    for endpoint in endpoints:
        try:
            products = []
            seen_ids = set()
            page = 1

            while True:
                query = urllib.parse.urlencode({"limit": 250, "page": page})
                data = http_json(f"{endpoint}?{query}")
                batch = data.get("products", [])
                if not batch:
                    break

                new_items = []
                for product in batch:
                    product_id = str(product.get("id", ""))
                    if product_id and product_id not in seen_ids:
                        seen_ids.add(product_id)
                        new_items.append(product)

                if not new_items:
                    break

                products.extend(new_items)

                if len(batch) < 250:
                    break

                page += 1
                if page > 100:
                    raise RuntimeError("Pagination safety limit exceeded")
                time.sleep(0.25)

            if products:
                return products
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Royal.az catalogue could not be fetched: {last_error}")


def clean_price(value):
    if value in (None, ""):
        return None
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def product_snapshot(product):
    handle = product.get("handle") or ""
    product_url = f"{BASE_URL}/products/{handle}" if handle else BASE_URL

    variants = {}
    for variant in product.get("variants", []):
        variant_id = str(variant.get("id", ""))
        if not variant_id:
            continue
        variants[variant_id] = {
            "title": variant.get("title") or "Default",
            "price": clean_price(variant.get("price")),
            "compare_at_price": clean_price(variant.get("compare_at_price")),
            "available": variant.get("available"),
        }

    return {
        "id": str(product.get("id", "")),
        "title": product.get("title") or "Adsız məhsul",
        "handle": handle,
        "url": product_url,
        "vendor": product.get("vendor") or "",
        "updated_at": product.get("updated_at"),
        "variants": variants,
    }


def build_snapshot(products):
    result = {}
    for product in products:
        item = product_snapshot(product)
        if item["id"]:
            result[item["id"]] = item
    return result


def load_snapshot():
    if not SNAPSHOT_PATH.exists():
        return None
    with SNAPSHOT_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(snapshot):
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT_PATH.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def money(value):
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f} ₼"
    except (TypeError, ValueError):
        return f"{value} ₼"


def availability(value):
    if value is True:
        return "Stokda ✅"
    if value is False:
        return "Stokda deyil ❌"
    return "Naməlum"


def detect_changes(old, new):
    changes = []

    old_ids = set(old)
    new_ids = set(new)

    for product_id in sorted(new_ids - old_ids):
        p = new[product_id]
        changes.append(
            "🆕 YENİ MƏHSUL\n"
            f"{p['title']}\n"
            f"🔗 {p['url']}"
        )

    for product_id in sorted(old_ids - new_ids):
        p = old[product_id]
        changes.append(
            "🗑️ KATALOQDAN ÇIXDI\n"
            f"{p['title']}\n"
            f"🔗 {p['url']}"
        )

    for product_id in sorted(old_ids & new_ids):
        before = old[product_id]
        after = new[product_id]
        title = after["title"]
        url = after["url"]

        old_variants = before.get("variants", {})
        new_variants = after.get("variants", {})
        old_variant_ids = set(old_variants)
        new_variant_ids = set(new_variants)

        for variant_id in sorted(new_variant_ids - old_variant_ids):
            v = new_variants[variant_id]
            changes.append(
                "➕ YENİ VARİANT\n"
                f"{title} — {v['title']}\n"
                f"Qiymət: {money(v.get('price'))}\n"
                f"🔗 {url}"
            )

        for variant_id in sorted(old_variant_ids - new_variant_ids):
            v = old_variants[variant_id]
            changes.append(
                "➖ VARİANT SİLİNDİ\n"
                f"{title} — {v['title']}\n"
                f"🔗 {url}"
            )

        for variant_id in sorted(old_variant_ids & new_variant_ids):
            ov = old_variants[variant_id]
            nv = new_variants[variant_id]
            variant_name = nv.get("title") or "Default"
            suffix = "" if variant_name in ("Default", "Default Title") else f" — {variant_name}"

            if ov.get("price") != nv.get("price"):
                changes.append(
                    "💰 QİYMƏT DƏYİŞDİ\n"
                    f"{title}{suffix}\n"
                    f"Əvvəl: {money(ov.get('price'))}\n"
                    f"İndi: {money(nv.get('price'))}\n"
                    f"🔗 {url}"
                )

            if ov.get("compare_at_price") != nv.get("compare_at_price"):
                changes.append(
                    "🏷️ ENDİRİM QİYMƏTİ DƏYİŞDİ\n"
                    f"{title}{suffix}\n"
                    f"Əvvəlki köhnə qiymət: {money(ov.get('compare_at_price'))}\n"
                    f"Yeni köhnə qiymət: {money(nv.get('compare_at_price'))}\n"
                    f"🔗 {url}"
                )

            if ov.get("available") != nv.get("available"):
                changes.append(
                    "📦 STOK STATUSU DƏYİŞDİ\n"
                    f"{title}{suffix}\n"
                    f"Əvvəl: {availability(ov.get('available'))}\n"
                    f"İndi: {availability(nv.get('available'))}\n"
                    f"🔗 {url}"
                )

    return changes


def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN və TELEGRAM_CHAT_ID secrets əlavə edilməyib")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"Telegram error: {result}")


def send_changes(changes):
    if not changes:
        return

    header = f"🔔 Royal.az monitor — {len(changes)} dəyişiklik\n\n"
    chunks = []
    current = header

    for change in changes:
        addition = change + "\n\n"
        if len(current) + len(addition) > TELEGRAM_MAX:
            chunks.append(current.rstrip())
            current = addition
        else:
            current += addition

    if current.strip():
        chunks.append(current.rstrip())

    for chunk in chunks:
        telegram_send(chunk)
        time.sleep(0.5)


def main():
    print("Royal.az kataloqu götürülür...")
    products = fetch_products()
    current = build_snapshot(products)
    print(f"Tapılan məhsul sayı: {len(current)}")

    if not current:
        raise RuntimeError("0 məhsul tapıldı; snapshot yenilənmədi")

    previous = load_snapshot()

    if previous is None:
        save_snapshot(current)
        telegram_send(
            "✅ Royal.az monitor aktivdir.\n"
            f"İlkin baza yaradıldı: {len(current)} məhsul.\n"
            "Bundan sonrakı yoxlamalarda dəyişiklik olarsa bildiriş gələcək."
        )
        print("İlkin snapshot yaradıldı.")
        return

    # Protect against temporary partial catalogue responses that would otherwise
    # look like hundreds of products were removed at once.
    if len(previous) >= 20 and len(current) < int(len(previous) * 0.70):
        raise RuntimeError(
            f"Safety stop: əvvəl {len(previous)}, indi yalnız {len(current)} məhsul gəldi. "
            "Snapshot dəyişdirilmədi."
        )

    changes = detect_changes(previous, current)
    if changes:
        print(f"{len(changes)} dəyişiklik tapıldı.")
        send_changes(changes)
        save_snapshot(current)
    else:
        print("Dəyişiklik yoxdur.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
