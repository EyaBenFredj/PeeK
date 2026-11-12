"""PeeK free collector — runs daily on GitHub Actions, writes products.json for the PeeK app (12 source markets).
Sources: Google Trends (11 countries + worldwide as China proxy) via pytrends + Reddit product subreddits (public JSON).
Optional: Meta Ad Library API (set META_TOKEN secret) for active-ad counts incl. Tunisia (country=TN).
Usage: pip install pytrends requests && python collector.py
"""
import json, os, re, time, random, requests
from pytrends.request import TrendReq

UA = {"User-Agent": "PeeK-collector/1.0"}
SUBS = ["shutupandtakemymoney", "BuyItForLife", "gadgets", "INEEEEDIT", "DidntKnowIWantedThat"]
SEED = [l.strip() for l in open("seeds.txt")] if os.path.exists("seeds.txt") else []
MAX_KEYWORDS = 25  # 12 markets x 25 keywords stays well inside GitHub's free 6h job limit
STOP = set("the a an this that my for with of and to in on is it i you new best".split())

def reddit_candidates():
    out = {}
    for s in SUBS:
        try:
            r = requests.get(f"https://www.reddit.com/r/{s}/top.json?t=week&limit=50", headers=UA, timeout=20)
            for c in r.json()["data"]["children"]:
                t = re.sub(r"\[.*?\]|\(.*?\)", "", c["data"]["title"]).strip()
                words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]+", t) if w.lower() not in STOP]
                if 1 < len(words) <= 6:
                    k = " ".join(words[:4]).lower()
                    out[k] = max(out.get(k, 0), c["data"]["score"])
        except Exception as e:
            print("reddit", s, e)
        time.sleep(2)
    return [k for k, _ in sorted(out.items(), key=lambda x: -x[1])]

def trends(pt, kw, geo):
    try:
        pt.build_payload([kw], timeframe="today 12-m", geo=geo)
        df = pt.interest_over_time()
        if df.empty:
            return None
        weekly = df[kw].tail(16).round().astype(int).tolist()  # 12-month range is weekly already
        return weekly if len(weekly) >= 8 else None
    except Exception as e:
        print("trends", kw, geo, e)
        time.sleep(30)
        return None

def meta_ads(kw, country):
    tok = os.getenv("META_TOKEN")
    if not tok:
        return None
    try:
        r = requests.get("https://graph.facebook.com/v21.0/ads_archive", timeout=30, params={
            "access_token": tok, "search_terms": kw, "ad_reached_countries": json.dumps([country]),
            "ad_active_status": "ACTIVE", "fields": "id,ad_delivery_start_time", "limit": 200})
        return len(r.json().get("data", []))
    except Exception as e:
        print("meta", e)
        return None

def main():
    kws = list(dict.fromkeys(SEED + reddit_candidates()))[:MAX_KEYWORDS]
    pt = TrendReq(hl="en-US", tz=0)
    prods = []
    for kw in kws:
        # PeeK market code -> Google geo. China has no Google, so worldwide ("") is used as a proxy.
        GEOS = {"US": "US", "UK": "GB", "DE": "DE", "FR": "FR", "ES": "ES", "IT": "IT", "CN": "",
                "JP": "JP", "KR": "KR", "TR": "TR", "AE": "AE", "BR": "BR"}
        tr = {}
        for code, geo in GEOS.items():
            tr[code] = trends(pt, kw, geo)
            time.sleep(random.uniform(4, 8))  # be polite, avoid 429
        if not tr["US"]:
            continue
        tr = {k: (v or tr["US"]) for k, v in tr.items()}
        origin = max(tr, key=lambda k: sum(tr[k][-4:]) - sum(tr[k][-8:-4]))
        p = {"name": kw.title(), "cat": "Uncategorized", "source": "collector", "origin": origin,
             "trend": tr, "hooks": [],
             "ads": meta_ads(kw, "US") or 0, "tnSellers": meta_ads(kw, "TN") or 0}
        prods.append(p)
        print("ok", kw)
    json.dump(prods, open("products.json", "w"), ensure_ascii=False, indent=1)
    print(len(prods), "products written")

if __name__ == "__main__":
    main()
