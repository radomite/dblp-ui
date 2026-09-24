import gzip
import hashlib
import io
import json
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from app import manager
from app import search as search_module
from app.manual import OVERRIDES, load_consolidations
from app.migrate_artifacts import migrate
from app.importer import create_index
from app import server as web
from app.search import author_identity, autocomplete, closest, fold_accents, normalized_title, same_work_title, search


XML = b'''<?xml version="1.0" encoding="ISO-8859-1"?>
<!DOCTYPE dblp SYSTEM "dblp-2023-06-28.dtd">
<dblp>
<article key="journals/example/One" mdate="2026-01-01">
<author>Ada Example</author><title>Database &auml;lgorithms &amp; tools.</title>
<year>2026</year><journal>ACM Trans. Database Syst.</journal><pages>1-12</pages>
<ee>https://doi.org/10.1234/example</ee>
</article>
<inproceedings key="conf/icml/Two" mdate="2026-01-01">
<author>Grace Example</author><title>Learning systems.</title>
<year>2026</year><booktitle>International Conference on Machine Learning</booktitle>
</inproceedings>
<article key="journals/example/Three" mdate="2026-01-02">
<author>Grace Example</author><title>Learning systems.</title>
<year>2027</year><journal>Journal of Learning Systems</journal>
</article>
<article key="journals/corr/Four" mdate="2026-01-03">
<author>Grace Example</author><title>Learning systems (preliminary version).</title>
<year>2028</year><journal>CoRR</journal>
</article>
<article key="journals/example/Five" mdate="2026-01-04">
<author>Ada Example</author><title>Learning systems.</title>
<year>2029</year><journal>Another Journal</journal>
</article>
<book key="books/example/Six" mdate="2026-01-05">
<author>Ada Example</author><title>A monograph on algorithms.</title><year>2026</year>
</book>
<data key="data/example/Seven" mdate="2026-01-06">
<author>Ada Example</author><title>Algorithm data artifact.</title><year>2026</year><publisher>Zenodo</publisher>
</data>
<www key="homepages/example/Eight" mdate="2026-01-07">
<author>Ada Example</author><title>Home Page</title><year>2026</year>
</www>
<www key="homepages/example/Nine" mdate="2026-01-08">
<author>Grace Example</author><title>Home Page</title><year>2026</year>
</www>
<www key="homepages/example/Ten" mdate="2026-01-09">
<author>Ada Example</author><title>Home-Page</title><year>2025</year>
</www>
<article key="journals/iacr/Eleven" mdate="2026-01-10">
<author>Cryptographer Example</author><title>A sample IACR preprint.</title><year>2024</year>
<journal>IACR Cryptol. ePrint Arch.</journal>
</article>
</dblp>'''


class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


class ManagerTests(unittest.TestCase):
    def test_title_variants_are_narrow_and_author_grouping_stays_exact(self):
        self.assertEqual(normalized_title('Almost-Ramanujan Expanders.'), normalized_title('Almost Ramanujan Expanders'))
        self.assertEqual(normalized_title('Learning systems (preliminary version).'), normalized_title('Learning systems.'))
        self.assertTrue(same_work_title(normalized_title('On Pseudorandomness with respect to Deterministic Observes.'), normalized_title('On Pseudorandomness with respect to Deterministic Observers.')))
        self.assertFalse(same_work_title(normalized_title('A direct sum theorem for corruption.'), normalized_title('A strong direct product theorem for corruption.')))

    def test_author_identity_ignores_order_but_not_people(self):
        self.assertEqual(author_identity("Ada Example, Grace Example", "one"), author_identity("Grace Example, Ada Example", "two"))
        self.assertNotEqual(author_identity("Ada Example", "one"), author_identity("Grace Example", "two"))

    def test_manual_rules_are_loaded(self):
        self.assertEqual(OVERRIDES["venues"]["IACR Cryptol. ePrint Arch."], {"label": "IACR", "preprint": True})
        self.assertEqual(load_consolidations(), {})

    def test_author_autocomplete_uses_publication_count(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE author_names (name_norm TEXT, name TEXT, publications INTEGER)")
            conn.executemany("INSERT INTO author_names VALUES (?,?,?)", [
                ("alan z", "Alan Z", 2), ("alan b", "Alan B", 10), ("alan a", "Alan A", 10),
            ])
            self.assertEqual([item["name"] for item in autocomplete(conn, "Alan", "author")], ["Alan A", "Alan B", "Alan Z"])

    def test_author_autocomplete_ignores_accents(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE author_folded (name_folded TEXT, name TEXT, publications INTEGER)")
            conn.executemany("INSERT INTO author_folded VALUES (?,?,?)", [
                (fold_accents("Dániel Marx"), "Dániel Marx", 100),
                (fold_accents("Dàniel Other"), "Dàniel Other", 10),
            ])
            self.assertEqual([item["name"] for item in autocomplete(conn, "Daniel Marx", "author")], ["Dániel Marx"])

    def test_status_while_first_index_is_building(self):
        missing_db = Path(__file__).parent / ".missing-dblp.sqlite3"
        self.assertFalse(missing_db.exists())
        with patch.object(web, "DB_PATH", missing_db), ThreadingHTTPServer(("127.0.0.1", 0), web.Handler) as httpd:
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{httpd.server_port}/api/status") as response:
                    self.assertFalse(json.load(response)["ready"])
                    self.assertIn("noindex", response.headers["X-Robots-Tag"])
            finally:
                httpd.shutdown()
                thread.join(timeout=5)

    def test_monthly_slot(self):
        slot = manager.draw_update(2027, 1, timezone.utc, lambda n: n - 1)
        self.assertEqual(slot, datetime(2027, 1, 3, 3, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(manager.next_month(2026, 12), (2027, 1))
        self.assertEqual(
            manager.resolve_source_url(manager.SOURCE_URL, datetime(2026, 9, 23, tzinfo=timezone.utc)),
            "https://drops.dagstuhl.de/storage/artifacts/dblp/xml/2026/dblp-2026-09-01.xml.gz",
        )

    def test_artifact_migration_preserves_existing_index(self):
        archive = Path(__file__).parent / ".migration-dblp.xml.gz"
        db = Path(__file__).parent / ".migration-dblp.sqlite3"
        try:
            compressed = gzip.compress(XML)
            archive.write_bytes(compressed)
            create_index(archive, db, limit=5, extra_metadata={"source_sha256": hashlib.sha256(compressed).hexdigest()})
            manager.ensure_author_index(db)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
                conn.commit()
            self.assertEqual(migrate(archive, db), 1)
            with closing(sqlite3.connect(db)) as conn:
                conn.row_factory = sqlite3.Row
                self.assertEqual(search(conn, q="artifact", category="artifact")["results"][0]["dblp_key"], "data/example/Seven")
                self.assertEqual(autocomplete(conn, "Ada", "author")[0]["publications"], 3)
                self.assertEqual(dict(conn.execute("SELECT key,value FROM metadata"))["schema_version"], "3")
        finally:
            archive.unlink(missing_ok=True)
            db.unlink(missing_ok=True)
            db.with_name(db.name + ".building").unlink(missing_ok=True)

    def test_download_build_skip_unchanged_and_preserve_on_failure(self):
        directory = Path(__file__).parent
        db = directory / ".test-dblp.sqlite3"
        state_path = directory / ".test-refresh-state.json"
        archive = directory / "dblp.download.xml.gz"
        building = directory / ".test-dblp.sqlite3.building"
        for path in (db, state_path, archive, building):
            path.unlink(missing_ok=True)
        state = {}
        data = gzip.compress(XML)
        try:
            with patch.object(manager, "urlopen", return_value=Response(data)):
                self.assertTrue(manager.refresh(state, db, directory, state_path, "https://example.test/dblp.xml.gz", timezone.utc, 0))
            self.assertFalse(archive.exists())
            self.assertEqual(state["phase"], "idle")
            slot = datetime.fromisoformat(state["next_update_at"])
            self.assertIn(slot.day, (1, 2, 3))
            self.assertGreaterEqual(slot.hour, 1)
            self.assertLess(slot.hour, 4)
            self.assertEqual(manager.initial_state(db, state_path, timezone.utc)["next_update_at"], state["next_update_at"])
            with closing(sqlite3.connect(db)) as conn:
                conn.row_factory = sqlite3.Row
                self.assertEqual(conn.execute("SELECT title FROM publications ORDER BY id").fetchone()[0], "Database älgorithms & tools.")
                journal = search(conn, venue="TODS")["results"]
                self.assertEqual(journal[0]["dblp_key"], "journals/example/One")
                self.assertEqual(search(conn, q="TODS")["results"][0]["dblp_key"], "journals/example/One")
                self.assertEqual(search(conn, q="database", venue="TODS")["results"][0]["dblp_key"], "journals/example/One")
                self.assertEqual(search(conn, author="Ada Example", category="journal")["results"][0]["reference_type"], "journal")
                self.assertEqual(search(conn, author="Grace Example", category="conference")["results"][0]["version_count"], 3)
                self.assertEqual(search(conn, author="Ada Example", category="monograph")["results"][0]["dblp_key"], "books/example/Six")
                self.assertEqual(search(conn, author="Ada Example", category="artifact")["results"][0]["dblp_key"], "data/example/Seven")
                self.assertEqual(search(conn, category="artifact")["results"][0]["dblp_key"], "data/example/Seven")
                self.assertEqual({group["dblp_key"] for group in search(conn, author="Ada Example", category="monograph,artifact")["results"]}, {"books/example/Six", "data/example/Seven"})
                self.assertEqual(search(conn, category="informal")["results"][0]["version_count"], 3)
                self.assertEqual(search(conn, q="sample IACR", category="informal")["results"][0]["reference_type"], "preprint")
                self.assertFalse(search(conn, q="sample IACR", category="journal")["results"])
                self.assertFalse(search(conn, category="journal", q="algorithm data artifact")["results"])
                self.assertEqual(sorted(group["version_count"] for group in search(conn, q="Home Page")["results"]), [1, 2])
                self.assertIn("@misc{DBLP:data/example/Seven,", conn.execute("SELECT bibtex FROM publications WHERE dblp_key='data/example/Seven'").fetchone()[0])
                learning = search(conn, venue="ICML")["results"][0]
                self.assertEqual(learning["dblp_key"], "journals/example/Three")
                self.assertEqual([item["dblp_key"] for item in learning["versions"]], ["journals/example/Three", "conf/icml/Two", "journals/corr/Four"])
                learning_groups = search(conn, q="learning")["results"]
                self.assertEqual(sorted(group["version_count"] for group in learning_groups), [1, 3])
                self.assertEqual(learning_groups[0]["version_count"], 3)
                newest = search(conn, q="learning", sort="year_desc")
                self.assertEqual([group["dblp_key"] for group in newest["results"]], ["journals/example/Five", "journals/example/Three"])
                self.assertEqual(search(conn, q="learning", sort="year_desc", limit=1, offset=1)["results"][0]["dblp_key"], "journals/example/Three")
                self.assertEqual(search(conn, q="learning", limit=1)["next_offset"], 1)
                manual_keys = frozenset(("journals/example/Three", "homepages/example/Nine", "journals/example/Five"))
                with patch.object(search_module, "CONSOLIDATIONS_BY_KEY", {key: manual_keys for key in manual_keys}):
                    linked = search(conn, q="learning")["results"]
                    grace = next(group for group in linked if group["dblp_key"] == "journals/example/Three")
                    self.assertIn("homepages/example/Nine", {version["dblp_key"] for version in grace["versions"]})
                    self.assertIn("conf/icml/Two", {version["dblp_key"] for version in grace["versions"]})
                    self.assertNotIn("journals/example/Five", {version["dblp_key"] for version in grace["versions"]})
                    from_homepage = search(conn, q="Home Page")["results"]
                    grace_from_homepage = next(group for group in from_homepage if group["dblp_key"] == "journals/example/Three")
                    self.assertEqual({version["dblp_key"] for version in grace_from_homepage["versions"]},
                                     {"homepages/example/Nine", "journals/example/Three", "conf/icml/Two", "journals/corr/Four"})
                self.assertIn("journals/example/One", [group["dblp_key"] for group in search(conn, author="Ada Example")["results"]])
                self.assertEqual(closest(conn, "databse algorithms")[0]["dblp_key"], "journals/example/One")
                bibtex = conn.execute("SELECT bibtex FROM publications WHERE dblp_key='journals/example/One'").fetchone()[0]
                self.assertIn("@article{DBLP:journals/example/One,", bibtex)
                self.assertIn("title = {Database älgorithms \\& tools.}", bibtex)
                self.assertIn("pages = {1-12}", bibtex)
                self.assertIn("doi = {10.1234/example}", bibtex)
            with patch.object(web, "DB_PATH", db), ThreadingHTTPServer(("127.0.0.1", 0), web.Handler) as httpd:
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{httpd.server_port}"
                    with urlopen(base + "/api/search?venue=TODS") as response:
                        self.assertIn("noindex", response.headers["X-Robots-Tag"])
                        self.assertNotIn("bibtex", json.load(response)["results"][0])
                    with urlopen(base + "/api/search?category=artifact") as response:
                        self.assertEqual(json.load(response)["results"][0]["dblp_key"], "data/example/Seven")
                    with urlopen(base + "/api/search?author=Ada%20Example&limit=300") as response:
                        self.assertTrue(json.load(response)["results"])
                    with urlopen(base + "/") as response:
                        self.assertIn("noindex", response.headers["X-Robots-Tag"])
                        html = response.read().decode("utf-8")
                        self.assertIn("Add best reference for", html)
                        self.assertIn('id="show-cart"', html)
                        self.assertIn('id="reset-cart"', html)
                        self.assertIn('name="checkout-mode"', html)
                        self.assertNotIn('id="save-cart"', html)
                    keys = ["journals/example/One", "conf/icml/Two"]
                    def post(path, payload):
                        request = Request(base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                        with urlopen(request) as response:
                            return json.load(response)
                    cards = post("/api/records", {"keys": keys})
                    self.assertEqual([item["dblp_key"] for item in cards["records"]], keys)
                    self.assertEqual(cards["missing"], [])
                    self.assertNotIn("bibtex", cards["records"][0])
                    bibliography = post("/api/bibtex", {"keys": keys})
                    self.assertEqual(bibliography["entries"][0]["bibtex"], bibtex)
                    self.assertEqual([entry["key"] for entry in bibliography["entries"]], ["DBLP:" + key for key in keys])
                    self.assertEqual(bibliography["missing"], [])
                finally:
                    httpd.shutdown()
                    thread.join(timeout=5)
            original_hash = manager.read_metadata(db)["source_sha256"]
            with patch.object(manager, "urlopen", return_value=Response(data)), patch.object(manager, "create_index", side_effect=AssertionError("Unchanged archive rebuilt")):
                self.assertTrue(manager.refresh(state, db, directory, state_path, "https://example.test/dblp.xml.gz", timezone.utc, 0))
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("UPDATE metadata SET value='1' WHERE key='schema_version'")
                conn.commit()
            with patch.object(manager, "urlopen", return_value=Response(data)):
                self.assertTrue(manager.refresh(state, db, directory, state_path, "https://example.test/dblp.xml.gz", timezone.utc, 0))
            self.assertEqual(manager.read_metadata(db)["schema_version"], "3")
            with patch.object(manager, "urlopen", return_value=Response(b"not gzip")):
                self.assertFalse(manager.refresh(state, db, directory, state_path, "https://example.test/dblp.xml.gz", timezone.utc, 0))
            self.assertEqual(manager.read_metadata(db)["source_sha256"], original_hash)
            self.assertEqual(state["phase"], "error")
            self.assertFalse(archive.exists())
        finally:
            for path in (db, state_path, archive, building):
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
