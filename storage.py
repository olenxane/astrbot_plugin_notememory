import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

LAYERS = {"short", "archived", "important"}


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


class NoteStore:
    def __init__(self, path: Path):
        self.path = path

    async def _run(self, operation):
        def run():
            with closing(sqlite3.connect(self.path, timeout=15)) as db:
                db.row_factory = sqlite3.Row
                with db:
                    return operation(db)

        return await asyncio.to_thread(run)

    async def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def setup(db):
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scopes (
                    scope TEXT PRIMARY KEY,
                    label TEXT NOT NULL DEFAULT '',
                    provider_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    last_summary_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    error_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '',
                    normalized TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    layer TEXT NOT NULL CHECK(layer IN ('short','archived','important')),
                    source TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_active_at REAL NOT NULL,
                    archived_at REAL,
                    recall_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS notes_scope_layer
                    ON notes(scope, layer, last_active_at);
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    turn_key TEXT NOT NULL,
                    text TEXT NOT NULL,
                    offset INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    UNIQUE(scope, turn_key)
                );
                CREATE INDEX IF NOT EXISTS turns_scope ON turns(scope, id);
            """)

        await self._run(setup)

    @staticmethod
    def _scope(db, scope, label="", provider_id=""):
        db.execute(
            """INSERT INTO scopes(scope,label,provider_id,created_at) VALUES(?,?,?,?)
            ON CONFLICT(scope) DO UPDATE SET
                label=CASE WHEN excluded.label!='' THEN excluded.label ELSE scopes.label END,
                provider_id=CASE WHEN excluded.provider_id!=''
                    THEN excluded.provider_id ELSE scopes.provider_id END""",
            (scope, label, provider_id, time.time()),
        )

    async def touch_scope(self, scope, label="", provider_id=""):
        await self._run(lambda db: self._scope(db, scope, label, provider_id))

    async def provider_id(self, scope):
        def fetch(db):
            row = db.execute("SELECT provider_id FROM scopes WHERE scope=?", (scope,)).fetchone()
            return row[0] if row else ""

        return await self._run(fetch)

    async def scopes(self):
        return await self._run(
            lambda db: [
                dict(row)
                for row in db.execute(
                    "SELECT scope,label FROM scopes WHERE scope LIKE 'user:%' ORDER BY created_at DESC"
                )
            ]
        )

    async def archive_expired(self, days, scope=None, now=None):
        now = time.time() if now is None else now

        def archive(db):
            sql = "UPDATE notes SET layer='archived',archived_at=? WHERE layer='short' AND last_active_at<=?"
            params = [now, now - days * 86400]
            if scope is not None:
                sql += " AND scope=?"
                params.append(scope)
            return db.execute(sql, params).rowcount

        return await self._run(archive)

    @staticmethod
    def _insert(db, scope, content, tags, layer, source, created_by, now):
        note_id = uuid.uuid4().hex[:12]
        db.execute(
            """INSERT INTO notes(id,scope,content,tags,normalized,search_text,layer,source,
                created_by,created_at,updated_at,last_active_at,archived_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                note_id,
                scope,
                content,
                tags,
                normalize(content),
                normalize(content + " " + tags),
                layer,
                source,
                created_by,
                now,
                now,
                now,
                now if layer == "archived" else None,
            ),
        )
        return dict(db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())

    async def create(self, scope, content, tags="", layer="short", source="command", created_by=""):
        def create(db):
            self._scope(db, scope)
            return self._insert(db, scope, content, tags, layer, source, created_by, time.time())

        return await self._run(create)

    async def get(self, scope, note_id):
        def get(db):
            row = db.execute(
                "SELECT * FROM notes WHERE scope=? AND id=?", (scope, note_id)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(get)

    async def update(self, scope, note_id, content, tags, layer):
        def update(db):
            now = time.time()
            result = db.execute(
                """UPDATE notes SET content=?,tags=?,normalized=?,search_text=?,layer=?,
                updated_at=?,last_active_at=?,archived_at=? WHERE scope=? AND id=?""",
                (
                    content,
                    tags,
                    normalize(content),
                    normalize(content + " " + tags),
                    layer,
                    now,
                    now,
                    now if layer == "archived" else None,
                    scope,
                    note_id,
                ),
            )
            if not result.rowcount:
                raise ValueError("未找到当前用户的这条便签")
            return dict(
                db.execute(
                    "SELECT * FROM notes WHERE scope=? AND id=?", (scope, note_id)
                ).fetchone()
            )

        return await self._run(update)

    async def delete(self, scope, note_id):
        return await self._run(
            lambda db: (
                db.execute("DELETE FROM notes WHERE scope=? AND id=?", (scope, note_id)).rowcount
                > 0
            )
        )

    async def list_notes(self, scope, layer="all", keyword="", page=1, page_size=10):
        def fetch(db):
            where, params = ["scope=?"], [scope]
            if layer != "all":
                where.append("layer=?")
                params.append(layer)
            for term in normalize(keyword).split():
                where.append("instr(search_text,?)>0")
                params.append(term)
            clause = " AND ".join(where)
            total = db.execute(f"SELECT COUNT(*) FROM notes WHERE {clause}", params).fetchone()[0]
            rows = db.execute(
                f"""SELECT * FROM notes WHERE {clause}
                    ORDER BY CASE layer WHEN 'important' THEN 0 WHEN 'short' THEN 1 ELSE 2 END,
                    updated_at DESC,id LIMIT ? OFFSET ?""",
                params + [page_size, (page - 1) * page_size],
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size),
            }

        return await self._run(fetch)

    async def active_notes(self, scope):
        return await self._run(
            lambda db: [
                dict(row)
                for row in db.execute(
                    """SELECT * FROM notes WHERE scope=? AND layer IN ('short','important')
            ORDER BY CASE layer WHEN 'important' THEN 0 ELSE 1 END,created_at,id""",
                    (scope,),
                )
            ]
        )

    async def recall(self, scope, note_ids):
        def recall(db):
            now, result = time.time(), []
            for note_id in note_ids:
                db.execute(
                    """UPDATE notes SET layer='short',last_active_at=?,archived_at=NULL,
                    recall_count=recall_count+1 WHERE scope=? AND id=? AND layer='archived'""",
                    (now, scope, note_id),
                )
                row = db.execute(
                    "SELECT * FROM notes WHERE scope=? AND id=?", (scope, note_id)
                ).fetchone()
                if row:
                    result.append(dict(row))
            return result

        return await self._run(recall)

    async def stats(self, scope):
        def stats(db):
            counts = {"short": 0, "archived": 0, "important": 0}
            counts.update(
                {
                    row[0]: row[1]
                    for row in db.execute(
                        "SELECT layer,COUNT(*) FROM notes WHERE scope=? GROUP BY layer", (scope,)
                    )
                }
            )
            state = db.execute("SELECT * FROM scopes WHERE scope=?", (scope,)).fetchone()
            pending = db.execute("SELECT COUNT(*) FROM turns WHERE scope=?", (scope,)).fetchone()[0]
            return {
                "counts": counts,
                "pending_rounds": pending,
                "last_summary_at": state["last_summary_at"] if state else 0,
                "last_error": state["last_error"] if state else "",
            }

        return await self._run(stats)

    async def append_turn(self, scope, turn_key, text):
        await self._run(
            lambda db: db.execute(
                "INSERT OR IGNORE INTO turns(scope,turn_key,text,created_at) VALUES(?,?,?,?)",
                (scope, turn_key, text, time.time()),
            )
        )

    async def due_scopes(self, rounds, hours, now=None):
        now = time.time() if now is None else now

        def fetch(db):
            rows = db.execute("""
                SELECT s.scope,s.last_summary_at,s.error_at,COUNT(t.id) AS pending,
                    MIN(t.created_at) AS oldest
                FROM scopes s JOIN turns t ON s.scope=t.scope GROUP BY s.scope
            """).fetchall()
            return [
                row["scope"]
                for row in rows
                if now - row["error_at"] >= 300
                and (
                    (rounds > 0 and row["pending"] >= rounds)
                    or (
                        hours > 0
                        and now - max(row["oldest"], row["last_summary_at"]) >= hours * 3600
                    )
                )
            ]

        return await self._run(fetch)

    async def pending_max_id(self, scope):
        return await self._run(
            lambda db: db.execute("SELECT MAX(id) FROM turns WHERE scope=?", (scope,)).fetchone()[0]
        )

    async def summary_batch(self, scope, max_id, max_chars):
        def batch(db):
            rows = db.execute(
                "SELECT id,text,offset FROM turns WHERE scope=? AND id<=? ORDER BY id LIMIT 200",
                (scope, max_id),
            ).fetchall()
            pieces, consumed, budget = [], [], max_chars
            for row in rows:
                remaining = row["text"][row["offset"] :]
                header = f"\n[对话记录 {row['id']}，起点 {row['offset']}]\n"
                available = budget - len(header)
                if available < 1:
                    break
                part = remaining[:available]
                pieces.append(header + part)
                consumed.append((row["id"], row["offset"] + len(part), len(part) == len(remaining)))
                budget -= len(header) + len(part)
                if budget < 100:
                    break
            return "".join(pieces), consumed

        return await self._run(batch)

    async def commit_summary(self, scope, notes, consumed):
        def commit(db):
            now, added = time.time(), []
            for content in notes:
                existing = db.execute(
                    "SELECT id FROM notes WHERE scope=? AND normalized=? LIMIT 1",
                    (scope, normalize(content)),
                ).fetchone()
                if existing is None:
                    added.append(
                        self._insert(db, scope, content, "", "short", "summary", "LLM", now)
                    )
            for turn_id, offset, done in consumed:
                if done:
                    db.execute("DELETE FROM turns WHERE scope=? AND id=?", (scope, turn_id))
                else:
                    db.execute(
                        "UPDATE turns SET offset=? WHERE scope=? AND id=?", (offset, scope, turn_id)
                    )
            db.execute(
                "UPDATE scopes SET last_summary_at=?,last_error='',error_at=0 WHERE scope=?",
                (now, scope),
            )
            return added

        return await self._run(commit)

    async def summary_error(self, scope, message):
        await self._run(
            lambda db: db.execute(
                "UPDATE scopes SET last_error=?,error_at=? WHERE scope=?",
                (message[:500], time.time(), scope),
            )
        )

    async def export_notes(self, scope):
        rows = await self._run(
            lambda db: [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM notes WHERE scope=? ORDER BY created_at,id", (scope,)
                )
            ]
        )
        return json.dumps(
            {"version": 1, "scope": scope, "notes": rows}, ensure_ascii=False, indent=2
        )
