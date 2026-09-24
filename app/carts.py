"""Batch lookups for the browser's citation cart."""

from .search import FIELDS, row_dict


MAX_KEYS = 1000


def validate_keys(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_KEYS:
        raise ValueError(f"Cart must contain 1 to {MAX_KEYS} keys")
    if any(not isinstance(key, str) or not 1 <= len(key) <= 300 for key in value):
        raise ValueError("Invalid publication key")
    if len(set(value)) != len(value):
        raise ValueError("Cart contains duplicate keys")
    return value


def bibliography(conn, keys):
    entries, missing = [], []
    for key in validate_keys(keys):
        row = conn.execute("SELECT bibtex FROM publications WHERE dblp_key=?", (key,)).fetchone()
        if row is None:
            missing.append(key)
        else:
            entries.append({"key": "DBLP:" + key, "bibtex": row[0]})
    return {"entries": entries, "missing": missing}


def records(conn, keys):
    found, missing = [], []
    for key in validate_keys(keys):
        row = conn.execute(f"SELECT {FIELDS} FROM publications p WHERE p.dblp_key=?", (key,)).fetchone()
        if row is None:
            missing.append(key)
        else:
            found.append(row_dict(row))
    return {"records": found, "missing": missing}
