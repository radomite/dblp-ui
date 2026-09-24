# Local dblp mirror

A small web form and read-only JSON API over the dblp XML dump. The image contains only the application. On first start it downloads the archive from dblp and builds a search index in a persistent Docker volume.

## Start

From this directory, with Docker Compose installed:

```sh
docker compose up -d --build
docker compose logs -f dblp
```

Open <http://localhost:8080/>. Other group members on the same network can use `http://HOST_IP:8080/`. The form searches titles/topics and authors, including venue acronyms in the topic field. Publication type pills at the right edge of the search card filter journals, conferences, monographs, artifacts, informal publications, and other records; all are selected by default. Click a type to show only it, Shift-click to add another type, or click × to show all types again. Their counts show loaded result groups containing each type and update as more results load. Autocomplete ignores accents and diacritics, so `Daniel Marx` suggests `Dániel Marx`; author suggestions show names without publication counts. Scrolling loads more results automatically, ordered by the latest year in each group. Each result groups publications with identical authors and matching titles, allowing punctuation, common edition notes, and minor typos in titles (author order and letter case are ignored). Authors and title occupy two lines on the left; venue pills sit alongside them on the right, with the year beneath each venue. CoRR, Electron. Colloquium Comput. Complex., and IACR Cryptol. ePrint Arch. display as arXiv, ECCC, and IACR. IACR ePrint records count as informal publications. Colors are violet for journals, blue for conferences, yellow for monographs, brown for artifacts, and gray for informal publications. The pills show versions in priority order: journal, conference, then other types and preprints. Click a row to copy its preferred `DBLP:` key and add it to the cart, or click a venue pill to copy and add that version. Added rows are highlighted. Numeric suffixes on author names are hidden in the display. The compact cart beside Search can show its publications as rows, check out full BibTeX to clipboard or `.bib` download, or reset after confirmation. BibTeX is fetched only at checkout; cart keys persist in the browser's local storage. Set `DBLP_PORT` in a `.env` file to change the host port. The first download and import can take a long time and require substantial free disk space (plan for tens of GB). Watch progress with `docker compose logs -f dblp`. The web form and `/api/status` are available during indexing; search becomes available when the index is ready. Later starts reuse the index. A new index schema triggers a rebuild on deployment.

The container chooses one random local time on days 1–3 of each month, between 01:00 and 03:59:59, to download the current archive again. The default timezone is `Europe/Berlin`; set `DBLP_TIMEZONE` in `.env` to another [IANA timezone](https://www.iana.org/time-zones) if needed. The chosen time is saved in the volume and survives restarts. If the container was stopped at that time, it updates when it starts again. Failed updates retry hourly. Searches remain available against the existing index while the new archive is downloaded and indexed. The index is replaced only after a successful import. If the downloaded bytes are identical, the existing index is retained. The temporary archive is deleted after each attempt.

The default source is Dagstuhl's published monthly snapshot at `https://drops.dagstuhl.de/storage/artifacts/dblp/xml/{year}/dblp-{date}.xml.gz`; `{date}` expands to `YYYY-MM-01`. If the new monthly release is not available at the scheduled time, the service retries hourly. Set `DBLP_URL` in `.env` to change the source; `{year}`, `{month}`, and `{date}` are optional URL placeholders. The existing `dblp.xml.gz` in this folder is not mounted or included in the Docker image.

The service has no authentication. Keep it on a trusted network, or put it behind your own authenticated reverse proxy before internet exposure.

## API

All endpoints are GET and return UTF-8 JSON.

| Endpoint | Parameters | Purpose |
| --- | --- | --- |
| `/api/status` | — | Index metadata, refresh phase, next update time, and last error if any |
| `/api/search` | `q`, `author`, `venue`, `category`, `year_from`, `year_to`, `type`, `sort`, `limit`, `offset` | Search publications. `q` matches title, authors, venue, and venue acronyms. `author` matches an author name prefix. `venue` matches venue names and acronyms. `category` accepts a comma-separated subset of `journal`, `conference`, `monograph`, `artifact`, `informal`, and `other`; omit it for all types. The year parameters remain available in the API. `sort` is `relevance`, `year_desc`, or `year_asc`. Each result groups the same title and authors, with a preferred reference at top level and every matching publication in `versions`, including each version's `reference_type`. The response also has `has_more` and `next_offset`; use `next_offset` for the next request because `limit` counts matching publications before grouping. |
| `/api/autocomplete` | `q`, `kind=title\|author`, `limit` | Title or author suggestions |
| `/api/closest` | `q`, `limit` | Similar titles, including spelling errors; each result is a complete title group |
| `/api/record` | `key` | One record by its dblp key, including people |
| `POST /api/records` | JSON `{ "keys": ["journals/..."] }` | Return publication cards in key order, plus missing keys |
| `POST /api/bibtex` | JSON `{ "keys": ["journals/..."] }` | Return full BibTeX entries in key order, plus any missing keys |

Example:

```sh
curl 'http://localhost:8080/api/search?q=graph+neural+network&limit=5'
curl 'http://localhost:8080/api/search?venue=TODS&limit=5'
curl 'http://localhost:8080/api/autocomplete?kind=author&q=Alan'
curl 'http://localhost:8080/api/closest?q=grph%20neural%20netwroks'
```

`limit` defaults to 20 for search, or 300 when `author` is present, and is capped at 300. The web form requests 300 records for the first author search and 20 for later pages. Autocomplete and closest default to 10 and are capped at 25. `offset` is capped at 1,000,000. A query, author, or venue is required for `/api/search`. BibTeX is generated locally from the XML fields in the index and returned only by `/api/bibtex`, so checkout does not contact dblp.

## Local development

On Linux, Python 3.13 and system timezone data are sufficient; no Python packages are needed. On Windows, install the Python `tzdata` package for `Europe/Berlin` or set `DBLP_TIMEZONE=UTC`. For a local archive you can still run the indexer directly:

```sh
python -m app.importer
python -m app.server
```

For the automatic download and monthly scheduler, run `python -m app.manager` with `DBLP_DATA_DIR` and `DBLP_DB` pointing to a writable directory. `DBLP_HOST` and `DBLP_PORT` control the web binding. The import uses an offline copy of standard named character entities because the dump refers to `dblp.dtd`, which is not in this folder. The indexer parses the archive as XML and retains titles, authors/editors, year, venue, electronic edition, DOI link, record type, dblp key, and modification date. It does not store every XML field.

Run the focused tests with `python -m unittest discover -s tests`.

Data source: [dblp XML dump](https://dblp.org/xml/), [dump format](https://dblp.uni-trier.de/faq/What%2Bdo%2BI%2Bfind%2Bin%2Bdblp%2Bxml.html), [parsing notes](https://dblp.uni-trier.de/faq/How%2Bto%2Bparse%2Bdblp%2Bxml.html). dblp metadata is released under CC0 1.0.
