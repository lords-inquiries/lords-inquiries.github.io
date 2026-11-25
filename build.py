#!/usr/bin/env python3
"""Build a static page of open House of Lords committee inquiries and calls for evidence.

Data comes from the UK Parliament Committees API (https://committees-api.parliament.uk).
Uses only the Python standard library.

Usage:
    python build.py                      # fetch live data, write site/
    python build.py --sample tests/sample.json   # build from saved API responses (offline test)
    python build.py --save-raw raw.json  # also save the raw API responses for debugging
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from email.utils import format_datetime
from zoneinfo import ZoneInfo

API = "https://committees-api.parliament.uk/api"
LONDON = ZoneInfo("Europe/London")
UTC = dt.timezone.utc
REPO_URL = os.environ.get("REPO_URL", "https://github.com/lords-inquiries/lords-inquiries.github.io")
SITE_URL = os.environ.get("SITE_URL", "https://lords-inquiries.github.io/")
MIN_ITEMS = 10  # refuse to publish a suspiciously empty page
HERE = os.path.dirname(os.path.abspath(__file__))


# ---------- fetching ----------

def get_json(path, params, raw_log=None):
    url = f"{API}/{path}?{urllib.parse.urlencode(params, doseq=True)}"
    last_err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "lords-inquiries/1.0 (+%s)" % REPO_URL,
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            if raw_log is not None:
                raw_log[url] = data
            return data
        except Exception as e:  # network blip or 5xx: back off and retry
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def get_all(path, params, raw_log=None, page=100):
    items, skip = [], 0
    while True:
        data = get_json(path, {**params, "Take": page, "Skip": skip}, raw_log)
        batch = data.get("items") or []
        items.extend(batch)
        total = data.get("totalResults", len(items))
        skip += page
        if not batch or skip >= total:
            return items


class Live:
    def __init__(self):
        self.raw = {}

    def committees(self, house):
        return get_all("Committees", {"House": house}, self.raw)

    def business(self, committee_id):
        return get_all("CommitteeBusiness", {"CommitteeId": committee_id, "Status": "Open"}, self.raw)


class Sample:
    """Replays saved responses: {"committees": {"Lords": [...], "Joint": [...]}, "business": {"193": [...]}}"""

    def __init__(self, path):
        with open(path) as f:
            self.d = json.load(f)
        self.raw = {}

    def committees(self, house):
        return self.d["committees"].get(house, [])

    def business(self, committee_id):
        return self.d["business"].get(str(committee_id), [])


# ---------- shaping ----------

def parse_dt(s):
    """API datetimes are UK local time with no offset. Return an aware UTC datetime."""
    if not s:
        return None
    s = s.rstrip("Z")
    d = dt.datetime.fromisoformat(s[:19])
    return d.replace(tzinfo=LONDON).astimezone(UTC)


def from_utc(s):
    return dt.datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=UTC)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def day(s):
    return s[:10] if s else None


REPORT_PREFIX = re.compile(r"^\s*(\d+(st|nd|rd|th)\s+)?report\s*[-–—:]\s*", re.I)


def report_of(b):
    lr = b.get("latestReport") or {}
    if not isinstance(lr, dict):
        return None, None
    title = lr.get("description") or lr.get("title") or lr.get("name")
    date = lr.get("publicationStartDate") or lr.get("publicationDate") or lr.get("date")
    if title:
        title = REPORT_PREFIX.sub("", title).strip() or title
    return title, day(date)


def written(p):
    t = (p.get("submissionType") or "WrittenEvidence")
    t = t.get("name", "") if isinstance(t, dict) else str(t)
    return "written" in t.lower() or "evidence" in t.lower()


def shape(b, committee_id, now):
    btype = b.get("type") or {}
    is_inquiry = bool(btype.get("isInquiry")) or (btype.get("name") == "Inquiry")
    open_periods = [p for p in (b.get("openSubmissionPeriods") or []) if written(p)]
    live = []
    for p in open_periods:
        end = parse_dt(p.get("endDate"))
        if end is None or end > now:
            live.append(end)
    if not is_inquiry and not live:
        return None  # one-off sessions, regular sessions, bill scrutiny with no call
    item = {"i": b["id"], "t": (b.get("title") or "").strip(), "c": committee_id, "o": day(b.get("openDate"))}
    if not is_inquiry and btype.get("name"):
        item["k"] = btype["name"]
    if live:
        dated = [e for e in live if e]
        item["d"] = iso(min(dated)) if dated else "none"
    else:
        closed = [parse_dt(p.get("endDate")) for p in (b.get("closedSubmissionPeriods") or []) if written(p)]
        closed = [c for c in closed if c]
        if closed:
            item["x"] = max(closed).astimezone(LONDON).strftime("%Y-%m-%d")
    r, rd = report_of(b)
    if r:
        item["r"], item["rd"] = r, rd
    return item


def build_data(src, now):
    committees = {}
    for house in ("Lords", "Joint"):
        for c in src.committees(house):
            end = parse_dt(c.get("endDate"))
            if end and end < now:
                continue  # dissolved
            committees[c["id"]] = [c.get("name", "").strip(), 1 if house == "Joint" else 0]

    items, seen = [], {}
    for cid in sorted(committees):
        for b in src.business(cid):
            if b.get("id") in seen:  # same inquiry run jointly or by a sub-committee: credit both
                seen[b["id"]].setdefault("a", []).append(cid)
                continue
            it = shape(b, cid, now)
            if it:
                seen[it["i"]] = it
                items.append(it)

    used = {it["c"] for it in items} | {a for it in items for a in it.get("a", [])}
    return {
        "generated": iso(now),
        "source": API,
        "committees": {str(k): v for k, v in committees.items() if k in used},
        "items": sorted(items, key=lambda x: x["o"] or "", reverse=True),
    }


# ---------- output ----------

def is_open(it, now):
    return it.get("d") == "none" or (it.get("d") and from_utc(it["d"]) > now)


def rss(data, now):
    def work(i):
        return f"https://committees.parliament.uk/work/{i}/"
    entries = []
    for it in data["items"]:
        if not it.get("d"):
            continue
        cname = " / ".join(data["committees"][str(c)][0] for c in [it["c"], *it.get("a", [])])
        when = "No closing date listed" if it["d"] == "none" else "Closes " + from_utc(it["d"]).astimezone(LONDON).strftime("%-d %B %Y, %H:%M")
        opened = dt.datetime.fromisoformat(it["o"]).replace(tzinfo=UTC) if it.get("o") else now
        entries.append(
            "<item><title>{t}</title><link>{l}</link><guid isPermaLink=\"false\">call-{i}</guid>"
            "<description>{d}</description><pubDate>{p}</pubDate></item>".format(
                t=html.escape(f"{it['t']} ({cname})"), l=work(it["i"]), i=it["i"],
                d=html.escape(f"{cname}. {when}."), p=format_datetime(opened)))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            "<title>Open House of Lords calls for evidence</title>"
            f"<link>{html.escape(SITE_URL)}</link>"
            "<description>Lords and joint committee calls for evidence currently accepting written submissions. Unofficial.</description>"
            f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>" + "".join(entries) + "</channel></rss>\n")


def write_site(data, out, now):
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(HERE, "template.html")) as f:
        tpl = f.read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = tpl.replace("/*__DATA__*/null", blob).replace("__REPO__", html.escape(REPO_URL))
    with open(os.path.join(out, "index.html"), "w") as f:
        f.write(page)
    with open(os.path.join(out, "data.json"), "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "feed.xml"), "w") as f:
        f.write(rss(data, now))
    open(os.path.join(out, ".nojekyll"), "w").close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", help="build from a saved sample file instead of the live API")
    ap.add_argument("--out", default="site")
    ap.add_argument("--now", help="pretend the current time is this ISO datetime (UTC), for testing")
    ap.add_argument("--save-raw", help="save raw API responses to this file")
    a = ap.parse_args()

    now = dt.datetime.fromisoformat(a.now.rstrip("Z")).replace(tzinfo=UTC) if a.now else dt.datetime.now(UTC).replace(microsecond=0)
    src = Sample(a.sample) if a.sample else Live()
    data = build_data(src, now)
    if a.save_raw:
        with open(a.save_raw, "w") as f:
            json.dump(src.raw, f)

    n_open = sum(1 for it in data["items"] if is_open(it, now))
    print(f"{len(data['items'])} current inquiries, {n_open} open calls, {len(data['committees'])} committees")
    if len(data["items"]) < MIN_ITEMS:
        sys.exit(f"Only {len(data['items'])} items found: refusing to publish (the API may be down or changed).")
    write_site(data, a.out, now)
    print(f"Wrote {a.out}/index.html, data.json, feed.xml")


if __name__ == "__main__":
    main()
