"""Add dblp data records to an existing v2 index without rebuilding all records."""

from collections import Counter
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

from .importer import INSERT_PUBLICATION, SCHEMA_VERSION, records
from .search import fold_accents


def migrate(xml_path, db_path):
    xml_path, db_path = Path(xml_path), Path(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        metadata = dict(conn.execute("SELECT key,value FROM metadata"))
        if metadata.get("schema_version") != "2":
            raise ValueError("Artifact migration requires a version 2 index")
        with xml_path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != metadata.get("source_sha256"):
            raise ValueError("Artifact migration requires the archive used by the current index")
        artifacts = [record for record in records(xml_path) if record[1] == "data"]
        print(f"Parsed {len(artifacts):,} artifact records", flush=True)
        counts = Counter()
        names = {}
        added = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for record in artifacts:
                cursor = conn.execute("INSERT OR IGNORE INTO publications(dblp_key,record_type,title,authors,year,venue,venue_aliases,doi,ee,mdate,bibtex) VALUES (?,?,?,?,?,?,?,?,?,?,?)", record[:11])
                if not cursor.rowcount:
                    continue
                publication_id = cursor.lastrowid
                conn.execute("INSERT INTO pub_fts(rowid,title,authors,venue,venue_aliases) VALUES (?,?,?,?,?)", (publication_id, record[2], record[3], record[5], record[6]))
                conn.execute("INSERT INTO title_grams(rowid,title) VALUES (?,?)", (publication_id, record[2]))
                for name, pid in record[11]:
                    if not name:
                        continue
                    norm = name.casefold()
                    conn.execute("INSERT INTO publication_authors VALUES (?,?,?,?)", (publication_id, name, pid, norm))
                    counts[norm] += 1
                    names[norm] = min(name, names.get(norm, name))
                added += 1
                if added % 1000 == 0:
                    print(f"Added {added:,} artifacts", flush=True)
            for norm, count in counts.items():
                row = conn.execute("SELECT name, publications FROM author_names WHERE name_norm=?", (norm,)).fetchone()
                display = min(names[norm], row[0]) if row else names[norm]
                total = count + (row[1] if row else 0)
                if row:
                    conn.execute("UPDATE author_names SET name=?,publications=? WHERE name_norm=?", (display, total, norm))
                    updated = conn.execute(
                        "UPDATE author_folded SET name_folded=?,name=?,publications=? WHERE name_folded=? AND name=?",
                        (fold_accents(display), display, total, fold_accents(row[0]), row[0]),
                    )
                    if not updated.rowcount:
                        conn.execute("INSERT INTO author_folded VALUES (?,?,?)", (fold_accents(display), display, total))
                else:
                    conn.execute("INSERT INTO author_names VALUES (?,?,?)", (norm, display, total))
                    conn.execute("INSERT INTO author_folded VALUES (?,?,?)", (fold_accents(display), display, total))
            conn.execute("UPDATE metadata SET value=? WHERE key='schema_version'", (SCHEMA_VERSION,))
            conn.execute("UPDATE metadata SET value=CAST(value AS INTEGER)+? WHERE key='records'", (added,))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    print(f"Artifact migration ready: {added:,} records", flush=True)
    return added


if __name__ == "__main__":
    import os
    migrate(os.environ.get("DBLP_XML", "/data/dblp.download.xml.gz"), os.environ.get("DBLP_DB", "/data/dblp.sqlite3"))
