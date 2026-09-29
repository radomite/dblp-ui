# OpenSearch integration, 2026-09-29

## Question

Make the local dblp UI accept browser OpenSearch calls shaped like `https://dblp.org/search?app=OpenSearch&q=%s`.

## Observations

- A direct request to dblp's `/search?app=OpenSearch&q=graph` returns `200` and `text/html; charset=utf-8`, with a search results page. It is not a JSON API response.
- dblp's `/xml/osd.xml` advertises a `text/html` URL template `https://dblp.org/search?app=OpenSearch&amp;q={searchTerms}`. Its page also links to this description file.
- This app already restores `q` from the URL and searches on page load. Its proxy removes `/dblp-api` before forwarding requests.

## Decision

Serve the existing search page at `/search`, expose a local OpenSearch description at `/xml/osd.xml`, and link to it from the page. Configure the public base URL in deployment so the description advertises the proxy path. Keep all search work on the local SQLite index.

Before deployment, the user also requested a narrower centered page so the year labels fit in the left gutter, and a small mirror-age label beside Search. The age uses the last successful refresh timestamp, falling back to the index download timestamp.

## Sources

- https://dblp.org/search?app=OpenSearch&q=graph
- https://dblp.org/xml/osd.xml

## Verification and deployment

- The local test suite passed, including requests to `/search?app=OpenSearch&q=graph` and XML parsing of `/xml/osd.xml` with the configured public base URL.
- The first Docker build from the restored Windows checkout failed to start because `entrypoint.sh` had CRLF line endings. The previous image was restored immediately. A Docker build normalization step and a Git LF rule for shell scripts fixed the image; its entrypoint was checked before redeployment.
- The deployed `20260929-2` container is healthy. The public OpenSearch route returns HTML with `noindex`; the description advertises `https://algodat.ur.de/dblp-api/search?app=OpenSearch&q={searchTerms}`. The existing local index remains ready.
