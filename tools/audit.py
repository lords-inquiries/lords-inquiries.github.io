"""One-off audit: are any open Lords/joint calls for evidence missed by build.py?"""
import json, sys, datetime as dt
sys.path.insert(0, ".")
import build

now = dt.datetime.now(dt.timezone.utc)
def periods(b):
    return [{"start": p.get("startDate"), "end": p.get("endDate"), "promoted": p.get("isPromoted"),
             "msg": bool((p.get("statusMessage") or "").strip()), "type": p.get("submissionType")}
            for p in (b.get("openSubmissionPeriods") or [])]

houses = {}
allc = build.get_all("Committees", {})
for c in allc:
    houses[c["id"]] = (c.get("name"), c.get("house"), c.get("endDate"))
print("AUDIT committees by house:", {h: sum(1 for v in houses.values() if v[1] == h) for h in {v[1] for v in houses.values()}})

lords_ids = [i for i, v in houses.items() if v[1] in ("Lords", "Joint")]
print("AUDIT Lords+Joint committees (any end date):", len(lords_ids))

# 1. Global list Parliament flags as accepting evidence
glob = build.get_all("CommitteeBusiness", {"CurrentlyAcceptingEvidence": "true"})
print("AUDIT global CurrentlyAcceptingEvidence:", len(glob))

# 2. Every Lords/Joint committee, no Status filter: anything with an open period
found = {}
for cid in lords_ids:
    try:
        for b in build.get_all("CommitteeBusiness", {"CommitteeId": cid}):
            if b.get("openSubmissionPeriods"):
                found.setdefault(b["id"], (cid, b))
    except Exception as e:
        print("AUDIT fail", cid, e)
for bid, (cid, b) in sorted(found.items()):
    real = [p for p in (b.get("openSubmissionPeriods") or []) if build.is_real_call(p)]
    print("AUDIT LORDS", bid, "|", houses[cid][0], "|", b.get("title"), "|", (b.get("type") or {}).get("name"),
          "| real" if real else "| placeholder", "|", json.dumps(periods(b)))

# 3. Global items not seen in the Lords crawl
for b in glob:
    if b["id"] not in found:
        print("AUDIT GLOBAL-ONLY", b["id"], "|", b.get("title"), "|", json.dumps(periods(b)))
