import difflib
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
PRODUCT_SNAPSHOT_PATH = Path("state/royal_snapshot.json")
PRODUCT_SEO_PATH = Path("state/royal_product_seo.json")
COLLECTION_SNAPSHOT_PATH = Path("state/royal_collections.json")
USER_AGENT = "Mozilla/5.0 (compatible; RoyalMonitor/3.0; +https://github.com/KhganaHajizada/royal-price-monitor)"
TELEGRAM_MAX = 3500
SEO_SEED_PER_RUN = 150


def http_text(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*",
            "Accept-Language": "az,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def http_json(url, timeout=30):
    return json.loads(http_text(url, timeout=timeout))


def fetch_paginated(endpoint, key):
    items, seen, page = [], set(), 1
    while True:
        query = urllib.parse.urlencode({"limit": 250, "page": page})
        data = http_json(f"{endpoint}?{query}")
        batch = data.get(key, [])
        if not batch:
            break
        fresh = []
        for item in batch:
            iid = str(item.get("id", ""))
            if iid and iid not in seen:
                seen.add(iid)
                fresh.append(item)
        if not fresh:
            break
        items.extend(fresh)
        if len(batch) < 250:
            break
        page += 1
        if page > 100:
            raise RuntimeError("Pagination safety limit exceeded")
        time.sleep(0.2)
    return items


def fetch_products():
    last_error = None
    for endpoint in [f"{BASE_URL}/products.json", f"{BASE_URL}/collections/all/products.json"]:
        try:
            products = fetch_paginated(endpoint, "products")
            if products:
                return products
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Royal.az catalogue could not be fetched: {last_error}")


def fetch_collections():
    try:
        return fetch_paginated(f"{BASE_URL}/collections.json", "collections")
    except Exception as exc:
        print(f"Collection endpoint alınmadı: {exc}")
        return []


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
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", str(value), flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def short(value, limit=500):
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def changed_fragments(old_text, new_text, limit=700):
    old_words = re.findall(r"\S+", old_text or "")
    new_words = re.findall(r"\S+", new_text or "")
    matcher = difflib.SequenceMatcher(a=old_words, b=new_words)
    removed, added = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("delete", "replace") and i1 != i2:
            removed.append(" ".join(old_words[i1:i2]))
        if tag in ("insert", "replace") and j1 != j2:
            added.append(" ".join(new_words[j1:j2]))
    r = short(" … ".join(removed), limit) if removed else "—"
    a = short(" … ".join(added), limit) if added else "—"
    return r, a


def text_change_message(icon, label, title, url, old_text, new_text):
    removed, added = changed_fragments(old_text, new_text)
    return (
        f"{icon} {label}\n{title}\n"
        f"➖ Silindi / dəyişdi: {removed}\n"
        f"➕ Əlavə olundu / yeni: {added}\n"
        f"🔗 {url}"
    )


def extract_meta(html_text):
    title = ""
    description = ""

    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.I | re.S)
    if m:
        title = clean_text(m.group(1))

    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\'][^>]*>',
        r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\'][^>]*>',
    ]
    for pattern in patterns:
        m = re.search(pattern, html_text, flags=re.I | re.S)
        if m:
            description = clean_text(m.group(1))
            break

    return {"meta_title": title, "meta_description": description}


def fetch_page_meta(url):
    return extract_meta(http_text(url))


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
        "updated_at": product.get("updated_at") or "",
        "variants": variants,
    }


def collection_snapshot(collection):
    handle = collection.get("handle") or ""
    return {
        "id": str(collection.get("id", "")),
        "title": collection.get("title") or "Adsız kolleksiya",
        "handle": handle,
        "url": f"{BASE_URL}/collections/{handle}" if handle else BASE_URL,
        "description": clean_text(collection.get("body_html")),
        "updated_at": collection.get("updated_at") or "",
        "published_at": collection.get("published_at") or "",
    }


def build_snapshot(items, builder):
    result = {}
    for item in items:
        snap = builder(item)
        if snap["id"]:
            result[snap["id"]] = snap
    return result


def load_json(path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
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


def list_diff(old_list, new_list):
    old_set, new_set = set(old_list or []), set(new_list or [])
    removed = sorted(old_set - new_set)
    added = sorted(new_set - old_set)
    return ", ".join(removed) or "—", ", ".join(added) or "—"


def detect_product_changes(old, new, seo_state):
    changes = []
    old_ids, new_ids = set(old), set(new)

    for pid in sorted(new_ids - old_ids):
        p = new[pid]
        prices = [v.get("price") for v in p.get("variants", {}).values() if v.get("price") is not None]
        price_text = money(prices[0]) if len(set(prices)) == 1 and prices else (" / ".join(money(x) for x in prices[:3]) if prices else "—")
        changes.append(f"🆕 YENİ MƏHSUL\n{p['title']}\nQiymət: {price_text}\nBrend: {p.get('vendor') or '—'}\n🔗 {p['url']}")
        try:
            seo_state[pid] = fetch_page_meta(p["url"])
        except Exception as exc:
            print(f"SEO baseline alınmadı {p['url']}: {exc}")

    for pid in sorted(old_ids - new_ids):
        p = old[pid]
        changes.append(f"🗑️ KATALOQDAN / SATIŞDAN ÇIXDI\n{p['title']}\n🔗 {p['url']}")
        seo_state.pop(pid, None)

    for pid in sorted(old_ids & new_ids):
        before, after = old[pid], new[pid]
        title, url = after["title"], after["url"]

        if "title" in before and before.get("title") != after.get("title"):
            changes.append(f"✏️ MƏHSUL ADI DƏYİŞDİ\nƏvvəl: {before.get('title')}\nİndi: {after.get('title')}\n🔗 {url}")
        if "description" in before and before.get("description") != after.get("description"):
            changes.append(text_change_message("📝", "AÇIQLAMADA DƏYİŞİKLİK", title, url, before.get("description", ""), after.get("description", "")))
        if "vendor" in before and before.get("vendor") != after.get("vendor"):
            changes.append(f"🏷️ BREND / VENDOR DƏYİŞDİ\n{title}\nƏvvəl: {before.get('vendor') or '—'}\nİndi: {after.get('vendor') or '—'}\n🔗 {url}")
        if "product_type" in before and before.get("product_type") != after.get("product_type"):
            changes.append(f"🗂️ KATEQORİYA / PRODUCT TYPE DƏYİŞDİ\n{title}\nƏvvəl: {before.get('product_type') or '—'}\nİndi: {after.get('product_type') or '—'}\n🔗 {url}")
        if "tags" in before and before.get("tags") != after.get("tags"):
            removed, added = list_diff(before.get("tags"), after.get("tags"))
            changes.append(f"🔖 TAG DƏYİŞİKLİYİ\n{title}\n➖ Silindi: {removed}\n➕ Əlavə olundu: {added}\n🔗 {url}")
        if "images" in before and before.get("images") != after.get("images"):
            removed, added = list_diff(before.get("images"), after.get("images"))
            changes.append(f"🖼️ ŞƏKİL DƏYİŞİKLİYİ\n{title}\n➖ Silinən: {short(removed, 450)}\n➕ Əlavə edilən: {short(added, 450)}\n🔗 {url}")
        if "options" in before and before.get("options") != after.get("options"):
            changes.append(text_change_message("⚙️", "MƏHSUL SEÇİMLƏRİ DƏYİŞDİ", title, url, json.dumps(before.get("options") or [], ensure_ascii=False), json.dumps(after.get("options") or [], ensure_ascii=False)))
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

        if before.get("updated_at") and before.get("updated_at") != after.get("updated_at"):
            try:
                current_meta = fetch_page_meta(url)
                previous_meta = seo_state.get(pid)
                if previous_meta:
                    if previous_meta.get("meta_title") != current_meta.get("meta_title"):
                        changes.append(text_change_message("🔎", "SEO META TITLE DƏYİŞDİ", title, url, previous_meta.get("meta_title", ""), current_meta.get("meta_title", "")))
                    if previous_meta.get("meta_description") != current_meta.get("meta_description"):
                        changes.append(text_change_message("🔎", "SEO META DESCRIPTION DƏYİŞDİ", title, url, previous_meta.get("meta_description", ""), current_meta.get("meta_description", "")))
                seo_state[pid] = current_meta
            except Exception as exc:
                print(f"Product SEO yoxlanmadı {url}: {exc}")

    return changes


def seed_product_seo(current, seo_state):
    missing = [pid for pid in current if pid not in seo_state][:SEO_SEED_PER_RUN]
    if not missing:
        return
    print(f"SEO baseline: {len(missing)} məhsul")
    for pid in missing:
        p = current[pid]
        try:
            seo_state[pid] = fetch_page_meta(p["url"])
        except Exception as exc:
            print(f"SEO baseline alınmadı {p['url']}: {exc}")
        time.sleep(0.05)


def detect_collection_changes(old, new):
    changes = []
    old_ids, new_ids = set(old), set(new)

    for cid in sorted(new_ids - old_ids):
        c = new[cid]
        try:
            c.update(fetch_page_meta(c["url"]))
        except Exception as exc:
            print(f"Collection SEO baseline alınmadı {c['url']}: {exc}")
        changes.append(f"🆕 YENİ KATALOQ / KOLLEKSİYA\n{c['title']}\n🔗 {c['url']}")

    for cid in sorted(old_ids - new_ids):
        c = old[cid]
        changes.append(f"🗑️ KATALOQ / KOLLEKSİYA SİLİNDİ\n{c['title']}\n🔗 {c['url']}")

    for cid in sorted(old_ids & new_ids):
        before, after = old[cid], new[cid]
        title, url = after["title"], after["url"]
        if before.get("title") != after.get("title"):
            changes.append(f"✏️ KATALOQ ADI DƏYİŞDİ\nƏvvəl: {before.get('title')}\nİndi: {after.get('title')}\n🔗 {url}")
        if before.get("description") != after.get("description"):
            changes.append(text_change_message("📝", "KATALOQ AÇIQLAMASI DƏYİŞDİ", title, url, before.get("description", ""), after.get("description", "")))
        if before.get("handle") != after.get("handle"):
            changes.append(f"🔗 KATALOQ URL-i DƏYİŞDİ\n{title}\nƏvvəl: {before.get('url')}\nİndi: {after.get('url')}")
        if before.get("published_at") != after.get("published_at"):
            changes.append(f"🌐 KATALOQ YAYIM STATUSU DƏYİŞDİ\n{title}\nƏvvəl: {before.get('published_at') or '—'}\nİndi: {after.get('published_at') or '—'}\n🔗 {url}")

        if before.get("updated_at") and before.get("updated_at") != after.get("updated_at"):
            try:
                current_meta = fetch_page_meta(url)
                old_meta_title = before.get("meta_title")
                old_meta_desc = before.get("meta_description")
                if old_meta_title is not None and old_meta_title != current_meta.get("meta_title"):
                    changes.append(text_change_message("🔎", "KATALOQ SEO META TITLE DƏYİŞDİ", title, url, old_meta_title, current_meta.get("meta_title", "")))
                if old_meta_desc is not None and old_meta_desc != current_meta.get("meta_description"):
                    changes.append(text_change_message("🔎", "KATALOQ SEO META DESCRIPTION DƏYİŞDİ", title, url, old_meta_desc, current_meta.get("meta_description", "")))
                after.update(current_meta)
            except Exception as exc:
                print(f"Collection SEO yoxlanmadı {url}: {exc}")
        else:
            if "meta_title" in before:
                after["meta_title"] = before.get("meta_title", "")
                after["meta_description"] = before.get("meta_description", "")

    return changes


def seed_collection_seo(current):
    for cid, c in current.items():
        if "meta_title" in c:
            continue
        try:
            c.update(fetch_page_meta(c["url"]))
        except Exception as exc:
            print(f"Collection SEO baseline alınmadı {c['url']}: {exc}")
        time.sleep(0.05)


def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
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
    print("Royal.az məhsullar və kataloqlar yoxlanılır...")
    products = build_snapshot(fetch_products(), product_snapshot)
    collections = build_snapshot(fetch_collections(), collection_snapshot)
    print(f"Məhsul: {len(products)} | Kataloq: {len(collections)}")

    if not products:
        raise RuntimeError("0 məhsul tapıldı; snapshot yenilənmədi")

    previous_products = load_json(PRODUCT_SNAPSHOT_PATH)
    previous_collections = load_json(COLLECTION_SNAPSHOT_PATH, {})
    seo_state = load_json(PRODUCT_SEO_PATH, {})

    if previous_products is None:
        seed_product_seo(products, seo_state)
        seed_collection_seo(collections)
        save_json(PRODUCT_SNAPSHOT_PATH, products)
        save_json(PRODUCT_SEO_PATH, seo_state)
        save_json(COLLECTION_SNAPSHOT_PATH, collections)
        telegram_send(
            "✅ Royal.az monitor aktivdir.\n"
            f"İlkin baza yaradıldı: {len(products)} məhsul, {len(collections)} kataloq.\n"
            "Bundan sonrakı yoxlamalarda yalnız dəyişiklik olarsa bildiriş gələcək."
        )
        return

    if len(previous_products) >= 20 and len(products) < int(len(previous_products) * 0.70):
        raise RuntimeError(
            f"Safety stop: əvvəl {len(previous_products)}, indi yalnız {len(products)} məhsul gəldi. Snapshot dəyişdirilmədi."
        )

    changes = []
    changes.extend(detect_product_changes(previous_products, products, seo_state))
    changes.extend(detect_collection_changes(previous_collections, collections))

    seed_product_seo(products, seo_state)
    seed_collection_seo(collections)

    if changes:
        print(f"{len(changes)} dəyişiklik tapıldı.")
        send_changes(changes)
    else:
        print("Dəyişiklik yoxdur — Telegram-a mesaj göndərilmir.")

    save_json(PRODUCT_SNAPSHOT_PATH, products)
    save_json(PRODUCT_SEO_PATH, seo_state)
    save_json(COLLECTION_SNAPSHOT_PATH, collections)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
