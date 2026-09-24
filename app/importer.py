"""Stream a dblp XML dump into a replaceable SQLite search index."""

import gzip
import html.entities
import io
import os
from pathlib import Path
import re
import sqlite3
import time
import xml.etree.ElementTree as ET

from .bibtex import FIELD_ORDER, make_bibtex
from .venues import aliases

PUBLICATION_TYPES = {
    "article", "inproceedings", "proceedings", "book", "incollection",
    "phdthesis", "mastersthesis", "www", "data",
}
DOCTYPE = re.compile(rb"<!DOCTYPE\s+dblp\s+SYSTEM\s+['\"][^'\"]+\.dtd['\"]\s*>")
XML_PATH = Path(os.environ.get("DBLP_XML", "dblp.xml.gz"))
DB_PATH = Path(os.environ.get("DBLP_DB", "dblp.sqlite3"))
SCHEMA_VERSION = "3"
INSERT_PUBLICATION = (
    "INSERT INTO publications(dblp_key,record_type,title,authors,year,venue,venue_aliases,doi,ee,mdate,bibtex) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
)


def _entity_declarations():
    # dblp's DTD declares HTML-style named character entities. Keep parsing
    # completely offline, with no network access or need for an extra DTD file.
    names = {
        name[:-1]: value for name, value in html.entities.html5.items()
        if name.endswith(";") and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name[:-1])
    }
    names.pop("amp", None)
    names.pop("lt", None)
    names.pop("gt", None)
    names.pop("apos", None)
    names.pop("quot", None)
    return "<!DOCTYPE dblp [\n" + "\n".join(
        f'<!ENTITY {name} "{"".join(f"&#{ord(c)};" for c in value)}">'
        for name, value in names.items()
    ) + "\n]>"


class DblpStream(io.RawIOBase):
    def __init__(self, source):
        self.source = source
        head = source.read(4096)
        replacement = _entity_declarations().encode("ascii")
        self.pending, count = DOCTYPE.subn(lambda _: replacement, head, count=1)
        if count != 1:
            raise ValueError("Expected a dblp.xml DOCTYPE in the archive header")

    def readable(self):
        return True

    def read(self, size=-1):
        if size < 0:
            result, self.pending = self.pending + self.source.read(), b""
            return result
        result, self.pending = self.pending[:size], self.pending[size:]
        return result + self.source.read(size - len(result))


def text_of(element):
    return "".join(element.itertext()).strip()


def records(path):
    with gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb") as source:
        stream = DblpStream(source)
        context = ET.iterparse(stream, events=("start", "end"))
        _, root = next(context)
        for event, element in context:
            if event == "end" and element.tag in PUBLICATION_TYPES and element.get("key"):
                fields = {}
                for child in element:
                    fields.setdefault(child.tag, []).append(child)
                title = text_of(fields["title"][0]) if fields.get("title") else ""
                people = fields.get("author", []) + fields.get("editor", [])
                authors = [(text_of(person), person.get("pid")) for person in people]
                year_text = text_of(fields["year"][0]) if fields.get("year") else ""
                year = int(year_text) if re.fullmatch(r"\d{4}", year_text) else None
                venue = next((text_of(fields[tag][0]) for tag in ("booktitle", "journal") if fields.get(tag)), "")
                searchable_venue = bool(venue)
                if not venue and element.tag == "proceedings":
                    venue = title
                    searchable_venue = True
                if not venue and fields.get("school"):
                    venue = text_of(fields["school"][0])
                if not venue and element.tag == "data":
                    venue = next((text_of(fields[tag][0]) for tag in ("series", "publisher") if fields.get(tag)), "Data artifact")
                ee = text_of(fields["ee"][0]) if fields.get("ee") else ""
                doi = next((text_of(item) for item in fields.get("ee", []) if "doi.org/" in text_of(item)), "")
                bib_fields = {tag: text_of(fields[tag][0]) for tag in FIELD_ORDER if tag in fields and tag not in ("author", "editor", "url")}
                for role in ("author", "editor"):
                    if fields.get(role):
                        bib_fields[role] = " and ".join(
                            re.sub(r" \d{4}$", "", text_of(person)) for person in fields[role]
                        )
                if ee:
                    bib_fields["ee"] = ee
                yield (
                    element.get("key"), element.tag, title,
                    ", ".join(name for name, _ in authors), year, venue, aliases(venue) if searchable_venue else "", doi, ee,
                    element.get("mdate"), make_bibtex(element.get("key"), element.tag, bib_fields), authors,
                )
                root.clear()


def create_index(xml_path=XML_PATH, db_path=DB_PATH, limit=None, extra_metadata=None):
    xml_path, db_path = Path(xml_path), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = db_path.with_name(db_path.name + ".building")
    if temporary.exists():
        temporary.unlink()
    conn = sqlite3.connect(temporary)
    count = 0
    started = time.monotonic()
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=-131072")
        conn.executescript(Path(__file__).with_name("schema.sql").read_text())
        publications, authors = [], []
        for record in records(xml_path):
            count += 1
            publications.append(record[:11])
            authors.extend((count, name, pid, name.casefold()) for name, pid in record[11] if name)
            if len(publications) >= 5000:
                conn.executemany(INSERT_PUBLICATION, publications)
                conn.executemany("INSERT INTO publication_authors VALUES (?,?,?,?)", authors)
                conn.commit()
                publications.clear(); authors.clear()
            if count % 100000 == 0:
                print(f"Indexed {count:,} records in {time.monotonic()-started:.0f}s", flush=True)
            if limit and count >= limit:
                break
        if publications:
            conn.executemany(INSERT_PUBLICATION, publications)
            conn.executemany("INSERT INTO publication_authors VALUES (?,?,?,?)", authors)
            conn.commit()
        print("Building author and search indexes...", flush=True)
        conn.executescript("""
            CREATE INDEX publication_authors_name ON publication_authors(name_norm, publication_id);
            CREATE INDEX publication_authors_pid ON publication_authors(pid, publication_id) WHERE pid IS NOT NULL;
            CREATE INDEX publication_authors_pub ON publication_authors(publication_id);
            CREATE INDEX publications_year ON publications(year);
            CREATE INDEX publications_type_year ON publications(record_type, year DESC, id DESC);
            CREATE INDEX publications_title ON publications(title COLLATE NOCASE);
            CREATE TABLE author_names AS SELECT name_norm, min(name) AS name, count(*) AS publications
              FROM publication_authors GROUP BY name_norm;
            CREATE UNIQUE INDEX author_names_norm ON author_names(name_norm);
            INSERT INTO pub_fts(pub_fts) VALUES ('rebuild');
            INSERT INTO title_grams(title_grams) VALUES ('rebuild');
        """)
        metadata = [
            ("schema_version", SCHEMA_VERSION),
            ("records", str(count)), ("source", xml_path.name),
            ("source_size", str(xml_path.stat().st_size)),
            ("source_mtime_ns", str(xml_path.stat().st_mtime_ns)),
        ]
        metadata.extend((key, str(value)) for key, value in (extra_metadata or {}).items())
        conn.executemany("INSERT INTO metadata VALUES (?,?)", metadata)
        conn.commit()
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.close()
        os.replace(temporary, db_path)
        print(f"Ready: {count:,} records in {time.monotonic()-started:.0f}s", flush=True)
    except BaseException:
        conn.close()
        if temporary.exists():
            temporary.unlink()
        raise


def main():
    if not XML_PATH.is_file():
        raise SystemExit(f"Archive not found: {XML_PATH}")
    if DB_PATH.is_file():
        try:
            with sqlite3.connect(DB_PATH) as conn:
                metadata = dict(conn.execute("SELECT key,value FROM metadata"))
            if metadata.get("schema_version") == SCHEMA_VERSION and metadata.get("source_size") == str(XML_PATH.stat().st_size) and metadata.get("source_mtime_ns") == str(XML_PATH.stat().st_mtime_ns):
                print("Using existing index", flush=True)
                return
        except sqlite3.DatabaseError:
            pass
    create_index()


if __name__ == "__main__":
    main()
