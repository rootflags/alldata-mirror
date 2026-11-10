# alldata-mirror

Mirrors one vehicle's repair information from ALLDATA Repair (my.alldata.com) to local files, then
builds a static website from them that you can browse and search offline.

You give it a vehicle ID, such as `57903` from `https://my.alldata.com/repair/#/vehicle/57903`. It
saves everything for that vehicle: the section menus, repair articles, wiring diagrams, parts and
labor tables, specifications, maintenance schedules, technical service bulletins (TSBs) and their
images and PDFs. It never touches other vehicles, your account or anything off-site.

A full vehicle is around 17,000 pages and 12,000 images (about 2 GB). At the default pace of one page
every 5–7 seconds, that takes about 40 hours.

## Requirements

- An ALLDATA Repair subscription with access to the vehicle.
- Microsoft Edge or Google Chrome, to log in and copy your session cookies.
- Python 3.10 or newer. The scripts use only Python's standard library; nothing else to install.
- Linux, macOS, or Windows with WSL. `setup.sh` is a shell script; the Python scripts run anywhere.

## Install

```sh
git clone https://github.com/rootflags/alldata-mirror.git ~/alldata-mirror
cd ~/alldata-mirror
./setup.sh
```

`setup.sh`:
1. creates your config, `~/.alldata-mirror/alldata-mirror.conf`, from `alldata-mirror.conf`;
2. asks where mirrors should go and how long to pause between pages;
3. checks for Python 3.10 or newer, and offers to install it;
4. makes `~/.alldata-mirror` and your cookies private, and creates the mirror folder;
5. checks your cookies file, and tells you when your login expires.

Run it again any time to review the settings. Enter keeps the current value.

### Settings

`~/.alldata-mirror/alldata-mirror.conf`:

```ini
[crawl]
# Seconds to wait before following the next link: a number, or MIN-MAX for a random pause
delay = 5.01-6.99
# Where mirrors are written; each vehicle gets its own folder in it
output = ~/alldata-download
# Your my.alldata.com session cookies, copied from the browser
cookies = ~/.alldata-mirror/cookies.txt
```

Below, `<output>` means the `output` folder and `<vid>` the vehicle ID.

## Copying your cookies

ALLDATA uses DataDome and Akamai bot protection, so alldata-mirror doesn't log in by itself: an
automated login gets a CAPTCHA, and retrying gets your network temporarily blocked. Instead, it uses
the login from your own browser:

1. Log in to [my.alldata.com](https://my.alldata.com) in Edge or Chrome, and open your vehicle.
2. Right-click anywhere on the page and choose **Inspect** (or press F12).
3. In the DevTools panel, open the **Application** tab, then on the left **Storage > Cookies >
   https://my.alldata.com**. If you don't see the Application tab, look under `»`.
4. Click any row in the cookie list, press **Ctrl-A** to select them all, then **Ctrl-C** to copy
   (Cmd-A and Cmd-C on a Mac).
5. Paste the whole list into `~/.alldata-mirror/cookies.txt`, ie:
   ```sh
   cat > ~/.alldata-mirror/cookies.txt
   ```
   paste, press Enter, then Ctrl-D.
6. Run `./setup.sh` again. It makes the file private and tells you how long your login lasts.

The login (the `Access-Token` cookie) lasts about two weeks. When it runs out, the crawler stops
with HTTP 401: copy fresh cookies the same way and run the crawler again; it continues where it
stopped. Cookies the site updates during a crawl are kept in `~/.alldata-mirror/session.json`, and a
newer `cookies.txt` always replaces them.

**`cookies.txt` and `session.json` are a live login to your ALLDATA account.** Keep them private,
and never share them or commit them anywhere.

## Usage

```sh
python3 crawl.py 57903 --max-pages 10   # a short test: 10 pages, about a minute
python3 crawl.py 57903                  # the whole vehicle, into <output>/57903/raw/
python3 build.py 57903                  # the website, in <output>/57903/site/
```

A full crawl takes many hours. Run it inside `screen` or `tmux`, or with `nohup`, and follow its
progress:

```sh
mkdir -p ~/alldata-download/57903
nohup python3 crawl.py 57903 >> ~/alldata-download/57903/run.out 2>&1 &
tail -f ~/alldata-download/57903/run.out     # a progress line every 25 pages
tail -f ~/alldata-download/57903/crawl.log   # every request: time, status, seconds, bytes, path
```

If it stops (a dropped connection, an expired login, Ctrl-C), run the same command again: it
continues where it left off. `build.py` can be run at any time, even partway through a crawl; it
takes about 20 seconds for a full vehicle.

### crawl.py options

| Option | |
|---|---|
| `--delay N` or `--delay MIN-MAX` | Pause between pages, overriding the config |
| `--max-pages N` | Stop after N pages, for a test run |
| `--start PATH` | Start somewhere other than the vehicle's top page (`components/1`) |
| `--output DIR` | Mirror folder, overriding the config (`build.py` takes it too) |
| `--from-har FILE` | Offline test: serve responses from a browser HAR capture instead of the site |

## Viewing the mirror

Open `<output>/<vid>/site/index.html` straight from disk, or serve the folder. Serving it also shows
the text around each match in search results:

```sh
python3 -m http.server 8000 -b 127.0.0.1 -d ~/alldata-download/57903
# then open http://localhost:8000/site/
```

To browse it from another computer over SSH, forward the port: `ssh -L 8000:localhost:8000 you@server`,
or in PuTTY, Connection > SSH > Tunnels, source port `8000`, destination `localhost:8000`.

`site/` links to the images, diagrams and PDFs in `raw/`, so copy `<output>/<vid>/` as a whole. Every
link is relative, so the folder works anywhere. If you move it, rebuild it with `--output` pointing at
its new parent folder, ie: `python3 build.py 57903 --output /new/parent`.

### On a web server

To publish your mirrors on your own (private) website, set `output` to a folder the web server
serves, ie: `/var/www/html/alldata`. Then for each vehicle:

- the website is at `https://your.site/alldata/<vid>/site/index.html`;
- to make `https://your.site/alldata/<vid>/` go there too, add `<output>/<vid>/index.html`:
  ```html
  <!doctype html><meta charset="utf-8"><title>ALLDATA mirror</title>
  <meta http-equiv="refresh" content="0; url=site/index.html">
  <a href="site/index.html">ALLDATA mirror</a>
  ```
- the crawler's own files (`crawl.db`, `crawl.log`, `run.out`) hold no passwords or cookies, but
  there's no need to serve them: `chmod 600` them, or block them in the web server's config.

ALLDATA's content is licensed to you for your own use. Keep such a website private (a password, or
your local network only).

### Search

Every page has a search box at the top right. Press `/` to jump to it and Esc to clear it.

- Each word you type must start a word on the page, so `P04` finds P0410 and P0420.
- Matches in titles and whole words rank first.
- A document filed under several sections shows up once.
- TSB numbers work, ie: `91 17 43 2042097`.

The index lives in `site/search/` as plain files that load as you type, so it needs no server setup.

## How it behaves

The crawler behaves like one person clicking through the site:

- **One link at a time,** with a random 5.01–6.99 second pause before following the next one. A
  page's images download right after it with no pause, like a browser loading a page.
- **Only GET requests** under `/ADAG/repair/ADConnect/v5/carids/<vid>/`, plus the vehicle's
  description (`/ADAG/repair/car-lookup/v3/carids/<vid>`). Other vehicles, account and profile
  pages, logout, Help & Feedback, and every other host are never requested. Links it skips are
  recorded in the `skipped` table of `crawl.db`.
- **Stops instead of retrying** on HTTP 401 (login expired), 403 (blocked), 429 (rate limited),
  503/504 (throttled), when 3 of the last 10 requests took 20 seconds or more, or on a network error.
- **Resumable:** progress is saved after every request in `<output>/<vid>/crawl.db`. Files are
  written to a temporary name and renamed, so an interrupted run never leaves a half-written file.
- **Honest:** it identifies itself as `alldata-mirror/0.1 (personal offline mirror)`.

The website `build.py` makes points every link at a local file. Links to anything that wasn't
mirrored, and every link to an external site, are removed. Website addresses that appear in article
text, not as links, are left as they are.

## Files

| Path | |
|---|---|
| `setup.sh` | Creates your config and checks everything is in place |
| `alldata-mirror.conf` | The config template `setup.sh` copies to `~/.alldata-mirror/` |
| `crawl.py` | The crawler |
| `build.py` | Builds the website and search index from a crawl |
| `search.js` | The search box (copied into each website by `build.py`) |
| `common.py` | Reads the config |
| `NOTES.md` | How ALLDATA's API is laid out: endpoints, page types, known failures |
| `<output>/<vid>/raw/` | Everything downloaded, laid out like the API's paths (`components/…`, `images/…`, `svgs/…`, `guid/…`) |
| `<output>/<vid>/site/` | The generated website |
| `<output>/<vid>/crawl.db` | Crawl state (SQLite): every URL with its status, plus skipped links |
| `<output>/<vid>/crawl.log` | One line per request |

## Known limitations

- **Expected failures:** a full crawl ends with a few thousand failed requests, and that's normal.
  - TSB "self" links (`.../show_itypes/tsbs/<id>`) return 404. The bulletin's content comes with its
    TSB page instead, as a PDF or as scanned page images.
  - "Click for full-size image" pages (`.../image_article/<n>`) return 406. The viewer links those
    straight to the image, which is already mirrored.

  See `NOTES.md` for details.
- **Duplicate images:** each thumbnail ALLDATA serves is identical to its full-size image, so about
  40% of the image storage is duplicates. They're kept so the mirror stays plain, portable files.
- **New page types:** so far it has been used on one vehicle. A page type the viewer doesn't know yet
  is shown as its raw data, with links removed; `build.py` can be taught to render it, then re-run.
- **The site can change:** this relies on how my.alldata.com works today (see `NOTES.md`).
- **Personal use:** this is for offline access to a subscription you pay for. Check ALLDATA's terms
  of use, keep the pace slow, and don't share the mirrors.

## License

GPL-3.0; see `LICENSE`.
