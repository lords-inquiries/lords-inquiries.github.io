"""One-off audit: which committee owns each business Parliament flags as accepting evidence?"""
import json, sys, time
sys.path.insert(0, ".")
import build

build_get = build.get_json
allc = build.get_all("Committees", {})
print("AUDIT committees:", len(allc))
for c in allc:
    for attempt in range(3):
        try:
            items = build.get_all("CommitteeBusiness", {"CommitteeId": c["id"], "CurrentlyAcceptingEvidence": "true"})
            break
        except Exception as e:
            items = None
            time.sleep(5)
    if items is None:
        print("AUDIT FAIL", c["id"], c.get("name"), c.get("house"))
        continue
    for b in items:
        ps = b.get("openSubmissionPeriods") or []
        real = any(build.is_real_call(p) for p in ps)
        print("AUDIT", c.get("house"), "|", c["id"], c.get("name"), "|", b["id"], b.get("title"), "|", "real" if real else "placeholder",
              "|", [p.get("endDate") for p in ps])
