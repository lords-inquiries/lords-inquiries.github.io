# Open House of Lords inquiries and calls for evidence

A free, unofficial website listing every current inquiry run by a House of Lords select committee (plus joint committees with Lords members), with open calls for evidence at the top, each with a countdown to its deadline.

**Live site:** https://lords-inquiries.github.io/

## How it works

- `build.py` queries the public [UK Parliament Committees API](https://committees-api.parliament.uk) for every Lords and joint committee, collects their open committee business, and keeps inquiries plus anything with an open written-evidence period. One-off and regular evidence sessions are left out.
- It writes a static site to `site/`: `index.html` (the page), `data.json` (the data, for reuse) and `feed.xml` (an RSS feed of open calls).
- A GitHub Actions workflow (`.github/workflows/update.yml`) runs every morning, rebuilds the site and publishes it with GitHub Pages. If the API returns suspiciously little data, the build stops and yesterday's site stays up.

No dependencies beyond the Python 3.9+ standard library.

## Running it yourself

```bash
python build.py                    # live data -> site/
python build.py --sample tests/sample.json --now 2026-11-25T23:30:00Z --out /tmp/site   # offline test
```

To refresh the site straight away, open the **Actions** tab, choose **Update site**, then **Run workflow**.

## Notes

- Committees sometimes keep older inquiries marked as current after reporting; the site follows Parliament's own status.
- API dates are UK local time; the site shows deadlines in UK time.
- This is not an official Parliament service. Always check the committee's own page before submitting evidence.
