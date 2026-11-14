"""PeeK free collector — runs daily on GitHub Actions and updates products.json for the PeeK site.

What it does each run:
  * Reads your keywords from seeds.txt (falls back to a built-in list) + trending posts from Reddit.
  * Pulls 16 weeks of Google Trends for 12 markets (worldwide is used as the China proxy).
  * Optional: with a META_TOKEN secret, counts active Meta ads (worldwide + Tunisia) and saves the top real ads.
  * MERGES into the existing products.json: nothing is ever deleted. Each product keeps
    first_seen / last_seen / runs, a growing list of sources (where it was found) and its saved ads.
    Fields you edit in PeeK are stored in your browser and are never overwritten.
"""
import json, os, re, time, random, datetime, requests
from pytrends.request import TrendReq

TODAY = datetime.date.today().isoformat()
UA = {"User-Agent": "PeeK-collector/2.0"}
SUBS = ["shutupandtakemymoney", "BuyItForLife", "gadgets", "INEEEEDIT", "DidntKnowIWantedThat"]
DEFAULT_SEEDS = ["neck fan", "heated lunch box", "label printer", "spin scrubber", "garment steamer",
                 "kids drawing projector", "ice roller", "bakhoor burner", "blanket hoodie", "oil sprayer bottle"]
GEOS = {"US": "US", "UK": "GB", "DE": "DE", "FR": "FR", "ES": "ES", "IT": "IT", "CN": "",
        "JP": "JP", "KR": "KR", "TR": "TR", "AE": "AE", "BR": "BR"}
MAX_KEYWORDS = 25          # 12 markets x 25 keywords stays well inside GitHub's free 6h job limit
MAX_SOURCES = 25           # per product, newest kept
STOP = set("the a an this that my for with of and to in on is it i you new best".split())


def load_existing():
    if not os.path.exists("products.json"):
        return {}
    try:
        data = json.load(open("products.json", encoding="utf-8"))
        items = data["products"] if isinstance(data, dict) else data
        return {p.get("query", p["name"]).lower(): p for p in items}
    except Exception as e:
        print("could not read existing products.json:", e)
        return {}


def seed_candidates():
    seeds = ([l.strip() for l in open("seeds.txt", encoding="utf-8") if l.strip()]
             if os.path.exists("seeds.txt") else DEFAULT_SEEDS)
    return {s.lower(): [{"type": "seed", "title": "From your keyword list (seeds.txt)", "date": TODAY}] for s in seeds}


def reddit_candidates():
    out = {}
    for s in SUBS:
        try:
            r = requests.get(f"https://www.reddit.com/r/{s}/top.json?t=week&limit=50", headers=UA, timeout=20)
            for c in r.json()["data"]["children"]:
                d = c["data"]
                t = re.sub(r"\[.*?\]|\(.*?\)", "", d["title"]).strip()
                words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]+", t) if w.lower() not in STOP]
                if 1 < len(words) <= 6:
                    k = " ".join(words[:4]).lower()
                    img = ""
                    try:
                        img = d["preview"]["images"][0]["source"]["url"].replace("&amp;", "&")
                    except Exception:
                        pass
                    out.setdefault(k, []).append({
                        "type": "reddit", "title": f"r/{s}: {d['title'][:120]}",
                        "url": "https://www.reddit.com" + d["permalink"],
                        "note": f"{d['score']} upvotes", "date": TODAY, "image": img})
        except Exception as e:
            print("reddit blocked/failed:", s, e)
        time.sleep(2)
    return out


def trends(pt, kw, geo):
    try:
        pt.build_payload([kw], timeframe="today 12-m", geo=geo)
        df = pt.interest_over_time()
        if df.empty:
            return None
        weekly = df[kw].tail(16).round().astype(int).tolist()
        return weekly if len(weekly) >= 8 else None
    except Exception as e:
        print("trends", kw, geo or "WW", e)
        time.sleep(30)
        return None


def meta_ads(kw, country):
    """Returns (active_ad_count, oldest_ad_age_days, top_ads) or None without a token."""
    tok = os.getenv("META_TOKEN")
    if not tok:
        return None
    try:
        r = requests.get("https://graph.facebook.com/v21.0/ads_archive", timeout=30, params={
            "access_token": tok, "search_terms": kw, "ad_reached_countries": json.dumps([country]),
            "ad_active_status": "ACTIVE", "limit": 200,
            "fields": "id,page_name,ad_delivery_start_time,ad_snapshot_url"})
        ads = r.json().get("data", [])
        ages, top = [], []
        for a in ads:
            st = (a.get("ad_delivery_start_time") or "")[:10]
            if st:
                ages.append((datetime.date.today() - datetime.date.fromisoformat(st)).days)
        # longest-running first = most likely profitable
        for a in sorted(ads, key=lambda a: a.get("ad_delivery_start_time") or "9999")[:5]:
            top.append({"page": a.get("page_name"), "start": (a.get("ad_delivery_start_time") or "")[:10],
                        "country": country,
                        "url": f"https://www.facebook.com/ads/library/?id={a['id']}"})
        return len(ads), (max(ages) if ages else 0), top
    except Exception as e:
        print("meta", e)
        return None


def add_sources(p, new):
    have = {(s.get("type"), s.get("url"), s.get("title")) for s in p.get("sources", [])}
    for s in new:
        if s.get("image") and not p.get("image"):
            p["image"] = s["image"]
        s = {k: v for k, v in s.items() if k != "image"}
        if (s.get("type"), s.get("url"), s.get("title")) not in have:
            p.setdefault("sources", []).append(s)
    p["sources"] = p.get("sources", [])[-MAX_SOURCES:]


def main():
    db = load_existing()
    cands = seed_candidates()
    for k, src in reddit_candidates().items():
        cands.setdefault(k, []).extend(src)
    # new candidates first, then refresh the saved products that were updated longest ago
    new = [k for k in cands if k not in db]
    old = sorted(db, key=lambda k: db[k].get("last_seen", ""))
    order = list(dict.fromkeys(new[:12] + old))[:MAX_KEYWORDS]
    print(f"library: {len(db)} saved | candidates: {len(cands)} | checking {len(order)} this run")

    pt = TrendReq(hl="en-US", tz=0)
    updated = 0
    for kw in order:
        tr = {}
        for code, geo in GEOS.items():
            tr[code] = trends(pt, kw, geo)
            time.sleep(random.uniform(4, 8))  # be polite, avoid 429
        if not tr["US"]:
            print("no trend data:", kw, "(kept as before)" if kw in db else "(skipped)")
            continue
        tr = {k: (v or tr["US"]) for k, v in tr.items()}
        origin = max(tr, key=lambda k: sum(tr[k][-4:]) - sum(tr[k][-8:-4]))
        p = db.get(kw) or {"name": kw.title(), "query": kw, "cat": "Uncategorized", "hooks": [],
                           "first_seen": TODAY, "sources": [], "runs": 0}
        p.update({"source": "collector", "origin": origin, "trend": tr, "last_seen": TODAY,
                  "runs": p.get("runs", 0) + 1})
        q = requests.utils.quote(kw)
        add_sources(p, cands.get(kw, []) + [{
            "type": "trends", "title": "Google Trends: rising in " + origin,
            "url": f"https://trends.google.com/trends/explore?date=today%2012-m&q={q}", "date": TODAY}])
        ww, tn = meta_ads(kw, "US"), meta_ads(kw, "TN")
        if ww:
            p["ads"], p["adDays"], p["adSamples"] = ww[0], ww[1], ww[2]
            add_sources(p, [{"type": "meta", "title": f"Meta Ad Library: {ww[0]} active ads (US)",
                             "url": f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&q={q}",
                             "note": f"oldest ad running {ww[1]} days", "date": TODAY}])
        if tn:
            p["tnSellers"] = tn[0]
            p["adSamples"] = (p.get("adSamples") or []) + tn[2][:3]
        db[kw] = p
        updated += 1
        print("ok", kw, "origin", origin)

    print(f"updated {updated} products | library now holds {len(db)}")
    if updated == 0:
        raise SystemExit("No trend data collected (Google Trends rate-limited or blocked). "
                         "Your saved products.json was NOT changed; re-run later.")
    items = sorted(db.values(), key=lambda p: p.get("last_seen", ""), reverse=True)
    json.dump({"updated": TODAY, "count": len(items), "products": items},
              open("products.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
