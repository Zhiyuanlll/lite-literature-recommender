from __future__ import annotations
import hashlib
import html
import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".lite-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def normalized(value):
    return " ".join(re.findall(r"[^\W_]+", html.unescape(str(value)).casefold(), re.UNICODE))


def doi(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value)
    return value if re.match(r"^10\.\d{4,9}/\S+$", value) else ""


def arxiv(value):
    value = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", str(value or "").strip())
    value = re.sub(r"v\d+(?:\.pdf)?$", "", value)
    return value if re.match(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})$", value) else ""


class FileLock:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0)
        self.stream.write(b"0")
        self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("另一个推荐任务正在运行，请稍后查看结果。")
        return self

    def __exit__(self, *args):
        if self.stream:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS topics(id TEXT PRIMARY KEY, profile TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profile_versions(topic_id TEXT,version TEXT,profile TEXT NOT NULL,PRIMARY KEY(topic_id,version));
                CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases(kind TEXT,value TEXT,paper_id TEXT REFERENCES papers(id),PRIMARY KEY(kind,value));
                CREATE TABLE IF NOT EXISTS redirects(old_id TEXT PRIMARY KEY,new_id TEXT NOT NULL REFERENCES papers(id));
                CREATE TABLE IF NOT EXISTS feedback(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, topic_id TEXT NOT NULL,paper_id TEXT NOT NULL,version TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,source_key TEXT UNIQUE);
                CREATE INDEX IF NOT EXISTS feedback_lookup ON feedback(topic_id,version,paper_id,seq);
                CREATE TABLE IF NOT EXISTS policy(topic_id TEXT,version TEXT,data TEXT NOT NULL,PRIMARY KEY(topic_id,version));
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,status TEXT NOT NULL,mode TEXT NOT NULL,created_at TEXT NOT NULL,week TEXT NOT NULL,result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recommendations(run_id TEXT REFERENCES runs(id),topic_id TEXT,paper_id TEXT REFERENCES papers(id),rank INTEGER,data TEXT NOT NULL,PRIMARY KEY(run_id,topic_id,paper_id));
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def topics(self):
        with self.connect() as conn:
            return [json.loads(r["profile"]) for r in conn.execute("SELECT profile FROM topics ORDER BY id")]

    def save_topic(self, profile):
        with self.connect() as conn:
            conn.execute("INSERT INTO topics VALUES(?,?) ON CONFLICT(id) DO UPDATE SET profile=excluded.profile", (profile["topic_id"], dump(profile)))
            conn.execute("INSERT OR IGNORE INTO profile_versions VALUES(?,?,?)", (profile["topic_id"], str(profile["profile_version"]), dump(profile)))

    def profile_version(self, topic_id, version):
        with self.connect() as conn:
            row = conn.execute("SELECT profile FROM profile_versions WHERE topic_id=? AND version=?", (topic_id, str(version))).fetchone()
            return json.loads(row[0]) if row else None

    def upsert_paper(self, paper):
        paper = dict(paper)
        paper["doi"], paper["arxiv_id"] = doi(paper.get("doi")), arxiv(paper.get("arxiv_id"))
        prefix = "demo_" if paper.get("is_demo") else ""
        aliases = [(prefix + "doi", paper["doi"]), (prefix + "arxiv", paper["arxiv_id"])]
        for version in paper.get("versions", []):
            if isinstance(version, dict):
                aliases.extend([(prefix + "doi", doi(version.get("doi"))), (prefix + "arxiv", arxiv(version.get("arxiv_id")))])
        title = normalized(paper.get("title"))
        authors = paper.get("authors") or []
        author = normalized(authors[0]) if authors else "unknown_author|" + str(paper.get("url", ""))
        # Titles alone do not identify different works by different authors.
        aliases.append((prefix + "title_author", title + "|" + author))
        aliases = list(dict.fromkeys((kind, value) for kind, value in aliases if value))
        if not title:
            raise ValueError("论文题名不能为空")
        with self.connect() as conn:
            identities = []
            for kind, value in aliases:
                row = conn.execute("SELECT paper_id FROM aliases WHERE kind=? AND value=?", (kind, value)).fetchone()
                if row:
                    candidate = json.loads(conn.execute("SELECT data FROM papers WHERE id=?", (row[0],)).fetchone()[0])
                    if kind.endswith("title_author") and paper["doi"] and candidate.get("doi") and paper["doi"] != candidate["doi"]:
                        continue
                    identities.append(row[0])
            identities = list(dict.fromkeys(identities))
            pid = identities[0] if identities else "p_" + digest(aliases[0])[:24]
            row = conn.execute("SELECT data FROM papers WHERE id=?", (pid,)).fetchone()
            old = json.loads(row[0]) if row else {}
            for losing in identities[1:]:
                value = json.loads(conn.execute("SELECT data FROM papers WHERE id=?", (losing,)).fetchone()[0])
                for key, v in value.items():
                    if not old.get(key) and v:
                        old[key] = v
                if len(value.get("abstract", "")) > len(old.get("abstract", "")):
                    old["abstract"] = value["abstract"]
                old["sources"] = sorted(set(old.get("sources", []) + value.get("sources", [])))
                old["versions"] = old.get("versions", []) + value.get("versions", [])
                conn.execute("UPDATE aliases SET paper_id=? WHERE paper_id=?", (pid, losing))
                conn.execute("INSERT OR REPLACE INTO redirects VALUES(?,?)", (losing, pid))
                conn.execute("UPDATE redirects SET new_id=? WHERE new_id=?", (pid, losing))
                # Historical feedback events and delivered recommendation rows stay immutable.
                # Resolve redirects when reading them, rather than releasing budget or rewriting snapshots.
            merged = {**old, **{k: v for k, v in paper.items() if v not in (None, "", [])}}
            if len(old.get("abstract", "")) > len(paper.get("abstract", "")):
                merged["abstract"] = old["abstract"]
            merged["paper_id"] = pid
            merged["sources"] = sorted(set(old.get("sources", []) + paper.get("sources", []) + [paper.get("source", "unknown")]))
            merged["versions"] = list({dump(v): v for v in old.get("versions", []) + paper.get("versions", [])}.values())
            conn.execute("INSERT INTO papers VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (pid, dump(merged)))
            for kind, value in aliases:
                conn.execute("INSERT OR IGNORE INTO aliases VALUES(?,?,?)", (kind, value, pid))
            return merged

    def paper(self, pid):
        with self.connect() as conn:
            redirect = conn.execute("SELECT new_id FROM redirects WHERE old_id=?", (pid,)).fetchone()
            if redirect:
                pid = redirect[0]
            row = conn.execute("SELECT data FROM papers WHERE id=?", (pid,)).fetchone()
            return json.loads(row[0]) if row else None

    def latest_feedback(self, topic_id, version):
        with self.connect() as conn:
            rows = conn.execute("SELECT payload,paper_id FROM feedback WHERE topic_id=? AND version=? ORDER BY seq", (topic_id, str(version)))
            redirects = {r[0]: r[1] for r in conn.execute("SELECT old_id,new_id FROM redirects")}
            latest = {}
            for row in rows:
                pid = redirects.get(row["paper_id"], row["paper_id"])
                latest[pid] = {**json.loads(row["payload"]), "paper_id": pid}
            return latest

    def append_feedback(self, payload, created_at, source_key=None):
        with self.connect() as conn:
            if source_key and conn.execute("SELECT 1 FROM feedback WHERE source_key=?", (source_key,)).fetchone():
                return False
            ids = [payload["paper_id"]] + [r[0] for r in conn.execute("SELECT old_id FROM redirects WHERE new_id=?", (payload["paper_id"],))]
            placeholders = ",".join("?" for _ in ids)
            row = conn.execute("SELECT payload FROM feedback WHERE topic_id=? AND paper_id IN (" + placeholders + ") AND version=? ORDER BY seq DESC LIMIT 1", (payload["topic_id"], *ids, str(payload["profile_version"]))).fetchone()
            if row and {**json.loads(row[0]), "paper_id": payload["paper_id"]} == payload:
                return False
            event_id = digest([payload, created_at, os.urandom(8).hex()])
            conn.execute("INSERT INTO feedback(event_id,topic_id,paper_id,version,created_at,payload,source_key) VALUES(?,?,?,?,?,?,?)", (event_id, payload["topic_id"], payload["paper_id"], str(payload["profile_version"]), created_at, dump(payload), source_key))
            return True

    def get_policy(self, topic_id, version):
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM policy WHERE topic_id=? AND version=?", (topic_id, str(version))).fetchone()
            return json.loads(row[0]) if row else {"digest": None, "adjustments": {}}

    def save_policy(self, topic_id, version, policy):
        with self.connect() as conn:
            conn.execute("INSERT INTO policy VALUES(?,?,?) ON CONFLICT(topic_id,version) DO UPDATE SET data=excluded.data", (topic_id, str(version), dump(policy)))

    def run(self, run_id):
        with self.connect() as conn:
            row = conn.execute("SELECT result FROM runs WHERE id=?", (run_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_run(self, result, week, recommendations=None):
        with self.connect() as conn:
            conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,result=excluded.result", (result["run_id"], result["status"], result["mode"], result["created_at"], week, dump(result)))
            if recommendations is not None:
                conn.execute("DELETE FROM recommendations WHERE run_id=?", (result["run_id"],))
                for i, paper in enumerate(recommendations, 1):
                    conn.execute("INSERT INTO recommendations VALUES(?,?,?,?,?)", (result["run_id"], paper["topic_id"], paper["paper_id"], i, dump(paper)))

    def run_papers(self, run_id):
        with self.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT data FROM recommendations WHERE run_id=? ORDER BY rank", (run_id,))]

    def runs(self):
        with self.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT result FROM runs ORDER BY created_at DESC LIMIT 80")]

    def seen(self, topic_id, mode):
        with self.connect() as conn:
            redirects = {r[0]: r[1] for r in conn.execute("SELECT old_id,new_id FROM redirects")}
            return {redirects.get(r[0], r[0]) for r in conn.execute("SELECT DISTINCT paper_id FROM recommendations JOIN runs ON runs.id=run_id WHERE topic_id=? AND mode=? AND runs.status IN ('complete','partial')", (topic_id, mode))}

    def used_budget(self, week, topic_id=None):
        with self.connect() as conn:
            query = "SELECT COUNT(*) FROM recommendations JOIN runs ON runs.id=run_id WHERE runs.week=? AND runs.mode='real' AND runs.status IN ('complete','partial')"
            params = [week]
            if topic_id is not None:
                query += " AND recommendations.topic_id=?"
                params.append(topic_id)
            return conn.execute(query, params).fetchone()[0]
