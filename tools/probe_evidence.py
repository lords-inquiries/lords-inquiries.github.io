"""Temporary probe of evidence endpoints for the S&T Committee."""
import json, sys, urllib.request
sys.path.insert(0, ".")
import st_build as s

for kind in ("OralEvidence", "WrittenEvidence"):
    d = s.capi(kind, CommitteeId=193, Take=2)
    print("PROBE", kind, "total", d.get("totalResults"))
    items = d.get("items") or []
    if items:
        print("PROBE", kind, "item", json.dumps(items[0], ensure_ascii=False)[:3000])
    d2 = s.capi(kind, CommitteeId=193, StartDate="2016-01-01", Take=1)
    print("PROBE", kind, "since2016 total", d2.get("totalResults"))
    if items:
        i = items[0]["id"]
        for fmt in ("Html", "Pdf", "Text", "Txt", "Docx"):
            url = f"{s.COMMITTEES_API}/{kind}/{i}/Document/{fmt}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "lords-inquiries/1.0"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
                    print("PROBE doc", kind, fmt, r.status, r.headers.get("Content-Type"), len(body), repr(body[:400]))
            except Exception as e:
                print("PROBE doc", kind, fmt, "FAIL", e)
