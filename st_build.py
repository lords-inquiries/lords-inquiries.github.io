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
import glob
import html
import json
import math
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
SINCE = "2016-01-01"
SITE_URL = os.environ.get("ST_SITE_URL", "https://lords-inquiries.github.io/science-and-technology/")  # reports published on or after this date
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

    def events(self, since):
        out, skip = [], 0
        while True:
            data = capi("Events", CommitteeId=COMMITTEE_ID, StartDateFrom=since, Take=100, Skip=skip)
            items = data.get("items") or []
            out += items
            skip += 100
            if not items or skip >= data.get("totalResults", 0):
                break
        details = []
        for e in out:
            if "oral evidence" in (pick(pick(e, "eventType", default={}), "name", default="") or "").lower():
                det = get_json(f"{COMMITTEES_API}/Events/{e['id']}")
                if DEBUG and not details:
                    sample("Event detail activities", pick(det, "activities"))
                details.append(det)
        return details

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

    def events(self, since):
        return self.d.get("events", [])

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


PAPER_PAGE = re.compile(r"^(https://publications\.parliament\.uk/pa/[^?#]+?/([^/]+))/[^/]+\.html?$")


def pub_url(p):
    """Link to the report as a PDF where possible.

    On publications.parliament.uk a paper's PDF sits in its folder, named after
    the folder (.../ldsctech/256/25602.htm -> .../ldsctech/256/256.pdf). Failing
    that, use the committee site's copy of the document, then the HTML page.
    """
    url = pick(p, "additionalContentUrl")
    m = PAPER_PAGE.match(url or "")
    if m:
        return f"{m.group(1)}/{m.group(2)}.pdf"
    docs = pick(p, "documents", default=[]) or []
    for d in docs:
        doc_id = pick(d, "documentId", "id")
        if doc_id:
            return f"https://committees.parliament.uk/publications/{p['id']}/documents/{doc_id}/default/"
    if url:
        return url
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


def subject(t):
    """The subject part of a title: before the colon or dash, without quote marks."""
    t = re.split(r"\s*[:?]\s+|\s+[-–—]\s+", (t or "").strip())[0]
    return t.strip(" \"'“”‘’").strip()


def debate_title_core(t):
    # drop a trailing "(Science and Technology Committee Report)", "(S&T Committee Report)", "(STC Report)" etc.
    return re.sub(r"\([^)]*\breport\b[^)]*\)\s*$", "", t or "", flags=re.I).strip(" -:")


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


# ---------- evidence sessions ----------

LONDON = None
try:
    from zoneinfo import ZoneInfo
    LONDON = ZoneInfo("Europe/London")
except Exception:
    pass


def uk_to_utc(s):
    """API times are UK local without an offset."""
    if not s:
        return None
    d = dt.datetime.fromisoformat(s[:19])
    return d.replace(tzinfo=LONDON).astimezone(UTC) if LONDON else d.replace(tzinfo=UTC)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def witness(a):
    name = pick(a, "name", "displayName")
    if not name and isinstance(pick(a, "person"), dict):
        name = pick(a["person"], "name", "displayName")
    orgs = []
    for o in pick(a, "organisations", default=[]) or []:
        org = pick(o, "name")
        role = pick(o, "role")
        if org or role:
            orgs.append(", ".join(x for x in (role, org) if x))
    ctx = pick(a, "additionalContext")
    return {"n": (name or "").strip() or None, "o": "; ".join(orgs) or (ctx or None)}


def shape_event(e):
    acts = []
    for a in pick(e, "activities", default=[]) or []:
        if pick(a, "isPrivate"):
            continue
        kind = pick(a, "activityType", default="") or ""
        ws = [witness(x) for x in sorted(pick(a, "attendees", default=[]) or [], key=lambda x: pick(x, "displayOrder", default=0) or 0)]
        ws = [w for w in ws if w["n"] or w["o"]]
        acts.append({"s": iso(uk_to_utc(pick(a, "startDate"))), "e": iso(uk_to_utc(pick(a, "endDate"))), "k": kind, "w": ws})
    if not acts:
        return None  # nothing public at this meeting
    bus = [pick(b, "title") for b in pick(e, "committeeBusinesses", default=[]) or [] if pick(b, "title")]
    starts = [a["s"] for a in acts if a["s"]]
    ends = [a["e"] for a in acts if a["e"]]
    return {
        "id": e["id"],
        "s": min(starts) if starts else iso(uk_to_utc(pick(e, "startDate"))),
        "e": max(ends) if ends else iso(uk_to_utc(pick(e, "endDate"))),
        "loc": pick(e, "location"),
        "x": bool(pick(e, "cancelledDate")),
        "tv": pick(e, "parliamentTvUrl"),
        "inq": list(dict.fromkeys(bus)),
        "a": acts,
        "url": f"https://committees.parliament.uk/event/{e['id']}/formal-meeting-oral-evidence-session/",
    }


def ics_text(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line):
    out, b = [], line.encode("utf-8")
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def make_ics(events, now, page_url):
    def stamp(s):
        return s.replace("-", "").replace(":", "")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//lords-inquiries//S&T evidence sessions//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:Lords Science and Technology Committee: evidence sessions",
             "X-WR-CALDESC:Public evidence sessions of the House of Lords Science and Technology Committee. Unofficial.",
             "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"]
    for ev in events:
        topic = " / ".join(ev["inq"]) or "Evidence session"
        desc = []
        for a in ev["a"]:
            t = uk_to_local_hm(a["s"])
            desc.append((t + " " if t else "") + (a["k"] or "Session") + ":")
            for w in a["w"]:
                desc.append("- " + ", ".join(x for x in (w["n"], w["o"]) if x))
        if ev.get("tv"):
            desc.append("Watch: " + ev["tv"])
        desc.append("Details: " + ev["url"])
        desc.append("Unofficial calendar: " + page_url)
        lines += ["BEGIN:VEVENT", f"UID:st-event-{ev['id']}@lords-inquiries.github.io", f"DTSTAMP:{stamp(iso(now))}",
                  f"DTSTART:{stamp(ev['s'])}", f"DTEND:{stamp(ev['e'] or ev['s'])}",
                  "SUMMARY:" + ics_text(("CANCELLED: " if ev["x"] else "") + "Lords S&T Committee: " + topic),
                  "LOCATION:" + ics_text(ev.get("loc") or "House of Lords"),
                  "DESCRIPTION:" + ics_text("\n".join(desc)), "URL:" + ev["url"],
                  "STATUS:" + ("CANCELLED" if ev["x"] else "CONFIRMED"), "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(ics_fold(l) for l in lines) + "\r\n"


def uk_to_local_hm(s):
    if not s:
        return ""
    d = dt.datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=UTC)
    if LONDON:
        d = d.astimezone(LONDON)
    return d.strftime("%H:%M")


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

    # Fallback: some debates are titled by subject only, without
    # "(Science and Technology Committee Report)". Search for each unmatched
    # report's subject and inquiry name, and accept only close title matches.
    for r in reports:
        if r.get("deb") or not r["d"]:
            continue
        terms = {subject(r["t"]), subject(r["inq"] or "")} - {""}
        target = words(r["t"]) | words(r["inq"] or "")
        found = []
        for term in terms:
            try:
                for h in src.hansard_debates(term, r["d"]):
                    title = re.sub(r"\s+", " ", pick(h, "title", default="")).strip()
                    date = day(pick(h, "sittingDate"))
                    section = pick(h, "debateSection", default="")
                    if section not in ("Lords Chamber", "Grand Committee") or not date or date < r["d"]:
                        continue
                    if date > str(int(r["d"][:4]) + 4) + r["d"][4:]:
                        continue  # too long after the report to be its debate
                    w = words(debate_title_core(title))
                    hit = w & target
                    if len(hit) >= 2 and len(hit) / len(w) >= 0.75:
                        link = hansard_link(h)
                        if link:
                            found.append((len(hit) / len(w), date, title, link))
            except Exception as e:
                log(f"Hansard subject search failed for {term!r}: {e}")
        if DEBUG:
            log(f"  fallback {r['t'][:50]!r} terms={sorted(terms)} -> {[(round(f[0],2), f[1], f[2]) for f in found][:5]}")
        if found:
            found.sort(key=lambda f: (-f[0], f[1]))  # best score, then earliest
            sc, date, title, link = found[0]
            r["deb"] = {"d": date, "url": link, "t": title}

    # link reports that have a recommendations explorer (built by recs_build.py)
    viz = explorers()
    for r in reports:
        x = viz.get(r.get("paper")) or viz.get(squash(r.get("t")))
        if x:
            r["viz"] = x["url"]
            if x["lead"]:  # the report's own lead departments beat the API's single respondingDepartment
                if r.get("dept") != x["lead"]:
                    log(f"Lead department for {r['t'][:50]!r}: {r.get('dept')!r} -> {x['lead']!r}")
                r["dept"] = x["lead"]

    reports.sort(key=lambda r: r["d"] or "", reverse=True)

    events = []
    try:
        since = (now - dt.timedelta(days=120)).strftime("%Y-%m-%d")
        for e in src.events(since):
            se = shape_event(e)
            if se:
                events.append(se)
        events.sort(key=lambda e: e["s"] or "")
        log(f"Events: {len(events)} public evidence sessions since {since}")
        for ev in events[-4:]:
            log(f"  {ev['s']} | {' / '.join(ev['inq'])[:50]} | tv={bool(ev['tv'])} | " + "; ".join(", ".join(x for x in (w['n'], w['o']) if x) for a in ev['a'] for w in a['w'])[:300])
    except Exception as ex:
        log(f"Events failed, continuing without them: {ex}")

    return {"generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "committee": COMMITTEE_NAME, "committeeId": COMMITTEE_ID,
            "reports": reports, "events": events}


def squash(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def explorers():
    """Map 'HL Paper N (session)' to the recommendations explorer page for that report,
    and to its lead departments (the report JSON's optional "lead" list)."""
    out = {}
    for path in glob.glob(os.path.join(HERE, "reports", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            lead = d.get("lead") or []
            x = {
                "url": f"../recommendations/{os.path.splitext(os.path.basename(path))[0]}/",
                "lead": " and ".join([", ".join(lead[:-1]), lead[-1]]) if len(lead) > 1 else "".join(lead),
            }
            out[f"{d['paper']} ({d['session']})"] = x
            out[squash(d["title"])] = x  # fallback if the API gives the paper number differently
        except Exception as e:
            log(f"Skipping {path}: {e}")
    return out


# ----- the reports in brief: one card per report presentation, in that presentation's own look -----
# Each card draws the report's numbered paragraphs the way its presentation does (the report JSON's
# "card" sets the motif and colours): drops on an incision, salt caverns, a double helix, or the peaks
# of a DNA profile. Recommendations are solid, conclusions hollow or fainter; longer paragraphs are larger.

CARD_DEFAULT = {"motif": "bars", "bg": "#1d181a", "fg": "#f1ecee", "mute": "#b3a8ac", "hi": "#d4485b"}


def _slots(recs, gap):
    xs, slot = [], 0.0
    for i, r in enumerate(recs):
        if i and r["ch"] != recs[i - 1]["ch"]:
            slot += gap
        xs.append(slot + .5)
        slot += 1
    return xs, slot


def _n(v):
    return f"{v:.1f}"


def _drop(x, y, h, r):
    if h < 2 * r + .6:
        rr = max(.3, h / 2)
        cy = y + h - rr
        return f"M{_n(x - rr)},{_n(cy)}a{_n(rr)},{_n(rr)} 0 1,0 {_n(2 * rr)},0a{_n(rr)},{_n(rr)} 0 1,0 {_n(-2 * rr)},0Z"
    n, b, k = max(.5, r * .3), y + h - r, h - 2 * r
    return (f"M{_n(x - n)},{_n(y)}C{_n(x - n)},{_n(y + k * .55)} {_n(x - r)},{_n(y + k * .7)} {_n(x - r)},{_n(b)}"
            f"A{_n(r)},{_n(r)} 0 0,0 {_n(x + r)},{_n(b)}C{_n(x + r)},{_n(y + k * .7)} {_n(x + n)},{_n(y + k * .55)} {_n(x + n)},{_n(y)}Z")


def _capsule(x, yt, yb, r):
    return f"M{_n(x - r)},{_n(yt + r)}A{_n(r)},{_n(r)} 0 0,1 {_n(x + r)},{_n(yt + r)}V{_n(yb - r)}A{_n(r)},{_n(r)} 0 0,1 {_n(x - r)},{_n(yb - r)}Z"


def _size(recs, lo):
    top = max(r["words"] for r in recs) or 1
    return [lo + (1 - lo) * (r["words"] / top) ** .5 for r in recs]


def motif_drops(recs, c):
    """Bleeding to death: a drop for each paragraph, hanging from an incision."""
    x0, L, y0, H = 22, 436, 32, 166
    xs, S = _slots(recs, 2.4)
    U, sz = L / S, _size(recs, .22)
    r = max(1.6, min(U * .42, 6.5))
    out = [f'<line x1="{x0 - 10}" y1="{y0}" x2="{x0 + L + 10}" y2="{y0}" stroke="{c["hi"]}" stroke-width="2" stroke-linecap="round"/>']
    for i, rec in enumerate(recs):
        d = _drop(x0 + xs[i] * U, y0, max(2 * r + 1, H * sz[i]), r)
        out.append(f'<path d="{d}" fill="{c["hi"]}"/>' if rec["kind"] == "rec"
                   else f'<path d="{d}" fill="{c["bg"]}" stroke="{c["hi"]}" stroke-width="1.1"/>')
    return "".join(out)


def motif_caverns(recs, c):
    """Long-duration energy storage: a salt cavern for each paragraph, beneath the ground."""
    x0, L, y0, H = 22, 436, 28, 176
    xs, S = _slots(recs, 2.4)
    U, sz = L / S, _size(recs, .26)
    r, sh = max(1.7, min(U * .36, 7)), H * .2
    out = [f'<rect x="{x0 - 10}" y="{_n(y0 + sh * .55)}" width="{L + 20}" height="{_n(H - sh * .55 + 4)}" rx="5" fill="{c.get("glass", c["bg"])}"/>',
           '<path d="' + "".join(f"M{_n(x0 + x * U)},{y0}V{_n(y0 + sh)}" for x in xs) + f'" stroke="{c["mute"]}" stroke-width="1"/>',
           f'<line x1="{x0 - 10}" y1="{y0}" x2="{x0 + L + 10}" y2="{y0}" stroke="{c["fg"]}" stroke-width="1.6" stroke-linecap="round"/>']
    for i, rec in enumerate(recs):
        yt = y0 + sh
        d = _capsule(x0 + xs[i] * U, yt, yt + max(2 * r + 1, (H - sh) * sz[i]), r)
        fill = c["hi"] if rec["kind"] == "rec" else c["bg"]
        out.append(f'<path d="{d}" fill="{fill}" stroke="{c["hi"]}" stroke-width="1.2"/>')
    return "".join(out)


def motif_helix(recs, c):
    """Innovation in the NHS: a double helix with a rung for each paragraph."""
    N, ax, bx, cy, R, tw, ph = len(recs), 26, 454, 106, 80, 2 * math.pi / 15, .9
    out, back, front = [], [], []
    for s in (1, -1):
        b, f, prev, last, M = "", "", None, None, N * 8
        for k in range(M + 1):
            t = k / M
            th = ph + (t * N - .5) * tw
            pt = f"{_n(ax + (bx - ax) * t)},{_n(cy + s * R * math.sin(th))}"
            is_f = math.cos(th) * s >= 0
            if is_f != prev:
                seg = ("M" + last + "L" + pt) if last else "M" + pt
                if is_f:
                    f += seg
                else:
                    b += seg
            elif is_f:
                f += "L" + pt
            else:
                b += "L" + pt
            prev, last = is_f, pt
        back.append(b)
        front.append(f)
    out += [f'<path d="{b}" fill="none" stroke="{c["fg"]}" stroke-width="2" opacity=".28"/>' for b in back if b]
    for i, rec in enumerate(recs):
        th = ph + i * tw
        x = ax + (bx - ax) * (i + .5) / N
        y1, y2 = cy + R * math.sin(th), cy - R * math.sin(th)
        col = c.get("rr", c["fg"]) if rec["kind"] == "rec" else c.get("rc", c["mute"])
        out.append(f'<line x1="{_n(x)}" y1="{_n(y1)}" x2="{_n(x)}" y2="{_n(y2)}" stroke="{col}" stroke-width="3" stroke-linecap="round" opacity="{.4 + .6 * abs(math.sin(th)):.2f}"/>')
    out += [f'<path d="{f}" fill="none" stroke="{c["fg"]}" stroke-width="2" opacity=".9"/>' for f in front if f]
    return "".join(out)


def motif_peaks(recs, c):
    """Rebuilding forensic science: a DNA profile with a peak for each paragraph, grouped by chapter."""
    x0, L, base, H = 22, 436, 198, 140
    xs, S = _slots(recs, 1.6)
    U = L / S
    xs = [x0 + x * U for x in xs]
    top = max(r["words"] for r in recs) or 1
    hs = [H * (16 + 76 * (r["words"] / top) ** .5) / 100 for r in recs]
    sig = U * .3
    g = lambda dx, h: h * math.exp(-dx * dx / (2 * sig * sig))
    out = []
    for ch in sorted({r["ch"] for r in recs}, key=[r["ch"] for r in recs].index):
        idx = [i for i, r in enumerate(recs) if r["ch"] == ch]
        a, b = xs[idx[0]] - U * .45, xs[idx[-1]] + U * .45
        out.append(f'<rect x="{_n(a)}" y="{_n(base - H - 32)}" width="{_n(b - a)}" height="10" fill="none" stroke="{c["mute"]}" stroke-width="1"/>')
    for i, rec in enumerate(recs):
        x, h = xs[i], hs[i]
        pts = " ".join(f"L{_n(x + k / 4 * sig)},{_n(base - g(k / 4 * sig, h))}" for k in range(-12, 13))
        col = c.get("rr", c["fg"]) if rec["kind"] == "rec" else c.get("rc", c["mute"])
        out.append(f'<path d="M{_n(x - 3 * sig)},{base} {pts} L{_n(x + 3 * sig)},{base} Z" fill="{col}"/>')
    trace = []
    for k in range(701):
        x = x0 + L * k / 700
        y = sum(g(x - xi, hs[i]) for i, xi in enumerate(xs) if abs(x - xi) < 4 * sig)
        y += 1.6 + 1.1 * math.sin(x * .13) + .7 * math.sin(x * .37 + 1.3)
        trace.append(f"{'L' if k else 'M'}{_n(x)},{_n(base - y)}")
    out.append(f'<path d="{"".join(trace)}" fill="none" stroke="{c["fg"]}" stroke-width="1.2" opacity=".55" stroke-linejoin="round"/>')
    out.append(f'<line x1="{x0}" y1="{base}" x2="{x0 + L}" y2="{base}" stroke="{c["fg"]}" stroke-width="1" opacity=".35"/>')
    return "".join(out)


def motif_bars(recs, c):
    """Any other report: a band for each paragraph, wider for longer ones."""
    x0, L = 22, 436
    total = sum(r["words"] for r in recs)
    out, x = [], x0
    for r in recs:
        w = L * r["words"] / total
        col = c["hi"] if r["kind"] == "rec" else c["mute"]
        out.append(f'<rect x="{_n(x)}" y="70" width="{_n(max(.6, w - 1.2))}" height="70" rx="2" fill="{col}"/>')
        x += w
    return "".join(out)


MOTIFS = {"drops": motif_drops, "caverns": motif_caverns, "helix": motif_helix, "peaks": motif_peaks, "bars": motif_bars}
NUMBER_WORDS = "zero one two three four five six seven eight nine ten eleven twelve".split()


def briefs_html():
    """The 'reports in brief' section: a card for every report with a presentation, newest first."""
    cards = []
    for path in sorted(glob.glob(os.path.join(HERE, "reports", "*.json"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        deck = os.path.join(HERE, "reports", slug + ".deck.html")
        if not os.path.exists(deck):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            with open(deck, encoding="utf-8") as f:
                slides = len(re.findall(r'<section class="slide[ "]', f.read()))
            c = {**CARD_DEFAULT, **(d.get("card") or {})}
            svg = MOTIFS.get(c["motif"], motif_bars)(d["recs"], c)
            n = len(d["recs"])
            pub = dt.date.fromisoformat(d["published"])
            when = f"{pub.day} {pub.strftime('%B %Y')}"
            e = html.escape
            url = f"../recommendations/{slug}/"
            cards.append((d["published"], slug, f'''<li class="brief" style="--cb:{e(c["bg"])};--cf:{e(c["fg"])};--cm:{e(c["mute"])};--ch:{e(c.get("link", c["hi"]))}">
<svg class="motif" viewBox="0 0 480 212" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">{svg}</svg>
<div class="bbody"><h3 class="bt"><a href="{url}">{e(d["title"])}<span class="vh">: visualise the report</span></a></h3>
<p class="bm">{e(d["paper"])}, {when}.<span class="bc"> {slides} slides; {n} conclusions and recommendations.</span></p>
<p class="bx"><span class="go" aria-hidden="true">Visualise the report</span></p></div></li>'''))
        except Exception as ex:
            log(f"Skipping card for {path}: {ex}")
    if not cards:
        return ""
    cards.sort(reverse=True)
    k = len(cards)
    count = NUMBER_WORDS[k] if k < len(NUMBER_WORDS) else str(k)
    return f'''<section class="band briefs" aria-labelledby="h-briefs"><div class="wrap">
<h2 id="h-briefs">The reports in brief</h2>
<p class="sub">Interactive guides to Science and Technology Committee reports: the evidence, the committee’s conclusions and what it wants done. </p>
<ul class="shelf">{"".join(c for _, _, c in cards)}</ul>
</div></section>'''


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
    n_pdf = sum(1 for r in data["reports"] if r["url"].endswith(".pdf"))
    print(f"S&T: {n_pdf} of {n} report links go straight to a PDF")
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
        f.write(tpl.replace("/*__DATA__*/null", blob).replace("<!--__BRIEFS__-->", briefs_html()))
    with open(os.path.join(a.out, "data.json"), "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    with open(os.path.join(a.out, "sessions.ics"), "w", newline="") as f:
        f.write(make_ics(data["events"], now, SITE_URL))
    print(f"Wrote {a.out}/index.html and data.json")


if __name__ == "__main__":
    main()
