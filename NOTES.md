# ALLDATA API notes

How my.alldata.com's repair API is laid out, as worked out from browser network captures (HAR files)
and a full crawl of vehicle 57903 (a 2018 Audi A3 Quattro Cabriolet) in November 2026. The site may
change; if crawl.py or build.py stop working, a new capture is the place to start.

## Endpoints

All plain `GET`s returning `application/hal+json`. The browser calls them on
`https://my.alldata.com/ADAG/...`. The `_links` inside responses point at
`https://external-api.alldata.com/ADAG/...` with the same paths. We rewrite those to
my.alldata.com and never call external-api directly.

| Path (under `/ADAG/repair/ADConnect/v5/carids/<vid>`) | What it is |
|---|---|
| `/components/1` | vehicle root ("Vehicle") |
| `/components/<cid>/?flatten=true` | component node: `_embedded.data.components[]` (children, each with `hasComponents`, `dataTypes`, nested `informationTypes[]`) and `_embedded.data.informationTypes[]` |
| `/components/<cid>/itypes/<tid>/?flatten=true` | information type: either sub-`informationTypes[]` (e.g. TSB categories) or `nonStandards[]` (documents) |
| `/components/<cid>/itypes/<tid>/nonstandards/<nid>/?flatten=true` | a document: `_embedded.data.article` = `{title, content (full HTML page), id (guid), _links}` |
| `/images/<name>_png/?locale=en_US&region=Region_1` | full-size image (PNG), fetched when the user zooms. The app requests it with a double slash, `//ADAG/...` |
| `/thumbnails/<name>_png/?locale=en_US&region=Region_1` | thumbnail; this is the `<img src>` inside article HTML. Also `_gif` icons (e.g. `na-ad-achtung_gif`) |
| `/guid/<guid>_html#<anchor>` | cross-reference from one article to another (response not yet captured) |
| `/components/<cid>/itypes/<tid>/nonstandards/<nid>/html_article` | the article's `self` link (not called by the app) |

Article HTML links (all relative, all under `carids/<vid>`): `<a href>` to `components/<cid>` and
`guid/...`, `<img src>` to `thumbnails/...`. No scripts or iframes. Inline `<style>` uses
`&#123;`/`&#125;` entities for braces.

**Everything vehicle-specific, images included, lives under `/ADAG/repair/ADConnect/v5/carids/<vid>/`**,
so that prefix makes a clean crawl scope. The only other site file seen is
`/repair/assets/imgs/placeholder.png`.

Other:
- `/ADAG/repair/car-lookup/v3/carids/<vid>` returns the vehicle description.
- `/ADAG/repair/ADConnect/v5/account/recent_cars` holds account data, so the crawler skips it.
- `/ADAG/sso/profile/v2` is the session check (allowed). `/ADAG/sso/login` handles login.
- The `upath=` query parameter is the ancestor component path (e.g. `1,4057,8`). The app also adds `locale=en_US`.

Every response embeds `ancestors[]` (the breadcrumb).

Sample: vehicle 57903 → `components/4057` (Engine, Cooling and Exhaust) → `components/8`
(Engine) has 83 child components and 168 information-type links.

## Third-party (always blocked)
siteintercept.qualtrics.com (surveys/feedback), ec.walkme.com, cdn.walkme.com,
cdn.cookielaw.org, go-mpulse.net, dynatrace, js.datadome.co. The crawler never contacts any of them.

## Bot protection
DataDome and Akamai Bot Manager are in front of everything. Logging in with a headless browser
(Playwright) gets a 403 with a captcha-delivery.com URL (`t=bv`), and retrying leads to "Access is
temporarily restricted". Plain GETs with the cookies of a browser session that is logged in pass, at a
human pace (one page every 5-7 seconds) for a full ~40-hour crawl.

## Page types
`_embedded.data` holds one of: `components`/`informationTypes` (menus), `nonStandards` (document
lists), `article` (HTML in `content`; PDF TSBs have empty content plus `_links.pdf` → `guid/<name>_pdf/pdf`),
`content` (guid/ cross-reference article), `partsAndLabor`, or `parts`+`labors`
(`.../parts_and_labor_article`), `tsbs` (bulletin list), `schedule` (`.../maintenance_schedule`),
`intervals` (`.../maintenance_table`), `quickSpecifications`. Wiring diagrams are articles whose
`<img src>` points at `svgs/<name>_svg`; SVGs link to each other by bare name (`na-ad-..._svg`).
Some components are unlinked group headings with children nested under `_embedded.components`.

## Expected failures (no content missing)
- `.../show_itypes/tsbs/<id>` (a TSB's self link) returns 404. Not needed: every TSB page carries
  either HTML `content` (scanned bulletin pages as `images/na-vw-..._jpg`) or `_links.pdf`.
  For 57903, of 613 unique TSBs, 327 came as PDF and 286 as page images (checked against a browser capture).
- `.../itypes/<n>/image_article/<n>` returns 406. It's the "Click for full-size image" viewer page;
  the image is right above the link and mirrored, and build.py points the link at it.

## Crawl scope and cookies

Scope: only `/ADAG/repair/ADConnect/v5/carids/<vid>/...` and `/ADAG/repair/car-lookup/v3/carids/<vid>`.
Links outside that are recorded in the `skipped` table of crawl.db and never fetched.
The viewer strips every link that isn't mirrored, plus all http(s) URLs.

Cookies: `cookies.txt` is the browser DevTools' Application > Cookies table copied as text
(tab-separated: name, value, domain, path, expires, ...). Copying the table can lose the first row's
name; that row is Akamai's `_abck`, and crawl.py fills it in. The crawler sends only these cookies.
Cookies the site updates are saved to `~/.alldata-mirror/session.json`; a newer `cookies.txt` replaces it.

## Storage
Every `thumbnails/<name>` the site serves is byte-identical to `images/<name>` (all 5,982 pairs for
57903), so about 0.88 GB of that 2.1 GB mirror is duplicates. They're kept on purpose: the mirror stays
plain files with no hard links, and the crawler fetches what a browser would.
