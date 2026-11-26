#!/usr/bin/env python3
"""Build a full-text search index of the Lords Science and Technology Committee's
oral and written evidence (BETA).

1. Lists every oral evidence transcript and written submission since 2016 from the
   UK Parliament Committees API.
2. Downloads each document's text once and keeps it in a cache folder, so later runs
   only fetch new documents. A time budget stops a run from taking too long; the
   next run carries on where it left off.
3. Writes one small HTML page per document into a temporary folder and indexes them
   with Pagefind (https://pagefind.app). Only the index is published, not the pages:
   search results link to the official documents on committees.parliament.uk.

Usage:
    python evidence_build.py --cache .evidence-cache --pages /tmp/evidence-pages --stats site/.../evidence-index/stats.json
    then: npx -y pagefind@1.3.0 --site /tmp/evidence-pages --output-path site/.../evidence-index
    python evidence_build.py --sample tests/evidence_sample.json ...   # offline test
"""
import argparse
import base64
import concurrent.futures as cf
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

API = "https://committees-api.parliament.uk/api"
COMMITTEE_ID = int(os.environ.get("ST_COMMITTEE_ID", "193"))
SINCE = "2016-01-01"
MAX_CHARS = 400_000  # per document, to keep the index a sensible size
CACHE_VERSION = 1


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, accept="application/json", timeout=60, tries=4):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "lords-inquiries/1.0 (evidence search)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                raise
            last = e
        except Exception as e:
            last = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def get_all(kind):
    out, skip = [], 0
    while True:
        q = urllib.parse.urlencode({"CommitteeId": COMMITTEE_ID, "StartDate": SINCE, "Take": 100, "Skip": skip})
        data = json.loads(get(f"{API}/{kind}?{q}"))
        items = data.get("items") or []
        out += items
        skip += 100
        if not items or skip >= data.get("totalResults", 0):
            return out


# ---------- HTML to text ----------

class TextOut(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "blockquote"}
    SKIP = {"style", "script", "head", "title", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(raw):
    p = TextOut()
    p.feed(raw)
    text = "".join(p.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
    text = "\n".join(l for l in lines if l)
    return text[:MAX_CHARS]


# ---------- sources ----------

class Live:
    def list(self, kind):
        return get_all(kind)

    def text(self, kind, item):
        files = ((item.get("document") or {}).get("files") or [])
        if any(f.get("fileDataFormat") == "Html" for f in files):
            payload = json.loads(get(f"{API}/{kind}/{item['id']}/Document/Html"))
            raw = base64.b64decode(payload.get("data") or "").decode("utf-8", "replace")
            return html_to_text(raw)
        legacy = item.get("legacyHtmlUrl")
        if legacy:
            raw = get(legacy, accept="text/html").decode("utf-8", "replace")
            return html_to_text(raw)
        return None


class Sample:
    def __init__(self, path):
        with open(path) as f:
            self.d = json.load(f)

    def list(self, kind):
        return self.d[kind]

    def text(self, kind, item):
        raw = self.d["docs"].get(f"{kind}/{item['id']}")
        return html_to_text(raw) if raw else None


# ---------- describing documents ----------

def who(item):
    names = []
    for w in item.get("witnesses") or []:
        orgs = [o for o in (w.get("organisations") or []) if o.get("name")]
        person = (w.get("name") or "").strip()
        if person and orgs:
            names.append(f"{person} ({orgs[0]['name']})")
        elif person:
            names.append(person)
        elif orgs:
            names.append(orgs[0]["name"])
        elif w.get("additionalContext"):
            names.append(w["additionalContext"])
    if item.get("anonymous"):
        names = names or ["Anonymous"]
    return list(dict.fromkeys(names))


def inquiries(item):
    bus = item.get("committeeBusinesses") or ([item["committeeBusiness"]] if item.get("committeeBusiness") else [])
    return [b.get("title") for b in bus if b and b.get("title")]


def describe(kind, item):
    oral = kind == "OralEvidence"
    date = (item.get("activityStartDate") or item.get("meetingDate") or item.get("publicationDate") or "")[:10]
    ws = who(item)
    inq = inquiries(item)
    if oral:
        title = "Oral evidence: " + (", ".join(ws[:2]) + (" and others" if len(ws) > 2 else "") if ws else "session")
        link = item.get("legacyHtmlUrl") or f"https://committees.parliament.uk/oralevidence/{item['id']}/html/"
    else:
        ref = item.get("internalReference") or ""
        title = (f"{ref}: " if ref else "") + (", ".join(ws[:2]) if ws else "Written evidence")
        link = item.get("legacyHtmlUrl") or f"https://committees.parliament.uk/writtenevidence/{item['id']}/html/"
    return {"title": title, "date": date, "witnesses": ws, "inquiry": " / ".join(inq), "link": link}


def page(kind, item, meta, text):
    e = html.escape
    paras = "\n".join(f"<p>{e(p)}</p>" for p in text.split("\n"))
    who_line = "; ".join(meta["witnesses"])
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{e(meta['title'])}</title></head><body>
<h1 data-pagefind-meta="title">{e(meta['title'])}</h1>
<span data-pagefind-filter="type">{'Oral' if kind == 'OralEvidence' else 'Written'}</span>
<span data-pagefind-meta="inquiry">{e(meta['inquiry'])}</span>
<span data-pagefind-meta="who">{e(who_line[:300])}</span>
<span data-pagefind-meta="date">{e(meta['date'])}</span>
<span data-pagefind-meta="link">{e(meta['link'])}</span>
<span data-pagefind-sort="date">{e(meta['date'])}</span>
<main data-pagefind-body>
{paras}
<p>{e(meta['inquiry'])}</p><p>{e(who_line)}</p>
</main></body></html>"""


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=".evidence-cache")
    ap.add_argument("--pages", required=True, help="temporary folder for pages to index")
    ap.add_argument("--stats", required=True, help="where to write stats.json")
    ap.add_argument("--budget", type=int, default=900, help="seconds to spend downloading this run")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--sample")
    a = ap.parse_args()

    src = Sample(a.sample) if a.sample else Live()
    start = time.time()
    stats = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "since": SINCE}
    os.makedirs(a.pages, exist_ok=True)

    for kind, short in (("OralEvidence", "oral"), ("WrittenEvidence", "written")):
        items = src.list(kind)
        cdir = os.path.join(a.cache, short)
        os.makedirs(cdir, exist_ok=True)

        def cached(item):
            path = os.path.join(cdir, f"{item['id']}.json")
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        d = json.load(f)
                    if d.get("v") == CACHE_VERSION:
                        return d
                except Exception:
                    pass
            return None

        todo = [it for it in items if cached(it) is None]
        log(f"{kind}: {len(items)} listed, {len(items) - len(todo)} cached, {len(todo)} to fetch")

        def fetch(item):
            if time.time() - start > a.budget:
                return item, "skipped"
            try:
                text = src.text(kind, item)
            except Exception as e:
                return item, f"error: {e}"
            with open(os.path.join(cdir, f"{item['id']}.json"), "w") as f:
                json.dump({"v": CACHE_VERSION, "text": text or ""}, f, ensure_ascii=False)
            return item, "ok" if text else "empty"

        counts = {}
        with cf.ThreadPoolExecutor(max_workers=a.workers) as pool:
            for item, status in pool.map(fetch, todo):
                key = status.split(":")[0]
                counts[key] = counts.get(key, 0) + 1
                if key == "error" and counts[key] <= 5:
                    log(f"  {kind} {item['id']}: {status[:200]}")
        log(f"  fetched this run: {counts}")

        indexed = 0
        for item in items:
            d = cached(item)
            if not d or not d.get("text"):
                continue
            meta = describe(kind, item)
            with open(os.path.join(a.pages, f"{short}-{item['id']}.html"), "w") as f:
                f.write(page(kind, item, meta, d["text"]))
            indexed += 1
        stats[short] = {"listed": len(items), "indexed": indexed}
        log(f"  {kind}: {indexed} of {len(items)} indexed")

    os.makedirs(os.path.dirname(a.stats), exist_ok=True)
    with open(a.stats, "w") as f:
        json.dump(stats, f)
    print(f"Evidence: oral {stats['oral']['indexed']}/{stats['oral']['listed']}, written {stats['written']['indexed']}/{stats['written']['listed']}")


if __name__ == "__main__":
    main()
