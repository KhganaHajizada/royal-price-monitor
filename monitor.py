import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://royal.az"
SNAPSHOT_PATH = Path("state/royal_snapshot.json")
USER_AGENT = "Mozilla/5.0 (compatible; RoyalPriceMonitor/2.0; +https://github.com/KhganaHajizada/royal-price-monitor)"
TELEGRAM_MAX = 3500


def http_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_products():
    endpoints = [f"{BASE_URL}/products.json", f"{BASE_URL}/collections/all/products.json"]
    last_error = None
    for endpoint in endpoints:
        try:
            products, seen, page = [], set(), 1
            while True:
                data = http_json(f"{endpoint}?{urllib.parse.urlencode({'limit': 250, 'page': page})}")
                batch = data.get("products", [])
                if not batch:
                    break
                fresh = []
                for p in batch:
                    pid = str(p.get("id", ""))
                    if pid and pid not in seen:
                        seen.add(pid)
                        fresh.append(p)
                if not fresh:
                    break
                products.extend(fresh)
                if len(batch) < 250:
                    break
                page += 1
                if page > 100:
                    raise RuntimeError("Pagination safety limit exceeded")
                time.sleep(0.2)
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


def clean_text(value):
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def image_urls(product):
    result = []
    for image in product.get("images", []) or []:
        src = image.get("src") if isinstance(image, dict) else None
        if src:
            result.append(src)
    return result


def option_snapshot(product):
    result = []
    for opt in product.get("options", []) or []:
        if isinstance(opt, dict):
            result.append({"name": opt.get("name") or "", "values": opt.get("values") or []})
    return result


def product_snapshot(product):
    handle = product.get("handle") or ""
    variants = {}
    for variant in product.get("variants", []) or []:
        vid = str(variant.get("id", ""))
        if not vid:
            continue
        variants[vid] = {
            "title": variant.get("title") or "Default",
            "price": clean_price(variant.get("price")),
            "compare_at_price": clean_price(variant.get("compare_at_price")),
            "available": variant.get("available"),
            "sku": variant.get("sku") or "",
            "grams": variant.get("grams"),
        }
    return {
        "id": str(product.get("id", "")),
        "title": product.get("title") or "Adsız məhsul",
        "handle": handle,
        "url": f"{BASE_URL}/products/{handle}" if handle else BASE_URL,
        "description": clean_text(product.get("body_html")),
        "vendor": product.get("vendor") or "",
        "product_type": product.get("product_type") or "",
        "tags": sorted(product.get("tags") or []),
        "images": image_urls(product),
        "options": option_snapshot(product),
        "variants": variants,
    }


def build_snapshot(products):
    result = {}
    for p in products:
        item = product_snapshot(p)
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
    return "Stokda ✅" if value is True else "Stokda deyil ❌" if value is False else "Naməlum"


def short(value, limit=450):
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def field_change(changes, before, after, key, label, icon, title, url, formatter=lambda x: x or "—"):
    # Old snapshots may not yet contain newly introduced fields. Baseline them silently once.
    if key not in before or before.get(key) == after.get(key):
        return
    changes.append(
        f"{icon} {label}\n{title}\n"
        f"Əvvəl: {short(formatter(before.get(key)))}\n"
        f"İndi: {short(formatter(after.get(key)))}\n"
        f"🔗 {url}"
    )


def detect_changes(old, new):
    changes = []
    old_ids, new_ids = set(old), set(new)

    for pid in sorted(new_ids - old_ids):
        p = new[pid]
        prices = [v.get("price") for v in p.get("variants", {}).values() if v.get("price") is not None]
        price_text = money(prices[0]) if len(set(prices)) == 1 and prices else (" / ".join(money(x) for x in prices[:3]) if prices else "—")
        changes.append(f"🆕 YENİ MƏHSUL\n{p['title']}\nQiymət: {price_text}\nBrend: {p.get('vendor') or '—'}\n🔗 {p['url']}")

    for pid in sorted(old_ids - new_ids):
        p = old[pid]
        changes.append(f"🗑️ KATALOQDAN / SATIŞDAN ÇIXDI\n{p['title']}\n🔗 {p['url']}")

    for pid in sorted(old_ids & new_ids):
        before, after = old[pid], new[pid]
        title, url = after["title"], after["url"]

        field_change(changes, before, after, "title", "MƏHSUL ADI DƏYİŞDİ", "✏️", title, url)
        field_change(changes, before, after, "description", "AÇIQLAMA DƏYİŞDİ", "📝", title, url)
        field_change(changes, before, after, "vendor", "BREND / VENDOR DƏYİŞDİ", "🏷️", title, url)
        field_change(changes, before, after, "product_type", "KATEQORİYA / PRODUCT TYPE DƏYİŞDİ", "🗂️", title, url)
        field_change(changes, before, after, "tags", "TAG-LƏR DƏYİŞDİ", "🔖", title, url, lambda x: ", ".join(x or []) or "—")
        field_change(changes, before, after, "images", "MƏHSUL ŞƏKİLLƏRİ DƏYİŞDİ", "🖼️", title, url, lambda x: f"{len(x or [])} şəkil")
        field_change(changes, before, after, "options", "MƏHSUL SEÇİMLƏRİ DƏYİŞDİ", "⚙️", title, url, lambda x: json.dumps(x or [], ensure_ascii=False))
        if "handle" in before and before.get("handle") != after.get("handle"):
            changes.append(f"🔗 MƏHSUL URL-i DƏYİŞDİ\n{title}\nƏvvəl: {before.get('url')}\nİndi: {after.get('url')}")

        ov, nv = before.get("variants", {}), after.get("variants", {})
        old_vids, new_vids = set(ov), set(nv)
        for vid in sorted(new_vids - old_vids):
            v = nv[vid]
            changes.append(f"➕ YENİ VARİANT\n{title} — {v['title']}\nQiymət: {money(v.get('price'))}\n{availability(v.get('available'))}\n🔗 {url}")
        for vid in sorted(old_vids - new_vids):
            v = ov[vid]
            changes.append(f"➖ VARİANT SİLİNDİ\n{title} — {v['title']}\n🔗 {url}")
        for vid in sorted(old_vids & new_vids):
            a, b = ov[vid], nv[vid]
            vn = b.get("title") or "Default"
            suffix = "" if vn in ("Default", "Default Title") else f" — {vn}"
            if a.get("price") != b.get("price"):
                changes.append(f"💰 QİYMƏT DƏYİŞDİ\n{title}{suffix}\nƏvvəl: {money(a.get('price'))}\nİndi: {money(b.get('price'))}\n🔗 {url}")
            if a.get("compare_at_price") != b.get("compare_at_price"):
                changes.append(f"🏷️ ENDİRİM / KÖHNƏ QİYMƏT DƏYİŞDİ\n{title}{suffix}\nƏvvəl: {money(a.get('compare_at_price'))}\nİndi: {money(b.get('compare_at_price'))}\n🔗 {url}")
            if a.get("available") != b.get("available"):
                changes.append(f"📦 STOK STATUSU DƏYİŞDİ\n{title}{suffix}\nƏvvəl: {availability(a.get('available'))}\nİndi: {availability(b.get('available'))}\n🔗 {url}")
            if "title" in a and a.get("title") != b.get("title"):
                changes.append(f"✏️ VARİANT ADI DƏYİŞDİ\n{title}\nƏvvəl: {a.get('title')}\nİndi: {b.get('title')}\n🔗 {url}")
            if "sku" in a and a.get("sku") != b.get("sku"):
                changes.append(f"🔢 SKU DƏYİŞDİ\n{title}{suffix}\nƏvvəl: {a.get('sku') or '—'}\nİndi: {b.get('sku') or '—'}\n🔗 {url}")
            if "grams" in a and a.get("grams") != b.get("grams"):
                changes.append(f"⚖️ ÇƏKİ MƏLUMATI DƏYİŞDİ\n{title}{suffix}\nƏvvəl: {a.get('grams')} g\nİndi: {b.get('grams')} g\n🔗 {url}")
    return changes


def telegram_send(text):
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN və TELEGRAM_CHAT_ID secrets əlavə edilməyib")
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"Telegram error: {result}")


def send_changes(changes):
    if not changes:
        return
    header = f"🔔 Royal.az monitor — {len(changes)} dəyişiklik\n\n"
    chunks, current = [], header
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
        time.sleep(0.4)


def main():
    print("Royal.az kataloqu götürülür...")
    current = build_snapshot(fetch_products())
    print(f"Tapılan məhsul sayı: {len(current)}")
    if not current:
        raise RuntimeError("0 məhsul tapıldı; snapshot yenilənmədi")

    previous = load_snapshot()
    if previous is None:
        save_snapshot(current)
        telegram_send(f"✅ Royal.az monitor aktivdir.\nİlkin baza yaradıldı: {len(current)} məhsul.\nBundan sonrakı yoxlamalarda yalnız dəyişiklik olarsa bildiriş gələcək.")
        return

    if len(previous) >= 20 and len(current) < int(len(previous) * 0.70):
        raise RuntimeError(f"Safety stop: əvvəl {len(previous)}, indi yalnız {len(current)} məhsul gəldi. Snapshot dəyişdirilmədi.")

    changes = detect_changes(previous, current)
    if changes:
        print(f"{len(changes)} dəyişiklik tapıldı.")
        send_changes(changes)
    else:
        print("Dəyişiklik yoxdur — Telegram-a mesaj göndərilmir.")

    # Always persist the current snapshot. This also silently upgrades older snapshot schemas.
    save_snapshot(current)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
