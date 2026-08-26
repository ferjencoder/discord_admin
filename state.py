from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
except ImportError:  # local SQLite-only development remains supported
    psycopg = None
    dict_row = None
    ConnectionPool = None


@dataclass(frozen=True)
class AwayRecord:
    discord_user_id: int
    game_name: str | None
    until_utc: datetime
    reason: str


@dataclass(frozen=True)
class MemberLink:
    discord_user_id: int
    game_name: str
    game_user_id: str | None
    source: str


@dataclass(frozen=True)
class MemberProfile:
    discord_user_id: int
    game_name: str | None
    game_user_id: str | None
    troop_level: str | None
    troop_level_source: str | None
    updated_at_utc: datetime


@dataclass(frozen=True)
class VerificationRequest:
    discord_user_id: int
    requested_game_name: str
    requested_game_user_id: str | None
    requested_at_utc: datetime
    source: str
    queue_channel_id: int | None
    queue_message_id: int | None


@dataclass(frozen=True)
class VerificationHistoryRecord:
    history_id: str
    discord_user_id: int
    requested_game_name: str
    requested_game_user_id: str | None
    requested_at_utc: datetime
    source: str
    decision: str
    reviewed_by_user_id: int
    reviewed_at_utc: datetime
    reason: str


class _ConnectionAdapter:
    def __init__(self, raw: Any, backend: str):
        self.raw = raw
        self.backend = backend

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        if self.backend == "postgres":
            sql = sql.replace("?", "%s")
        return self.raw.execute(sql, params)


class AdminState:
    """Persistent OZY Admin state.

    PostgreSQL is preferred for Render/production when STATE_DATABASE_URL or
    DATABASE_URL is configured. SQLite remains supported for local development
    and for paid Render services with a persistent disk mounted under STATE_DB.
    """

    def __init__(self, path: Path, database_url: str | None = None):
        self.path = path
        self.database_url = (database_url or "").strip() or None
        self.backend = "postgres" if self.database_url else "sqlite"
        self._lock = RLock()
        if self.backend == "sqlite":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif psycopg is None or ConnectionPool is None:
            raise RuntimeError(
                "STATE_DATABASE_URL/DATABASE_URL is configured but psycopg is not installed. "
                "Install requirements.txt before starting OZY Admin."
            )
        self._pool = None
        if self.backend == "postgres":
            assert self.database_url is not None and ConnectionPool is not None
            self._pool = ConnectionPool(
                conninfo=self.database_url,
                min_size=1,
                max_size=4,
                kwargs={"row_factory": dict_row},
                open=True,
            )
        self._init_db()

    @property
    def storage_label(self) -> str:
        return "PostgreSQL" if self.backend == "postgres" else f"SQLite ({self.path})"

    @contextmanager
    def _conn(self):
        with self._lock:
            if self.backend == "postgres":
                assert self._pool is not None
                with self._pool.connection() as raw:
                    conn = _ConnectionAdapter(raw, self.backend)
                    try:
                        yield conn
                        raw.commit()
                    except Exception:
                        raw.rollback()
                        raise
                return

            raw = sqlite3.connect(self.path)
            raw.row_factory = sqlite3.Row
            conn = _ConnectionAdapter(raw, self.backend)
            try:
                yield conn
                raw.commit()
            except Exception:
                raw.rollback()
                raise
            finally:
                raw.close()


    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    def _init_db(self) -> None:
        if self.backend == "postgres":
            statements = [
                """CREATE TABLE IF NOT EXISTS member_links (
                    discord_user_id BIGINT PRIMARY KEY,
                    game_name TEXT NOT NULL,
                    game_user_id TEXT,
                    linked_at_utc TEXT NOT NULL,
                    source TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS away (
                    discord_user_id BIGINT PRIMARY KEY,
                    game_name TEXT,
                    until_utc TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS daily_schedule_posts (
                    local_date TEXT PRIMARY KEY,
                    channel_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    posted_at_utc TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS welcomed_members (
                    discord_user_id BIGINT PRIMARY KEY,
                    welcomed_at_utc TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS verification_requests (
                    discord_user_id BIGINT PRIMARY KEY,
                    requested_game_name TEXT NOT NULL,
                    requested_game_user_id TEXT,
                    requested_at_utc TEXT NOT NULL,
                    source TEXT NOT NULL,
                    queue_channel_id BIGINT,
                    queue_message_id BIGINT
                )""",
                """CREATE TABLE IF NOT EXISTS verification_history (
                    history_id TEXT PRIMARY KEY,
                    discord_user_id BIGINT NOT NULL,
                    requested_game_name TEXT NOT NULL,
                    requested_game_user_id TEXT,
                    requested_at_utc TEXT NOT NULL,
                    source TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reviewed_by_user_id BIGINT NOT NULL,
                    reviewed_at_utc TEXT NOT NULL,
                    reason TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS member_profiles (
                    discord_user_id BIGINT PRIMARY KEY,
                    game_name TEXT,
                    game_user_id TEXT,
                    troop_level TEXT,
                    troop_level_source TEXT,
                    updated_at_utc TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                )""",
                "ALTER TABLE member_links ADD COLUMN IF NOT EXISTS game_user_id TEXT",
                "ALTER TABLE verification_requests ADD COLUMN IF NOT EXISTS requested_game_user_id TEXT",
                "ALTER TABLE verification_requests ADD COLUMN IF NOT EXISTS queue_channel_id BIGINT",
                "ALTER TABLE verification_requests ADD COLUMN IF NOT EXISTS queue_message_id BIGINT",
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_member_links_game_user_id
                   ON member_links(game_user_id)
                   WHERE game_user_id IS NOT NULL AND game_user_id <> ''""",
                "CREATE INDEX IF NOT EXISTS idx_verification_history_discord_user ON verification_history(discord_user_id)",
                "CREATE INDEX IF NOT EXISTS idx_verification_history_reviewed_at ON verification_history(reviewed_at_utc)",
            ]
            with self._conn() as conn:
                for statement in statements:
                    conn.execute(statement)
            return

        with self._conn() as conn:
            conn.raw.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS member_links (
                    discord_user_id INTEGER PRIMARY KEY,
                    game_name TEXT NOT NULL,
                    game_user_id TEXT,
                    linked_at_utc TEXT NOT NULL,
                    source TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS away (
                    discord_user_id INTEGER PRIMARY KEY,
                    game_name TEXT,
                    until_utc TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_schedule_posts (
                    local_date TEXT PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    posted_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS welcomed_members (
                    discord_user_id INTEGER PRIMARY KEY,
                    welcomed_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS verification_requests (
                    discord_user_id INTEGER PRIMARY KEY,
                    requested_game_name TEXT NOT NULL,
                    requested_game_user_id TEXT,
                    requested_at_utc TEXT NOT NULL,
                    source TEXT NOT NULL,
                    queue_channel_id INTEGER,
                    queue_message_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS verification_history (
                    history_id TEXT PRIMARY KEY,
                    discord_user_id INTEGER NOT NULL,
                    requested_game_name TEXT NOT NULL,
                    requested_game_user_id TEXT,
                    requested_at_utc TEXT NOT NULL,
                    source TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reviewed_by_user_id INTEGER NOT NULL,
                    reviewed_at_utc TEXT NOT NULL,
                    reason TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS member_profiles (
                    discord_user_id INTEGER PRIMARY KEY,
                    game_name TEXT,
                    game_user_id TEXT,
                    troop_level TEXT,
                    troop_level_source TEXT,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );
                """
            )

            def columns(table: str) -> set[str]:
                return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

            member_link_columns = columns("member_links")
            if "game_user_id" not in member_link_columns:
                conn.execute("ALTER TABLE member_links ADD COLUMN game_user_id TEXT")

            verification_columns = columns("verification_requests")
            if "requested_game_user_id" not in verification_columns:
                conn.execute("ALTER TABLE verification_requests ADD COLUMN requested_game_user_id TEXT")
            if "queue_channel_id" not in verification_columns:
                conn.execute("ALTER TABLE verification_requests ADD COLUMN queue_channel_id INTEGER")
            if "queue_message_id" not in verification_columns:
                conn.execute("ALTER TABLE verification_requests ADD COLUMN queue_message_id INTEGER")

            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_member_links_game_user_id
                   ON member_links(game_user_id)
                   WHERE game_user_id IS NOT NULL AND game_user_id <> ''"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_verification_history_discord_user ON verification_history(discord_user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_verification_history_reviewed_at ON verification_history(reviewed_at_utc)"
            )

    def set_member_profile_identity(
        self,
        discord_user_id: int,
        *,
        game_name: str | None,
        game_user_id: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        stable_id = (game_user_id or "").strip() or None
        canonical = (game_name or "").strip() or None
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO member_profiles(
                    discord_user_id, game_name, game_user_id,
                    troop_level, troop_level_source, updated_at_utc
                )
                VALUES (?, ?, ?, NULL, NULL, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    game_name=excluded.game_name,
                    game_user_id=COALESCE(excluded.game_user_id, member_profiles.game_user_id),
                    updated_at_utc=excluded.updated_at_utc
                """,
                (discord_user_id, canonical, stable_id, now),
            )

    def set_troop_level(
        self,
        discord_user_id: int,
        troop_level: str | None,
        source: str,
        *,
        game_name: str | None = None,
        game_user_id: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        level = (troop_level or "").strip().upper() or None
        stable_id = (game_user_id or "").strip() or None
        canonical = (game_name or "").strip() or None
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO member_profiles(
                    discord_user_id, game_name, game_user_id,
                    troop_level, troop_level_source, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    game_name=COALESCE(excluded.game_name, member_profiles.game_name),
                    game_user_id=COALESCE(excluded.game_user_id, member_profiles.game_user_id),
                    troop_level=excluded.troop_level,
                    troop_level_source=excluded.troop_level_source,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (discord_user_id, canonical, stable_id, level, source, now),
            )

    def get_member_profile(self, discord_user_id: int) -> MemberProfile | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT discord_user_id, game_name, game_user_id,
                          troop_level, troop_level_source, updated_at_utc
                   FROM member_profiles WHERE discord_user_id=?""",
                (discord_user_id,),
            ).fetchone()
        if not row:
            return None
        return MemberProfile(
            discord_user_id=int(row["discord_user_id"]),
            game_name=row["game_name"],
            game_user_id=row["game_user_id"],
            troop_level=row["troop_level"],
            troop_level_source=row["troop_level_source"],
            updated_at_utc=datetime.fromisoformat(row["updated_at_utc"]),
        )

    def set_link(self, discord_user_id: int, game_name: str, source: str, game_user_id: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        stable_id = (game_user_id or "").strip() or None
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO member_links(discord_user_id, game_name, game_user_id, linked_at_utc, source)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(discord_user_id) DO UPDATE SET
                       game_name=excluded.game_name,
                       game_user_id=COALESCE(excluded.game_user_id, member_links.game_user_id),
                       linked_at_utc=excluded.linked_at_utc,
                       source=excluded.source""",
                (discord_user_id, game_name, stable_id, now, source),
            )
        self.set_member_profile_identity(discord_user_id, game_name=game_name, game_user_id=stable_id)

    def get_link(self, discord_user_id: int) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT game_name FROM member_links WHERE discord_user_id=?", (discord_user_id,)).fetchone()
        return row["game_name"] if row else None

    def get_link_record(self, discord_user_id: int) -> MemberLink | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT discord_user_id, game_name, game_user_id, source FROM member_links WHERE discord_user_id=?",
                (discord_user_id,),
            ).fetchone()
        if not row:
            return None
        return MemberLink(int(row["discord_user_id"]), row["game_name"], row["game_user_id"], row["source"])

    def linked_user_for_game_name(self, game_name: str) -> int | None:
        target = game_name.casefold()
        for discord_user_id, linked_name in self.all_links().items():
            if linked_name.casefold() == target:
                return discord_user_id
        return None

    def linked_user_for_identity(self, game_name: str, game_user_id: str | None = None) -> int | None:
        stable_id = (game_user_id or "").strip()
        if stable_id:
            with self._conn() as conn:
                row = conn.execute("SELECT discord_user_id FROM member_links WHERE game_user_id=?", (stable_id,)).fetchone()
            if row:
                return int(row["discord_user_id"])
        return self.linked_user_for_game_name(game_name)

    def remove_link(self, discord_user_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM member_links WHERE discord_user_id=?", (discord_user_id,))

    def all_links(self) -> dict[int, str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT discord_user_id, game_name FROM member_links").fetchall()
        return {int(row["discord_user_id"]): row["game_name"] for row in rows}

    def all_link_records(self) -> dict[int, MemberLink]:
        with self._conn() as conn:
            rows = conn.execute("SELECT discord_user_id, game_name, game_user_id, source FROM member_links").fetchall()
        return {
            int(row["discord_user_id"]): MemberLink(
                int(row["discord_user_id"]), row["game_name"], row["game_user_id"], row["source"]
            ) for row in rows
        }

    def set_away(self, discord_user_id: int, game_name: str | None, until_utc: datetime, reason: str) -> None:
        if until_utc.tzinfo is None:
            raise ValueError("until_utc must be timezone-aware")
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO away(discord_user_id, game_name, until_utc, reason, created_at_utc)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(discord_user_id) DO UPDATE SET
                       game_name=excluded.game_name,
                       until_utc=excluded.until_utc,
                       reason=excluded.reason,
                       created_at_utc=excluded.created_at_utc""",
                (discord_user_id, game_name, until_utc.astimezone(timezone.utc).isoformat(), reason, now),
            )

    def clear_away(self, discord_user_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM away WHERE discord_user_id=?", (discord_user_id,))

    def get_away(self, discord_user_id: int) -> AwayRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT discord_user_id, game_name, until_utc, reason FROM away WHERE discord_user_id=?",
                (discord_user_id,),
            ).fetchone()
        if not row:
            return None
        return AwayRecord(int(row["discord_user_id"]), row["game_name"], datetime.fromisoformat(row["until_utc"]), row["reason"])

    def expired_away(self, now_utc: datetime) -> list[AwayRecord]:
        if now_utc.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT discord_user_id, game_name, until_utc, reason FROM away WHERE until_utc <= ?",
                (now_utc.astimezone(timezone.utc).isoformat(),),
            ).fetchall()
        return [
            AwayRecord(int(row["discord_user_id"]), row["game_name"], datetime.fromisoformat(row["until_utc"]), row["reason"])
            for row in rows
        ]

    def set_verification_request(
        self,
        discord_user_id: int,
        game_name: str,
        source: str,
        game_user_id: str | None = None,
    ) -> VerificationRequest:
        now = datetime.now(timezone.utc).isoformat()
        stable_id = (game_user_id or "").strip() or None
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO verification_requests(
                       discord_user_id, requested_game_name, requested_game_user_id,
                       requested_at_utc, source, queue_channel_id, queue_message_id
                   ) VALUES (?, ?, ?, ?, ?, NULL, NULL)
                   ON CONFLICT(discord_user_id) DO UPDATE SET
                       requested_game_name=excluded.requested_game_name,
                       requested_game_user_id=excluded.requested_game_user_id,
                       requested_at_utc=excluded.requested_at_utc,
                       source=excluded.source,
                       queue_channel_id=NULL,
                       queue_message_id=NULL""",
                (discord_user_id, game_name, stable_id, now, source),
            )
        request = self.get_verification_request_record(discord_user_id)
        assert request is not None
        return request

    def set_verification_message(self, discord_user_id: int, channel_id: int, message_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE verification_requests SET queue_channel_id=?, queue_message_id=? WHERE discord_user_id=?",
                (channel_id, message_id, discord_user_id),
            )

    def get_verification_request(self, discord_user_id: int) -> str | None:
        record = self.get_verification_request_record(discord_user_id)
        return record.requested_game_name if record else None

    def get_verification_request_record(self, discord_user_id: int) -> VerificationRequest | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT discord_user_id, requested_game_name, requested_game_user_id,
                          requested_at_utc, source, queue_channel_id, queue_message_id
                   FROM verification_requests WHERE discord_user_id=?""",
                (discord_user_id,),
            ).fetchone()
        if not row:
            return None
        return VerificationRequest(
            discord_user_id=int(row["discord_user_id"]),
            requested_game_name=row["requested_game_name"],
            requested_game_user_id=row["requested_game_user_id"],
            requested_at_utc=datetime.fromisoformat(row["requested_at_utc"]),
            source=row["source"],
            queue_channel_id=int(row["queue_channel_id"]) if row["queue_channel_id"] is not None else None,
            queue_message_id=int(row["queue_message_id"]) if row["queue_message_id"] is not None else None,
        )

    def clear_verification_request(self, discord_user_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM verification_requests WHERE discord_user_id=?", (discord_user_id,))

    def all_verification_requests(self) -> dict[int, str]:
        return {r.discord_user_id: r.requested_game_name for r in self.all_verification_request_records()}

    def all_verification_request_records(self) -> list[VerificationRequest]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT discord_user_id, requested_game_name, requested_game_user_id,
                          requested_at_utc, source, queue_channel_id, queue_message_id
                   FROM verification_requests ORDER BY requested_at_utc"""
            ).fetchall()
        return [
            VerificationRequest(
                discord_user_id=int(row["discord_user_id"]),
                requested_game_name=row["requested_game_name"],
                requested_game_user_id=row["requested_game_user_id"],
                requested_at_utc=datetime.fromisoformat(row["requested_at_utc"]),
                source=row["source"],
                queue_channel_id=int(row["queue_channel_id"]) if row["queue_channel_id"] is not None else None,
                queue_message_id=int(row["queue_message_id"]) if row["queue_message_id"] is not None else None,
            ) for row in rows
        ]

    def resolve_verification_request(
        self,
        discord_user_id: int,
        *,
        decision: str,
        reviewed_by_user_id: int,
        reason: str = "",
    ) -> VerificationRequest | None:
        request = self.get_verification_request_record(discord_user_id)
        if request is None:
            return None
        reviewed_at = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO verification_history(
                       history_id, discord_user_id, requested_game_name, requested_game_user_id,
                       requested_at_utc, source, decision, reviewed_by_user_id,
                       reviewed_at_utc, reason
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid.uuid4().hex,
                    request.discord_user_id,
                    request.requested_game_name,
                    request.requested_game_user_id,
                    request.requested_at_utc.isoformat(),
                    request.source,
                    decision,
                    reviewed_by_user_id,
                    reviewed_at.isoformat(),
                    reason.strip(),
                ),
            )
            conn.execute("DELETE FROM verification_requests WHERE discord_user_id=?", (discord_user_id,))
        return request

    def verification_history(self, limit: int = 25) -> list[VerificationHistoryRecord]:
        limit = max(1, min(int(limit), 100))
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT history_id, discord_user_id, requested_game_name, requested_game_user_id,
                          requested_at_utc, source, decision, reviewed_by_user_id,
                          reviewed_at_utc, reason
                   FROM verification_history ORDER BY reviewed_at_utc DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            VerificationHistoryRecord(
                history_id=row["history_id"],
                discord_user_id=int(row["discord_user_id"]),
                requested_game_name=row["requested_game_name"],
                requested_game_user_id=row["requested_game_user_id"],
                requested_at_utc=datetime.fromisoformat(row["requested_at_utc"]),
                source=row["source"],
                decision=row["decision"],
                reviewed_by_user_id=int(row["reviewed_by_user_id"]),
                reviewed_at_utc=datetime.fromisoformat(row["reviewed_at_utc"]),
                reason=row["reason"],
            ) for row in rows
        ]

    def was_welcomed(self, discord_user_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM welcomed_members WHERE discord_user_id=?", (discord_user_id,)).fetchone()
        return row is not None

    def mark_welcomed(self, discord_user_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO welcomed_members(discord_user_id, welcomed_at_utc)
                   VALUES (?, ?)
                   ON CONFLICT(discord_user_id) DO UPDATE SET welcomed_at_utc=excluded.welcomed_at_utc""",
                (discord_user_id, now),
            )

    def clear_welcomed(self, discord_user_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM welcomed_members WHERE discord_user_id=?", (discord_user_id,))

    def mark_schedule_posted(self, local_date: str, channel_id: int, message_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO daily_schedule_posts(local_date, channel_id, message_id, posted_at_utc)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(local_date) DO UPDATE SET
                       channel_id=excluded.channel_id,
                       message_id=excluded.message_id,
                       posted_at_utc=excluded.posted_at_utc""",
                (local_date, channel_id, message_id, now),
            )

    def schedule_posted(self, local_date: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM daily_schedule_posts WHERE local_date=?", (local_date,)).fetchone()
        return row is not None

    def set_value(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO bot_state(key, value, updated_at_utc)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at_utc=excluded.updated_at_utc""",
                (key, value, now),
            )

    def get_value(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def delete_value(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM bot_state WHERE key=?", (key,))
