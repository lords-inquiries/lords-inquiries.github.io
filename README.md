# Open House of Lords inquiries and calls for evidence

A free, unofficial website listing every current inquiry run by a House of Lords select committee (plus joint committees with Lords members), with open calls for evidence at the top, each with a countdown to its deadline.

**Live site:** https://lords-inquiries.github.io/

## How it works

- `build.py` queries the public [UK Parliament Committees API](https://committees-api.parliament.uk) for every Lords and joint committee, collects their open committee business, and keeps inquiries plus anything with an open written-evidence period. One-off and regular evidence sessions are left out.
- It writes a static site to `site/`: `index.html` (the page), `data.json` (the data, for reuse) and `feed.xml` (an RSS feed of open calls).
- A GitHub Actions workflow (`.github/workflows/update.yml`) runs every morning (three scheduled slots between about 5am and 8am UTC, in case GitHub skips one), rebuilds the site and publishes it with GitHub Pages. If the API returns suspiciously little data, the build stops and yesterday's site stays up.

No dependencies beyond the Python 3.9+ standard library.

## Report recommendation explorers

`recs_build.py` turns each file in `reports/` into an interactive page at `/recommendations/<file name>/`, for exploring one report's conclusions and recommendations: a map of the report with every numbered paragraph as a band, highlighting by who is asked to act or by topic, a diagram of who is asked to act, and the full text section by section. The reports tracker links to it from the report's row.

- [Innovation in the NHS: Personalised Medicine and AI—lost opportunities?](https://lords-inquiries.github.io/recommendations/innovation-in-the-nhs/) (HL Paper 93, 2026–27)

Each JSON file is transcribed from the report's summary of conclusions and recommendations and checked against the paragraphs in the body of the report, which also gives whether each is a conclusion (bold) or a recommendation (bold italic) and its page. Who is asked to act, the topics and the grouping under the committee's headline priorities are read from the wording by hand. `python recs_build.py --check` validates the files. The typefaces (Archivo, Newsreader; SIL Open Font License) are in `recs_assets/fonts/`.

## Running it yourself

```bash
python build.py                    # live data -> site/
python build.py --sample tests/sample.json --now 2026-11-25T23:30:00Z --out /tmp/site   # offline test
python recs_build.py --out site/recommendations   # report recommendation explorers
```

To refresh the site straight away, open the **Actions** tab, choose **Update site**, then **Run workflow**.

## Notes

- Committees sometimes keep older inquiries marked as current after reporting; the site follows Parliament's own status.
- API dates are UK local time; the site shows deadlines in UK time.
- This is not an official Parliament service. Always check the committee's own page before submitting evidence.

## Science and Technology Committee tracker

`st_build.py` builds `/science-and-technology/`: the committee's reports with links to evidence, Government responses and Lords debates (found in Hansard), plus upcoming evidence sessions and a subscribable calendar (`sessions.ics`).

`evidence_build.py` (**beta**) builds a full-text search of the committee's oral and written evidence since 2016. Document text is cached between runs with the Actions cache, and [Pagefind](https://pagefind.app) builds a static search index; only the index is published, and results link to the official documents.
