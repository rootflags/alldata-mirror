#!/usr/bin/env python3
"""Build a static local viewer from a crawled mirror.

Usage: .venv/bin/python build.py <vehicle-id> [--output DIR]

Reads <output>/<vid>/raw/ and crawl.db, and writes <output>/<vid>/site/ (open site/index.html,
or serve it with `python3 -m http.server -d <output>/<vid>`). Every link goes to a local
file; links to anything not mirrored, and all external URLs, are stripped.

It also writes the search index for the header's search box (search.js) to site/search/:
  search/meta.js     page count, index pieces that exist
  search/w/P0.js     every word starting with P0: the pages it's on (in the text and in the title)
  search/p/3.js      pages 1500-1999: link, title, section (breadcrumb), whether it has text
Pages are numbered in crawl order; a page list is the gaps between page numbers, in hex.
Identical pages (the same TSB filed under several sections) are indexed once. Menus are
indexed by title only.
"""
import argparse
import html
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urldefrag

from common import load_crawl_config
from crawl import normalize

CSS = """
:root { --fg:#1d232b; --muted:#5d6875; --bg:#fff; --line:#d9dee4; --link:#0b5cad; --hdr:#2b333d; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e3e7ec; --muted:#9aa5b1; --bg:#16191d; --line:#333a42; --link:#7db7f0; --hdr:#0e1013; }
  .article { background:#fff; color:#111; border-radius:4px; padding:12px; }
}
body { margin:0; font:15px/1.5 system-ui, sans-serif; color:var(--fg); background:var(--bg); }
header { background:var(--hdr); color:#fff; padding:8px 16px; display:flex; flex-wrap:wrap;
  align-items:center; gap:4px 12px; position:relative; }
header .brand { flex:1 1 auto; min-width:0; }
form.search { flex:0 1 320px; margin:0; }
form.search input { width:100%; box-sizing:border-box; font:inherit; font-size:14px; padding:5px 10px;
  border:1px solid #56606c; border-radius:4px; background:#fff; color:#111; }
#search-results { position:absolute; right:16px; top:100%; z-index:10; width:min(600px, calc(100vw - 32px));
  max-height:75vh; overflow:auto; box-sizing:border-box; padding:8px 12px 12px; background:var(--bg);
  color:var(--fg); border:1px solid var(--line); border-radius:0 0 6px 6px; box-shadow:0 8px 24px rgba(0,0,0,.25);
  font-size:14px; }
#search-results[hidden] { display:none; }
#search-results .s-status { color:var(--muted); margin:2px 0 8px; }
#search-results .s-hit { margin-bottom:10px; }
#search-results .s-title { color:var(--link); font-weight:600; }
#search-results .s-section { color:var(--muted); font-size:12px; }
#search-results .s-snippet { font-size:13px; }
#search-results mark { background:#fff3a0; color:#111; padding:0; }
#search-results button { font:inherit; font-size:13px; padding:4px 10px; cursor:pointer; }
header a { color:#fff; text-decoration:none; font-weight:600; }
header .car { color:#c9d1da; font-size:13px; }
main { max-width:1100px; margin:0 auto; padding:12px 16px 48px; }
a { color:var(--link); }
.crumbs { font-size:13px; color:var(--muted); margin:6px 0 4px; }
.crumbs a { color:var(--muted); }
h1 { font-size:22px; margin:4px 0 14px; }
ul.list { list-style:none; padding:0; margin:0; border-top:1px solid var(--line); }
ul.list > li { padding:8px 2px; border-bottom:1px solid var(--line); }
.itypes { font-size:13px; margin-top:2px; }
.itypes > * { margin-right:14px; }
.missing { color:var(--muted); }
.group { font-weight:600; }
.meta { color:var(--muted); }
object.pdf { width:100%; height:80vh; border:1px solid var(--line); }
ul.list.sub { margin:6px 0 0 18px; border-top:0; }
ul.list.sub > li { border-bottom:0; padding:3px 0; }
.article img { max-width:100%; height:auto; }
.article img.thumbnail { max-width:320px; border:1px solid var(--line); cursor:zoom-in; }
table { border-collapse:collapse; width:100%; margin:0 0 20px; font-size:14px; }
th, td { border-bottom:1px solid var(--line); padding:5px 8px; text-align:left; vertical-align:top; }
td.num { text-align:right; white-space:nowrap; }
h2 { font-size:17px; margin:18px 0 6px; }
details pre { white-space:pre-wrap; font-size:12px; }
"""


def esc(s):
    return html.escape(str(s or ""), quote=True)


def parts_and_labor(pl):
    out = []
    rows = [(q.get("name", ""), p) for q in (pl.get("parts") or {}).get("qualifiers") or []
            for p in q.get("part") or []]
    if rows:
        out.append("<h2>Parts</h2><table><tr><th>Group</th><th>Part</th><th>Part number</th>"
                   "<th>Price</th></tr>")
        out += [f"<tr><td>{esc(g)}</td><td>{esc(p.get('name'))}</td><td>{esc(p.get('partnumber'))}</td>"
                f"<td class=num>{esc(p.get('price'))}</td></tr>" for g, p in rows]
        out.append("</table>")

    labor = []

    def walk(qs, path, op):
        for q in qs or []:
            here = path + [q.get("name", "")]
            if q.get("labor"):
                labor.append((op, " › ".join(here), q["labor"]))
            walk(q.get("qualifiers"), here, op)
    for op in (pl.get("labors") or {}).get("operations") or []:
        walk(op.get("qualifiers"), [], op.get("operation", ""))
    if labor:
        out.append("<h2>Labor</h2><table><tr><th>Operation</th><th>Item</th><th>Std hrs</th>"
                   "<th>Warranty hrs</th><th>Skill</th><th>Note</th></tr>")
        out += [f"<tr><td>{esc(op)}</td><td>{esc(name)}</td><td class=num>{esc(l.get('standardtime'))}</td>"
                f"<td class=num>{esc(l.get('warrantytime'))}</td><td>{esc(l.get('skill'))}</td>"
                f"<td>{esc(l.get('note'))}</td></tr>" for op, name, l in labor]
        out.append("</table>")
    return "".join(out) or '<p class="missing">No parts or labor listed.</p>'


def spec_value(s):
    """'10.0 mm (0.394 in)' from a quick-spec entry; metric and US when both exist."""
    def one(sys_):
        v = (s.get("valuesBySystem") or {}).get(sys_) or {}
        r = (s.get("rangesBySystem") or {}).get(sys_) or {}
        if v.get("value"):
            return f"{v['value']} {v.get('unit') or ''}".strip()
        if r.get("range"):
            return f"{r['range']} {r.get('unit') or ''}".strip()
        return None
    metric, us = one("metric"), one("us")
    if metric and us and metric != us:
        return f"{metric} ({us})"
    return metric or us or " ".join(x for x in (s.get("value") or s.get("range"), s.get("unit")) if x) or ""


def quick_specs(groups):
    out = []
    for g in groups or []:
        rows = []

        def add(spec, depth):
            pad = f' style="padding-left:{8 + 18 * depth}px"' if depth else ""
            rows.append(f"<tr><td{pad}>{esc(spec.get('name'))}</td><td>{esc(spec_value(spec))}</td>"
                        f"<td>{esc(spec.get('notes') or '')}</td></tr>")
            for q in spec.get("qualifiers") or spec.get("subQualifiers") or []:
                add(q, depth + 1)
        for spec in g.get("specs") or []:
            add(spec, 0)
        out.append(f"<h2>{esc(g.get('component'))}</h2><table><tr><th>Item</th><th>Value</th>"
                   f"<th>Notes</th></tr>{''.join(rows)}</table>")
    return "".join(out) or '<p class="missing">No specifications listed.</p>'


class Site:
    def __init__(self, vid, outdir):
        self.vid, self.out = vid, outdir
        self.raw, self.site = outdir / "raw", outdir / "site"
        db = sqlite3.connect(outdir / "crawl.db")
        rows = db.execute("SELECT key, kind, file FROM items WHERE status='done'").fetchall()
        self.pages = {k: f for k, kind, f in rows if kind == "page" and f.endswith(".json")}
        # images, plus SVG diagrams and PDFs that the crawler fetched as pages
        self.assets = {k: f for k, kind, f in rows if kind != "car" and not f.endswith(".json")}
        self.car = {}
        car_file = self.raw / "car.json"
        if car_file.exists():
            self.car = json.loads(car_file.read_text())
        self.stripped = 0
        self.index = []  # (key, href from site root, title, section, text, doc id) for the search
        # an article's id is its guid, so guid/ links can point at the nonstandards page
        self.guid_page = {}
        for key, f in self.pages.items():
            art = self.article(json.loads((self.raw / f).read_text()))
            if art and art.get("id"):
                self.guid_page[f"/ADAG/repair/ADConnect/v5/carids/{vid}/guid/{art['id']}"] = key

    @staticmethod
    def data(doc):
        return (doc.get("_embedded") or {}).get("data") or {}

    def article(self, doc):
        a = self.data(doc).get("article")
        return a if isinstance(a, dict) else None

    def page_file(self, key):
        return "index.html" if key.endswith("/components/1") else self.pages[key][:-5] + ".html"

    def href(self, from_key, target, fragment=""):
        """Relative link from one page to another page or asset key, or None if not mirrored."""
        here = (self.site / self.page_file(from_key)).parent
        if target in self.assets:
            dest = self.raw / self.assets[target]
        else:
            target = self.guid_page.get(target, target)
            if target not in self.pages:
                return None
            dest = self.site / self.page_file(target)
        return os.path.relpath(dest, here) + (f"#{fragment}" if fragment else "")

    def link(self, from_key, raw_href, text):
        key, _ = normalize(raw_href, self.vid)
        h = key and self.href(from_key, key)
        if h:
            return f'<a href="{esc(h)}">{esc(text)}</a>'
        return f'<span class="missing">{esc(text)}</span>'

    def item_link(self, key, item):
        href = (item.get("_links") or {}).get("self", {}).get("href")
        if href:
            return self.link(key, href, item.get("title", "?"))
        return f'<span class="group">{esc(item.get("title", "?"))}</span>'

    def components(self, key, comps):
        """List items for child components; unlinked group headings nest their children."""
        items = []
        for c in comps or []:
            emb = c.get("_embedded") or {}
            line = self.item_link(key, c)
            its = [self.item_link(key, t) for t in emb.get("informationTypes") or []]
            if its:
                line += f'<div class="itypes">{"".join(its)}</div>'
            sub = self.components(key, emb.get("components"))
            if sub:
                line += '<ul class="list sub">' + "".join(f"<li>{i}</li>" for i in sub) + "</ul>"
            items.append(line)
        return items

    # ------------------------------------------------------------ article HTML

    def rewrite_links(self, key, body):
        """Point href/src at local files; strip anything not mirrored."""
        def attr(m):
            name, value = m.group(1), html.unescape(m.group(2))
            url, frag = urldefrag(value)
            if not url and frag:  # in-page anchor
                return m.group(0)
            target, _ = normalize(url, self.vid)
            h = target and self.href(key, target, frag)
            if h is None and target and "/thumbnails/" in target:  # fall back to the full image
                h = self.href(key, target.replace("/thumbnails/", "/images/"))
            if h is None:
                self.stripped += 1
                return f'data-stripped-{name}=""'
            return f'{name}="{esc(h)}"'

        return re.sub(r'\b(href|src)\s*=\s*"([^"]*)"', attr, body, flags=re.I)

    def rewrite_article(self, key, content):
        styles = "".join(re.findall(r"<style[^>]*>.*?</style>", content, re.S | re.I))
        styles = styles.replace("&#123;", "{").replace("&#125;", "}")
        # ALLDATA's auto-linking reaches into the CSS too ("vertical-align: <a href=...>top</a>")
        styles = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", styles, flags=re.S | re.I)
        m = re.search(r"<body[^>]*>(.*)</body>", content, re.S | re.I)
        body = m.group(1) if m else re.sub(r"<head>.*?</head>", "", content, flags=re.S | re.I)
        # "Click for full-size image" goes to an image_article viewer page the API won't serve;
        # point it at the image just above it instead
        body = re.sub(r'(<img\b[^>]*\bsrc="([^"]*)"[^>]*>\s*(?:<br\s*/?>\s*)?<a class="image" href=")'
                      r'[^"]*/image_article/[^"]*"', r'\1\2"', body)
        body = self.rewrite_links(key, body)

        # a thumbnail opens its full-size image when that was mirrored
        def zoom(m):
            src = m.group(1)
            full = src.replace("/thumbnails/", "/images/")
            if full != src and (self.site / self.page_file(key)).parent.joinpath(full).exists():
                return f'<a href="{full}">{m.group(0)}</a>'
            return m.group(0)
        body = re.sub(r'<img\b[^>]*\bsrc="([^"]*)"[^>]*>', zoom, body)
        return f'{styles}<div class="article">{body}</div>'

    # ------------------------------------------------------------ special page types

    def tsb_list(self, key, tsbs):
        if not tsbs:
            return '<p class="missing">No bulletins listed.</p>'
        rows = "".join(
            f"<tr><td>{esc(t.get('date'))}</td><td>{esc(t.get('number'))}</td>"
            f"<td>{self.item_link(key, t)}</td></tr>" for t in tsbs)
        return f"<table><tr><th>Date</th><th>Number</th><th>Title</th></tr>{rows}</table>"

    def tsb(self, key, doc, art):
        out = [f'<p class="meta">TSB {esc(doc.get("number"))} &middot; {esc(doc.get("date"))}</p>']
        pdf = (art.get("_links") or {}).get("pdf", {}).get("href")
        target = pdf and normalize(pdf, self.vid)[0]
        h = target and self.href(key, target)
        if h:
            out.append(f'<p><a href="{esc(h)}">Open the bulletin (PDF)</a></p>'
                       f'<object class="pdf" data="{esc(h)}" type="application/pdf"></object>')
        else:
            out.append('<p class="missing">The bulletin text was not mirrored.</p>')
        return "".join(out)

    def schedule(self, key, data):
        out = []
        if data.get("condition"):
            out.append(f'<p class="meta">{esc(data["condition"])}</p>')
        for op in data.get("schedule") or []:
            rows = "".join(
                f"<tr><td>{self.item_link(key, {**c, 'title': c.get('name')})}"
                f"{' (' + esc(c['qualifier']) + ')' if c.get('qualifier') else ''}</td>"
                f"<td>{self.rewrite_links(key, c.get('notes') or '')}</td></tr>"
                for c in op.get("components") or [])
            out.append(f"<h2>{esc(op.get('operation'))}</h2>"
                       f"<table><tr><th>Item</th><th>Notes</th></tr>{rows}</table>")
        return "".join(out)

    def intervals(self, key, data):
        items = []
        for i in data.get("intervals") or []:
            label = f"{i.get('miles')} miles / {i.get('kilometers')} km"
            items.append(self.item_link(key, {**i, "title": label}))
        out = '<ul class="list">' + "".join(f"<li>{i}</li>" for i in items) + "</ul>"
        if data.get("footnote"):
            out += f'<p class="meta" style="white-space:pre-line">{esc(data["footnote"])}</p>'
        return out

    # ------------------------------------------------------------ page rendering

    def render(self, key):
        doc = json.loads((self.raw / self.pages[key]).read_text())
        data = self.data(doc)
        art = self.article(doc)
        ancestors = (doc.get("_embedded") or {}).get("ancestors") or data.get("ancestors") or []
        parts = []

        crumbs = [self.link(key, a["_links"]["self"]["href"], a.get("title", "?"))
                  for a in ancestors if a.get("_links", {}).get("self")]
        if crumbs:
            parts.append(f'<div class="crumbs">{" › ".join(crumbs)}</div>')
        title = doc.get("title") or (art or {}).get("title") or key.rsplit("/", 1)[-1]
        if not (art and art.get("content")):  # articles carry their own heading
            parts.append(f"<h1>{esc(title)}</h1>")

        items = self.components(key, data.get("components"))
        for field in ("informationTypes", "nonStandards"):
            for t in data.get(field) or []:
                items.append(self.item_link(key, t))
        if items:
            parts.append('<ul class="list">' + "".join(f"<li>{i}</li>" for i in items) + "</ul>")

        if art and art.get("content"):
            parts.append(self.rewrite_article(key, art["content"]))
        elif isinstance(data.get("content"), str):  # guid/ cross-reference article
            parts.append(self.rewrite_article(key, data["content"]))
        elif data.get("partsAndLabor"):
            parts.append(parts_and_labor(data["partsAndLabor"]))
        elif data.get("labors") or data.get("parts"):  # parts_and_labor_article
            parts.append(parts_and_labor(data))
        elif data.get("tsbs") is not None:
            parts.append(self.tsb_list(key, data["tsbs"]))
        elif art is not None and doc.get("type") == "TSB":
            parts.append(self.tsb(key, doc, art))
        elif data.get("schedule") is not None:
            parts.append(self.schedule(key, data))
        elif data.get("quickSpecifications") is not None:
            parts.append(quick_specs(data["quickSpecifications"]))
        elif data.get("intervals") is not None:
            parts.append(self.intervals(key, data))
        elif not items:
            # content type the viewer doesn't know yet; keep it readable, minus links
            dump = json.dumps({k: v for k, v in doc.items() if k not in ("_links", "car")}, indent=1)
            dump = re.sub(r"https?://[^\s\"]+", "[url removed]", dump)
            parts.append('<p class="missing">This page type is not rendered yet. Raw data:</p>'
                         f"<details open><pre>{esc(dump[:200000])}</pre></details>")

        section = " › ".join(a.get("title", "?") for a in ancestors)
        # menus and bulletin lists are found by their title only
        menu = (bool(items) and not (art or data.get("content"))) or data.get("tsbs") is not None
        doc_id = (art or {}).get("id") or (key.rsplit("/", 1)[1] if "/guid/" in key else None)
        self.index.append((key, self.page_file(key), title, section,
                           "" if menu else search_text(doc, data, art), doc_id))

        root = self.href(key, f"/ADAG/repair/ADConnect/v5/carids/{self.vid}/components/1") or "#"
        here = (self.site / self.page_file(key)).parent
        site_root = os.path.relpath(self.site, here) + "/"
        page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                f"<title>{esc(title)}</title><style>{CSS}</style>"
                f'<script src="{esc(site_root)}search.js" defer></script></head><body>'
                f'<header><span class="brand"><a href="{esc(root)}">ALLDATA mirror</a> '
                f'<span class="car">{esc(self.car.get("description", "vehicle " + self.vid))}</span></span>'
                f'<form id="search" class="search" role="search" data-root="{esc(site_root)}" '
                f'onsubmit="return false"><input type="search" placeholder="Search this vehicle  ( / )" '
                f'aria-label="Search this vehicle" autocomplete="off"></form>'
                f'<div id="search-results" hidden></div></header>'
                f'<main>{"".join(parts)}</main></body></html>')
        # final pass: nothing in the mirror may point off-site, or at an absolute path (the site has
        # to work wherever it is put)
        page, n = re.subn(r'\b(href|src)="(?:(?:https?:)?//|/)[^"]*"', r'data-stripped-\1=""', page, flags=re.I)
        self.stripped += n
        dest = self.site / self.page_file(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page)

    def build(self):
        for key in self.pages:
            self.render(key)
        self.site.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve().parent / "search.js", self.site / "search.js")
        self.indexed = write_search(self.index, self.site / "search")
        return len(self.pages)


# ---------------------------------------------------------------- search index

CHUNK = 500  # pages per search/p/N.js
WORD_RE = re.compile(r"[A-Za-z0-9]+")


def html_text(s):
    s = re.sub(r"<(script|style|title)\b.*?</\1>", " ", s, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


def search_text(doc, data, art):
    """The words a page should be found by, besides its title."""
    if art and art.get("content"):
        return html_text(art["content"])
    if isinstance(data.get("content"), str):
        return html_text(data["content"])
    out = []
    if doc.get("type") == "TSB":
        out += [doc.get("number") or "", doc.get("date") or ""]

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k not in ("_links", "ancestors", "car", "valuesBySystem", "rangesBySystem"):
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            out.append(html_text(o) if "<" in o else o)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            out.append(str(o))
    walk({k: v for k, v in data.items() if k != "article"})
    return " ".join(out)


def words(text):
    return {w for w in WORD_RE.findall(text.upper()) if len(w) > 1}


def gaps(pages):
    out, last = [], 0
    for p in sorted(pages):
        out.append(format(p - last, "x"))
        last = p
    return ",".join(out)


def js(fn, *args):
    return "MirrorSearch.%s(%s);\n" % (fn, ",".join(json.dumps(a, ensure_ascii=False, separators=(",", ":"))
                                                  for a in args))


def write_search(entries, dest):
    """Write the index into dest (replacing it as a whole); returns the number of pages indexed."""
    # the same document filed under several sections (and its guid/ copy, which has no breadcrumb)
    # is indexed once, under its shortest section path
    seen, keep = set(), set()
    for key, href, title, section, text, doc_id in sorted(
            entries, key=lambda e: ("/guid/" in e[0], len(e[0]), e[0])):
        sig = doc_id or (title, " ".join(text.split()))
        if text and sig in seen:
            continue
        seen.add(sig)
        keep.add(key)
    pages = [e[:5] for e in entries if e[0] in keep]

    index = {}  # word -> ({text pages}, {title pages})
    for n, (key, href, title, section, text) in enumerate(pages):
        for w in words(title):
            index.setdefault(w, (set(), set()))[1].add(n)
        for w in words(text) - words(title):
            index.setdefault(w, (set(), set()))[0].add(n)

    new = dest.with_name(dest.name + ".new")
    shutil.rmtree(new, ignore_errors=True)
    (new / "w").mkdir(parents=True)
    (new / "p").mkdir()
    shards = {}
    for w, (in_text, in_title) in index.items():
        shards.setdefault(w[:2], {})[w] = [gaps(in_text), gaps(in_title)]
    for k, ws in shards.items():
        (new / "w" / f"{k}.js").write_text(js("words", k, ws), encoding="utf-8")
    for c in range(0, len(pages), CHUNK):
        sections, rows = [], []
        for key, href, title, section, text in pages[c:c + CHUNK]:
            if section not in sections:
                sections.append(section)
            rows.append([href, title, sections.index(section), 1 if text.strip() else 0])
        (new / "p" / f"{c // CHUNK}.js").write_text(js("pages", c // CHUNK, {"sections": sections, "pages": rows}),
                                                 encoding="utf-8")
    (new / "meta.js").write_text(js("meta", {"pages": len(pages), "chunk": CHUNK, "shards": "".join(sorted(shards))}),
                                 encoding="utf-8")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(new, dest)
    return len(pages)


def main():
    cfg = load_crawl_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vehicle_id")
    ap.add_argument("--output", type=Path, default=cfg["output"], help="mirror root directory")
    args = ap.parse_args()
    outdir = args.output.expanduser() / args.vehicle_id
    if not (outdir / "crawl.db").exists():
        sys.exit(f"nothing crawled yet in {outdir}")
    site = Site(args.vehicle_id, outdir)
    n = site.build()
    print(f"built {n} pages ({site.indexed} searchable), stripped {site.stripped} unmirrored/external links "
          f"-> {site.site}/index.html")


if __name__ == "__main__":
    main()
