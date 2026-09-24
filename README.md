# dblp UI

A self-hosted search page and JSON API for the [dblp XML data](https://dblp.org/xml/). The Docker image contains the application, not the data. On first start, it downloads a monthly XML snapshot and builds a SQLite index in a persistent volume. Searches and BibTeX checkout use that local index; they do not query dblp live.

## Run

```sh
docker compose up -d --build
docker compose logs -f dblp
```

Open <http://localhost:8080/>. Set `DBLP_PORT` in a `.env` file to change the host port. The first download and import take time and need tens of GB of free disk space. The page and `/api/status` are available during import; searches become available when `/api/status` reports `"ready": true`.

The service has no authentication. The page and API send `noindex` headers, but those do not restrict access. Add an authenticated reverse proxy if access should be limited.

## Search page

- Search titles, topics, venue acronyms, and authors. Author autocomplete ignores accents; for example, `Daniel Marx` suggests `Dániel Marx`.
- Filter by journal, conference, monographs, artifacts, informal publications, or other records. Click a type to show only that type; click it again to show all types. Counts refer to result groups loaded so far, with `+` while more author results remain unread.
- The URL records the title query, author, and selected type. Browser Back and Forward restore the search, and copied URLs reopen it.
- Results load as you scroll and are ordered by their newest publication year. A row groups versions with the same complete author set and matching titles, allowing punctuation differences, common edition notes, and small typos.
- Venue pills show the year below the venue. `CoRR`, `Electron. Colloquium Comput. Complex.`, and `IACR Cryptol. ePrint Arch.` display as `arXiv`, `ECCC`, and `IACR`. Informal publications, including IACR ePrint, are gray; journals are violet, conferences blue, artifacts brown, and monographs yellow.
- The citation cart starts **Off** on every page load, so a row or venue pill opens that publication's web link. Turn it **On** to copy a `DBLP:` key and add it to the cart when clicking a row, or click a venue pill to choose a specific version. The default version favors a journal, then a conference. A small cart icon appears beside each pill while the cart is on.
- Turning the cart off keeps its existing keys for later. Cart keys remain saved in the browser; the On/Off switch resets to Off on reload.
- **Show** opens a cart-only view. **Checkout** collects full BibTeX entries and copies them to the clipboard or downloads a `.bib` file. Keys persist in the browser's local storage. Full BibTeX is fetched only at checkout.

The page hides dblp's numeric author suffixes and terminal periods in titles. These display changes do not alter the indexed records or BibTeX.

## Manual curation

Edit [`app/manual_overrides.json`](app/manual_overrides.json) to change venue labels or classify venues and dblp key prefixes as preprints. The checked-in rules display CoRR as arXiv, the Electronic Colloquium as ECCC, and IACR ePrint as IACR. Their `preprint` flags also control search filters and preferred-reference ranking.

Edit [`app/manual_consolidations.json`](app/manual_consolidations.json) to link records whose titles changed while the publication remained the same. Add an entry to `groups`, for example:

```json
{"keys": ["journals/corr/Example26", "conf/example/Example27"], "note": "Renamed conference version of the preprint"}
```

Use the actual dblp keys from search results. An explicit link can cross title changes, but records are grouped only when their complete author sets match. Missing keys have no effect. Changes take effect when the app restarts; the SQLite index does not need to be rebuilt.

## Data refresh

The app chooses one random local time on days 1–3 of each month between 01:00 and 03:59 to download the current snapshot. The default timezone is `Europe/Berlin`; set `DBLP_TIMEZONE` to another IANA timezone if needed. The scheduled time survives container restarts. Failed updates retry hourly.

Searches continue to use the existing index during download and import. A new index replaces it only after a successful build; unchanged downloads keep the current index. Temporary archives are removed after each attempt.

The default source is Dagstuhl's monthly snapshot:

```text
https://drops.dagstuhl.de/storage/artifacts/dblp/xml/{year}/dblp-{date}.xml.gz
```

`{date}` expands to `YYYY-MM-01`. Set `DBLP_URL` to override the source; `{year}`, `{month}`, and `{date}` are available as placeholders. A local `dblp.xml.gz` is neither mounted nor included in the image.

## API

Responses are UTF-8 JSON. All endpoints are read-only; `/api/records` and `/api/bibtex` use POST to accept lists of keys.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/status` | Index readiness, metadata, refresh state, and next update |
| GET | `/api/search` | Search publication groups |
| GET | `/api/autocomplete` | Title or author suggestions |
| GET | `/api/closest` | Similar titles for a misspelled query |
| GET | `/api/record` | One record, selected by `key` |
| POST | `/api/records` | Records for a JSON body such as `{"keys":["journals/..."]}` |
| POST | `/api/bibtex` | Full BibTeX for the same JSON body |

`/api/search` accepts `q`, `author`, `venue`, `category`, `year_from`, `year_to`, `type`, `sort`, `limit`, and `offset`. `category` is a comma-separated subset of `journal`, `conference`, `monograph`, `artifact`, `informal`, and `other`. `sort` can be `relevance` (default), `year_desc`, or `year_asc`. A query, author, venue, or category filter is needed to return results.

Each search result contains the preferred reference at the top level and all matching publications in `versions`. `limit` counts matching publications before grouping, so a page can have fewer groups than its limit. Use `next_offset` for the next request while `has_more` is true. The default limit is 20, or 300 when `author` is supplied; the maximum is 300. The web page requests 300 records for the first author page and 20 for later pages.

```sh
curl 'http://localhost:8080/api/search?author=Avi%20Wigderson&sort=year_desc'
curl 'http://localhost:8080/api/search?q=graph&category=journal,conference&limit=20'
curl 'http://localhost:8080/api/autocomplete?kind=author&q=Daniel%20Marx'
curl 'http://localhost:8080/api/closest?q=grph%20neural%20netwroks'
```

## Development

The application uses Python 3.13 and the standard library. Run the focused tests with:

```sh
python -m unittest discover -s tests
```

For a local archive named `dblp.xml.gz`, run `python -m app.importer` and then `python -m app.server`. To run the download and scheduler without Docker, use `python -m app.manager` with `DBLP_DATA_DIR` and `DBLP_DB` set to writable paths. On Windows, install Python's `tzdata` package for `Europe/Berlin` or set `DBLP_TIMEZONE=UTC`.

Data format references: [dblp XML overview](https://dblp.uni-trier.de/faq/What%2Bdo%2BI%2Bfind%2Bin%2Bdblp%2Bxml.html) and [parsing notes](https://dblp.uni-trier.de/faq/How%2Bto%2Bparse%2Bdblp%2Bxml.html). dblp metadata is released under CC0 1.0.
