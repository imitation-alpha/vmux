"""Local SQLite persistence for normalized agent workspace state."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import default_capabilities, empty_context, semantic_delta

SCHEMA_VERSION = 2
MIN_REVIEW_INTERVAL_MINUTES = 5
MAX_REVIEW_INTERVAL_MINUTES = 1440
MAX_RECOVERY_ITEMS = 50
RECOVERY_CURSOR_VERSION = 1


class AgentStore:
    def __init__(self, path: str, retention_days: int = 30):
        self.path = os.path.expanduser(path)
        self.retention_days = max(1, min(3650, int(retention_days)))
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    def open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            directory = os.path.dirname(self.path)
            if directory:
                created_directory = not os.path.isdir(directory)
                os.makedirs(directory, mode=0o700, exist_ok=True)
                if created_directory:
                    try:
                        os.chmod(directory, 0o700)
                    except OSError:
                        pass
            if not os.path.exists(self.path):
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                    os.close(fd)
                except FileExistsError:
                    pass
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            conn = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA secure_delete=ON")
            self._conn = conn
            self._migrate()
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            for suffix in ("-wal", "-shm"):
                try:
                    os.chmod(self.path + suffix, 0o600)
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        self.open()
        assert self._conn is not None
        return self._conn

    @contextmanager
    def transaction(self):
        with self._lock:
            conn = self.conn
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @contextmanager
    def read_transaction(self):
        """Hold one SQLite snapshot without acquiring a write reservation."""
        with self._lock:
            conn = self.conn
            try:
                conn.execute("BEGIN")
                # Establish the snapshot before any response timestamp or
                # section query can observe a later projection.
                conn.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone()
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _migrate(self) -> None:
        conn = self.conn
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            )"""
        )
        row = conn.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
        current_version = int(row["version"] if row else 0)
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                "agent database schema %d is newer than supported schema %d"
                % (current_version, SCHEMA_VERSION)
            )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id TEXT PRIMARY KEY,
                runtime TEXT NOT NULL,
                native_session_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                lifecycle TEXT NOT NULL DEFAULT 'observing',
                association TEXT NOT NULL DEFAULT 'probable',
                pane_id TEXT,
                target TEXT,
                pane_pid TEXT,
                pane_created REAL,
                pane_incarnation TEXT,
                binding_revision INTEGER NOT NULL DEFAULT 0,
                binding_source TEXT NOT NULL DEFAULT 'automatic',
                source_path TEXT NOT NULL,
                source_cwd TEXT NOT NULL,
                log_inode INTEGER,
                log_offset INTEGER NOT NULL DEFAULT 0,
                parser_version TEXT NOT NULL DEFAULT '',
                capabilities_json TEXT NOT NULL DEFAULT '{}',
                extraction_health TEXT NOT NULL DEFAULT 'ok',
                last_event_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(runtime, native_session_id)
            );
            CREATE INDEX IF NOT EXISTS agent_sessions_updated_idx ON agent_sessions(updated_at DESC);
            CREATE INDEX IF NOT EXISTS agent_sessions_pane_idx ON agent_sessions(pane_id);

            CREATE TABLE IF NOT EXISTS agent_contexts (
                session_id TEXT PRIMARY KEY REFERENCES agent_sessions(id) ON DELETE CASCADE,
                revision INTEGER NOT NULL DEFAULT 0,
                context_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_snapshots (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                context_json TEXT NOT NULL,
                delta_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(session_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS snapshots_session_seq_idx
                ON agent_snapshots(session_id, sequence DESC);
            CREATE INDEX IF NOT EXISTS snapshots_created_idx ON agent_snapshots(created_at);

            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
                native_event_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                kind TEXT NOT NULL,
                priority TEXT NOT NULL,
                options_json TEXT NOT NULL,
                input_map_json TEXT NOT NULL,
                recommendation TEXT,
                allow_custom INTEGER NOT NULL DEFAULT 0,
                prompt_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                reply_idempotency_key TEXT,
                selected_option_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(session_id, native_event_id),
                UNIQUE(reply_idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS decisions_status_idx ON decisions(status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL UNIQUE REFERENCES agent_sessions(id) ON DELETE CASCADE,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
                native_event_id TEXT,
                client_message_id TEXT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(session_id, native_event_id),
                UNIQUE(session_id, client_message_id)
            );
            CREATE INDEX IF NOT EXISTS messages_session_created_idx
                ON chat_messages(session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS session_visits (
                session_id TEXT PRIMARY KEY REFERENCES agent_sessions(id) ON DELETE CASCADE,
                snapshot_id TEXT,
                snapshot_sequence INTEGER NOT NULL DEFAULT 0,
                visited_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_reviews (
                session_id TEXT PRIMARY KEY REFERENCES agent_sessions(id) ON DELETE CASCADE,
                snapshot_id TEXT,
                snapshot_sequence INTEGER NOT NULL DEFAULT 0,
                reviewed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                interval_minutes INTEGER
                    CHECK (interval_minutes IS NULL OR
                           interval_minutes BETWEEN 5 AND 1440),
                next_due_at REAL,
                last_digest_at REAL,
                urgent_pane_errors INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            """
        )
        now = time.time()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Another process may have completed this migration while this
            # connection was creating the idempotent tables above or waiting
            # for the write lock. Branch only on the version observed under
            # the lock so the v1 visit baseline is seeded exactly once.
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            current_version = int(row["version"] if row else 0)
            if current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    "agent database schema %d is newer than supported schema %d"
                    % (current_version, SCHEMA_VERSION)
                )
            if current_version < 1:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)",
                    (now,),
                )
            if current_version < 2:
                # Seed exactly once from the legacy shared visit baseline.  A
                # legacy client visiting an agent after v2 must not silently
                # acknowledge Review work on a later restart.
                conn.execute(
                    """INSERT OR IGNORE INTO session_reviews(
                           session_id,snapshot_id,snapshot_sequence,reviewed_at
                       )
                       SELECT session_id,snapshot_id,snapshot_sequence,visited_at
                       FROM session_visits"""
                )
                conn.execute(
                    """INSERT OR IGNORE INTO review_settings(
                           id,interval_minutes,next_due_at,last_digest_at,
                           urgent_pane_errors,updated_at
                       ) VALUES (1,NULL,NULL,NULL,0,?)""",
                    (now,),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (2, ?)",
                    (now,),
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO review_settings(
                           id,interval_minutes,next_due_at,last_digest_at,
                           urgent_pane_errors,updated_at
                       ) VALUES (1,NULL,NULL,NULL,0,?)""",
                    (now,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _loads(value: Any, default):
        try:
            parsed = json.loads(value or "")
            return parsed
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _dumps(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _canonical_dumps(value: Any) -> str:
        return json.dumps(
            value, separators=(",", ":"), ensure_ascii=False, sort_keys=True
        )

    @staticmethod
    def _cursor(value: Optional[str]) -> int:
        if value in (None, ""):
            return 0
        try:
            result = int(value)
        except (TypeError, ValueError):
            raise ValueError("bad cursor")
        if result < 0:
            raise ValueError("bad cursor")
        return result

    @classmethod
    def _encode_recovery_cursor(
        cls,
        *,
        kind: str,
        session_id: str,
        anchor_at: float,
        message_anchor: int,
        snapshot_anchor: int,
        direction: str,
        boundary: Tuple[float, int, int, str],
    ) -> str:
        payload = {
            "v": RECOVERY_CURSOR_VERSION,
            "k": kind,
            "s": session_id,
            "a": anchor_at,
            "m": message_anchor,
            "p": snapshot_anchor,
            "d": direction,
            "b": list(boundary),
        }
        raw = cls._canonical_dumps(payload).encode("utf-8")
        # The checksum catches accidental corruption. Cursors confer no extra
        # authority: authentication and the session id are still enforced.
        checksum = hashlib.sha256(b"vmux-recovery-v1\0" + raw).digest()[:12]
        token = base64.urlsafe_b64encode(checksum + raw).decode("ascii").rstrip("=")
        return "rc1." + token

    @classmethod
    def _decode_recovery_cursor(
        cls, value: Optional[str], *, kind: str, session_id: str
    ) -> Optional[Dict[str, Any]]:
        if value in (None, ""):
            return None
        text = str(value)
        if not text.startswith("rc1.") or len(text) > 1000:
            raise ValueError("bad recovery cursor")
        encoded = text[4:]
        try:
            decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            checksum, raw = decoded[:12], decoded[12:]
            if len(checksum) != 12 or not hashlib.sha256(
                b"vmux-recovery-v1\0" + raw
            ).digest()[:12] == checksum:
                raise ValueError
            payload = json.loads(raw)
            boundary = payload.get("b")
            if (
                payload.get("v") != RECOVERY_CURSOR_VERSION
                or payload.get("k") != kind
                or payload.get("s") != session_id
                or payload.get("d") not in ("older", "newer")
                or not isinstance(boundary, list)
                or len(boundary) != 4
            ):
                raise ValueError
            anchor_at = float(payload["a"])
            message_anchor = int(payload["m"])
            snapshot_anchor = int(payload["p"])
            normalized_boundary = (
                float(boundary[0]), int(boundary[1]), int(boundary[2]), str(boundary[3])
            )
            if (
                not math.isfinite(anchor_at)
                or not math.isfinite(normalized_boundary[0])
                or not 0 <= message_anchor <= 2**63 - 1
                or not 0 <= snapshot_anchor <= 2**63 - 1
                or normalized_boundary[1] not in (0, 1)
                or not 0 <= normalized_boundary[2] <= 2**63 - 1
                or not normalized_boundary[3]
                or len(normalized_boundary[3]) > 160
            ):
                raise ValueError
        except (
            binascii.Error,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ):
            raise ValueError("bad recovery cursor")
        return {
            "anchor_at": anchor_at,
            "message_anchor": message_anchor,
            "snapshot_anchor": snapshot_anchor,
            "direction": payload["d"],
            "boundary": normalized_boundary,
        }

    def upsert_session(self, runtime: str, native_session_id: str, source_path: str,
                       source_cwd: str, parser_version: str) -> Dict[str, Any]:
        now = time.time()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE runtime=? AND native_session_id=?",
                (runtime, native_session_id),
            ).fetchone()
            if row is None:
                session_id = str(uuid.uuid4())
                caps = default_capabilities("probable")
                conn.execute(
                    """INSERT INTO agent_sessions(
                        id,runtime,native_session_id,source_path,source_cwd,parser_version,
                        capabilities_json,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (session_id, runtime, native_session_id, source_path, source_cwd,
                     parser_version, self._dumps(caps), now, now),
                )
                context = empty_context(session_id, runtime)
                conn.execute(
                    "INSERT INTO agent_contexts(session_id,revision,context_json,updated_at) VALUES (?,?,?,?)",
                    (session_id, 0, self._dumps(context), now),
                )
                conv_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO conversations(id,session_id,created_at,updated_at) VALUES (?,?,?,?)",
                    (conv_id, session_id, now, now),
                )
            else:
                session_id = row["id"]
                conn.execute(
                    """UPDATE agent_sessions SET source_path=?,source_cwd=?,parser_version=?,updated_at=?
                       WHERE id=?""",
                    (source_path, source_cwd, parser_version, now, session_id),
                )
        return self.get_agent(session_id, internal=True)

    def get_agent(self, session_id: str, *, internal: bool = False) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self.conn.execute(
                """SELECT s.*,c.context_json,c.revision AS context_revision
                   FROM agent_sessions s JOIN agent_contexts c ON c.session_id=s.id WHERE s.id=?""",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        context = self._loads(row["context_json"], {})
        agent = {
            "id": row["id"],
            "runtime": row["runtime"],
            "native_session_id": row["native_session_id"],
            "title": row["title"] or context.get("goal") or context.get("current_task") or "%s session" % row["runtime"].title(),
            "lifecycle": row["lifecycle"],
            "association": row["association"],
            "pane_id": row["pane_id"],
            "target": row["target"],
            "binding_revision": row["binding_revision"],
            "revision": int(row["context_revision"]),
            "capabilities": self._loads(row["capabilities_json"], default_capabilities()),
            "extraction_health": row["extraction_health"],
            "last_event_at": row["last_event_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "context": context,
        }
        with self._lock:
            count = self.conn.execute(
                """SELECT COUNT(*) AS n FROM decisions WHERE session_id=?
                   AND status IN ('pending','submitting','unknown')""",
                (session_id,),
            ).fetchone()
        agent["pending_decisions_count"] = int(count["n"] if count else 0)
        if internal:
            agent["_source_path"] = row["source_path"]
            agent["_source_cwd"] = row["source_cwd"]
            agent["_log_inode"] = row["log_inode"]
            agent["_log_offset"] = row["log_offset"]
            agent["_parser_version"] = row["parser_version"]
            agent["_pane_pid"] = row["pane_pid"]
            agent["_pane_created"] = row["pane_created"]
            agent["_pane_incarnation"] = row["pane_incarnation"]
            agent["_binding_source"] = row["binding_source"]
        return agent

    def find_session(self, runtime: str, native_session_id: str, *, internal: bool = False):
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM agent_sessions WHERE runtime=? AND native_session_id=?",
                (runtime, native_session_id),
            ).fetchone()
        return self.get_agent(row["id"], internal=internal) if row else None

    def list_agents(self, cursor: Optional[str] = None, limit: int = 50) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        offset = self._cursor(cursor)
        limit = max(1, min(100, int(limit)))
        with self._lock:
            rows = self.conn.execute(
                "SELECT id FROM agent_sessions ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
        values = [self.get_agent(row["id"]) for row in rows[:limit]]
        return [v for v in values if v], str(offset + limit) if len(rows) > limit else None

    def update_binding(self, session_id: str, *, association: str, pane_id: Optional[str],
                       target: Optional[str], pane_pid: Optional[str], pane_created: Optional[float],
                       pane_incarnation: Optional[str], source: str, capabilities: Dict[str, Any]) -> Dict[str, Any]:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM agent_sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            changed = any((row[key] or None) != value for key, value in (
                ("association", association), ("pane_id", pane_id), ("pane_pid", pane_pid),
                ("pane_incarnation", pane_incarnation), ("binding_source", source),
            ))
            revision = row["binding_revision"] + (1 if changed else 0)
            conn.execute(
                """UPDATE agent_sessions SET association=?,pane_id=?,target=?,pane_pid=?,pane_created=?,
                   pane_incarnation=?,binding_source=?,binding_revision=?,capabilities_json=?,updated_at=?
                   WHERE id=?""",
                (association, pane_id, target, pane_pid, pane_created, pane_incarnation, source,
                 revision, self._dumps(capabilities), time.time(), session_id),
            )
        return self.get_agent(session_id, internal=True)

    def update_capabilities(self, session_id: str, capabilities: Dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE agent_sessions SET capabilities_json=?,updated_at=? WHERE id=?",
                (self._dumps(capabilities), time.time(), session_id),
            )

    def update_cursor(self, session_id: str, offset: int, inode: int, parser_version: str,
                      *, error: Optional[str] = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                """UPDATE agent_sessions SET log_offset=?,log_inode=?,parser_version=?,
                   extraction_health=?,updated_at=? WHERE id=?""",
                (offset, inode, parser_version, "degraded:" + error if error else "ok", time.time(), session_id),
            )

    def apply_projection(self, session_id: str, context: Dict[str, Any],
                         messages: Iterable[Dict[str, Any]], decisions: Iterable[Dict[str, Any]],
                         resolved_native_ids: Iterable[str]) -> Tuple[Optional[Dict[str, Any]], List[str], List[str]]:
        """Atomically reconcile visible events and create a snapshot if changed."""
        now = time.time()
        decision_ids: List[str] = []
        message_ids: List[str] = []
        message_history_truncated = False
        snapshot: Optional[Dict[str, Any]] = None
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT revision,context_json FROM agent_contexts WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            before = self._loads(row["context_json"], {})
            revision = int(row["revision"])

            conv = conn.execute("SELECT id FROM conversations WHERE session_id=?", (session_id,)).fetchone()
            conversation_id = conv["id"]
            for message in messages:
                native_id = str(message.get("native_event_id") or "") or None
                content = str(message.get("content") or "")
                if not content:
                    continue
                if float(message.get("created_at", now)) < now - self.retention_days * 86400:
                    message_history_truncated = True
                    continue
                # A locally-sent message becomes observed instead of being
                # duplicated when the runtime appends the matching user event.
                pending = None
                if message.get("role") == "user":
                    pending = conn.execute(
                        """SELECT id FROM chat_messages WHERE session_id=? AND role='user'
                           AND content=? AND status IN ('sent','submitting','unknown')
                           ORDER BY created_at DESC LIMIT 1""",
                        (session_id, content),
                    ).fetchone()
                if pending:
                    conn.execute(
                        "UPDATE chat_messages SET native_event_id=?,status='observed',updated_at=? WHERE id=?",
                        (native_id, now, pending["id"]),
                    )
                    message_ids.append(pending["id"])
                    continue
                msg_id = str(uuid.uuid4())
                before_changes = conn.total_changes
                conn.execute(
                    """INSERT OR IGNORE INTO chat_messages(
                       id,conversation_id,session_id,native_event_id,role,content,status,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (msg_id, conversation_id, session_id, native_id, message.get("role"), content,
                     message.get("status", "observed"), message.get("created_at", now), now),
                )
                if conn.total_changes > before_changes:
                    message_ids.append(msg_id)

            for decision in decisions:
                native_id = str(decision.get("native_event_id") or "")
                if not native_id:
                    continue
                if (float(decision.get("created_at", now)) < now - self.retention_days * 86400
                        and decision.get("status") != "pending"):
                    continue
                existing = conn.execute(
                    "SELECT id FROM decisions WHERE session_id=? AND native_event_id=?",
                    (session_id, native_id),
                ).fetchone()
                if existing:
                    continue
                dec_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO decisions(
                       id,session_id,native_event_id,title,description,kind,priority,options_json,
                       input_map_json,recommendation,allow_custom,prompt_fingerprint,status,revision,
                       created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (dec_id, session_id, native_id, decision["title"], decision["description"],
                     decision.get("kind", "question"), decision.get("priority", "normal"),
                     self._dumps(decision.get("options", [])), self._dumps(decision.get("input_map", {})),
                     decision.get("recommendation"), int(bool(decision.get("allow_custom"))),
                     decision.get("prompt_fingerprint", ""), decision.get("status", "unverified"), 1,
                     decision.get("created_at", now), now),
                )
                decision_ids.append(dec_id)

            for native_id in resolved_native_ids:
                conn.execute(
                    """UPDATE decisions SET status='resolved',revision=revision+1,updated_at=?
                       WHERE session_id=? AND (native_event_id=? OR native_event_id LIKE ?)
                       AND status IN ('pending','submitting','unverified')""",
                    (now, session_id, native_id, native_id + ":%"),
                )

            context = dict(context)
            if (
                message_history_truncated
                or before.get("message_history_truncated")
            ):
                # This boolean is the only retained trace of discarded
                # transcript content. Never retain the skipped content itself.
                context["message_history_truncated"] = True
            context_decisions = {
                str(item.get("id")): item
                for item in context.get("decisions", []) if item.get("id")
            }
            for decision in decisions:
                if decision.get("status") == "pending":
                    native_id = str(decision.get("native_event_id") or "")
                    if native_id:
                        context_decisions[native_id] = {
                            "id": native_id,
                            "title": decision.get("title", "Decision required"),
                            "created_at": decision.get("created_at", now),
                        }
            for native_id in resolved_native_ids:
                for key in list(context_decisions):
                    if key == native_id or key.startswith(native_id + ":"):
                        context_decisions.pop(key, None)
            context["decisions"] = list(context_decisions.values())[-100:]

            before_without_retention = dict(before)
            before_without_retention.pop("message_history_truncated", None)
            context_without_retention = dict(context)
            context_without_retention.pop("message_history_truncated", None)
            retention_metadata_only = bool(
                context != before
                and context_without_retention == before_without_retention
            )
            if retention_metadata_only:
                # Retention disclosure is durable metadata, not semantic
                # agent activity and therefore does not create Review work.
                conn.execute(
                    """UPDATE agent_contexts SET context_json=?,updated_at=?
                       WHERE session_id=?""",
                    (self._dumps(context), now, session_id),
                )
            elif context != before:
                revision += 1
                context = dict(context)
                context["revision"] = revision
                delta = semantic_delta(before, context)
                snap_id = str(uuid.uuid4())
                seq_row = conn.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 AS n FROM agent_snapshots WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                sequence = int(seq_row["n"])
                conn.execute(
                    "UPDATE agent_contexts SET revision=?,context_json=?,updated_at=? WHERE session_id=?",
                    (revision, self._dumps(context), now, session_id),
                )
                conn.execute(
                    """INSERT INTO agent_snapshots(id,session_id,sequence,context_json,delta_json,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (snap_id, session_id, sequence, self._dumps(context), self._dumps(delta), now),
                )
                conn.execute(
                    """UPDATE agent_sessions SET lifecycle=?,last_event_at=?,updated_at=? WHERE id=?""",
                    (context.get("lifecycle", "observing"), context.get("last_updated", now), now, session_id),
                )
                snapshot = {"id": snap_id, "agent_id": session_id, "sequence": sequence,
                            "created_at": now, "delta": delta, "context": context}
        return snapshot, decision_ids, message_ids

    def sync_verified_decision(self, session_id: str, decision: Dict[str, Any]):
        agent = self.get_agent(session_id)
        if not agent:
            raise KeyError(session_id)
        context = dict(agent["context"])
        values = {str(item.get("id")): item for item in context.get("decisions", []) if item.get("id")}
        native_id = str(decision.get("native_event_id") or "")
        values[native_id] = {
            "id": native_id, "title": decision.get("title", "Decision required"),
            "created_at": decision.get("created_at", time.time()),
        }
        context["decisions"] = list(values.values())[-100:]
        return self.apply_projection(session_id, context, [], [], [])[0]

    def _public_decision(self, row: sqlite3.Row, *, internal: bool = False) -> Dict[str, Any]:
        agent = self.get_agent(row["session_id"], internal=True)
        options = self._loads(row["options_json"], [])
        value = {
            "id": row["id"], "agent_id": row["session_id"],
            "native_event_id": row["native_event_id"], "title": row["title"],
            "description": row["description"], "kind": row["kind"],
            "priority": row["priority"], "options": options,
            "options_fingerprint": hashlib.sha256(
                self._canonical_dumps(options).encode("utf-8")
            ).hexdigest(),
            "recommendation": row["recommendation"], "allow_custom": bool(row["allow_custom"]),
            "status": row["status"], "revision": row["revision"],
            "binding_revision": agent["binding_revision"] if agent else 0,
            "prompt_fingerprint": row["prompt_fingerprint"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
        if internal:
            value["_input_map"] = self._loads(row["input_map_json"], {})
            value["_prompt_fingerprint"] = row["prompt_fingerprint"]
            value["_reply_idempotency_key"] = row["reply_idempotency_key"]
        return value

    def get_decision(self, decision_id: str, *, internal: bool = False):
        with self._lock:
            row = self.conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
        return self._public_decision(row, internal=internal) if row else None

    def list_decisions(self, cursor: Optional[str] = None, limit: int = 50,
                       status: Optional[str] = None, session_id: Optional[str] = None):
        offset = self._cursor(cursor)
        limit = max(1, min(100, int(limit)))
        clauses, args = ["status!='unverified'"], []
        if status:
            clauses.append("status=?")
            args.append(status)
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM decisions%s ORDER BY updated_at DESC,id LIMIT ? OFFSET ?" % where,
                tuple(args) + (limit + 1, offset),
            ).fetchall()
        return ([self._public_decision(r) for r in rows[:limit]],
                str(offset + limit) if len(rows) > limit else None)

    def list_unverified_decisions(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM decisions WHERE session_id=? AND status='unverified' ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [self._public_decision(row, internal=True) for row in rows]

    def decision_ids_for_native(self, session_id: str, native_ids: Iterable[str]) -> List[str]:
        wanted = list(native_ids)
        if not wanted:
            return []
        with self._lock:
            rows = self.conn.execute(
                """SELECT id,native_event_id FROM decisions WHERE session_id=?
                   AND status IN ('pending','submitting','unverified')""",
                (session_id,),
            ).fetchall()
        return [
            row["id"] for row in rows
            if any(row["native_event_id"] == native_id
                   or row["native_event_id"].startswith(native_id + ":") for native_id in wanted)
        ]

    def verify_decision(self, decision_id: str, input_map: Dict[str, str],
                        prompt_fingerprint: str) -> Dict[str, Any]:
        with self.transaction() as conn:
            conn.execute(
                """UPDATE decisions SET status='pending',input_map_json=?,prompt_fingerprint=?,
                   revision=revision+1,updated_at=? WHERE id=? AND status='unverified'""",
                (self._dumps(input_map), prompt_fingerprint, time.time(), decision_id),
            )
        return self.get_decision(decision_id)

    def mark_decision_submitting(self, decision_id: str, option_id: str, key: str) -> Dict[str, Any]:
        now = time.time()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM decisions WHERE reply_idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                return self._public_decision(existing)
            conn.execute(
                """UPDATE decisions SET status='submitting',revision=revision+1,
                   reply_idempotency_key=?,selected_option_id=?,updated_at=? WHERE id=?""",
                (key, option_id, now, decision_id),
            )
        return self.get_decision(decision_id)

    def reserve_sent_message(self, session_id: str, content: str,
                             client_message_id: str) -> Dict[str, Any]:
        now = time.time()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id=? AND client_message_id=?",
                (session_id, client_message_id),
            ).fetchone()
            if existing:
                return self._public_message(existing)
            conv = conn.execute("SELECT id FROM conversations WHERE session_id=?", (session_id,)).fetchone()
            msg_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO chat_messages(
                   id,conversation_id,session_id,client_message_id,role,content,status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (msg_id, conv["id"], session_id, client_message_id, "user", content, "submitting", now, now),
            )
            row = conn.execute("SELECT * FROM chat_messages WHERE id=?", (msg_id,)).fetchone()
            return self._public_message(row)

    def set_message_status(self, message_id: str, status: str) -> Dict[str, Any]:
        if status not in ("sent", "observed", "unknown", "failed"):
            raise ValueError("bad message status")
        with self.transaction() as conn:
            conn.execute(
                "UPDATE chat_messages SET status=?,updated_at=? WHERE id=?",
                (status, time.time(), message_id),
            )
            row = conn.execute("SELECT * FROM chat_messages WHERE id=?", (message_id,)).fetchone()
        return self._public_message(row)

    def get_message_by_client_id(self, session_id: str, client_message_id: str):
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM chat_messages WHERE session_id=? AND client_message_id=?",
                (session_id, client_message_id),
            ).fetchone()
        return self._public_message(row) if row else None

    def get_decision_by_reply_key(self, key: str, *, internal: bool = False):
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM decisions WHERE reply_idempotency_key=?", (key,)
            ).fetchone()
        return self._public_decision(row, internal=internal) if row else None

    def mark_decision_unknown(self, decision_id: str) -> Dict[str, Any]:
        with self.transaction() as conn:
            conn.execute(
                """UPDATE decisions SET status='unknown',revision=revision+1,updated_at=?
                   WHERE id=? AND status='submitting'""",
                (time.time(), decision_id),
            )
        return self.get_decision(decision_id)

    @staticmethod
    def _public_message(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"], "agent_id": row["session_id"], "role": row["role"],
            "content": row["content"], "status": row["status"],
            "native_event_id": row["native_event_id"], "client_message_id": row["client_message_id"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list_messages(
        self,
        session_id: str,
        cursor: Optional[str] = None,
        limit: int = 100,
        *,
        q: Optional[str] = None,
        role: Optional[str] = None,
        after: Optional[float] = None,
        before: Optional[float] = None,
        with_metadata: bool = False,
    ):
        offset = self._cursor(cursor)
        limit = max(1, min(200, int(limit)))
        query = str(q or "").strip()
        if len(query) > 200:
            raise ValueError("q must be at most 200 characters")
        if role not in (None, "", "user", "assistant"):
            raise ValueError("role must be user or assistant")
        after_value = float(after) if after is not None else None
        before_value = float(before) if before is not None else None
        if (
            after_value is not None
            and not math.isfinite(after_value)
            or before_value is not None
            and not math.isfinite(before_value)
        ):
            raise ValueError("after and before must be finite epoch seconds")
        if (
            after_value is not None
            and before_value is not None
            and after_value > before_value
        ):
            raise ValueError("after must be less than or equal to before")
        clauses = ["session_id=?"]
        args: List[Any] = [session_id]
        if query:
            escaped = (
                query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            clauses.append("content LIKE ? ESCAPE '\\'")
            args.append("%" + escaped + "%")
        if role:
            clauses.append("role=?")
            args.append(role)
        if after_value is not None:
            clauses.append("created_at>=?")
            args.append(after_value)
        if before_value is not None:
            clauses.append("created_at<=?")
            args.append(before_value)
        where = " AND ".join(clauses)
        with self._lock:
            rows = self.conn.execute(
                """SELECT * FROM chat_messages WHERE %s
                   ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""" % where,
                tuple(args) + (limit + 1, offset),
            ).fetchall()
            bounds = self.conn.execute(
                """SELECT MIN(created_at) AS retained_from,
                          MAX(created_at) AS retained_to
                   FROM chat_messages WHERE session_id=?""",
                (session_id,),
            ).fetchone()
            session = self.conn.execute(
                """SELECT s.created_at,c.context_json,
                          r.snapshot_id AS reviewed_snapshot_id,
                          r.snapshot_sequence AS reviewed_snapshot_sequence,
                          rs.created_at AS reviewed_snapshot_at,
                          r.reviewed_at
                   FROM agent_sessions s
                   JOIN agent_contexts c ON c.session_id=s.id
                   LEFT JOIN session_reviews r ON r.session_id=s.id
                   LEFT JOIN agent_snapshots rs
                     ON rs.id=r.snapshot_id AND rs.session_id=s.id
                   WHERE s.id=?""",
                (session_id,),
            ).fetchone()
        # Pages are returned chronologically for direct chat rendering.
        values = [self._public_message(r) for r in reversed(rows[:limit])]
        next_cursor = str(offset + limit) if len(rows) > limit else None
        if not with_metadata:
            return values, next_cursor
        cutoff = time.time() - self.retention_days * 86400
        metadata = {
            "retained_from": bounds["retained_from"] if bounds else None,
            "retained_to": bounds["retained_to"] if bounds else None,
            # Deep Context must keep a stable "since last review" boundary even
            # after the acknowledged card disappears from GET /api/review.
            "reviewed_snapshot_id": (
                session["reviewed_snapshot_id"] if session else None
            ),
            "reviewed_snapshot_sequence": (
                int(session["reviewed_snapshot_sequence"])
                if session and session["reviewed_snapshot_sequence"] is not None
                else None
            ),
            "reviewed_snapshot_at": (
                session["reviewed_snapshot_at"] if session else None
            ),
            "reviewed_at": session["reviewed_at"] if session else None,
            "history_truncated": bool(
                session
                and (
                    float(session["created_at"]) < cutoff
                    or bool(
                        self._loads(session["context_json"], {}).get(
                            "message_history_truncated"
                        )
                    )
                )
            ),
            "filters": {
                "q": query or None,
                "role": role or None,
                "after": after_value,
                "before": before_value,
            },
        }
        return values, next_cursor, metadata

    @classmethod
    def _public_timeline_event(
        cls, row: sqlite3.Row, *, include_context: bool = True
    ) -> Dict[str, Any]:
        event = {
            "id": row["id"],
            "agent_id": row["session_id"],
            "sequence": row["sequence"],
            "created_at": row["created_at"],
            "occurred_at": row["created_at"],
            "delta": cls._loads(row["delta_json"], {}),
            "context": cls._loads(row["context_json"], {}) if include_context else None,
        }
        delta = event["delta"]
        lifecycle = delta.get("lifecycle_changed", {}).get("to")
        if lifecycle == "waiting":
            event.update({"type": "decision", "title": "Agent is waiting for a decision"})
        elif lifecycle == "completed":
            event.update({"type": "completed", "title": "Agent completed its work"})
        elif delta.get("new_blockers"):
            event.update({"type": "blocker", "title": "A new blocker was reported"})
        elif delta.get("completed"):
            count = len(delta["completed"])
            event.update({
                "type": "progress",
                "title": "%d item%s completed" % (count, "" if count == 1 else "s"),
            })
        elif delta.get("goal_changed") or delta.get("current_task_changed"):
            event.update({"type": "context", "title": "Agent context changed"})
        else:
            event.update({"type": "activity", "title": "Agent activity updated"})
        return event

    def timeline(self, session_id: Optional[str], cursor: Optional[str] = None, limit: int = 50):
        offset = self._cursor(cursor)
        limit = max(1, min(100, int(limit)))
        with self._lock:
            if session_id:
                rows = self.conn.execute(
                    """SELECT * FROM agent_snapshots WHERE session_id=?
                       ORDER BY sequence DESC LIMIT ? OFFSET ?""",
                    (session_id, limit + 1, offset),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM agent_snapshots ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                    (limit + 1, offset),
                ).fetchall()
        events = [
            self._public_timeline_event(row, include_context=bool(session_id))
            for row in rows[:limit]
        ]
        return events, str(offset + limit) if len(rows) > limit else None

    @staticmethod
    def _recovery_order_predicate(
        relation: str, boundary: Tuple[float, int, int, str]
    ) -> Tuple[str, Tuple[Any, ...]]:
        if relation not in ("<", ">"):
            raise ValueError("bad recovery cursor direction")
        occurred_at, kind_rank, source_order, resource_id = boundary
        sql = (
            "(occurred_at {r} ? OR (occurred_at=? AND "
            "(kind_rank {r} ? OR (kind_rank=? AND "
            "(source_order {r} ? OR (source_order=? AND resource_id {r} ?))))))"
        ).format(r=relation)
        return sql, (
            occurred_at,
            occurred_at,
            kind_rank,
            kind_rank,
            source_order,
            source_order,
            resource_id,
        )

    def _recovery_page(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        cursor_kind: str,
        cursor: Optional[str],
        limit: int,
        generated_at: float,
        message_anchor: int,
        snapshot_anchor: int,
        only_kind_rank: Optional[int] = None,
    ) -> Dict[str, Any]:
        parsed = self._decode_recovery_cursor(
            cursor, kind=cursor_kind, session_id=session_id
        )
        anchor_at = parsed["anchor_at"] if parsed else generated_at
        message_anchor = parsed["message_anchor"] if parsed else message_anchor
        snapshot_anchor = parsed["snapshot_anchor"] if parsed else snapshot_anchor
        direction = parsed["direction"] if parsed else "older"
        boundary = parsed["boundary"] if parsed else None
        limit = max(1, min(MAX_RECOVERY_ITEMS, int(limit)))

        cte = """WITH activity AS (
            SELECT 'visible_message' AS activity_kind,0 AS kind_rank,
                   m.rowid AS source_order,m.id AS resource_id,
                   m.created_at AS occurred_at,m.*,
                   NULL AS sequence,NULL AS delta_json,NULL AS context_json
            FROM chat_messages m
            WHERE m.session_id=? AND m.rowid<=? AND m.updated_at<=?
            UNION ALL
            SELECT 'semantic_event' AS activity_kind,1 AS kind_rank,
                   s.sequence AS source_order,s.id AS resource_id,
                   s.created_at AS occurred_at,
                   s.id AS id,NULL AS conversation_id,s.session_id,
                   NULL AS native_event_id,NULL AS client_message_id,
                   NULL AS role,NULL AS content,NULL AS status,
                   s.created_at,s.created_at AS updated_at,
                   s.sequence,s.delta_json,s.context_json
            FROM agent_snapshots s
            WHERE s.session_id=? AND s.sequence<=? AND s.created_at<=?
        )"""
        cte_args: Tuple[Any, ...] = (
            session_id,
            message_anchor,
            anchor_at,
            session_id,
            snapshot_anchor,
            anchor_at,
        )

        def where_for(
            relation: Optional[str], key: Optional[Tuple[float, int, int, str]]
        ) -> Tuple[str, Tuple[Any, ...]]:
            clauses: List[str] = []
            args: Tuple[Any, ...] = ()
            if only_kind_rank is not None:
                clauses.append("kind_rank=?")
                args += (only_kind_rank,)
            if relation and key:
                predicate, predicate_args = self._recovery_order_predicate(
                    relation, key
                )
                clauses.append(predicate)
                args += predicate_args
            return (" WHERE " + " AND ".join(clauses) if clauses else ""), args

        relation = ">" if direction == "newer" else "<"
        page_where, page_args = where_for(relation if boundary else None, boundary)
        sql_order = "ASC" if direction == "newer" else "DESC"
        rows = conn.execute(
            cte
            + " SELECT * FROM activity"
            + page_where
            + " ORDER BY occurred_at %s,kind_rank %s,source_order %s,resource_id %s LIMIT ?"
            % ((sql_order,) * 4),
            cte_args + page_args + (limit,),
        ).fetchall()
        if direction != "newer":
            rows = list(reversed(rows))

        def key(row: sqlite3.Row) -> Tuple[float, int, int, str]:
            return (
                float(row["occurred_at"]),
                int(row["kind_rank"]),
                int(row["source_order"]),
                str(row["resource_id"]),
            )

        low = key(rows[0]) if rows else boundary
        high = key(rows[-1]) if rows else boundary

        def exists(relation: str, edge: Optional[Tuple[float, int, int, str]]) -> bool:
            if edge is None:
                return False
            where, args = where_for(relation, edge)
            return conn.execute(
                cte + " SELECT 1 FROM activity" + where + " LIMIT 1",
                cte_args + args,
            ).fetchone() is not None

        has_older = exists("<", low)
        has_newer = exists(">", high)

        def encoded(
            requested_direction: str,
            edge: Optional[Tuple[float, int, int, str]],
            available: bool,
        ) -> Optional[str]:
            if not available or edge is None:
                return None
            return self._encode_recovery_cursor(
                kind=cursor_kind,
                session_id=session_id,
                anchor_at=anchor_at,
                message_anchor=message_anchor,
                snapshot_anchor=snapshot_anchor,
                direction=requested_direction,
                boundary=edge,
            )

        if parsed is None:
            cursor_status = "current"
        elif rows:
            cursor_status = "valid"
        elif has_older or has_newer:
            cursor_status = "data_unavailable"
        else:
            cursor_status = "exhausted"
        return {
            "rows": rows,
            "older_cursor": encoded("older", low, has_older),
            "newer_cursor": encoded("newer", high, has_newer),
            "cursor_status": cursor_status,
            "anchor_at": anchor_at,
            "message_anchor": message_anchor,
            "snapshot_anchor": snapshot_anchor,
        }

    @staticmethod
    def _runtime_session_state(agent: Dict[str, Any]) -> str:
        lifecycle = str(agent.get("lifecycle") or agent.get("context", {}).get("lifecycle") or "")
        association = str(agent.get("association") or "")
        if association == "confirmed" and lifecycle not in ("offline", "completed"):
            return "live_bound"
        if lifecycle in ("offline", "completed") or association == "unavailable":
            return "offline"
        if association in ("probable", "ambiguous") and agent.get("last_event_at") is not None:
            return "observed_unbound"
        return "unknown"

    def recovery(
        self,
        session_id: str,
        *,
        message_limit: int = 20,
        timeline_limit: int = 20,
        activity_limit: int = 20,
        message_cursor: Optional[str] = None,
        timeline_cursor: Optional[str] = None,
        activity_cursor: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return one bounded, non-mutating SQLite view of recovery state."""
        with self.read_transaction() as conn:
            generated_at = time.time()
            agent = self.get_agent(session_id)
            if not agent:
                return None
            context_row = conn.execute(
                "SELECT revision,updated_at FROM agent_contexts WHERE session_id=?",
                (session_id,),
            ).fetchone()
            anchors = conn.execute(
                """SELECT
                       COALESCE((SELECT MAX(rowid) FROM chat_messages WHERE session_id=?),0)
                           AS message_anchor,
                       COALESCE((SELECT MAX(sequence) FROM agent_snapshots WHERE session_id=?),0)
                           AS snapshot_anchor""",
                (session_id, session_id),
            ).fetchone()
            message_anchor = int(anchors["message_anchor"])
            snapshot_anchor = int(anchors["snapshot_anchor"])

            newest = conn.execute(
                "SELECT * FROM agent_snapshots WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            oldest = conn.execute(
                "SELECT * FROM agent_snapshots WHERE session_id=? ORDER BY sequence LIMIT 1",
                (session_id,),
            ).fetchone()
            review = conn.execute(
                "SELECT * FROM session_reviews WHERE session_id=?", (session_id,)
            ).fetchone()
            visit = conn.execute(
                "SELECT * FROM session_visits WHERE session_id=?", (session_id,)
            ).fetchone()
            baseline_record = review or visit
            basis = (
                "shared_review"
                if review
                else "legacy_shared_visit"
                if visit
                else "available_history"
            )
            baseline = None
            if baseline_record and baseline_record["snapshot_id"]:
                baseline = conn.execute(
                    "SELECT * FROM agent_snapshots WHERE id=? AND session_id=?",
                    (baseline_record["snapshot_id"], session_id),
                ).fetchone()
            changes_truncated = bool(
                baseline_record and baseline_record["snapshot_id"] and baseline is None
            )
            baseline_sequence = int(
                baseline_record["snapshot_sequence"] if baseline_record else 0
            )
            if changes_truncated and oldest:
                baseline = oldest
            if baseline_record and oldest:
                changes_truncated = changes_truncated or bool(
                    baseline_sequence < int(oldest["sequence"]) - 1
                )
            semantic_earlier_unavailable = bool(
                (
                    oldest
                    and (
                        int(oldest["sequence"]) > 1
                        or int(context_row["revision"])
                        > int(newest["sequence"] if newest else 0)
                    )
                )
                or (oldest is None and int(context_row["revision"]) > 0)
            )
            if not baseline_record:
                changes_truncated = semantic_earlier_unavailable
            baseline_context = self._loads(baseline["context_json"], {}) if baseline else {}

            message_page = self._recovery_page(
                conn,
                session_id=session_id,
                cursor_kind="messages",
                cursor=message_cursor,
                limit=message_limit,
                generated_at=generated_at,
                message_anchor=message_anchor,
                snapshot_anchor=snapshot_anchor,
                only_kind_rank=0,
            )
            timeline_page = self._recovery_page(
                conn,
                session_id=session_id,
                cursor_kind="timeline",
                cursor=timeline_cursor,
                limit=timeline_limit,
                generated_at=generated_at,
                message_anchor=message_anchor,
                snapshot_anchor=snapshot_anchor,
                only_kind_rank=1,
            )
            activity_page = self._recovery_page(
                conn,
                session_id=session_id,
                cursor_kind="activity",
                cursor=activity_cursor,
                limit=activity_limit,
                generated_at=generated_at,
                message_anchor=message_anchor,
                snapshot_anchor=snapshot_anchor,
            )

            message_rows = message_page.pop("rows")
            timeline_rows = timeline_page.pop("rows")
            activity_rows = activity_page.pop("rows")
            messages = [self._public_message(row) for row in message_rows]
            timeline = [self._public_timeline_event(row) for row in timeline_rows]
            entries = []
            for row in activity_rows:
                if row["activity_kind"] == "visible_message":
                    resource = self._public_message(row)
                else:
                    resource = self._public_timeline_event(row)
                entries.append({
                    "id": "%s:%s" % (row["activity_kind"], row["resource_id"]),
                    "kind": row["activity_kind"],
                    "occurred_at": row["occurred_at"],
                    "resource_id": row["resource_id"],
                    "resource": resource,
                })

            message_bounds = conn.execute(
                """SELECT MIN(created_at) AS retained_from,MAX(created_at) AS retained_to
                   FROM chat_messages WHERE session_id=?""",
                (session_id,),
            ).fetchone()
            cutoff = generated_at - self.retention_days * 86400
            message_history_truncated = bool(
                float(agent["created_at"]) < cutoff
                or agent["context"].get("message_history_truncated")
            )
            timeline_bounds = {
                "retained_from": oldest["created_at"] if oldest else None,
                "retained_to": newest["created_at"] if newest else None,
            }

            message_coverage = {
                "retained_from": message_bounds["retained_from"],
                "retained_to": message_bounds["retained_to"],
                "history_truncated": message_history_truncated,
                "unavailable_reason": (
                    "expired_or_deleted" if message_history_truncated else None
                ),
                "more_retained_older": message_page["older_cursor"] is not None,
                "more_retained_newer": message_page["newer_cursor"] is not None,
            }
            timeline_coverage = {
                **timeline_bounds,
                "history_truncated": semantic_earlier_unavailable,
                "unavailable_reason": (
                    "expired_or_deleted" if semantic_earlier_unavailable else None
                ),
                "more_retained_older": timeline_page["older_cursor"] is not None,
                "more_retained_newer": timeline_page["newer_cursor"] is not None,
            }
            changes = {
                "basis": basis,
                "baseline_snapshot_id": (
                    baseline_record["snapshot_id"] if baseline_record else None
                ),
                "as_of_snapshot_id": newest["id"] if newest else None,
                "history_truncated": changes_truncated,
                "unavailable_reason": (
                    "expired_or_deleted" if changes_truncated else None
                ),
                "delta": semantic_delta(baseline_context, agent["context"]),
            }
            return {
                "version": 1,
                "generated_at": generated_at,
                "consistency": "single_read",
                "agent": agent,
                "changes": changes,
                "recent_messages": {
                    "order": "oldest_to_newest",
                    "messages": messages,
                    "next_cursor": message_page["older_cursor"],
                    "previous_cursor": message_page["newer_cursor"],
                    "cursor_status": message_page["cursor_status"],
                    "coverage": message_coverage,
                },
                "recent_timeline": {
                    "order": "oldest_to_newest",
                    "events": timeline,
                    "next_cursor": timeline_page["older_cursor"],
                    "previous_cursor": timeline_page["newer_cursor"],
                    "cursor_status": timeline_page["cursor_status"],
                    "coverage": timeline_coverage,
                },
                "recent_activity": {
                    "order": "oldest_to_newest",
                    "tie_break": "occurred_at_kind_source_order_resource_id",
                    "entries": entries,
                    "older_cursor": activity_page["older_cursor"],
                    "newer_cursor": activity_page["newer_cursor"],
                    "cursor_status": activity_page["cursor_status"],
                    "coverage": {
                        "visible_messages": message_coverage,
                        "semantic_history": timeline_coverage,
                    },
                },
                "freshness": {
                    "observed_at": generated_at,
                    "projected_at": context_row["updated_at"],
                    "last_runtime_event_at": agent.get("last_event_at"),
                    "runtime_session": self._runtime_session_state(agent),
                    "model_context": "runtime_owned_unverified",
                },
            }

    def resume(self, session_id: str) -> Optional[Dict[str, Any]]:
        agent = self.get_agent(session_id)
        if not agent:
            return None
        with self._lock:
            visit = self.conn.execute(
                "SELECT * FROM session_visits WHERE session_id=?", (session_id,)
            ).fetchone()
            newest = self.conn.execute(
                "SELECT * FROM agent_snapshots WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            oldest = self.conn.execute(
                "SELECT * FROM agent_snapshots WHERE session_id=? ORDER BY sequence LIMIT 1",
                (session_id,),
            ).fetchone()
            baseline_seq = int(visit["snapshot_sequence"]) if visit else 0
            baseline = None
            if visit:
                baseline = self.conn.execute(
                    "SELECT context_json FROM agent_snapshots WHERE id=? AND session_id=?",
                    (visit["snapshot_id"], session_id),
                ).fetchone()
            history_truncated = bool(visit and baseline is None)
            if baseline is None and history_truncated and oldest:
                baseline = oldest
        baseline_context = self._loads(baseline["context_json"], {}) if baseline else {}
        changes = semantic_delta(baseline_context, agent["context"])
        decisions, _ = self.list_decisions(status="pending", session_id=session_id, limit=100)
        history_truncated = history_truncated or bool(
            visit and oldest and baseline_seq < int(oldest["sequence"]) - 1
        )
        return {
            "agent": agent, "changes": changes, "decisions": decisions,
            "pending_decisions": decisions,
            "goal": agent["context"].get("goal", ""),
            "current_task": agent["context"].get("current_task", ""),
            "progress": agent["context"].get("progress"),
            "blockers": agent["context"].get("blockers", []),
            "next_action": agent["context"].get("next_action", ""),
            "estimated_completion": agent["context"].get("estimated_completion"),
            "baseline_snapshot_id": visit["snapshot_id"] if visit else None,
            "as_of_snapshot_id": newest["id"] if newest else None,
            "history_truncated": history_truncated,
        }

    def review_groups(self) -> List[Dict[str, Any]]:
        """Return deterministic structured Review cards without mutating state."""
        groups: List[Dict[str, Any]] = []
        with self._lock:
            session_rows = self.conn.execute(
                "SELECT id FROM agent_sessions ORDER BY id"
            ).fetchall()
            for session_row in session_rows:
                session_id = session_row["id"]
                agent = self.get_agent(session_id)
                if not agent:
                    continue
                review = self.conn.execute(
                    "SELECT * FROM session_reviews WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                newest = self.conn.execute(
                    """SELECT * FROM agent_snapshots WHERE session_id=?
                       ORDER BY sequence DESC LIMIT 1""",
                    (session_id,),
                ).fetchone()
                oldest = self.conn.execute(
                    """SELECT * FROM agent_snapshots WHERE session_id=?
                       ORDER BY sequence LIMIT 1""",
                    (session_id,),
                ).fetchone()
                reviewed_snapshot = None
                if review and review["snapshot_id"]:
                    reviewed_snapshot = self.conn.execute(
                        """SELECT * FROM agent_snapshots
                           WHERE id=? AND session_id=?""",
                        (review["snapshot_id"], session_id),
                    ).fetchone()
                baseline = reviewed_snapshot
                history_truncated = bool(review and baseline is None)
                if baseline is None and history_truncated and oldest:
                    baseline = oldest
                baseline_context = (
                    self._loads(baseline["context_json"], {}) if baseline else {}
                )
                reviewed_sequence = int(review["snapshot_sequence"]) if review else 0
                newest_sequence = int(newest["sequence"]) if newest else 0
                has_unreviewed_snapshot = bool(
                    newest and newest_sequence > reviewed_sequence
                )
                changes = (
                    semantic_delta(baseline_context, agent["context"])
                    if has_unreviewed_snapshot
                    else {}
                )
                history_truncated = history_truncated or bool(
                    review
                    and oldest
                    and reviewed_sequence < int(oldest["sequence"]) - 1
                )

                decision_rows = self.conn.execute(
                    """SELECT * FROM decisions
                       WHERE session_id=? AND status IN ('pending','unknown')
                       ORDER BY created_at,id""",
                    (session_id,),
                ).fetchall()
                decisions = []
                for decision_row in decision_rows:
                    decision = self._public_decision(decision_row)
                    decision["review_status"] = (
                        "actionable"
                        if decision["status"] == "pending"
                        else "terminal_required"
                    )
                    decisions.append(decision)

                # Error attention belongs to an unreviewed structured
                # snapshot. It must not become a permanent session badge after
                # that displayed snapshot has been acknowledged.
                agent_error = bool(
                    has_unreviewed_snapshot
                    and agent.get("lifecycle") == "error"
                )
                extraction_degraded = bool(
                    has_unreviewed_snapshot
                    and str(agent.get("extraction_health") or "ok") != "ok"
                )
                if not changes and not decisions and not agent_error and not extraction_degraded:
                    continue

                pending = [item for item in decisions if item["status"] == "pending"]
                unknown = [item for item in decisions if item["status"] == "unknown"]
                urgent = [
                    item
                    for item in pending
                    if item.get("priority") in ("high", "critical")
                ]
                new_blockers = list(changes.get("new_blockers") or [])
                oldest_pending_at = min(
                    (float(item["created_at"]) for item in pending), default=None
                )
                oldest_unknown_at = min(
                    (float(item["created_at"]) for item in unknown), default=None
                )

                reasons: List[str] = []
                if urgent:
                    reasons.append("urgent_decision")
                if agent_error:
                    reasons.append("agent_error")
                if extraction_degraded:
                    reasons.append("extraction_degraded")
                if pending:
                    reasons.append("pending_decision")
                if unknown:
                    reasons.append("terminal_required_decision")
                if new_blockers:
                    reasons.append("new_blocker")
                if changes:
                    reasons.append("semantic_change")

                if urgent:
                    rank_reason = "urgent_decision"
                    rank_time = min(float(item["created_at"]) for item in urgent)
                    rank_tier = 0
                elif agent_error or extraction_degraded:
                    rank_reason = "error"
                    rank_time = float(
                        agent.get("last_event_at")
                        or agent.get("updated_at")
                        or 0
                    )
                    rank_tier = 1
                elif pending:
                    rank_reason = "pending_decision"
                    rank_time = float(oldest_pending_at or 0)
                    rank_tier = 2
                elif new_blockers:
                    rank_reason = "new_blocker"
                    rank_time = min(
                        float(item.get("created_at") or 0)
                        for item in new_blockers
                    )
                    rank_tier = 3
                else:
                    rank_reason = "changed"
                    first_unreviewed = self.conn.execute(
                        """SELECT MIN(created_at) AS created_at
                           FROM agent_snapshots
                           WHERE session_id=? AND sequence>?""",
                        (session_id, reviewed_sequence),
                    ).fetchone()
                    rank_time = float(
                        oldest_unknown_at
                        or (
                            first_unreviewed["created_at"]
                            if first_unreviewed
                            else None
                        )
                        or (newest["created_at"] if newest else 0)
                    )
                    rank_tier = 4

                groups.append(
                    {
                        "agent_id": session_id,
                        "agent": agent,
                        "as_of_snapshot_id": newest["id"] if newest else None,
                        "as_of_snapshot_sequence": (
                            int(newest["sequence"]) if newest else 0
                        ),
                        "as_of_snapshot_at": (
                            newest["created_at"] if newest else None
                        ),
                        "reviewed_snapshot_id": (
                            review["snapshot_id"] if review else None
                        ),
                        "reviewed_snapshot_sequence": reviewed_sequence,
                        "reviewed_snapshot_at": (
                            reviewed_snapshot["created_at"]
                            if reviewed_snapshot
                            else None
                        ),
                        "reviewed_at": review["reviewed_at"] if review else None,
                        "has_changes": bool(changes),
                        "history_truncated": history_truncated,
                        "changes": changes,
                        "decisions": decisions,
                        "oldest_pending_decision_at": oldest_pending_at,
                        "rank_reason": rank_reason,
                        "attention_reasons": reasons,
                        "_rank_key": (rank_tier, rank_time, session_id),
                    }
                )
        groups.sort(key=lambda item: item["_rank_key"])
        for group in groups:
            group.pop("_rank_key", None)
        return groups

    def get_review_settings(self) -> Dict[str, Any]:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM review_settings WHERE id=1"
            ).fetchone()
        if row is None:
            raise RuntimeError("review settings are unavailable")
        return {
            "interval_minutes": row["interval_minutes"],
            "next_due_at": row["next_due_at"],
            "last_digest_at": row["last_digest_at"],
            "urgent_pane_errors": bool(row["urgent_pane_errors"]),
            "updated_at": row["updated_at"],
        }

    def update_review_settings(
        self,
        *,
        interval_present: bool = False,
        interval_minutes: Optional[int] = None,
        urgent_pane_errors: Optional[bool] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        when = float(now if now is not None else time.time())
        if interval_present and interval_minutes is not None:
            interval_minutes = int(interval_minutes)
            if not (
                MIN_REVIEW_INTERVAL_MINUTES
                <= interval_minutes
                <= MAX_REVIEW_INTERVAL_MINUTES
            ):
                raise ValueError(
                    "interval_minutes must be null or between %d and %d"
                    % (
                        MIN_REVIEW_INTERVAL_MINUTES,
                        MAX_REVIEW_INTERVAL_MINUTES,
                    )
                )
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM review_settings WHERE id=1").fetchone()
            if row is None:
                raise RuntimeError("review settings are unavailable")
            current_interval = row["interval_minutes"]
            next_interval = (
                interval_minutes if interval_present else current_interval
            )
            next_due = row["next_due_at"]
            changed = False
            if interval_present and next_interval != current_interval:
                next_due = (
                    when + int(next_interval) * 60
                    if next_interval is not None
                    else None
                )
                changed = True
            next_urgent = (
                bool(urgent_pane_errors)
                if urgent_pane_errors is not None
                else bool(row["urgent_pane_errors"])
            )
            if next_urgent != bool(row["urgent_pane_errors"]):
                changed = True
            if changed:
                conn.execute(
                    """UPDATE review_settings
                       SET interval_minutes=?,next_due_at=?,
                           urgent_pane_errors=?,updated_at=?
                       WHERE id=1""",
                    (next_interval, next_due, int(next_urgent), when),
                )
        return self.get_review_settings()

    def review(self, session_id: str, snapshot_id: str) -> Dict[str, Any]:
        """Monotonically acknowledge a displayed snapshot and reset the timer."""
        when = time.time()
        with self.transaction() as conn:
            snap = conn.execute(
                """SELECT id,sequence FROM agent_snapshots
                   WHERE id=? AND session_id=?""",
                (snapshot_id, session_id),
            ).fetchone()
            if not snap:
                raise KeyError(snapshot_id)
            current = conn.execute(
                "SELECT * FROM session_reviews WHERE session_id=?",
                (session_id,),
            ).fetchone()
            advanced = (
                current is None
                or int(snap["sequence"]) > int(current["snapshot_sequence"])
            )
            if advanced:
                conn.execute(
                    """INSERT INTO session_reviews(
                           session_id,snapshot_id,snapshot_sequence,reviewed_at
                       ) VALUES (?,?,?,?)
                       ON CONFLICT(session_id) DO UPDATE SET
                           snapshot_id=excluded.snapshot_id,
                           snapshot_sequence=excluded.snapshot_sequence,
                           reviewed_at=excluded.reviewed_at""",
                    (session_id, snap["id"], snap["sequence"], when),
                )
            # A displayed card that advances the shared baseline completes
            # review work and resets the timer. Replaying that snapshot (or an
            # older one) is a no-op and must not postpone newer work.
            if advanced:
                settings = conn.execute(
                    "SELECT interval_minutes FROM review_settings WHERE id=1"
                ).fetchone()
                if settings and settings["interval_minutes"] is not None:
                    conn.execute(
                        """UPDATE review_settings
                           SET next_due_at=?,updated_at=? WHERE id=1""",
                        (
                            when + int(settings["interval_minutes"]) * 60,
                            when,
                        ),
                    )
            row = conn.execute(
                """SELECT r.*,s.created_at AS snapshot_at
                   FROM session_reviews r
                   LEFT JOIN agent_snapshots s
                     ON s.id=r.snapshot_id AND s.session_id=r.session_id
                   WHERE r.session_id=?""",
                (session_id,),
            ).fetchone()
        return {
            "agent_id": session_id,
            "snapshot_id": row["snapshot_id"],
            "snapshot_sequence": int(row["snapshot_sequence"]),
            "snapshot_at": row["snapshot_at"],
            "reviewed_at": row["reviewed_at"],
            "advanced": advanced,
        }

    def claim_review_due(
        self, *, has_work: bool, now: Optional[float] = None
    ) -> Dict[str, Any]:
        """Atomically advance one due window so restarts cannot duplicate it."""
        when = float(now if now is not None else time.time())
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM review_settings WHERE id=1").fetchone()
            if (
                row is None
                or row["interval_minutes"] is None
                or row["next_due_at"] is None
                or when < float(row["next_due_at"])
            ):
                return {
                    "claimed": False,
                    "has_work": bool(has_work),
                    "next_due_at": row["next_due_at"] if row else None,
                    "last_digest_at": row["last_digest_at"] if row else None,
                }
            next_due_at = when + int(row["interval_minutes"]) * 60
            last_digest_at = when if has_work else row["last_digest_at"]
            conn.execute(
                """UPDATE review_settings
                   SET next_due_at=?,last_digest_at=?,updated_at=? WHERE id=1""",
                (next_due_at, last_digest_at, when),
            )
        return {
            "claimed": True,
            "has_work": bool(has_work),
            "next_due_at": next_due_at,
            "last_digest_at": last_digest_at,
        }

    def visit(self, session_id: str, snapshot_id: str) -> Dict[str, Any]:
        with self.transaction() as conn:
            snap = conn.execute(
                "SELECT id,sequence FROM agent_snapshots WHERE id=? AND session_id=?",
                (snapshot_id, session_id),
            ).fetchone()
            if not snap:
                raise KeyError(snapshot_id)
            current = conn.execute(
                "SELECT snapshot_sequence FROM session_visits WHERE session_id=?", (session_id,)
            ).fetchone()
            if current is None or int(snap["sequence"]) > int(current["snapshot_sequence"]):
                conn.execute(
                    """INSERT INTO session_visits(session_id,snapshot_id,snapshot_sequence,visited_at)
                       VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET
                       snapshot_id=excluded.snapshot_id,snapshot_sequence=excluded.snapshot_sequence,
                       visited_at=excluded.visited_at""",
                    (session_id, snap["id"], snap["sequence"], time.time()),
                )
            row = conn.execute("SELECT * FROM session_visits WHERE session_id=?", (session_id,)).fetchone()
        return {"agent_id": session_id, "snapshot_id": row["snapshot_id"],
                "snapshot_sequence": row["snapshot_sequence"], "visited_at": row["visited_at"]}

    def delete_history(self, session_id: str) -> bool:
        with self.transaction() as conn:
            if not conn.execute("SELECT 1 FROM agent_sessions WHERE id=?", (session_id,)).fetchone():
                return False
            conn.execute("DELETE FROM agent_snapshots WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM decisions WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM session_visits WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM session_reviews WHERE session_id=?", (session_id,))
            row = conn.execute(
                "SELECT revision,context_json FROM agent_contexts WHERE session_id=?", (session_id,)
            ).fetchone()
            context = self._loads(row["context_json"], {})
            context["decisions"] = []
            context["message_history_truncated"] = True
            if context.get("lifecycle") == "waiting":
                context["lifecycle"] = "observing"
            context["revision"] = int(row["revision"]) + 1
            context["last_updated"] = time.time()
            conn.execute(
                "UPDATE agent_contexts SET revision=?,context_json=?,updated_at=? WHERE session_id=?",
                (context["revision"], self._dumps(context), time.time(), session_id),
            )
            caps = default_capabilities("unavailable")
            session = conn.execute(
                "SELECT association,capabilities_json FROM agent_sessions WHERE id=?", (session_id,)
            ).fetchone()
            caps.update(self._loads(session["capabilities_json"], {}))
            caps["decision_reply"] = "open_terminal"
            conn.execute(
                "UPDATE agent_sessions SET lifecycle=?,capabilities_json=?,updated_at=? WHERE id=?",
                (context["lifecycle"], self._dumps(caps), time.time(), session_id),
            )
        with self._lock:
            self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return True

    def prune(self, now: Optional[float] = None) -> None:
        cutoff = (now or time.time()) - self.retention_days * 86400
        with self.transaction() as conn:
            conn.execute("DELETE FROM agent_snapshots WHERE created_at<?", (cutoff,))
            truncated_sessions = conn.execute(
                """SELECT DISTINCT session_id FROM chat_messages
                   WHERE created_at<?""",
                (cutoff,),
            ).fetchall()
            for truncated in truncated_sessions:
                row = conn.execute(
                    """SELECT context_json FROM agent_contexts
                       WHERE session_id=?""",
                    (truncated["session_id"],),
                ).fetchone()
                if not row:
                    continue
                context = self._loads(row["context_json"], {})
                if context.get("message_history_truncated"):
                    continue
                context["message_history_truncated"] = True
                conn.execute(
                    """UPDATE agent_contexts SET context_json=?,updated_at=?
                       WHERE session_id=?""",
                    (
                        self._dumps(context),
                        time.time(),
                        truncated["session_id"],
                    ),
                )
            conn.execute("DELETE FROM chat_messages WHERE created_at<?", (cutoff,))
            conn.execute(
                "DELETE FROM decisions WHERE updated_at<? AND status NOT IN ('pending','submitting')", (cutoff,)
            )
            conn.execute(
                """DELETE FROM agent_sessions WHERE updated_at<? AND lifecycle IN
                   ('completed','offline','error')""", (cutoff,)
            )
