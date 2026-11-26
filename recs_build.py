#!/usr/bin/env python3
"""Build pages for exploring the conclusions and recommendations of committee reports.

Each reports/<slug>.json holds one report's summary of conclusions and
recommendations, transcribed from the published report and checked against
the paragraphs in its body. For each report this writes:

    <out>/<slug>/index.html          the report in brief: a slide presentation, from
                                     reports/<slug>.deck.html if there is one, otherwise
                                     a redirect to the explorer
    <out>/<slug>/explore/index.html  the explorer (recs_template.html)
    <out>/<slug>/data.json           the data
    <out>/<slug>/og.png              link-preview image, from reports/<slug>.og.png

and the shared typefaces go to <out>/fonts/. Set SITE_URL for absolute links
in link previews (default https://lords-inquiries.github.io/).

Usage:
    python recs_build.py --out site/recommendations
    python recs_build.py --check            # validate the JSON files only

Standard library only.
"""
import argparse
import glob
import html
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_URL = (os.environ.get("SITE_URL") or "https://lords-inquiries.github.io/").rstrip("/") + "/"
REDIRECT = """<!DOCTYPE html>
<html lang="en-GB"><head><meta charset="utf-8"><title>Redirecting</title>
<meta http-equiv="refresh" content="0; url=explore/">
<script>location.replace('explore/'+location.hash)</script></head>
<body><p><a href="explore/">Go to the explorer</a></p></body></html>
"""
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED = ("title", "committee", "number", "paper", "session", "published", "pdf", "finding",
            "chapters", "sections", "priorities", "actors", "topics", "recs")


def validate(d):
    """Return a list of problems with one report (empty if it is fine)."""
    errs = [f"missing {k}" for k in REQUIRED if not d.get(k)]
    if errs:
        return errs
    for k in ("published", "gr_due"):
        if d.get(k) and not DATE.match(d[k]):
            errs.append(f"{k} is not YYYY-MM-DD: {d[k]!r}")
    chapters = [c["n"] for c in d["chapters"]]
    sections = {s["id"]: s for s in d["sections"]}
    pris = {p["id"] for p in d["priorities"]}
    actors = [a["id"] for a in d["actors"]]
    if len(set(actors)) != len(actors):
        errs.append("duplicate actor ids")
    for t in d["topics"]:
        try:
            re.compile(t["re"])
        except re.error as e:
            errs.append(f"topic {t['id']}: bad pattern ({e})")
    used = set()
    last = (0, 0)
    for i, r in enumerate(d["recs"], 1):
        where = f"item {r.get('n', '?')}"
        if r.get("n") != i:
            errs.append(f"{where}: expected number {i} (items must run 1..N in order)")
        if r.get("ch") not in chapters:
            errs.append(f"{where}: unknown chapter {r.get('ch')!r}")
        sec = sections.get(r.get("sec"))
        if not sec:
            errs.append(f"{where}: unknown section {r.get('sec')!r}")
        elif sec["ch"] != r.get("ch"):
            errs.append(f"{where}: section {sec['id']} belongs to chapter {sec['ch']}")
        if r.get("pri") not in pris:
            errs.append(f"{where}: unknown priority {r.get('pri')!r}")
        if r.get("kind") not in ("rec", "con"):
            errs.append(f"{where}: kind must be 'rec' or 'con'")
        if not (r.get("text") or "").strip():
            errs.append(f"{where}: empty text")
        if not isinstance(r.get("para"), int) or not isinstance(r.get("page"), int):
            errs.append(f"{where}: para and page must be numbers")
        if r.get("kind") == "con" and r.get("to"):
            errs.append(f"{where}: conclusions do not ask anyone to act")
        if r.get("words") != len((r.get("text") or "").split()):
            errs.append(f"{where}: words does not match the text")
        key = (chapters.index(r["ch"]) if r.get("ch") in chapters else 0, r.get("para") or 0)
        if key < last:
            errs.append(f"{where}: out of order")
        last = key
        for a in r.get("to", []):
            if a not in actors:
                errs.append(f"{where}: unknown actor {a!r}")
            used.add(a)
    for a in actors:
        if a not in used:
            errs.append(f"actor {a!r} is listed but nothing names it")
    for k, v in (d.get("para_pages") or {}).items():
        if not str(k).isdigit() or not isinstance(v, int):
            errs.append(f"para_pages: bad entry {k!r}: {v!r}")
            break
    return errs


def load_all(src):
    out = []
    for path in sorted(glob.glob(os.path.join(src, "*.json"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        errs = ([] if SLUG.match(slug) else [f"bad file name {slug!r}"]) + validate(d)
        out.append((slug, d, errs))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/recommendations")
    ap.add_argument("--src", default=os.path.join(HERE, "reports"), help="folder of report JSON files")
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    a = ap.parse_args()

    reports = load_all(a.src)
    bad = [(s, e) for s, _, e in reports if e]
    for s, errs in bad:
        for e in errs:
            print(f"{s}: {e}", file=sys.stderr)
    if bad:
        sys.exit(f"{len(bad)} report file(s) failed validation: nothing written.")
    if not reports:
        print(f"No report files in {a.src}.")
        return
    for s, d, _ in reports:
        n_rec = sum(r["kind"] == "rec" for r in d["recs"])
        print(f"{s}: OK, {len(d['recs'])} items ({n_rec} recommendations), {len(d['actors'])} bodies, {len(d['sections'])} sections")
    if a.check:
        return

    with open(os.path.join(HERE, "recs_template.html"), encoding="utf-8") as f:
        tpl = f.read()
    fonts = os.path.join(a.out, "fonts")
    os.makedirs(fonts, exist_ok=True)
    for name in os.listdir(os.path.join(HERE, "recs_assets", "fonts")):
        shutil.copy(os.path.join(HERE, "recs_assets", "fonts", name), fonts)
    for slug, d, _ in reports:
        dest = os.path.join(a.out, slug)
        os.makedirs(os.path.join(dest, "explore"), exist_ok=True)
        blob = json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        n_rec = sum(r["kind"] == "rec" for r in d["recs"])
        desc = (f"Explore the {len(d['recs'])} conclusions and recommendations of the {d['committee']} report "
                f"{d['title']} ({d['paper']}): where they fall, who they ask to act ({n_rec} recommendations), and each in full.")
        page = (tpl.replace("__TITLE__", html.escape(d["title"] + ": the conclusions and recommendations"))
                   .replace("__DESC__", html.escape(desc))
                   .replace("__FONTS__", "../../fonts/")
                   .replace("__ROOT__", "../../../")
                   .replace("/*__DATA__*/null", blob))
        with open(os.path.join(dest, "explore", "index.html"), "w", encoding="utf-8") as f:
            f.write(page)
        with open(os.path.join(dest, "data.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)

        og = os.path.join(a.src, slug + ".og.png")
        if os.path.exists(og):
            shutil.copy(og, os.path.join(dest, "og.png"))
        deck_src = os.path.join(a.src, slug + ".deck.html")
        if os.path.exists(deck_src):
            with open(deck_src, encoding="utf-8") as f:
                deck = f.read()
            url = f"{SITE_URL}recommendations/{slug}/"
            if not os.path.exists(og):
                deck = "\n".join(l for l in deck.split("\n") if "__OG__" not in l and "twitter:card" not in l)
            deck = (deck.replace("__FONTS__", "../fonts/").replace("__ROOT__", "../../")
                        .replace("__URL__", url).replace("__OG__", url + "og.png")
                        .replace("/*__DATA__*/null", blob))
        else:
            deck = REDIRECT
        with open(os.path.join(dest, "index.html"), "w", encoding="utf-8") as f:
            f.write(deck)
        print(f"Wrote {dest}/ (" + ("presentation" if os.path.exists(deck_src) else "redirect") + " and explore/)")


if __name__ == "__main__":
    main()
