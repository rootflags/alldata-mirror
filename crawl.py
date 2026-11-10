#!/usr/bin/env python3
"""Mirror one vehicle from the ALLDATA repair API into <output>/<vehicle-id>/raw/.

Usage: .venv/bin/python crawl.py <vehicle-id> [--delay S] [--max-pages N] [--start PATH]
       .venv/bin/python crawl.py <vehicle-id> --from-har FILE --output DIR   (offline test)

Pages (components, information types, documents) are "clicks": one at a time, with a
random pause before following the next one (config [crawl] delay, a range such as
5.01-6.99 or a fixed number of seconds; default 5.01-6.99).
A page's images are fetched right after it with no pause.

The crawl stops at once on 401/403/429/503/504 (expired session, bot block or throttling)
rather than retrying, and also when 3 of the last 10 requests took 20 seconds or more.

Progress is kept in <output>/<vid>/crawl.db, so re-running continues where it stopped.

<output> is config [crawl] output in ~/.alldata-mirror/alldata-mirror.conf (or --output).
Cookies come from the file in config [crawl] cookies (default ~/.alldata-mirror/cookies.txt,
copied from the browser's DevTools > Application > Cookies). Cookies the site updates during
the crawl are kept in ~/.alldata-mirror/session.json.
"""
import argparse
import gzip
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

from common import CFG_DIR, FORBIDDEN, SITE_HOST, load_crawl_config, parse_delay

API = "/ADAG/repair/ADConnect/v5"
API_HOSTS = {SITE_HOST, "external-api.alldata.com"}  # external-api links are fetched via my.alldata.com
SESSION_FILE = CFG_DIR / "session.json"
USER_AGENT = "alldata-mirror/0.1 (personal offline mirror)"
REFERER = f"https://{SITE_HOST}/repair/"
FATAL = {401: "session expired or not logged in", 403: "blocked (bot protection?)",
         429: "rate limited", 503: "service unavailable (throttled?)",
         504: "gateway timeout (throttled?)"}
SLOW_SECONDS = 20     # a request this slow suggests we are being throttled ...
SLOW_LIMIT = (3, 10)  # ... and this many of the last N requests being slow stops the crawl
TIMEOUT = 60
ASSET_KINDS = ("thumbnails", "images")


class Fatal(Exception):
    pass


# ---------------------------------------------------------------- URLs and scope

def normalize(href, vid, base=None):
    """Map a link to (key, kind), or (None, reason) if it is out of scope.

    The key is the URL path without query or fragment; it identifies the item.
    """
    href = href.replace("&amp;", "&")
    if re.fullmatch(r"na-[\w-]+_svg", href):  # wiring diagrams link to each other by bare name
        href = f"{API}/carids/{vid}/svgs/{href}"
    url = urljoin(base or f"https://{SITE_HOST}/", href)
    u = urlparse(url)
    if u.scheme not in ("http", "https") or u.hostname not in API_HOSTS:
        return None, "off-site"
    path = re.sub(r"/+", "/", u.path).rstrip("/")
    if path.endswith("/html_article"):  # article self link; same document as its parent
        path = path[: -len("/html_article")]
    if FORBIDDEN.search(path):
        return None, "forbidden"
    if path == f"/ADAG/repair/car-lookup/v3/carids/{vid}":
        return path, "car"
    prefix = f"{API}/carids/{vid}/"
    if not path.startswith(prefix):
        return None, "other-vehicle-or-account"
    first = path[len(prefix):].split("/")[0]
    return path, ("asset" if first in ASSET_KINDS else "page")


def request_url(key, kind):
    if kind == "asset":
        return f"https://{SITE_HOST}{key}/?locale=en_US&region=Region_1"
    if kind == "car":
        return f"https://{SITE_HOST}{key}?locale=en_US"
    return f"https://{SITE_HOST}{key}/?flatten=true"


def local_path(key, vid, kind, ext):
    if kind == "car":
        return "car.json"
    rel = key.split(f"/carids/{vid}/", 1)[1]
    return f"{rel}.{ext}"


def extract_links(obj, found):
    """Collect every href in HAL _links and every href/src inside embedded HTML."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "href" and isinstance(v, str):
                found.append(v)
            else:
                extract_links(v, found)
    elif isinstance(obj, list):
        for v in obj:
            extract_links(v, found)
    elif isinstance(obj, str) and "<" in obj:
        found.extend(re.findall(r'(?:href|src)\s*=\s*"([^"#][^"]*)"', obj, re.I))


# ---------------------------------------------------------------- cookies

def load_cookies(cookie_file):
    """Prefer the session file (cookies the site has updated) if newer than the export."""
    if SESSION_FILE.exists() and (not cookie_file.exists() or
                                  SESSION_FILE.stat().st_mtime > cookie_file.stat().st_mtime):
        return json.loads(SESSION_FILE.read_text())
    if not cookie_file.exists():
        sys.exit(f"no cookies in {cookie_file}: log in to my.alldata.com, then copy the cookie table from "
                 "the browser's DevTools (Application > Cookies) into that file (see README.md)")
    jar = {}
    for line in cookie_file.read_text().splitlines():
        f = line.split("\t")
        if len(f) < 4:
            continue
        # Edge's cookie table copy can lose the first row's name; that row is Akamai's _abck
        jar[f[0] or "_abck"] = f[1]
    return jar


def save_cookies(jar):
    SESSION_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = SESSION_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(jar, fh)
    os.replace(tmp, SESSION_FILE)


# ---------------------------------------------------------------- fetchers

class HttpFetcher:
    def __init__(self, jar):
        self.jar = jar

    def get(self, url, kind):
        accept = ("image/*,*/*;q=0.8" if kind == "asset"
                  else "application/json,application/hal+json,text/plain")
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": accept, "Referer": REFERER,
            "Accept-Encoding": "gzip",
            "Cookie": "; ".join(f"{k}={v}" for k, v in self.jar.items())})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                status, headers, body = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            status, headers, body = e.code, e.headers, e.read()
        except TimeoutError:
            return 0, "", b"timed out"
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                return 0, "", b"timed out"
            raise Fatal(f"network error: {e.reason}")
        if headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        changed = False
        for sc in headers.get_all("Set-Cookie") or []:
            name, _, rest = sc.partition("=")
            value = rest.split(";", 1)[0]
            if self.jar.get(name.strip()) != value:
                self.jar[name.strip()] = value
                changed = True
        if changed:
            save_cookies(self.jar)
        return status, headers.get("Content-Type", ""), body


class HarFetcher:
    """Serves responses from saved HAR captures; anything not captured is a 404."""

    def __init__(self, har_files):
        import base64
        self.items = {}
        for har in har_files:
            for e in json.load(open(har))["log"]["entries"]:
                u = urlparse(e["request"]["url"])
                c = e["response"]["content"]
                if u.hostname != SITE_HOST or e["response"]["status"] != 200 or "text" not in c:
                    continue
                body = (base64.b64decode(c["text"]) if c.get("encoding") == "base64"
                        else c["text"].encode())
                self.items[re.sub(r"/+", "/", u.path).rstrip("/")] = (c.get("mimeType", ""), body)

    def get(self, url, kind):
        path = re.sub(r"/+", "/", urlparse(url).path).rstrip("/")
        if path in self.items:
            ctype, body = self.items[path]
            return 200, ctype, body
        return 404, "text/plain", b"not in capture"


# ---------------------------------------------------------------- crawl state

def open_db(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS items (
        key TEXT PRIMARY KEY, kind TEXT, status TEXT DEFAULT 'pending',
        http INTEGER, file TEXT, ctype TEXT, parent TEXT)""")
    db.execute("CREATE TABLE IF NOT EXISTS skipped (url TEXT PRIMARY KEY, reason TEXT, parent TEXT)")
    db.commit()
    return db


def enqueue(db, key, kind, parent):
    # rowid order = discovery order
    db.execute("INSERT OR IGNORE INTO items (key, kind, parent) VALUES (?, ?, ?)",
               (key, kind, parent))


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Crawler:
    def __init__(self, vid, outdir, fetcher, delay):
        self.vid, self.fetcher, self.delay = vid, fetcher, delay
        self.raw = outdir / "raw"
        self.db = open_db(outdir / "crawl.db")
        self.log = open(outdir / "crawl.log", "a")  # one line per request: status, seconds, bytes
        self.pages = self.assets = self.errors = 0
        self.recent = []  # slow-request flags for the throttling check

    def fetch(self, key, kind):
        t0 = time.monotonic()
        status, ctype, body = self.fetcher.get(request_url(key, kind), kind)
        elapsed = time.monotonic() - t0
        self.log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {status} {elapsed:6.2f}s "
                       f"{len(body):8d} {key}\n")
        self.log.flush()
        self.recent = (self.recent + [elapsed >= SLOW_SECONDS or status == 0])[-SLOW_LIMIT[1]:]
        if sum(self.recent) >= SLOW_LIMIT[0]:
            raise Fatal(f"{sum(self.recent)} of the last {len(self.recent)} requests took "
                        f"{SLOW_SECONDS}s or more; probably throttled")
        if status == 0:  # timed out; leave it pending for the next run
            print(f"  ! timed out {key}")
            return None
        if status in FATAL:
            snippet = body[:300].decode("utf-8", "replace")
            raise Fatal(f"HTTP {status} {FATAL[status]} on {key}\n  {snippet}")
        if status != 200:
            self.errors += 1
            self.db.execute("UPDATE items SET status='failed', http=? WHERE key=?", (status, key))
            self.db.commit()
            print(f"  ! HTTP {status} {key}")
            return None
        ext = ("json" if "json" in ctype else "png" if "png" in ctype else
               "gif" if "gif" in ctype else "jpg" if "jpeg" in ctype else
               "svg" if "svg" in ctype else "html" if "html" in ctype else
               "pdf" if "pdf" in ctype else "bin")
        rel = local_path(key, self.vid, kind, ext)
        write_atomic(self.raw / rel, body)
        self.db.execute("UPDATE items SET status='done', http=?, file=?, ctype=? WHERE key=?",
                        (status, rel, ctype, key))
        return body if ext == "json" else None

    def discover(self, key, body):
        try:
            data = json.loads(body)
        except ValueError:
            return
        links = []
        extract_links(data, links)
        for href in links:
            new, kind = normalize(href, self.vid)
            if new is None:
                self.db.execute("INSERT OR IGNORE INTO skipped VALUES (?, ?, ?)", (href, kind, key))
                continue
            enqueue(self.db, new, kind, key)
            if kind == "asset" and "/thumbnails/" in new:  # the full-size image behind a thumbnail
                enqueue(self.db, new.replace("/thumbnails/", "/images/"), "asset", key)

    def fetch_pending_assets(self):
        for key, kind in self.db.execute(
                "SELECT key, kind FROM items WHERE status='pending' AND kind='asset' ORDER BY rowid"
        ).fetchall():
            self.fetch(key, kind)
            self.assets += 1
        self.db.commit()

    def run(self, start, max_pages):
        enqueue(self.db, f"/ADAG/repair/car-lookup/v3/carids/{self.vid}", "car", None)
        enqueue(self.db, f"{API}/carids/{self.vid}/{start}", "page", None)
        self.db.commit()
        self.fetch_pending_assets()  # leftovers from an interrupted run
        first = True
        while max_pages is None or self.pages < max_pages:
            row = self.db.execute("SELECT key, kind FROM items WHERE status='pending' "
                                  "AND kind IN ('page', 'car') ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                break
            if not first:
                time.sleep(random.uniform(*self.delay))  # pause before following the next link
            first = False
            key, kind = row
            body = self.fetch(key, kind)
            if body is not None:
                self.discover(key, body)
            self.db.commit()
            self.pages += 1
            self.fetch_pending_assets()
            if self.pages % 25 == 0:
                print(f"  {self.pages} pages, {self.assets} assets, "
                      f"{self.count('pending')} pending", flush=True)

    def count(self, status):
        return self.db.execute("SELECT COUNT(*) FROM items WHERE status=?", (status,)).fetchone()[0]


def main():
    cfg = load_crawl_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vehicle_id", help="ALLDATA vehicle ID, e.g. 57903")
    ap.add_argument("--delay", type=parse_delay, default=cfg["delay"],
                    help="seconds between pages, N or MIN-MAX (config: %s-%s)" % cfg["delay"])
    ap.add_argument("--max-pages", type=int, help="stop after this many pages (for testing)")
    ap.add_argument("--start", default="components/1", help="where to start (default components/1)")
    ap.add_argument("--output", type=Path, default=cfg["output"], help="mirror root directory")
    ap.add_argument("--from-har", action="append", metavar="FILE",
                    help="offline test: serve responses from a HAR capture instead of the site")
    args = ap.parse_args()
    if not args.vehicle_id.isdigit():
        sys.exit("vehicle_id must be numeric")

    outdir = args.output.expanduser() / args.vehicle_id
    outdir.mkdir(parents=True, exist_ok=True)
    fetcher = HarFetcher(args.from_har) if args.from_har else HttpFetcher(load_cookies(cfg["cookies"]))
    crawler = Crawler(args.vehicle_id, outdir, fetcher, (0, 0) if args.from_har else args.delay)
    try:
        crawler.run(args.start, args.max_pages)
    except Fatal as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("interrupted; re-run to continue", file=sys.stderr)
        sys.exit(130)
    finally:
        crawler.db.commit()
        print(f"this run: {crawler.pages} pages, {crawler.assets} assets, {crawler.errors} errors; "
              f"total done {crawler.count('done')}, failed {crawler.count('failed')}, "
              f"pending {crawler.count('pending')}  ->  {outdir}")


if __name__ == "__main__":
    main()
