import sqlite3
import os

DB_DIR = os.path.expanduser("~/.sift")
DB_PATH = os.path.join(DB_DIR, "sift.db")


class DB:
    """SQLite FTS5-backed database manager for Sift search index."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                feed_url TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL DEFAULT 'feed',
                added_at TEXT NOT NULL DEFAULT (datetime('now'))
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
                title,
                content,
                content='pages',
                content_rowid='id',
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

    def close(self):
        self.conn.close()

    def add_source(self, name, feed_url, kind="feed"):
        cursor = self.conn.execute(
            "INSERT INTO sources (name, feed_url, kind) VALUES (?, ?, ?)",
            (name, feed_url, kind),
        )
        self.conn.commit()
        return cursor.lastrowid

    def add_page(self, url, title, content, source_id=None, pulse_id=None, link_depth=0):
        cursor = self.conn.execute(
            """INSERT INTO pages (url, title, content, source_id, pulse_id, link_depth)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   title = excluded.title,
                   content = excluded.content,
                   source_id = COALESCE(excluded.source_id, pages.source_id),
                   pulse_id = COALESCE(excluded.pulse_id, pages.pulse_id),
                   link_depth = excluded.link_depth,
                   fetched_at = datetime('now')""",
            (url, title, content, source_id, pulse_id, link_depth),
        )
        self.conn.commit()
        return cursor.lastrowid

    def search(self, query, limit=10, fresh=False):
        order_clause = (
            "ORDER BY rank / MAX(1.0, julianday('now') - COALESCE(julianday(p.fetched_at), julianday('now')))"
            if fresh
            else "ORDER BY rank"
        )
        sql = f"""SELECT p.id, p.url, p.title, p.content, p.source_id, p.fetched_at,
                         p.pulse_id, p.link_depth,
                         snippet(pages_fts, 1, '<b>', '</b>', '...', 64) AS excerpt
                  FROM pages_fts
                  JOIN pages p ON pages_fts.rowid = p.id
                  WHERE pages_fts MATCH ?
                  {order_clause}
                  LIMIT ?"""
        cursor = self.conn.execute(sql, (query, limit))
        return [dict(row) for row in cursor.fetchall()]

    def get_sources(self):
        cursor = self.conn.execute("SELECT * FROM sources ORDER BY added_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def get_stats(self):
        cursor = self.conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM pages) AS total_pages,
                (SELECT COUNT(*) FROM sources) AS total_sources,
                (SELECT COUNT(*) FROM pulses) AS total_pulses,
                (SELECT COUNT(*) FROM pages WHERE pulse_id IS NOT NULL) AS pulse_pages,
                (SELECT COUNT(*) FROM pages WHERE source_id IS NOT NULL) AS feed_pages,
                (SELECT MAX(fetched_at) FROM pages) AS newest_page
        """)
        return dict(cursor.fetchone())
