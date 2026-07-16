"""SQLite storage for Sift, with optional SQLCipher encryption."""

import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DB_DIR = os.path.expanduser("~/.sift")
DB_PATH = os.path.join(DB_DIR, "sift.db")
KEY_ENV = "SIFT_DB_KEY"


def _normalize_source_url(url):
    """Return a stable HTTP(S) source identity without fragments/default ports."""
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not scheme or not host:
        return url.strip()
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/") or ""
    return urlunsplit((scheme, host, path, parsed.query, ""))


class DatabaseError(RuntimeError):
    """Base class for database configuration and access errors."""


class MissingDatabaseKey(DatabaseError):
    """Raised when encrypted mode is requested without a key."""


class InvalidDatabaseKey(DatabaseError):
    """Raised when an encrypted database cannot be opened with the key."""


class _CipherRow(dict):
    """Row compatible with sqlite3.Row for SQLCipher's separate DB-API type."""

    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _cipher_row_factory(cursor, values):
    return _CipherRow([column[0] for column in cursor.description], values)


def _sqlcipher_module():
    try:
        import sqlcipher3 as driver
    except ImportError as exc:
        raise DatabaseError(
            "Encrypted mode requires the optional 'sqlcipher3' package; "
            "install Sift with the encrypted extra"
        ) from exc
    return driver


def _key_pragma(key):
    """Return a SQLCipher key pragma without exposing the key in exceptions."""
    if "\x00" in key:
        raise DatabaseError("Database key must not contain NUL characters")
    # SQLCipher's PRAGMA key syntax does not accept DB-API parameters.
    escaped = key.replace("'", "''")
    return f"PRAGMA key = '{escaped}'"


class DB:
    """SQLite FTS5 database manager; encryption is opt-in and fail-closed."""

    def __init__(self, db_path=None, *, encrypted=False, key=None):
        self.db_path = Path(db_path or DB_PATH).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.encrypted = encrypted
        if encrypted:
            self.key = key if key is not None else os.environ.get(KEY_ENV)
            if not self.key:
                raise MissingDatabaseKey(
                    f"Encrypted mode requires a non-empty {KEY_ENV} environment variable"
                )
            driver = _sqlcipher_module()
            # sqlcipher3 exposes connect at runtime, but its extension module does
            # not publish enough static metadata for pylint to discover it.
            self.conn = driver.connect(str(self.db_path))  # pylint: disable=no-member
            try:
                self.conn.execute(_key_pragma(self.key))
                self.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            except Exception as exc:
                self.conn.close()
                raise InvalidDatabaseKey(
                    "Unable to open encrypted database; check the database key"
                ) from exc
        else:
            self.key = None
            self.conn = sqlite3.connect(self.db_path)

        self.conn.row_factory = _cipher_row_factory if self.encrypted else sqlite3.Row
        self._configure()
        self._init_schema()
        self._migrate_source_uniqueness()

    def _configure(self):
        self.conn.execute("PRAGMA foreign_keys=ON")
        if self.encrypted:
            # WAL/shm sidecars can retain recoverable pages. DELETE journaling
            # keeps encrypted databases self-contained and avoids plaintext temp files.
            self.conn.execute("PRAGMA journal_mode=DELETE")
            self.conn.execute("PRAGMA temp_store=MEMORY")
        else:
            self.conn.execute("PRAGMA journal_mode=WAL")

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                feed_url TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'feed',
                added_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(feed_url, kind)
            );

            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                title TEXT,
                content TEXT,
                source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
                fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
                pulse_id INTEGER,
                link_depth INTEGER NOT NULL DEFAULT 0
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                title, content, content='pages', content_rowid='id',
                tokenize='porter unicode61'
            );

            CREATE TABLE IF NOT EXISTS pulses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                depth INTEGER NOT NULL DEFAULT 1,
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                finished_at TEXT,
                pages_found INTEGER NOT NULL DEFAULT 0
            );

            CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
                INSERT INTO pages_fts(rowid, title, content)
                VALUES (new.id, new.title, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
                INSERT INTO pages_fts(pages_fts, rowid, title, content)
                VALUES ('delete', old.id, old.title, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
                INSERT INTO pages_fts(pages_fts, rowid, title, content)
                VALUES ('delete', old.id, old.title, old.content);
                INSERT INTO pages_fts(rowid, title, content)
                VALUES (new.id, new.title, new.content);
            END;
        """)

    def _migrate_source_uniqueness(self):
        """Normalize/deduplicate sources and upgrade the legacy constraint."""
        indexes = self.conn.execute("PRAGMA index_list(sources)").fetchall()
        legacy = False
        for index in indexes:
            if not index["unique"]:
                continue
            columns = self.conn.execute(
                f"PRAGMA index_info('{index['name']}')"
            ).fetchall()
            if [column["name"] for column in columns] == ["feed_url"]:
                legacy = True
                break
        source_rows = self.conn.execute(
            "SELECT id, name, feed_url, kind, added_at FROM sources ORDER BY id"
        ).fetchall()
        retained = []
        retained_by_identity = {}
        duplicate_ids = {}
        needs_cleanup = False
        for row in source_rows:
            normalized = _normalize_source_url(row["feed_url"])
            identity = (normalized, row["kind"])
            retained_id = retained_by_identity.get(identity)
            if retained_id is not None:
                duplicate_ids[row["id"]] = retained_id
                needs_cleanup = True
                continue
            retained_by_identity[identity] = row["id"]
            retained.append(
                (row["id"], row["name"], normalized, row["kind"], row["added_at"])
            )
            needs_cleanup = needs_cleanup or normalized != row["feed_url"]

        if not legacy and not needs_cleanup:
            return

        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys=OFF")
        try:
            self.conn.execute("BEGIN")
            # Repointing ownership does not change indexed text. Avoid firing the
            # external-content FTS update trigger for legacy rows that predate
            # pages_fts, because deleting a missing FTS row corrupts the index.
            self.conn.execute("DROP TRIGGER IF EXISTS pages_au")
            for duplicate_id, retained_id in duplicate_ids.items():
                self.conn.execute(
                    "UPDATE pages SET source_id = ? WHERE source_id = ?",
                    (retained_id, duplicate_id),
                )
            self.conn.execute("DROP TABLE IF EXISTS sources_new")
            self.conn.execute("""
                CREATE TABLE sources_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    feed_url TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'feed',
                    added_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(feed_url, kind)
                )
            """)
            self.conn.executemany(
                """INSERT INTO sources_new (id, name, feed_url, kind, added_at)
                   VALUES (?, ?, ?, ?, ?)""",
                retained,
            )
            self.conn.execute("DROP TABLE sources")
            self.conn.execute("ALTER TABLE sources_new RENAME TO sources")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @classmethod
    def migrate_plaintext(cls, source_path, destination_path, key):
        """Copy a plaintext Sift DB into a new encrypted DB without deleting source."""
        if not key:
            raise MissingDatabaseKey("Migration requires a non-empty database key")
        source = cls(source_path)
        destination = cls(destination_path, encrypted=True, key=key)
        try:
            for table in ("sources", "pages", "pulses"):
                columns = {
                    "sources": "name, feed_url, kind, added_at",
                    "pages": "url, title, content, source_id, fetched_at, pulse_id, link_depth",
                    "pulses": "query, depth, started_at, finished_at, pages_found",
                }[table]
                rows = source.conn.execute(f"SELECT {columns} FROM {table}").fetchall()
                placeholders = ", ".join("?" for _ in columns.split(", "))
                destination.conn.executemany(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                    [tuple(row) for row in rows],
                )
            destination.conn.commit()
            destination.conn.execute("INSERT INTO pages_fts(pages_fts) VALUES ('rebuild')")
            destination.conn.commit()
        finally:
            source.close()
            destination.close()

    def add_source(self, name, feed_url, kind="feed"):
        normalized = _normalize_source_url(feed_url)
        rows = self.conn.execute(
            "SELECT id, feed_url FROM sources WHERE kind = ?", (kind,)
        ).fetchall()
        for row in rows:
            if _normalize_source_url(row["feed_url"]) == normalized:
                if row["feed_url"] != normalized:
                    self.conn.execute(
                        "UPDATE sources SET feed_url = ? WHERE id = ?",
                        (normalized, row["id"]),
                    )
                    self.conn.commit()
                return row["id"]
        try:
            cursor = self.conn.execute(
                "INSERT INTO sources (name, feed_url, kind) VALUES (?, ?, ?)",
                (name, normalized, kind),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            row = self.conn.execute(
                "SELECT id FROM sources WHERE feed_url = ? AND kind = ?",
                (normalized, kind),
            ).fetchone()
            if row is None:
                raise
            return row["id"]

    def add_page(self, url, title, content, source_id=None, pulse_id=None, link_depth=0):
        cursor = self.conn.execute(
            """INSERT INTO pages (url, title, content, source_id, pulse_id, link_depth)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET title = excluded.title,
                   content = excluded.content,
                   source_id = COALESCE(excluded.source_id, pages.source_id),
                   pulse_id = COALESCE(excluded.pulse_id, pages.pulse_id),
                   link_depth = excluded.link_depth, fetched_at = datetime('now')""",
            (url, title, content, source_id, pulse_id, link_depth),
        )
        self.conn.commit()
        return cursor.lastrowid

    def add_pulse(self, query, depth):
        """Create a pulse record and return its stable identifier."""
        cursor = self.conn.execute(
            "INSERT INTO pulses (query, depth) VALUES (?, ?)", (query, depth)
        )
        self.conn.commit()
        return cursor.lastrowid

    def finish_pulse(self, pulse_id, pages_found):
        """Mark a pulse complete with its final global page count."""
        self.conn.execute(
            "UPDATE pulses SET finished_at=datetime('now'),"
            " pages_found=? WHERE id=?",
            (pages_found, pulse_id),
        )
        self.conn.commit()

    def search(self, query, limit=10, fresh=False):
        if not query or not query.strip() or limit < 1:
            return []
        if re.search(r'[-:()*"\'\[\]\\]', query):
            query = f'"{query}"'
        order = (
            "ORDER BY rank / MAX(1.0, julianday('now') - "
            "COALESCE(julianday(p.fetched_at), julianday('now')))"
            if fresh else "ORDER BY rank"
        )
        sql = f"""SELECT p.id, p.url, p.title, p.content, p.source_id, p.fetched_at,
                   p.pulse_id, p.link_depth,
                   snippet(pages_fts, 1, '<b>', '</b>', '...', 64) AS excerpt
                   FROM pages_fts JOIN pages p ON pages_fts.rowid = p.id
                   WHERE pages_fts MATCH ? {order} LIMIT ?"""
        return [dict(row) for row in self.conn.execute(sql, (query, limit)).fetchall()]

    def get_sources(self, kind=None):
        sql = "SELECT * FROM sources"
        params = ()
        if kind is not None:
            sql += " WHERE kind = ?"
            params = (kind,)
        sql += " ORDER BY added_at DESC, id DESC"
        return [dict(row) for row in self.conn.execute(sql, params)]

    def get_stats(self):
        cursor = self.conn.execute("""
            SELECT (SELECT COUNT(*) FROM pages) AS total_pages,
                   (SELECT COUNT(*) FROM sources) AS total_sources,
                   (SELECT COUNT(*) FROM sources WHERE kind = 'feed') AS feed_sources,
                   (SELECT COUNT(*) FROM sources WHERE kind = 'crawl') AS crawl_sources,
                   (SELECT COUNT(*) FROM pulses) AS total_pulses,
                   (SELECT COUNT(*) FROM pages WHERE pulse_id IS NOT NULL) AS pulse_pages,
                   (SELECT COUNT(*) FROM pages p JOIN sources s ON s.id = p.source_id
                    WHERE s.kind = 'feed') AS feed_pages,
                   (SELECT COUNT(*) FROM pages p JOIN sources s ON s.id = p.source_id
                    WHERE s.kind = 'crawl') AS crawl_pages,
                   (SELECT MAX(fetched_at) FROM pages) AS newest_page
        """)
        return dict(cursor.fetchone())
