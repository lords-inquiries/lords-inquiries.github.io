"""Temporary: print raw Committees API event data for the S&T Committee."""
import json, sys, datetime as dt
sys.path.insert(0, ".")
import st_build as s

today = dt.date.today()
for params in ({"CommitteeId": 193, "StartDateFrom": str(today - dt.timedelta(days=60)), "Take": 30},
               {"CommitteeId": 193, "StartDate": str(today - dt.timedelta(days=60)), "Take": 30}):
    try:
        d = s.capi("Events", **params)
        items = d.get("items") or []
        print("PROBE params", params, "total", d.get("totalResults"), "n", len(items))
        for e in items[:40]:
            print("PROBE ev", e.get("id"), e.get("startDate"), "|", (e.get("eventType") or {}).get("name"), "|", e.get("name"), "|", e.get("cancelledDate"), "| keys", sorted(e.keys()))
    except Exception as ex:
        print("PROBE fail", params, ex)
d = s.capi("Events", CommitteeId=193, StartDateFrom=str(today - dt.timedelta(days=60)), Take=30)
items = d.get("items") or []
for e in items[:30]:
    if e.get("startDate", "") >= str(today - dt.timedelta(days=21)):
        det = s.get_json(f"{s.COMMITTEES_API}/Events/{e['id']}")
        print("PROBE detail", json.dumps(det, ensure_ascii=False)[:3500])
        break
try:
    b = s.capi("Broadcast/Meetings", CommitteeId=193)
    print("PROBE broadcast", json.dumps(b, ensure_ascii=False)[:1500])
except Exception as ex:
    print("PROBE broadcast fail", ex)
