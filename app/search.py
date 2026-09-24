"""Read-only queries shared by the HTTP API and tests."""

from difflib import SequenceMatcher
from datetime import datetime
import re
import sqlite3
import unicodedata

FIELDS = "p.dblp_key, p.record_type, p.title, p.authors, p.year, p.venue, p.doi, p.ee, p.mdate"
WORDS = re.compile(r"\w+", re.UNICODE)
PREPRINT_PREFIXES = ("journals/corr/", "journals/eccc/", "journals/iacr/", "journals/hal/", "journals/ssrn/", "journals/biorxiv/", "journals/medrxiv/")
PREPRINT_VENUES = ("corr", "eccc", "iacr cryptol. eprint arch.", "arxiv", "ssrn", "biorxiv", "medrxiv", "hal", "openreview")


def fold_accents(value):
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(char))


def match_query(query):
    words = WORDS.findall(query)[:12]
    return " AND ".join('"' + word.replace('"', '') + '"' + ('*' if i == len(words) - 1 else '') for i, word in enumerate(words))


def row_dict(row):
    result = dict(row)
    result["url"] = "https://dblp.org/rec/" + result["dblp_key"]
    result["reference_type"] = reference_type(result)
    return result


def reference_type(item):
    if item.get("record_type") == "data":
        return "artifact"
    if item.get("record_type") in ("book", "phdthesis", "mastersthesis"):
        return "monograph"
    venue = (item.get("venue") or "").casefold()
    key = item["dblp_key"].casefold()
    preprint = (
        key.startswith(PREPRINT_PREFIXES)
        or venue in PREPRINT_VENUES
        or any(word in venue for word in ("preprint", "arxiv", "research square", "chemrxiv", "techrxiv"))
    )
    if preprint:
        return "preprint"
    if item.get("record_type") == "article":
        return "journal"
    if item.get("record_type") == "inproceedings":
        return "conference"
    return "other"


def version_rank(item):
    """Prefer a journal publication, then a conference, then preprints."""
    category = {"journal": 0, "conference": 1, "monograph": 2, "artifact": 3, "other": 4, "preprint": 5}[item["reference_type"]]
    key = item["dblp_key"].casefold()
    return (category, not bool(item.get("doi")), -(item.get("year") or 0), key)


def author_identity(authors, dblp_key):
    names = tuple(sorted(" ".join(name.split()).casefold() for name in authors.split(", ") if name.strip()))
    return names or ("key:" + dblp_key,)


def normalized_title(title):
    title = fold_accents(title)
    title = re.sub(r"\((?:preliminary version|extended abstract|abstract)\)", " ", title)
    title = re.sub(r"(?<=\w)['’](?=\w)", "", title)
    return " ".join(WORDS.findall(title))


def same_work_title(left, right):
    if left == right:
        return True
    if min(len(left), len(right)) < 24 or abs(len(left) - len(right)) > max(3, .03 * max(len(left), len(right))):
        return False
    return SequenceMatcher(None, left, right).ratio() >= .97


def group_results(conn, hits):
    groups, seen, author_cache = [], set(), {}
    for hit in hits:
        if hit["dblp_key"] in seen:
            continue
        title = normalized_title(hit["title"])
        identity = author_identity(hit["authors"], hit["dblp_key"])
        if title and hit["authors"]:
            first_author = hit["authors"].split(", ", 1)[0].casefold()
            if first_author not in author_cache:
                by_authors = {}
                for row in conn.execute(
                    "SELECT DISTINCT p.id,p.title,p.authors,p.dblp_key FROM publication_authors a "
                    "JOIN publications p ON p.id=a.publication_id "
                    "WHERE a.name_norm=?",
                    (first_author,),
                ):
                    author_key = author_identity(row["authors"], row["dblp_key"])
                    by_authors.setdefault(author_key, []).append((row["id"], normalized_title(row["title"])))
                author_cache[first_author] = by_authors
            versions = [row_dict(conn.execute(
                f"SELECT {FIELDS} FROM publications p WHERE p.id=?", (publication_id,)
            ).fetchone()) for publication_id, candidate_title in author_cache[first_author].get(identity, [])
                if same_work_title(title, candidate_title)]
        else:
            versions = [hit]
        if not versions:
            versions = [hit]
        versions.sort(key=version_rank)
        seen.update(version["dblp_key"] for version in versions)
        group = dict(versions[0])
        group["group_key"] = "group:" + min(version["dblp_key"] for version in versions)
        group["versions"] = versions
        group["version_count"] = len(versions)
        groups.append(group)
    return groups


def search(conn, q="", author="", venue="", year_from=None, year_to=None, record_type="", limit=20, offset=0, sort="relevance", category=""):
    clauses, params = [], []
    q = q.strip()
    author = author.strip().casefold()
    venue = venue.strip()
    fts = []
    if q:
        query = match_query(q)
        if not query:
            return {"results": [], "has_more": False}
        fts.append(f"({query})")
    if venue:
        words = WORDS.findall(venue)[:8]
        if not words:
            return {"results": [], "has_more": False}
        venue_terms = []
        for index, word in enumerate(words):
            token = '"' + word + '"' + ('*' if index == len(words) - 1 else '')
            venue_terms.append(f"(venue:{token} OR venue_aliases:{token})")
        fts.append("(" + " AND ".join(venue_terms) + ")")
    if fts:
        clauses.append("pub_fts MATCH ?")
        params.append(" AND ".join(fts))
    if author and fts:
        clauses.append("EXISTS (SELECT 1 FROM publication_authors a WHERE a.publication_id=p.id AND a.name_norm>=? AND a.name_norm<?)")
        params.extend([author, author + "\uffff"])
    if year_from is not None:
        clauses.append("p.year>=?")
        params.append(year_from)
    if year_to is not None:
        clauses.append("p.year<=?")
        params.append(year_to)
    if record_type:
        clauses.append("p.record_type=?")
        params.append(record_type)
    if category:
        selected = set(part.strip() for part in category.split(","))
        allowed = {"journal", "conference", "monograph", "artifact", "informal", "other"}
        if not selected or not selected <= allowed:
            raise ValueError("category must contain journal, conference, monograph, artifact, informal, or other")
        preprint = "(" + " OR ".join("p.dblp_key LIKE ?" for _ in PREPRINT_PREFIXES)
        preprint += " OR lower(COALESCE(p.venue,'')) IN (" + ",".join("?" for _ in PREPRINT_VENUES) + "))"
        category_clauses, category_params = [], []
        for kind in ("journal", "conference", "monograph", "artifact", "informal", "other"):
            if kind not in selected:
                continue
            if kind == "journal":
                category_clauses.append("(p.record_type='article' AND NOT " + preprint + ")")
                category_params.extend(prefix + "%" for prefix in PREPRINT_PREFIXES)
                category_params.extend(PREPRINT_VENUES)
            elif kind == "conference":
                category_clauses.append("p.record_type='inproceedings'")
            elif kind == "monograph":
                category_clauses.append("p.record_type IN ('book','phdthesis','mastersthesis')")
            elif kind == "artifact":
                category_clauses.append("p.record_type='data'")
            elif kind == "informal":
                category_clauses.append("(p.record_type='article' AND " + preprint + ")")
                category_params.extend(prefix + "%" for prefix in PREPRINT_PREFIXES)
                category_params.extend(PREPRINT_VENUES)
            else:
                category_clauses.append("p.record_type IN ('proceedings','incollection','www')")
        clauses.append("(" + " OR ".join(category_clauses) + ")")
        params.extend(category_params)
    if not clauses:
        if not author:
            return {"results": [], "has_more": False}
    join = "JOIN pub_fts ON pub_fts.rowid=p.id" if fts else ""
    if author and not fts:
        clauses.append("p.id IN (SELECT publication_id FROM publication_authors WHERE name_norm>=? AND name_norm<?)")
        params.extend([author, author + "\uffff"])
    where = " AND ".join(clauses)
    if sort == "year_desc":
        order = "p.year DESC, p.id DESC"
    elif sort == "year_asc":
        order = "p.year ASC, p.id ASC"
    else:
        order = "bm25(pub_fts, 5.0, 1.0, 2.0, 3.0), p.id" if fts else "p.year DESC, p.id DESC"
    if author and not fts:
        ids = [row[0] for row in conn.execute(
            f"SELECT p.id FROM publications p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit + 1, offset],
        )]
        results = [row_dict(conn.execute(
            f"SELECT {FIELDS} FROM publications p WHERE p.id=?", (publication_id,)
        ).fetchone()) for publication_id in ids[:limit]]
        return {"results": group_results(conn, results), "has_more": len(ids) > limit, "next_offset": offset + min(limit, len(ids))}
    if fts and sort == "year_desc":
        # A broad FTS match can span decades. Sorting all of it before LIMIT is
        # expensive; start with recent years and widen until this page is full.
        floor = year_from if year_from is not None else 0
        cutoff = max(floor, min(datetime.now().year, year_to or datetime.now().year))
        step = 1
        while True:
            bounded = cutoff > floor
            rows = conn.execute(
                f"SELECT {FIELDS} FROM publications p {join} WHERE {where}"
                + (" AND p.year>=?" if bounded else "")
                + f" ORDER BY {order} LIMIT ? OFFSET ?",
                params + ([cutoff] if bounded else []) + [limit + 1, offset],
            ).fetchall()
            if len(rows) > limit or not bounded:
                break
            cutoff = max(floor, cutoff - step)
            step *= 2
    else:
        rows = conn.execute(
            f"SELECT {FIELDS} FROM publications p {join} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit + 1, offset],
        ).fetchall()
    hits = [row_dict(row) for row in rows[:limit]]
    return {"results": group_results(conn, hits), "has_more": len(rows) > limit, "next_offset": offset + len(hits)}


def autocomplete(conn, q, kind="title", limit=10):
    q = q.strip()
    if not q:
        return []
    if kind == "author":
        norm = fold_accents(q)
        table = "author_folded" if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='author_folded'").fetchone() else "author_names"
        if table == "author_names":
            norm = q.casefold()
        column = "name_folded" if table == "author_folded" else "name_norm"
        rows = conn.execute(
            f"SELECT name, publications FROM {table} WHERE {column}>=? AND {column}<? ORDER BY publications DESC, {column} ASC LIMIT ?",
            (norm, norm + "\uffff", limit),
        ).fetchall()
        return [dict(row) for row in rows]
    match = match_query(q)
    if not match:
        return []
    rows = conn.execute(
        "SELECT p.dblp_key, p.title, p.year FROM pub_fts JOIN publications p ON p.id=pub_fts.rowid "
        "WHERE pub_fts MATCH ? AND p.title<>'' ORDER BY bm25(pub_fts, 5.0, 0.0, 0.0, 0.0) LIMIT ?",
        (match, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def closest(conn, q, limit=10):
    q = q.strip()
    if len(q) < 3:
        return []
    words = WORDS.findall(q.casefold())[:8]
    if not words:
        return []
    # Short word prefixes tolerate missing or transposed letters. The
    # intersection stays selective, so retrieving candidates is fast even on
    # the complete dblp index; similarity reranks the candidate titles.
    prefixes = [word[:2] if len(word) >= 4 else word for word in words]
    query = " AND ".join(f"title:{prefix}*" for prefix in prefixes)
    statement = (
        "SELECT p.dblp_key, p.title, p.authors, p.year, p.venue, p.record_type FROM pub_fts "
        "JOIN publications p ON p.id=pub_fts.rowid WHERE pub_fts MATCH ? LIMIT 5000"
    )
    candidates = [dict(row) for row in conn.execute(statement, (query,))]
    if not candidates and len(words) > 1:
        for index in range(len(words)):
            relaxed = " AND ".join(f"title:{prefix}*" for i, prefix in enumerate(prefixes) if i != index)
            for row in conn.execute(statement, (relaxed,)):
                candidates.append(dict(row))
            if candidates:
                break
    def similarity(row):
        title = row["title"].casefold()
        needle = q.casefold()
        whole = SequenceMatcher(None, needle, title).ratio()
        if len(WORDS.findall(needle)) == 1:
            words = WORDS.findall(title)
            return max([whole] + [SequenceMatcher(None, needle, word).ratio() for word in words])
        return whole
    candidates.sort(key=similarity, reverse=True)
    return candidates[:limit]


def record(conn, key):
    row = conn.execute(f"SELECT {FIELDS} FROM publications p WHERE p.dblp_key=?", (key,)).fetchone()
    if row is None:
        return None
    result = row_dict(row)
    result["people"] = [dict(person) for person in conn.execute(
        "SELECT name,pid FROM publication_authors WHERE publication_id=(SELECT id FROM publications WHERE dblp_key=?)", (key,)
    )]
    return result
