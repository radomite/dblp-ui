"""Download and refresh the dblp archive without interrupting the web server."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .importer import SCHEMA_VERSION, create_index
from .migrate_artifacts import migrate as migrate_artifacts
from .server import DB_PATH, main as serve
from .search import fold_accents

DATA_DIR = Path(os.environ.get("DBLP_DATA_DIR", "/data"))
STATE_PATH = DATA_DIR / "refresh-state.json"
SOURCE_URL = os.environ.get(
    "DBLP_URL",
    "https://drops.dagstuhl.de/storage/artifacts/dblp/xml/{year}/dblp-{date}.xml.gz",
)
TIMEZONE_NAME = os.environ.get("DBLP_TIMEZONE", "Europe/Berlin")
RETRY_SECONDS = 3600


def configured_zone():
    return timezone.utc if TIMEZONE_NAME.upper() in ("UTC", "ETC/UTC") else ZoneInfo(TIMEZONE_NAME)


def next_month(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def draw_update(year, month, zone=None, randbelow=secrets.randbelow):
    """Pick uniformly among seconds on days 1-3, 01:00-03:59 local time."""
    zone = zone or configured_zone()
    day = 1 + randbelow(3)
    second = randbelow(3 * 3600)
    return datetime(year, month, day, 1, tzinfo=zone) + timedelta(seconds=second)


def now_local():
    return datetime.now(configured_zone())


def resolve_source_url(template, at):
    return template.format(year=at.strftime("%Y"), month=at.strftime("%m"), date=at.strftime("%Y-%m-01"))


def save_state(state, path=STATE_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_state(path=STATE_PATH):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def read_metadata(db_path=DB_PATH):
    if not db_path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
            return dict(conn.execute("SELECT key,value FROM metadata"))
    except sqlite3.Error:
        return {}


def ensure_author_index(db_path=DB_PATH):
    """Build accent-insensitive author lookup once per index, without reimporting XML."""
    with closing(sqlite3.connect(db_path)) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='author_folded'").fetchone():
            return
        print("Building accent-insensitive author lookup...", flush=True)
        conn.create_function("fold_accents", 1, fold_accents, deterministic=True)
        conn.execute("BEGIN")
        conn.execute("CREATE TABLE author_folded AS SELECT fold_accents(name) AS name_folded, name, publications FROM author_names")
        conn.execute("CREATE INDEX author_folded_name ON author_folded(name_folded)")
        conn.commit()
        print("Accent-insensitive author lookup ready", flush=True)


def download_archive(url, target, min_bytes=10_000_000):
    """Stream to a temporary file and return its SHA-256 digest."""
    target.unlink(missing_ok=True)
    digest = hashlib.sha256()
    total = 0
    request = Request(url, headers={"User-Agent": "dblp-local-mirror/1.0", "Accept": "application/gzip, application/octet-stream"})
    try:
        with urlopen(request, timeout=120) as response, target.open("wb") as output:
            expected = response.headers.get("Content-Length")
            expected = int(expected) if expected is not None else None
            while chunk := response.read(1024 * 1024):
                if total == 0 and not chunk.startswith(b"\x1f\x8b"):
                    raise ValueError("Download is not a gzip archive")
                output.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                if total % (100 * 1024 * 1024) < len(chunk):
                    print(f"Downloaded {total / 1024**2:.0f} MiB", flush=True)
            if total < min_bytes:
                raise ValueError(f"Archive is unexpectedly small ({total} bytes)")
            if expected is not None and total != expected:
                raise ValueError(f"Incomplete download ({total} of {expected} bytes)")
        return digest.hexdigest()
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def initial_state(db_path=DB_PATH, path=STATE_PATH, zone=None):
    zone = zone or configured_zone()
    state = load_state(path)
    if state.get("next_update_at"):
        return state
    metadata = read_metadata(db_path)
    if metadata.get("downloaded_at"):
        previous = datetime.fromisoformat(metadata["downloaded_at"]).astimezone(zone)
    else:
        previous = datetime.fromtimestamp(db_path.stat().st_mtime, zone)
    year, month = next_month(previous.year, previous.month)
    state = {
        "phase": "idle",
        "last_success_month": previous.strftime("%Y-%m"),
        "last_success_at": previous.isoformat(),
        "next_update_at": draw_update(year, month, zone).isoformat(),
    }
    save_state(state, path)
    return state


def refresh(state, db_path=DB_PATH, data_dir=DATA_DIR, state_path=STATE_PATH,
            url=SOURCE_URL, zone=None, min_bytes=10_000_000):
    zone = zone or configured_zone()
    archive = data_dir / "dblp.download.xml.gz"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        resolved_url = resolve_source_url(url, datetime.now(zone))
        state.update(phase="downloading", last_error=None, retry_at=None)
        save_state(state, state_path)
        print(f"Downloading {resolved_url}", flush=True)
        digest = download_archive(resolved_url, archive, min_bytes)
        metadata = read_metadata(db_path)
        if digest == metadata.get("source_sha256") and metadata.get("schema_version") == "2":
            state["phase"] = "indexing"
            save_state(state, state_path)
            migrate_artifacts(archive, db_path)
        elif digest != metadata.get("source_sha256") or metadata.get("schema_version") != SCHEMA_VERSION:
            state["phase"] = "indexing"
            save_state(state, state_path)
            create_index(archive, db_path, extra_metadata={
                "source_url": resolved_url,
                "source_sha256": digest,
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            })
            ensure_author_index(db_path)
        else:
            print("Archive unchanged; keeping existing index", flush=True)
        completed = datetime.now(zone)
        year, month = next_month(completed.year, completed.month)
        state.update(
            phase="idle", last_success_month=completed.strftime("%Y-%m"),
            last_success_at=completed.isoformat(),
            next_update_at=draw_update(year, month, zone).isoformat(),
            last_error=None, retry_at=None,
        )
        save_state(state, state_path)
        print(f"Next update: {state['next_update_at']}", flush=True)
        return True
    except Exception as exc:
        state.update(
            phase="error", last_error=str(exc),
            retry_at=(datetime.now(zone) + timedelta(seconds=RETRY_SECONDS)).isoformat(),
        )
        save_state(state, state_path)
        print(f"Refresh failed: {exc}; retrying in {RETRY_SECONDS // 60} minutes", flush=True)
        return False
    finally:
        archive.unlink(missing_ok=True)


def scheduler():
    while True:
        state = load_state()
        try:
            due = datetime.fromisoformat(state["next_update_at"])
            if state.get("retry_at"):
                due = max(due, datetime.fromisoformat(state["retry_at"]))
            delay = (due - now_local()).total_seconds()
            if delay > 0:
                time.sleep(min(delay, 3600))
                continue
            refresh(state)
        except Exception as exc:
            print(f"Scheduler error: {exc}; retrying in 60 minutes", flush=True)
            time.sleep(RETRY_SECONDS)


def worker():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if read_metadata().get("schema_version") != SCHEMA_VERSION:
        print("No compatible index found; downloading the dblp archive", flush=True)
        state = load_state()
        while not refresh(state):
            time.sleep(RETRY_SECONDS)
    else:
        initial_state()
        print("Using existing index", flush=True)
        ensure_author_index()
    scheduler()


def main():
    configured_zone()
    threading.Thread(target=worker, name="dblp-refresh", daemon=True).start()
    serve()


if __name__ == "__main__":
    main()
