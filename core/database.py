import os
import json
import sqlite3
from typing import Optional, Dict, List, Any
try:
    from .models import MovieRecord
except ImportError:
    from models import MovieRecord


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "cache.db")


class LocalDatabase:
    """SQLite cache to avoid duplicate network requests, supporting multi-account isolation."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Global metadata cache (shared across accounts)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS douban_subjects (
                    douban_id TEXT PRIMARY KEY,
                    title TEXT,
                    year INTEGER,
                    imdb_id TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS imdb_to_douban (
                    imdb_id TEXT PRIMARY KEY,
                    douban_id TEXT,
                    title TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Douban user watched movies (isolated by user_id)
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='user_douban_movies'")
            sql_row = cursor.fetchone()
            if sql_row and "PRIMARY KEY (user_id, douban_id)" not in sql_row[0]:
                # Migrate old schema to composite primary key
                cursor.execute("""
                    CREATE TABLE user_douban_movies_new (
                        user_id TEXT,
                        douban_id TEXT,
                        title TEXT,
                        year INTEGER,
                        imdb_id TEXT,
                        watched_at TEXT,
                        rating INTEGER,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, douban_id)
                    )
                """)
                cursor.execute("""
                    INSERT OR IGNORE INTO user_douban_movies_new (user_id, douban_id, title, year, imdb_id, watched_at, rating, updated_at)
                    SELECT user_id, douban_id, title, year, imdb_id, watched_at, rating, updated_at FROM user_douban_movies
                """)
                cursor.execute("DROP TABLE user_douban_movies")
                cursor.execute("ALTER TABLE user_douban_movies_new RENAME TO user_douban_movies")
            elif not sql_row:
                cursor.execute("""
                    CREATE TABLE user_douban_movies (
                        user_id TEXT,
                        douban_id TEXT,
                        title TEXT,
                        year INTEGER,
                        imdb_id TEXT,
                        watched_at TEXT,
                        rating INTEGER,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, douban_id)
                    )
                """)

            # 3. Trakt user sync meta (last_activities timestamps, isolated by account_id)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trakt_sync_meta (
                    account_id TEXT PRIMARY KEY,
                    last_movies_watched_at TEXT,
                    last_episodes_watched_at TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 4. Trakt user watched items (isolated by account_id)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_trakt_items (
                    account_id TEXT,
                    trakt_id INTEGER,
                    media_type TEXT,
                    title TEXT,
                    year INTEGER,
                    imdb_id TEXT,
                    tmdb_id INTEGER,
                    watched_at TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (account_id, trakt_id, media_type)
                )
            """)

            # 5. Sync logs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_platform TEXT,
                    target_platform TEXT,
                    identifier TEXT,
                    title TEXT,
                    watched_at TEXT,
                    status TEXT,
                    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 6. Ignored items (e.g., Douban hidden/unindexed or user-ignored)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ignored_sync_items (
                    account_id TEXT,
                    identifier TEXT,
                    title TEXT,
                    year INTEGER,
                    reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (account_id, identifier)
                )
            """)
            # 7. Dynamic platform credentials (for auto-login / token refresh persistence)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS platform_credentials (
                    platform TEXT PRIMARY KEY,
                    auth_data TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    # --- Global Metadata Methods ---

    def get_subject_by_douban_id(self, douban_id: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT douban_id, title, year, imdb_id FROM douban_subjects WHERE douban_id = ?",
                (douban_id,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "douban_id": row[0],
                    "title": row[1],
                    "year": row[2],
                    "imdb_id": row[3],
                }
            return None

    def save_douban_subject(self, douban_id: str, title: str, year: Optional[int], imdb_id: Optional[str]):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO douban_subjects (douban_id, title, year, imdb_id, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(douban_id) DO UPDATE SET
                    title = excluded.title,
                    year = coalesce(excluded.year, douban_subjects.year),
                    imdb_id = coalesce(excluded.imdb_id, douban_subjects.imdb_id),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (douban_id, title, year, imdb_id),
            )
            if imdb_id:
                cursor.execute(
                    """
                    INSERT INTO imdb_to_douban (imdb_id, douban_id, title, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(imdb_id) DO UPDATE SET
                        douban_id = excluded.douban_id,
                        title = excluded.title,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (imdb_id, douban_id, title),
                )
            conn.commit()

    def get_douban_id_by_imdb(self, imdb_id: str) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT douban_id FROM imdb_to_douban WHERE imdb_id = ?",
                (imdb_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    # --- Douban User Methods (Isolated by user_id) ---

    def get_user_douban_movies(self, user_id: str) -> List[Dict]:
        """Retrieve all cached watched movies for a specific Douban user, sorted by watched date descending."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT douban_id, title, year, imdb_id, watched_at, rating
                FROM user_douban_movies
                WHERE user_id = ?
                ORDER BY watched_at DESC, updated_at DESC
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
            return [
                {
                    "douban_id": r[0],
                    "title": r[1],
                    "year": r[2],
                    "imdb_id": r[3],
                    "watched_at": r[4],
                    "rating": r[5],
                }
                for r in rows
            ]

    def save_user_douban_movie(
        self,
        user_id: str,
        douban_id: str,
        title: str,
        year: Optional[int],
        imdb_id: Optional[str],
        watched_at: Optional[str],
        rating: Optional[int],
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_douban_movies (user_id, douban_id, title, year, imdb_id, watched_at, rating, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, douban_id) DO UPDATE SET
                    title = excluded.title,
                    year = coalesce(excluded.year, user_douban_movies.year),
                    imdb_id = coalesce(excluded.imdb_id, user_douban_movies.imdb_id),
                    watched_at = coalesce(excluded.watched_at, user_douban_movies.watched_at),
                    rating = coalesce(excluded.rating, user_douban_movies.rating),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, douban_id, title, year, imdb_id, watched_at, rating),
            )
            # Also update global douban_subjects metadata cache if imdb_id or title is known
            cursor.execute(
                """
                INSERT INTO douban_subjects (douban_id, title, year, imdb_id, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(douban_id) DO UPDATE SET
                    title = excluded.title,
                    year = coalesce(excluded.year, douban_subjects.year),
                    imdb_id = coalesce(excluded.imdb_id, douban_subjects.imdb_id),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (douban_id, title, year, imdb_id),
            )
            if imdb_id:
                cursor.execute(
                    """
                    INSERT INTO imdb_to_douban (imdb_id, douban_id, title, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(imdb_id) DO UPDATE SET
                        douban_id = excluded.douban_id,
                        title = excluded.title,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (imdb_id, douban_id, title),
                )
            conn.commit()

    def update_user_douban_movie_imdb(self, user_id: str, douban_id: str, imdb_id: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE user_douban_movies
                SET imdb_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND douban_id = ?
                """,
                (imdb_id, user_id, douban_id),
            )
            cursor.execute(
                """
                UPDATE douban_subjects
                SET imdb_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE douban_id = ?
                """,
                (imdb_id, douban_id),
            )
            # Fetch title for imdb_to_douban
            cursor.execute("SELECT title FROM user_douban_movies WHERE user_id = ? AND douban_id = ?", (user_id, douban_id))
            row = cursor.fetchone()
            title = row[0] if row else ""
            cursor.execute(
                """
                INSERT INTO imdb_to_douban (imdb_id, douban_id, title, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(imdb_id) DO UPDATE SET
                    douban_id = excluded.douban_id,
                    title = excluded.title,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (imdb_id, douban_id, title),
            )
            conn.commit()

    def delete_user_douban_movie(self, user_id: str, douban_id: str):
        """Remove a movie/show from user's cached Douban collection."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM user_douban_movies WHERE user_id = ? AND douban_id = ?",
                (user_id, douban_id),
            )
            conn.commit()

    # --- Trakt User Methods (Isolated by account_id) ---

    def get_trakt_sync_meta(self, account_id: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT account_id, last_movies_watched_at, last_episodes_watched_at FROM trakt_sync_meta WHERE account_id = ?",
                (account_id,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "account_id": row[0],
                    "last_movies_watched_at": row[1],
                    "last_episodes_watched_at": row[2],
                }
            return None

    def save_trakt_sync_meta(
        self,
        account_id: str,
        last_movies_watched_at: Optional[str],
        last_episodes_watched_at: Optional[str],
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO trakt_sync_meta (account_id, last_movies_watched_at, last_episodes_watched_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(account_id) DO UPDATE SET
                    last_movies_watched_at = excluded.last_movies_watched_at,
                    last_episodes_watched_at = excluded.last_episodes_watched_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (account_id, last_movies_watched_at, last_episodes_watched_at),
            )
            conn.commit()

    def get_user_trakt_items(self, account_id: str) -> List[MovieRecord]:
        """Load all cached Trakt watched items for a specific account."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT title, year, imdb_id, trakt_id, tmdb_id, watched_at, media_type
                FROM user_trakt_items
                WHERE account_id = ?
                ORDER BY watched_at DESC
                """,
                (account_id,),
            )
            rows = cursor.fetchall()
            return [
                MovieRecord(
                    title=r[0],
                    year=r[1],
                    imdb_id=r[2],
                    trakt_id=r[3],
                    tmdb_id=r[4],
                    watched_at=r[5],
                    media_type=r[6] or "movie",
                    source="trakt",
                )
                for r in rows
            ]

    def save_user_trakt_items(self, account_id: str, items: List[MovieRecord]):
        """Save Trakt watched items for a specific account."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for item in items:
                cursor.execute(
                    """
                    INSERT INTO user_trakt_items (account_id, trakt_id, media_type, title, year, imdb_id, tmdb_id, watched_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(account_id, trakt_id, media_type) DO UPDATE SET
                        title = excluded.title,
                        year = excluded.year,
                        imdb_id = coalesce(excluded.imdb_id, user_trakt_items.imdb_id),
                        tmdb_id = coalesce(excluded.tmdb_id, user_trakt_items.tmdb_id),
                        watched_at = excluded.watched_at,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        account_id,
                        item.trakt_id,
                        item.media_type,
                        item.title,
                        item.year,
                        item.imdb_id,
                        item.tmdb_id,
                        item.watched_at,
                    ),
                )
            conn.commit()

    # --- Sync Logs ---

    def log_sync(self, source: str, target: str, identifier: str, title: str, watched_at: Optional[str], status: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO sync_logs (source_platform, target_platform, identifier, title, watched_at, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, target, identifier, title, watched_at, status),
            )
            conn.commit()

    # --- Ignored Items & Custom Mappings ---

    def ignore_item(
        self,
        account_id: str,
        identifier: str,
        title: str = "",
        year: Optional[int] = None,
        reason: str = "douban_not_found",
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ignored_sync_items (account_id, identifier, title, year, reason, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(account_id, identifier) DO UPDATE SET
                    title = excluded.title,
                    year = excluded.year,
                    reason = excluded.reason,
                    created_at = CURRENT_TIMESTAMP
                """,
                (account_id, identifier, title, year, reason),
            )
            conn.commit()

    def unignore_item(self, account_id: str, identifier: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM ignored_sync_items WHERE account_id = ? AND identifier = ?",
                (account_id, identifier),
            )
            conn.commit()

    def get_ignored_items(self, account_id: Optional[str] = None) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if account_id:
                cursor.execute(
                    "SELECT identifier, title, year, reason, created_at, account_id FROM ignored_sync_items WHERE account_id = ? ORDER BY created_at DESC",
                    (account_id,),
                )
            else:
                cursor.execute(
                    "SELECT identifier, title, year, reason, created_at, account_id FROM ignored_sync_items ORDER BY created_at DESC"
                )
            rows = cursor.fetchall()
            return [
                {
                    "identifier": r[0],
                    "title": r[1],
                    "year": r[2],
                    "reason": r[3],
                    "created_at": r[4],
                    "account_id": r[5],
                }
                for r in rows
            ]

    def is_item_ignored(self, account_id: str, identifier: str) -> bool:
        if not identifier:
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM ignored_sync_items WHERE account_id = ? AND identifier = ?",
                (account_id, identifier),
            )
            return cursor.fetchone() is not None

    def get_all_imdb_to_douban(self) -> Dict[str, Dict]:
        """In-memory map of imdb_id -> {douban_id, title}."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT imdb_id, douban_id, title FROM imdb_to_douban WHERE imdb_id IS NOT NULL")
            return {r[0].lower(): {"douban_id": r[1], "title": r[2]} for r in cursor.fetchall() if r[0]}

    def save_platform_credential(self, platform: str, auth_data: Dict[str, Any]):
        """Persist refreshed token or cookie in local SQLite cache."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO platform_credentials (platform, auth_data, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(platform) DO UPDATE SET
                    auth_data = excluded.auth_data,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (platform, json.dumps(auth_data, ensure_ascii=False)),
            )
            conn.commit()

    def get_platform_credential(self, platform: str) -> Optional[Dict[str, Any]]:
        """Retrieve persisted refreshed token or cookie from local SQLite cache."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT auth_data FROM platform_credentials WHERE platform = ?", (platform,))
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except Exception:
                    pass
            return None

