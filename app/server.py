#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""播客选题库 —— 单文件后端（仅 Python 标准库）

数据模型:
  topics(id, title, notes, created_at, updated_at)
  items(id, topic_id, category[like|dislike|thought], content, created_at, updated_at)
  settings(key, value)   # password_hash / session_secret / sort_field / sort_order
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
DB_FILE = os.path.join(DATA_DIR, "topics.db")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8390"))

APP_NAME = "podcast-topic-library"
SESSION_TTL = 30 * 86400
SESSION_COOKIE = "pl_session"
PBKDF2_ITERS = 200_000
MAX_BODY = 5 * 1024 * 1024
CATEGORIES = ("like", "dislike", "thought")
SORT_FIELDS = ("updated_at", "created_at", "title")
SORT_ORDERS = ("asc", "desc")
MAX_LOGIN_FAILS = 10
LOCK_SECONDS = 300

_guard = {"fails": 0, "locked_until": 0.0}
_guard_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_topic ON items(topic_id);
"""


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


def db():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        defaults = {
            "session_secret": secrets.token_hex(32),
            "sort_field": "updated_at",
            "sort_order": "desc",
        }
        for k, v in defaults.items():
            if get_setting(conn, k) is None:
                set_setting(conn, k, v)


def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ITERS)
    return json.dumps({"s": salt.hex(), "h": dk.hex(), "i": PBKDF2_ITERS})


def verify_password(pw, stored):
    try:
        d = json.loads(stored)
        dk = hashlib.pbkdf2_hmac(
            "sha256", pw.encode("utf-8"), bytes.fromhex(d["s"]), int(d["i"])
        )
        return hmac.compare_digest(dk.hex(), d["h"])
    except Exception:
        return False


def make_token(secret):
    exp = str(int(time.time()) + SESSION_TTL)
    sig = hmac.new(secret.encode(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def check_token(secret, token):
    if not token or "." not in token:
        return False
    exp, _, sig = token.partition(".")
    if not re.fullmatch(r"\d+", exp) or not re.fullmatch(r"[0-9a-f]{64}", sig):
        return False
    want = hmac.new(secret.encode(), exp.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig):
        return False
    return int(exp) > time.time()


def now():
    return int(time.time())


def topic_dict(row, items_by_topic):
    return {
        "id": row["id"],
        "title": row["title"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "items": items_by_topic.get(row["id"], []),
    }


def item_dict(row):
    return {
        "id": row["id"],
        "topic_id": row["topic_id"],
        "category": row["category"],
        "content": row["content"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def read_sort(conn):
    f = get_setting(conn, "sort_field", "updated_at")
    o = get_setting(conn, "sort_order", "desc")
    if f not in SORT_FIELDS:
        f = "updated_at"
    if o not in SORT_ORDERS:
        o = "desc"
    return f, o


# ---------------- API 处理函数 ----------------

def api_state(ctx):
    with db() as conn:
        initialized = get_setting(conn, "password_hash") is not None
        result = {"initialized": initialized, "authed": ctx["authed"]}
        if ctx["authed"]:
            f, o = read_sort(conn)
            result["settings"] = {"sort_field": f, "sort_order": o}
        return result


def api_setup(ctx):
    body = ctx["body"]
    pw = body.get("password", "")
    if not isinstance(pw, str) or not (6 <= len(pw) <= 128):
        raise ApiError(400, "密码长度需在 6~128 个字符之间")
    with db() as conn:
        if get_setting(conn, "password_hash") is not None:
            raise ApiError(409, "初始密码已设置，请直接登录")
        set_setting(conn, "password_hash", hash_password(pw))
    ctx["set_cookie"] = True
    return {"ok": True}


def api_login(ctx):
    with _guard_lock:
        if time.time() < _guard["locked_until"]:
            wait = int(_guard["locked_until"] - time.time())
            raise ApiError(429, f"尝试次数过多，请 {wait} 秒后重试")
    body = ctx["body"]
    pw = body.get("password", "")
    with db() as conn:
        stored = get_setting(conn, "password_hash")
        secret = get_setting(conn, "session_secret")
    ok = isinstance(pw, str) and stored is not None and verify_password(pw, stored)
    if ok:
        with _guard_lock:
            _guard["fails"] = 0
        ctx["set_cookie"] = True
        return {"ok": True}
    with _guard_lock:
        _guard["fails"] += 1
        if _guard["fails"] >= MAX_LOGIN_FAILS:
            _guard["locked_until"] = time.time() + LOCK_SECONDS
            _guard["fails"] = 0
    time.sleep(0.4)
    raise ApiError(401, "密码错误")


def api_logout(ctx):
    ctx["set_cookie"] = False
    return {"ok": True}


def api_topics_list(ctx):
    with db() as conn:
        f, o = read_sort(conn)
        order = "ASC" if o == "asc" else "DESC"
        rows = conn.execute(
            f"SELECT * FROM topics ORDER BY {f} {order}, id DESC"
        ).fetchall()
        irows = conn.execute(
            "SELECT * FROM items ORDER BY created_at ASC, id ASC"
        ).fetchall()
        by_topic = {}
        for r in irows:
            by_topic.setdefault(r["topic_id"], []).append(item_dict(r))
        return {"topics": [topic_dict(r, by_topic) for r in rows]}


def _clean_text(v, field, maxlen=2000, required=True):
    if not isinstance(v, str):
        raise ApiError(400, f"{field} 必须是字符串")
    v = v.strip()
    if required and not v:
        raise ApiError(400, f"{field} 不能为空")
    if len(v) > maxlen:
        raise ApiError(400, f"{field} 过长（最多 {maxlen} 字）")
    return v


def api_topic_create(ctx):
    title = _clean_text(ctx["body"].get("title"), "标题", maxlen=200)
    notes = _clean_text(ctx["body"].get("notes", ""), "备注", maxlen=20000, required=False)
    t = now()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO topics(title, notes, created_at, updated_at) VALUES(?,?,?,?)",
            (title, notes, t, t),
        )
        return {"topic": {"id": cur.lastrowid, "title": title, "notes": notes,
                          "created_at": t, "updated_at": t, "items": []}}


def api_topic_update(ctx, tid):
    body = ctx["body"]
    with db() as conn:
        row = conn.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
        if not row:
            raise ApiError(404, "选题不存在")
        title = row["title"] if "title" not in body else _clean_text(body.get("title"), "标题", maxlen=200)
        notes = row["notes"] if "notes" not in body else _clean_text(body.get("notes"), "备注", maxlen=20000, required=False)
        conn.execute(
            "UPDATE topics SET title=?, notes=?, updated_at=? WHERE id=?",
            (title, notes, now(), tid),
        )
        return {"ok": True}


def api_topic_delete(ctx, tid):
    with db() as conn:
        cur = conn.execute("DELETE FROM topics WHERE id=?", (tid,))
        if cur.rowcount == 0:
            raise ApiError(404, "选题不存在")
        return {"ok": True}


def _bump_topic(conn, tid):
    conn.execute("UPDATE topics SET updated_at=? WHERE id=?", (now(), tid))


def api_item_create(ctx, tid):
    body = ctx["body"]
    category = body.get("category")
    if category not in CATEGORIES:
        raise ApiError(400, "category 必须是 like / dislike / thought")
    content = _clean_text(body.get("content"), "内容")
    t = now()
    with db() as conn:
        row = conn.execute("SELECT id FROM topics WHERE id=?", (tid,)).fetchone()
        if not row:
            raise ApiError(404, "选题不存在")
        cur = conn.execute(
            "INSERT INTO items(topic_id, category, content, created_at, updated_at) "
            "VALUES(?,?,?,?,?)",
            (tid, category, content, t, t),
        )
        _bump_topic(conn, tid)
        return {"item": {"id": cur.lastrowid, "topic_id": tid, "category": category,
                         "content": content, "created_at": t, "updated_at": t}}


def _get_item(conn, iid):
    row = conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
    if not row:
        raise ApiError(404, "内容不存在")
    return row


def api_item_update(ctx, iid):
    content = _clean_text(ctx["body"].get("content"), "内容")
    with db() as conn:
        row = _get_item(conn, iid)
        conn.execute(
            "UPDATE items SET content=?, updated_at=? WHERE id=?",
            (content, now(), iid),
        )
        _bump_topic(conn, row["topic_id"])
        return {"ok": True}


def api_item_delete(ctx, iid):
    with db() as conn:
        row = _get_item(conn, iid)
        conn.execute("DELETE FROM items WHERE id=?", (iid,))
        _bump_topic(conn, row["topic_id"])
        return {"ok": True}


def api_settings_get(ctx):
    with db() as conn:
        f, o = read_sort(conn)
        return {"settings": {"sort_field": f, "sort_order": o}}


def api_settings_put(ctx):
    body = ctx["body"]
    with db() as conn:
        if "sort_field" in body and body["sort_field"] not in SORT_FIELDS:
            raise ApiError(400, "无效的排序字段")
        if "sort_order" in body and body["sort_order"] not in SORT_ORDERS:
            raise ApiError(400, "无效的排序方向")
        if "sort_field" in body:
            set_setting(conn, "sort_field", body["sort_field"])
        if "sort_order" in body:
            set_setting(conn, "sort_order", body["sort_order"])
        f, o = read_sort(conn)
        return {"settings": {"sort_field": f, "sort_order": o}}


def api_password_change(ctx):
    body = ctx["body"]
    old = body.get("old_password", "")
    new = body.get("new_password", "")
    if not isinstance(new, str) or not (6 <= len(new) <= 128):
        raise ApiError(400, "新密码长度需在 6~128 个字符之间")
    with db() as conn:
        stored = get_setting(conn, "password_hash")
        if not isinstance(old, str) or not verify_password(old, stored):
            raise ApiError(401, "当前密码不正确")
        set_setting(conn, "password_hash", hash_password(new))
    ctx["set_cookie"] = True  # 换发新会话
    return {"ok": True}


def api_export(ctx):
    with db() as conn:
        topics = conn.execute("SELECT * FROM topics ORDER BY id ASC").fetchall()
        items = conn.execute("SELECT * FROM items ORDER BY id ASC").fetchall()
        by_topic = {}
        for r in items:
            by_topic.setdefault(r["topic_id"], []).append({
                "category": r["category"],
                "content": r["content"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            })
        data = {
            "app": APP_NAME,
            "version": 1,
            "exported_at": now(),
            "topics": [
                {
                    "title": t["title"],
                    "notes": t["notes"],
                    "created_at": t["created_at"],
                    "updated_at": t["updated_at"],
                    "items": by_topic.get(t["id"], []),
                }
                for t in topics
            ],
        }
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    ctx["download_name"] = f"podcast-topics-{stamp}.json"
    return data


def api_import(ctx):
    body = ctx["body"]
    data = body.get("data")
    mode = body.get("mode", "append")
    if mode not in ("append", "replace"):
        raise ApiError(400, "mode 必须是 append 或 replace")
    if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
        raise ApiError(400, "导入文件格式不正确（需要包含 topics 数组）")
    parsed = []
    for t in data["topics"]:
        if not isinstance(t, dict):
            continue
        title = _clean_text(t.get("title"), "标题", maxlen=200)
        notes = _clean_text(t.get("notes") or "", "备注", maxlen=20000, required=False)
        items = []
        for it in t.get("items", []) or []:
            if not isinstance(it, dict):
                continue
            if it.get("category") not in CATEGORIES:
                continue
            try:
                content = _clean_text(it.get("content"), "内容")
            except ApiError:
                continue
            items.append((it["category"], content))
        parsed.append((title, notes, _safe_ts(t.get("created_at")), _safe_ts(t.get("updated_at")), items))
    if not parsed:
        raise ApiError(400, "没有可导入的有效记录")
    n_topics = n_items = 0
    with db() as conn:
        if mode == "replace":
            conn.execute("DELETE FROM items")
            conn.execute("DELETE FROM topics")
        for title, notes, c_at, u_at, items in parsed:
            cur = conn.execute(
                "INSERT INTO topics(title, notes, created_at, updated_at) VALUES(?,?,?,?)",
                (title, notes, c_at, u_at),
            )
            n_topics += 1
            for cat, content in items:
                conn.execute(
                    "INSERT INTO items(topic_id, category, content, created_at, updated_at)"
                    " VALUES(?,?,?,?,?)",
                    (cur.lastrowid, cat, content, c_at, u_at),
                )
                n_items += 1
    return {"ok": True, "topics": n_topics, "items": n_items}


def _safe_ts(v):
    try:
        ts = int(v)
        return ts if 1000000000 < ts < 4102444800 else now()
    except (TypeError, ValueError):
        return now()


# ---------------- 路由表 ----------------

ROUTES = [
    ("GET", r"^/api/health$", lambda c: {"ok": True, "app": APP_NAME}, False),
    ("GET", r"^/api/state$", api_state, False),
    ("POST", r"^/api/setup$", api_setup, False),
    ("POST", r"^/api/login$", api_login, False),
    ("POST", r"^/api/logout$", api_logout, True),
    ("GET", r"^/api/topics$", api_topics_list, True),
    ("POST", r"^/api/topics$", api_topic_create, True),
    ("PUT", r"^/api/topics/(\d+)$", api_topic_update, True),
    ("DELETE", r"^/api/topics/(\d+)$", api_topic_delete, True),
    ("POST", r"^/api/topics/(\d+)/items$", api_item_create, True),
    ("PUT", r"^/api/items/(\d+)$", api_item_update, True),
    ("DELETE", r"^/api/items/(\d+)$", api_item_delete, True),
    ("GET", r"^/api/settings$", api_settings_get, True),
    ("PUT", r"^/api/settings$", api_settings_put, True),
    ("POST", r"^/api/password$", api_password_change, True),
    ("GET", r"^/api/export$", api_export, True),
    ("POST", r"^/api/import$", api_import, True),
]

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PTL/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---- 基础收发 ----

    def send_json(self, obj, status=200, set_cookie=None, download_name=None):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download_name:
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{download_name}",
            )
        if set_cookie is True:
            with db() as conn:
                secret = get_setting(conn, "session_secret")
            token = make_token(secret)
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL}; "
                f"HttpOnly; SameSite=Strict",
            )
        elif set_cookie is False:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
            )
        self.end_headers()
        self.wfile.write(payload)

    def read_body(self):
        length = self.headers.get("Content-Length")
        try:
            length = int(length or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ApiError(413, "请求体过大")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ApiError(400, "请求体不是合法的 JSON")

    def get_auth(self):
        cookie = self.headers.get("Cookie") or ""
        token = None
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == SESSION_COOKIE:
                token = v
                break
        if not token:
            return False
        with db() as conn:
            secret = get_setting(conn, "session_secret")
            initialized = get_setting(conn, "password_hash") is not None
        return initialized and check_token(secret, token)

    # ---- 请求分发 ----

    def dispatch(self, method):
        path = urlparse(self.path).path
        try:
            if method in ("GET", "HEAD") and not path.startswith("/api/"):
                return self.serve_static(path)
            for m, pattern, fn, needs_auth in ROUTES:
                if m != method:
                    continue
                match = re.match(pattern, path)
                if not match:
                    continue
                ctx = {"authed": self.get_auth(), "body": {}, "set_cookie": None}
                if needs_auth and not ctx["authed"]:
                    raise ApiError(401, "请先登录")
                if method in ("POST", "PUT"):
                    ctx["body"] = self.read_body()
                result = fn(ctx, *match.groups()) if match.groups() else fn(ctx)
                kwargs = {}
                if ctx.get("set_cookie") is not None:
                    kwargs["set_cookie"] = ctx["set_cookie"]
                if isinstance(result, dict) and "download_name" in ctx:
                    kwargs["download_name"] = ctx.pop("download_name")
                return self.send_json(result, **kwargs)
            raise ApiError(404, "接口不存在")
        except ApiError as e:
            return self.send_json({"error": e.msg}, status=e.status)
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            return self.send_json({"error": "服务器内部错误"}, status=500)

    def serve_static(self, path):
        if path in ("/", "/index.html"):
            fp = os.path.join(STATIC_DIR, "index.html")
        else:
            name = os.path.basename(path)
            fp = os.path.join(STATIC_DIR, name) if path.startswith("/static/") and name else None
        if not fp or not os.path.isfile(fp):
            return self.send_json({"error": "页面不存在"}, status=404)
        ext = os.path.splitext(fp)[1].lower()
        with open(fp, "rb") as f:
            payload = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"[{APP_NAME}] listening on http://{HOST}:{PORT}  data={DATA_DIR}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
