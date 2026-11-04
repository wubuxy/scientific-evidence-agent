"""Small persistent research repository. No dependency on production databases.

SQLite connections are short-lived. Every public entry point checks workspace
ownership before accessing children; filenames are generated from server UUIDs.
"""
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class NotFound(Exception):
    pass


class Unavailable(Exception):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.db_path = self.root / "research.sqlite3"

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS workspaces_owner ON workspaces(owner);
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    kind TEXT NOT NULL, parent_id TEXT, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS records_scope ON records(workspace_id, kind, parent_id);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id, seq);
            """)
            rows = db.execute("SELECT id, payload FROM records WHERE kind IN ('run', 'analysis', 'translation', 'discovery_job')").fetchall()
            for row in rows:
                run = json.loads(row["payload"])
                if run["status"] in {"queued", "running"}:
                    run.update(status="interrupted", error="服务已重启；请重新发起任务", updated_at=now())
                    db.execute("UPDATE records SET payload=? WHERE id=?", (json.dumps(run), row["id"]))

    def create_workspace(self, owner, data):
        item = dict(data, id=str(uuid.uuid4()), created_at=now())
        with self.connection() as db:
            db.execute("INSERT INTO workspaces VALUES (?, ?, ?)",
                       (item["id"], owner, json.dumps(item, ensure_ascii=False)))
        return item

    def workspace(self, owner, workspace_id):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM workspaces WHERE id=? AND owner=?",
                             (workspace_id, owner)).fetchone()
        if row is None:
            raise NotFound("工作区不存在")
        return json.loads(row["payload"])

    def workspaces(self, owner):
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM workspaces WHERE owner=? ORDER BY rowid DESC LIMIT 100",
                              (owner,)).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def put(self, owner, workspace_id, kind, data, parent_id=None, record_id=None):
        self.workspace(owner, workspace_id)
        item = dict(data, id=record_id or str(uuid.uuid4()), workspace_id=workspace_id)
        item.setdefault("created_at", now())
        with self.connection() as db:
            db.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?)",
                       (item["id"], workspace_id, kind, parent_id, json.dumps(item, ensure_ascii=False)))
        return item

    def get(self, owner, workspace_id, kind, record_id):
        self.workspace(owner, workspace_id)
        with self.connection() as db:
            row = db.execute("SELECT payload FROM records WHERE id=? AND workspace_id=? AND kind=?",
                             (record_id, workspace_id, kind)).fetchone()
        if row is None:
            raise NotFound("记录不存在")
        return json.loads(row["payload"])

    def list(self, owner, workspace_id, kind, parent_id=None, limit=100, offset=0, descending=False):
        self.workspace(owner, workspace_id)
        sql = "SELECT payload FROM records WHERE workspace_id=? AND kind=?"
        params = [workspace_id, kind]
        if parent_id is not None:
            sql += " AND parent_id=?"
            params.append(parent_id)
        with self.connection() as db:
            order = " DESC" if descending else " ASC"
            rows = db.execute(sql + " ORDER BY rowid" + order + " LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def update(self, owner, workspace_id, kind, record_id, **changes):
        # Read-modify-write under the same transaction, also used by cancellation.
        self.workspace(owner, workspace_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM records WHERE id=? AND workspace_id=? AND kind=?",
                             (record_id, workspace_id, kind)).fetchone()
            if not row:
                raise NotFound("记录不存在")
            item = json.loads(row["payload"])
            item.update(changes, updated_at=now())
            db.execute("UPDATE records SET payload=? WHERE id=?", (json.dumps(item, ensure_ascii=False), record_id))
        return item

    def add_source(self, owner, workspace_id, source, original=None, extension=""):
        self.workspace(owner, workspace_id)
        item = source.model_dump()
        lines, locations = [], []
        for block_index, block in enumerate(item["blocks"]):
            for line in block["text"].splitlines() or [block["text"]]:
                lines.append(line)
                locations.append(dict(block_index=block_index, **{k: block[k] for k in
                    ("page", "start_seconds", "end_seconds", "block_type")}))
        text = "\n".join(lines)
        digest = hashlib.sha256(json.dumps(item["blocks"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        item.update(source_hash=digest, line_count=len(lines), locations=locations, rag_indexed=False,
                    original_extension=extension if original is not None else None)
        source_id = str(uuid.uuid4())
        directory = self.root / workspace_id / source_id
        directory.mkdir(parents=True)
        (directory / "content.txt").write_text(text, encoding="utf-8")
        if original is not None:
            (directory / ("original" + extension)).write_bytes(original)
        # Publish metadata only after the files have been written successfully.
        return self.put(owner, workspace_id, "source", item, record_id=source_id)

    def source_text(self, owner, workspace_id, source_id):
        self.get(owner, workspace_id, "source", source_id)
        return (self.root / workspace_id / source_id / "content.txt").read_text(encoding="utf-8")

    def evidence(self, owner, workspace_id, source_id, start_line, end_line):
        source = self.get(owner, workspace_id, "source", source_id)
        lines = self.source_text(owner, workspace_id, source_id).splitlines()
        if start_line < 1 or end_line < start_line or start_line > len(lines):
            raise ValueError("行号超出资料范围")
        end_line = min(end_line, len(lines))
        quote = "\n".join(lines[start_line - 1:end_line])
        if len(quote) > 16000:
            raise ValueError("阅读结果超过 16000 字符，请缩小行范围")
        evidence_id = "ev_" + hashlib.sha256(
            f"{source_id}:{source['source_hash']}:{start_line}:{end_line}".encode()).hexdigest()[:32]
        try:
            return self.get(owner, workspace_id, "evidence", evidence_id)
        except NotFound:
            item = dict(source_id=source_id, source_hash=source["source_hash"], title=source["title"],
                        uri=source["uri"], quote=quote, start_line=start_line, end_line=end_line,
                        locations=source["locations"][start_line - 1:end_line])
            try:
                return self.put(owner, workspace_id, "evidence", item, record_id=evidence_id)
            except sqlite3.IntegrityError:  # another reader may have saved the same span
                return self.get(owner, workspace_id, "evidence", evidence_id)

    def event(self, owner, workspace_id, run_id, event_type, data):
        self.get(owner, workspace_id, "run", run_id)
        payload = dict(type=event_type, data=data, created_at=now())
        with self.connection() as db:
            cursor = db.execute("INSERT INTO events(workspace_id, run_id, payload) VALUES (?, ?, ?)",
                                (workspace_id, run_id, json.dumps(payload, ensure_ascii=False)))
            payload["id"] = cursor.lastrowid
        return payload

    def events(self, owner, workspace_id, run_id, after=0):
        self.get(owner, workspace_id, "run", run_id)
        with self.connection() as db:
            rows = db.execute("SELECT seq, payload FROM events WHERE workspace_id=? AND run_id=? AND seq>? ORDER BY seq LIMIT 100",
                              (workspace_id, run_id, after)).fetchall()
        return [dict(json.loads(r["payload"]), id=r["seq"]) for r in rows]
