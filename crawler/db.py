"""SQLite storage layer with resume support."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    name TEXT,
    url TEXT,
    user_id TEXT,
    username TEXT,
    fetched_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_search_results_key
    ON search_results(user_id);

CREATE TABLE IF NOT EXISTS profiles (
    user_id TEXT PRIMARY KEY,
    url TEXT,
    name TEXT,
    profile_photo_url TEXT,
    profile_photo_path TEXT,
    intro TEXT,
    bio TEXT,
    lives_in TEXT,
    from_city TEXT,
    birthday TEXT,
    gender TEXT,
    languages TEXT,
    work TEXT,
    education TEXT,
    family TEXT,
    relationship TEXT,
    quotes TEXT,
    links TEXT,
    contact_info TEXT,
    follower_count TEXT,
    raw_json TEXT,
    status TEXT,
    scraped_at TEXT
);
"""

# Columns added after the first release; older databases are migrated in place.
PROFILE_COLUMNS = {
    "bio": "TEXT",
    "birthday": "TEXT",
    "gender": "TEXT",
    "languages": "TEXT",
    "family": "TEXT",
    "quotes": "TEXT",
    "links": "TEXT",
    "contact_info": "TEXT",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(profiles)").fetchall()
        }
        for column, sql_type in PROFILE_COLUMNS.items():
            if column not in existing:
                self.conn.execute(
                    f"ALTER TABLE profiles ADD COLUMN {column} {sql_type}"
                )

    def close(self) -> None:
        self.conn.close()

    # -- search results -----------------------------------------------------
    def upsert_search_result(self, query: str, result: Dict[str, Any]) -> None:
        self.upsert_search_results(query, [result])

    def upsert_search_results(self, query: str, results: List[Dict[str, Any]]) -> int:
        """Insert many results with a single commit; returns rows written."""
        rows = []
        now = _now()
        for result in results:
            key = result.get("user_id") or result.get("username")
            if not key:
                continue
            rows.append(
                (
                    query,
                    result.get("name"),
                    result.get("url"),
                    # Store the stable key so username-only results still dedupe
                    # (NULL user_id never conflicts on the unique index).
                    key,
                    result.get("username"),
                    now,
                )
            )
        if not rows:
            return 0
        self.conn.executemany(
            """
            INSERT INTO search_results (query, name, url, user_id, username, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                url=excluded.url,
                username=excluded.username,
                fetched_at=excluded.fetched_at
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def search_result_keys(self) -> List[str]:
        rows = self.conn.execute("SELECT user_id FROM search_results").fetchall()
        return [r["user_id"] for r in rows]

    # -- profiles -----------------------------------------------------------
    def profile_status(self, user_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT status FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["status"] if row else None

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_profile(self, user_id: str, data: Dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO profiles (
                user_id, url, name, profile_photo_url, profile_photo_path,
                intro, bio, lives_in, from_city, birthday, gender, languages,
                work, education, family, relationship, quotes, links,
                contact_info, follower_count, raw_json, status, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                url=excluded.url,
                name=excluded.name,
                profile_photo_url=excluded.profile_photo_url,
                profile_photo_path=excluded.profile_photo_path,
                intro=excluded.intro,
                bio=excluded.bio,
                lives_in=excluded.lives_in,
                from_city=excluded.from_city,
                birthday=excluded.birthday,
                gender=excluded.gender,
                languages=excluded.languages,
                work=excluded.work,
                education=excluded.education,
                family=excluded.family,
                relationship=excluded.relationship,
                quotes=excluded.quotes,
                links=excluded.links,
                contact_info=excluded.contact_info,
                follower_count=excluded.follower_count,
                raw_json=excluded.raw_json,
                status=excluded.status,
                scraped_at=excluded.scraped_at
            """,
            (
                user_id,
                data.get("url"),
                data.get("name"),
                data.get("profile_photo_url"),
                data.get("profile_photo_path"),
                data.get("intro"),
                data.get("bio"),
                data.get("lives_in"),
                data.get("from_city"),
                data.get("birthday"),
                data.get("gender"),
                data.get("languages"),
                data.get("work"),
                data.get("education"),
                data.get("family"),
                data.get("relationship"),
                data.get("quotes"),
                data.get("links"),
                data.get("contact_info"),
                data.get("follower_count"),
                json.dumps(data, ensure_ascii=False) if data else None,
                data.get("status", "done"),
                _now(),
            ),
        )
        self.conn.commit()
