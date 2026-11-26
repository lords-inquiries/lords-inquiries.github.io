#!/usr/bin/env python3
"""Build a dashboard of House of Lords Science and Technology Committee reports.

For each report: the report itself, the inquiry's written and oral evidence,
the Government response(s), and any Lords debate on the report (found by
searching Hansard for "(Science and Technology Committee Report)" debate titles).

Sources: UK Parliament Committees API and Hansard API. Standard library only.

Usage:
    python st_build.py --out site/science-and-technology
    python st_build.py --sample tests/st_sample.json --out /tmp/st   # offline test
    python st_build.py --debug ...                                  # print raw API samples
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

COMMITTEES_API = "https://committees-api.parliament.uk/api"
HANSARD_API = "https://hansard-api.parliament.uk"
COMMITTEE_ID = int(os.environ.get("ST_COMMITTEE_ID", "193"))
COMMITTEE_NAME = "Science and Technology Committee"
SINCE = "2016-01-01"  # reports published on or after this date
HERE = os.path.dirname(os.path.abspath(__file__))
UTC = dt.timezone.utc
DEBUG = False


def log(*a):
    print(*a, file=sys.stderr)


def sample(label, obj):
    if DEBUG:
        log(f"--- {label} ---")
        log(json.dumps(obj, ensure_ascii=False)[:2500])


def get_json(url):
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "lords-inquiries/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def capi(path, **params):
    return get_json(f"{COMMITTEES_API}/{path}?{urllib.parse.urlencode(params, doseq=True)}")


def pick(d, *keys, default=None):
    """First present key, trying each name as given and with first letter swapped in case."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        for kk in (k, k[:1].upper() + k[1:], k[:1].lower() + k[1:]):
            if kk in d and d[kk] not in (None, ""):
                return d[kk]
    return default


class Live:
    def publications(self, type_ids):
        out, skip = [], 0
        while True:
            data = capi("Publications", CommitteeId=COMMITTEE_ID, PublicationTypeIds=type_ids,
                        StartDate=SINCE, SortOrder="PublicationDateDescending", Take=100, Skip=skip)
            items = data.get("items") or []
            if skip == 0 and items:
                sample(f"Publications types={type_ids} first item", items[0])
            out += items
            skip += 100
            if not items or skip >= data.get("totalResults", 0):
                return out

    def inquiries(self):
        out, skip = [], 0
        while True:
            data = capi("CommitteeBusiness", CommitteeId=COMMITTEE_ID, Take=100, Skip=skip)
            items = data.get("items") or []
            out += items
            skip += 100
            if not items or skip >= data.get("totalResults", 0):
                return out

    def evidence_count(self, kind, business_id):
        data = capi(kind, CommitteeBusinessId=business_id, Take=1)
        return data.get("totalResults")

    def hansard_debates(self, term, start):
        """Search Lords debate titles in Hansard. Returns a list of raw result dicts."""
        results, skip, take = [], 0, 50
        while True:
            q = {
                "queryParameters.searchTerm": term,
                "queryParameters.house": "Lords",
                "queryParameters.startDate": start,
                "queryParameters.take": take,
                "queryParameters.skip": skip,
            }
            data = get_json(f"{HANSARD_API}/search/debates.json?{urllib.parse.urlencode(q)}")
            if skip == 0:
                sample("Hansard debates search (top-level keys)", list(data.keys()) if isinstance(data, dict) else type(data).__name__)
            batch = pick(data, "results", default=[]) if isinstance(data, dict) else (data or [])
            if skip == 0 and batch:
                sample("Hansard first result", batch[0])
            results += batch
            total = pick(data, "totalResultCount", default=0) if isinstance(data, dict) else 0
            skip += take
            if not batch or skip >= (total or 0) or skip > 500:
                return results


class Sample:
    def __init__(self, path):
        with open(path) as f:
            self.d = json.load(f)

    def publications(self, type_ids):
        return [p for t in type_ids for p in self.d["publications"].get(str(t), [])]

    def inquiries(self):
        return self.d.get("business", [])

    def evidence_count(self, kind, business_id):
        return self.d["evidence"].get(kind, {}).get(str(business_id))

    def hansard_debates(self, term, start):
        return self.d.get("hansard", [])


# ---------- helpers ----------

def day(s):
    return s[:10] if isinstance(s, str) else None


PREFIX = re.compile(r"^\s*(\d+(st|nd|rd|th)\s+)?(special\s+)?report\s*(of session [\d–-]+)?\s*[-–—:]\s*", re.I)


def clean_title(s):
    s = (s or "").strip()
    return PREFIX.sub("", s).strip() or s


def paper(p):
    v = pick(p, "hlPaper")
    if isinstance(v, dict):
        n = pick(v, "number", "paperNumber")
        sess = pick(v, "sessionDescription", "session", "sessionName")
        if n:
            n = str(n)
            label = n if n.lower().startswith("hl") else f"HL Paper {n}"
            return label + (f" ({sess})" if isinstance(sess, str) else "")
        return None
    if v:
        return f"HL Paper {v}"
    return None


def pub_url(p):
    url = pick(p, "additionalContentUrl")
    if url:
        return url
    docs = pick(p, "documents", default=[]) or []
    for d in docs:
        doc_id = pick(d, "documentId", "id")
        if doc_id:
            return f"https://committees.parliament.uk/publications/{p['id']}/documents/{doc_id}/default/"
    return f"https://committees.parliament.uk/committee/{COMMITTEE_ID}/science-and-technology-committee/publications/"


def dept(p):
    d = pick(p, "respondingDepartment")
    if isinstance(d, dict):
        return pick(d, "name", "title")
    return d if isinstance(d, str) else None


def ref_ids(v):
    out = []
    for x in v or []:
        if isinstance(x, int):
            out.append(x)
        elif isinstance(x, dict) and pick(x, "id"):
            out.append(pick(x, "id"))
    return out


STOP = set("a an and the of in on for to with by from at is are be its it's more than not get uk science technology committee report".split())


def words(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in STOP and len(w) > 2}


def debate_title_core(t):
    return re.sub(r"\(\s*science\s+and\s+technology\s+committee\s+report\s*\)", "", t or "", flags=re.I).strip(" -:")


def hansard_link(r):
    ext = pick(r, "debateSectionExtId", "externalId", "extId")
    date = day(pick(r, "sittingDate", "date"))
    title = pick(r, "title", "debateSection", default="")
    if not (ext and date):
        return None
    slug = re.sub(r"[^A-Za-z0-9]+", "", title.title())[:80] or "Debate"
    return f"https://hansard.parliament.uk/Lords/{date}/debates/{ext}/{slug}"


def check_links(reports):
    """Debug only: confirm the link patterns resolve."""
    urls = []
    for r in reports:
        if r.get("bid") and r.get("we"):
            urls.append(f"https://committees.parliament.uk/work/{r['bid']}/default/publications/written-evidence/")
            urls.append(f"https://committees.parliament.uk/work/{r['bid']}/default/publications/oral-evidence/")
            break
    urls += [r["deb"]["url"] for r in reports if r.get("deb")][:2]
    urls += [g["url"] for r in reports for g in r["gr"]][:2]
    urls += [r["url"] for r in reports][:2]
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 lords-inquiries link check"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read(200000).decode("utf-8", "replace")
                t = re.search(r"<title>(.*?)</title>", body, re.S)
                log(f"LINK {resp.status} {u} -> {resp.geturl()} | {(t.group(1).strip() if t else '')[:120]}")
        except Exception as e:
            log(f"LINK FAIL {u}: {e}")


def match_inquiry(title, date, inquiries):
    """A brand-new report may not be linked to its inquiry yet: match by title."""
    best, best_key = {}, None
    tw = words(title)
    for b in inquiries:
        bw = words(pick(b, "title", default=""))
        if not bw or (date and day(pick(b, "openDate")) and day(pick(b, "openDate")) > date):
            continue
        score = len(bw & tw) / len(bw)
        k = (score, day(pick(b, "openDate")) or "")
        if score >= 0.6 and (best_key is None or k > best_key):
            best, best_key = b, k
    return best


# ---------- build ----------

def build(src, now):
    raw_reports = src.publications([1, 12])
    raw_responses = src.publications([2])
    gov_by_id = {r["id"]: r for r in raw_responses}

    # Government responses link back to the report through responseToPublicationId
    responses_for = {}
    for g in raw_responses:
        to = pick(g, "responseToPublicationId")
        if to:
            responses_for.setdefault(to, []).append(g["id"])

    # Lords debates on committee reports
    debates = []
    try:
        raw = []
        for term in ("Science and Technology Committee Report", "Science and Technology Committee"):
            raw += src.hansard_debates(term, SINCE)
        for r in raw:
            title = re.sub(r"\s+", " ", pick(r, "title", default="")).strip()
            if DEBUG:
                log(f"  hansard: {day(pick(r, 'sittingDate'))} | {pick(r, 'debateSection')} | {title}")
            if not re.search(r"science and technology committee report", title, re.I):
                continue  # skip e.g. motions to appoint the committee
            link = hansard_link(r)
            if link:
                debates.append({"title": title, "core": debate_title_core(title), "date": day(pick(r, "sittingDate", "date")), "url": link})
        log(f"Hansard: {len(debates)} Lords debates on S&T Committee reports")
    except Exception as e:
        log(f"Hansard search failed, continuing without debates: {e}")
    # de-duplicate (a debate may come back once per contribution)
    seen, uniq = set(), []
    for d in debates:
        if d["url"] not in seen:
            seen.add(d["url"])
            uniq.append(d)
    debates = uniq

    try:
        inquiries = [b for b in src.inquiries() if (pick(pick(b, "type", default={}), "isInquiry"))]
    except Exception as e:
        log(f"Inquiry list failed: {e}")
        inquiries = []

    reports, seen_titles = [], set()
    for p in sorted(raw_reports, key=lambda x: pick(x, "publicationStartDate", default="")):
        pid = p["id"]
        title = clean_title(pick(p, "description", default=""))
        if re.search(r"\bevidence\s*\(|\s[-–]\s*evidence\b", title, re.I):
            continue  # published evidence volumes, not reports
        key = re.sub(r"[^a-z0-9]", "", title.lower())
        if key in seen_titles:
            continue  # same report listed twice (e.g. HTML and PDF entries)
        seen_titles.add(key)
        date = day(pick(p, "publicationStartDate"))
        bus = [b for b in (pick(p, "businesses", default=[]) or []) if isinstance(b, dict)]
        inquiry = bus[0] if bus else match_inquiry(title, date, inquiries)
        bid = pick(inquiry, "id")

        rec = {
            "id": pid, "t": title, "d": date, "paper": paper(p), "url": pub_url(p),
            "kind": "Special report" if (pick(pick(p, "type", default={}), "id") == 12) else "Report",
            "dept": dept(p),
            "inq": clean_title(pick(inquiry, "title", default="")) or None, "bid": bid,
        }
        if bid:
            rec["work"] = f"https://committees.parliament.uk/work/{bid}/"
            for kind, key in (("WrittenEvidence", "we"), ("OralEvidence", "oe")):
                try:
                    rec[key] = src.evidence_count(kind, bid)
                except Exception as e:
                    log(f"{kind} count failed for business {bid}: {e}")

        grs = pick(p, "governmentResponses")
        if isinstance(grs, dict):
            rec["due"] = day(pick(grs, "responseDeadline"))
            grs = pick(grs, "publication", "publications", default=[])
        gids = list(dict.fromkeys(ref_ids(grs) + responses_for.get(pid, [])))
        gr = []
        for gid in gids:
            g = gov_by_id.get(gid)
            if g:
                gr.append({"t": clean_title(pick(g, "description", default="Government response")), "d": day(pick(g, "publicationStartDate")), "url": pub_url(g)})
            else:
                gr.append({"t": "Government response", "d": None, "url": f"https://committees.parliament.uk/publications/{gid}/"})
        rec["gr"] = sorted(gr, key=lambda x: x["d"] or "9999")

        reports.append(rec)

    # Each debate goes to the single best-matching report published before it;
    # ties go to the most recent such report.
    for d in debates:
        w = words(d["core"])
        if not w:
            continue
        best, best_key = None, None
        for r in reports:
            if r["d"] and d["date"] and d["date"] < r["d"]:
                continue
            score = len(w & (words(r["t"]) | words(r["inq"] or ""))) / len(w)
            k = (score, r["d"] or "")
            if score >= 0.5 and (best_key is None or k > best_key):
                best, best_key = r, k
        if best and (not best.get("deb") or d["date"] > best["deb"]["d"]):
            best["deb"] = {"d": d["date"], "url": d["url"], "t": d["title"]}

    reports.sort(key=lambda r: r["d"] or "", reverse=True)
    return {"generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "committee": COMMITTEE_NAME, "committeeId": COMMITTEE_ID, "reports": reports}


def main():
    global DEBUG
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample")
    ap.add_argument("--out", default="site/science-and-technology")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    DEBUG = a.debug
    now = dt.datetime.now(UTC).replace(microsecond=0)
    data = build(Sample(a.sample) if a.sample else Live(), now)

    n = len(data["reports"])
    n_gr = sum(1 for r in data["reports"] if r["gr"])
    n_deb = sum(1 for r in data["reports"] if r.get("deb"))
    print(f"S&T: {n} reports, {n_gr} with government response, {n_deb} with a matched Lords debate")
    if DEBUG:
        for r in data["reports"][:40]:
            log(f"  {r['d']} | {r['t'][:60]} | we={r.get('we')} oe={r.get('oe')} | gr={[g['d'] for g in r['gr']]} | deb={r.get('deb', {}).get('t')}")
    if DEBUG:
        log("DATAJSON " + json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    if n < 3:
        sys.exit("Too few reports found: refusing to publish.")

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(HERE, "st_template.html")) as f:
        tpl = f.read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(os.path.join(a.out, "index.html"), "w") as f:
        f.write(tpl.replace("/*__DATA__*/null", blob))
    with open(os.path.join(a.out, "data.json"), "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"Wrote {a.out}/index.html and data.json")


if __name__ == "__main__":
    main()
