from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "dataguard.db"
RULES_FILE = BASE_DIR / "rules.json"
PBKDF2_ITERATIONS = 310_000


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _default_rules_json() -> str:
    if RULES_FILE.exists():
        try:
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            return json.dumps(data, indent=4, ensure_ascii=False)
        except (OSError, json.JSONDecodeError):
            pass
    return json.dumps({"dataset": {}, "columns": {}}, indent=4)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def initialize_database() -> None:
    connection = get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_login TEXT
            )
        """)

        user_columns = [row[1] for row in cursor.execute("PRAGMA table_info(users)").fetchall()]
        if "last_login" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN last_login TEXT")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                default_primary_key TEXT DEFAULT 'customer_id',
                rules_json TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                user_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            )
        """)

        project_columns = [row[1] for row in cursor.execute("PRAGMA table_info(projects)").fetchall()]
        if "default_primary_key" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN default_primary_key TEXT DEFAULT 'customer_id'")
        if "rules_json" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN rules_json TEXT DEFAULT ''")
        if "updated_at" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN updated_at TEXT")
        if "user_id" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN user_id INTEGER")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                run_type TEXT NOT NULL,
                dataset_name TEXT,
                source_name TEXT,
                target_name TEXT,
                primary_key TEXT,
                score REAL,
                status TEXT NOT NULL,
                passed INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                report_path TEXT,
                created_at TEXT NOT NULL,
                result_id TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        run_columns = [row[1] for row in cursor.execute("PRAGMA table_info(runs)").fetchall()]
        if "result_id" not in run_columns:
            cursor.execute("ALTER TABLE runs ADD COLUMN result_id TEXT")

        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_result_id ON runs(result_id)")

        default_rules = _default_rules_json()
        cursor.execute("""
            UPDATE projects
            SET default_primary_key = COALESCE(NULLIF(default_primary_key, ''), 'customer_id'),
                rules_json = COALESCE(NULLIF(rules_json, ''), ?),
                updated_at = COALESCE(updated_at, created_at)
        """, (default_rules,))

        connection.commit()
    finally:
        connection.close()


def create_user(email: str, password: str) -> int:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Enter a valid email address.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email, hash_password(password), _now()),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def authenticate_user(email: str, password: str) -> sqlite3.Row | None:
    user = get_user_by_email(email)
    if user is None or not verify_password(password, user["password_hash"]):
        return None
    return user


def create_session(user_id: int) -> str:
    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    from datetime import timedelta, timezone
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=7)
    connection = get_connection()
    try:
        connection.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (user_id, token_hash, expires.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
        )
        connection.commit()
    finally:
        connection.close()
    return raw_token


def get_user_by_session(token: str) -> sqlite3.Row | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    from datetime import timezone
    connection = get_connection()
    try:
        row = connection.execute("""
            SELECT users.id, users.email, users.password_hash, users.created_at, users.last_login
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
        """, (token_hash, datetime.now(timezone.utc).isoformat(timespec="seconds"))).fetchone()
        return row
    finally:
        connection.close()


def delete_session(token: str) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = get_connection()
    try:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        connection.commit()
    finally:
        connection.close()


def get_user_count() -> int:
    connection = get_connection()
    try:
        return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    finally:
        connection.close()


def get_user_by_email(email: str) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT id, email, password_hash, created_at, last_login FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    finally:
        connection.close()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        return connection.execute(
            "SELECT id, email, password_hash, created_at, last_login FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        connection.close()


def set_last_login(user_id: int) -> None:
    connection = get_connection()
    try:
        connection.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), user_id))
        connection.commit()
    finally:
        connection.close()


def claim_orphan_projects(user_id: int) -> int:
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("UPDATE projects SET user_id = ? WHERE user_id IS NULL", (user_id,))
        connection.commit()
        return int(cursor.rowcount)
    finally:
        connection.close()


def create_project(
    name: str,
    description: str = "",
    default_primary_key: str = "customer_id",
    rules_json: str | None = None,
    user_id: int | None = None,
) -> int:
    connection = get_connection()
    try:
        cursor = connection.cursor()
        now = _now()
        rules = rules_json if rules_json is not None else _default_rules_json()
        cursor.execute("""
            INSERT INTO projects (
                name, description, default_primary_key, rules_json,
                created_at, updated_at, user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            name.strip(),
            description.strip(),
            default_primary_key.strip() or "customer_id",
            rules,
            now,
            now,
            user_id,
        ))
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def get_projects(user_id: int | None = None) -> list[sqlite3.Row]:
    connection = get_connection()
    try:
        base = """
            SELECT id, name, description, default_primary_key, rules_json,
                   created_at, updated_at, user_id
            FROM projects
        """
        if user_id is None:
            return connection.execute(base + " ORDER BY name").fetchall()
        return connection.execute(base + " WHERE user_id = ? ORDER BY name", (user_id,)).fetchall()
    finally:
        connection.close()


def get_project(project_id: int, user_id: int | None = None) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        base = """
            SELECT id, name, description, default_primary_key, rules_json,
                   created_at, updated_at, user_id
            FROM projects WHERE id = ?
        """
        if user_id is None:
            return connection.execute(base, (project_id,)).fetchone()
        return connection.execute(base + " AND user_id = ?", (project_id, user_id)).fetchone()
    finally:
        connection.close()


def update_project(
    project_id: int,
    name: str,
    description: str,
    default_primary_key: str,
    rules_json: str,
    user_id: int | None = None,
) -> None:
    connection = get_connection()
    try:
        cursor = connection.cursor()
        params = (
            name.strip(),
            description.strip(),
            default_primary_key.strip() or "customer_id",
            rules_json,
            _now(),
            project_id,
        )
        if user_id is None:
            cursor.execute("""
                UPDATE projects
                SET name=?, description=?, default_primary_key=?, rules_json=?, updated_at=?
                WHERE id=?
            """, params)
        else:
            cursor.execute("""
                UPDATE projects
                SET name=?, description=?, default_primary_key=?, rules_json=?, updated_at=?
                WHERE id=? AND user_id=?
            """, params[:-1] + (project_id, user_id))
        if cursor.rowcount == 0:
            raise ValueError("Project was not found or is not owned by the current user.")
        connection.commit()
    finally:
        connection.close()


def save_run(
    project_id: int | None,
    run_type: str,
    dataset_name: str | None = None,
    source_name: str | None = None,
    target_name: str | None = None,
    primary_key: str | None = None,
    score: float | None = None,
    status: str = "FAIL",
    passed: int = 0,
    failed: int = 0,
    report_path: str | None = None,
    result_id: str | None = None,
) -> int:
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO runs (
                project_id, run_type, dataset_name, source_name, target_name,
                primary_key, score, status, passed, failed, report_path,
                created_at, result_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            project_id, run_type, dataset_name, source_name, target_name,
            primary_key, score, status, passed, failed, report_path, _now(), result_id,
        ))
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def get_runs(limit: int = 100, user_id: int | None = None) -> list[sqlite3.Row]:
    connection = get_connection()
    try:
        query = """
            SELECT runs.id, runs.project_id, projects.name AS project_name,
                   runs.run_type, runs.dataset_name, runs.source_name,
                   runs.target_name, runs.primary_key, runs.score, runs.status,
                   runs.passed, runs.failed, runs.report_path, runs.created_at,
                   runs.result_id
            FROM runs
            LEFT JOIN projects ON projects.id = runs.project_id
        """
        params: tuple = ()
        if user_id is not None:
            query += " WHERE projects.user_id = ?"
            params = (user_id,)
        query += " ORDER BY runs.id DESC LIMIT ?"
        params += (limit,)
        return connection.execute(query, params).fetchall()
    finally:
        connection.close()


def get_run(run_id: int, user_id: int | None = None) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        query = """
            SELECT runs.*, projects.name AS project_name
            FROM runs LEFT JOIN projects ON projects.id = runs.project_id
            WHERE runs.id = ?
        """
        params: tuple = (run_id,)
        if user_id is not None:
            query += " AND projects.user_id = ?"
            params += (user_id,)
        return connection.execute(query, params).fetchone()
    finally:
        connection.close()


def get_run_by_result_id(result_id: str, user_id: int | None = None) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        query = """
            SELECT runs.*, projects.name AS project_name
            FROM runs LEFT JOIN projects ON projects.id = runs.project_id
            WHERE runs.result_id = ?
        """
        params: tuple = (result_id,)
        if user_id is not None:
            query += " AND projects.user_id = ?"
            params += (user_id,)
        return connection.execute(query, params).fetchone()
    finally:
        connection.close()


def get_run_by_report_path(report_path: str, user_id: int | None = None) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        query = """
            SELECT runs.*, projects.name AS project_name
            FROM runs LEFT JOIN projects ON projects.id = runs.project_id
            WHERE runs.report_path = ?
        """
        params: tuple = (report_path,)
        if user_id is not None:
            query += " AND projects.user_id = ?"
            params += (user_id,)
        return connection.execute(query, params).fetchone()
    finally:
        connection.close()


def get_dashboard_stats(user_id: int | None = None) -> dict[str, int]:
    connection = get_connection()
    try:
        join = " LEFT JOIN projects ON projects.id = runs.project_id"
        where = ""
        params: tuple = ()
        if user_id is not None:
            where = " WHERE projects.user_id = ?"
            params = (user_id,)

        total_runs = connection.execute(
            f"SELECT COUNT(*) FROM runs{join}{where}", params
        ).fetchone()[0]
        quality_runs = connection.execute(
            f"SELECT COUNT(*) FROM runs{join}{where}{' AND' if where else ' WHERE'} runs.run_type = 'Data Quality'",
            params,
        ).fetchone()[0]
        etl_runs = connection.execute(
            f"SELECT COUNT(*) FROM runs{join}{where}{' AND' if where else ' WHERE'} runs.run_type = 'ETL Validation'",
            params,
        ).fetchone()[0]
        failed_runs = connection.execute(
            f"SELECT COUNT(*) FROM runs{join}{where}{' AND' if where else ' WHERE'} runs.status = 'FAIL'",
            params,
        ).fetchone()[0]
        if user_id is None:
            projects = connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        else:
            projects = connection.execute("SELECT COUNT(*) FROM projects WHERE user_id = ?", (user_id,)).fetchone()[0]

        return {
            "total_runs": int(total_runs),
            "quality_runs": int(quality_runs),
            "etl_runs": int(etl_runs),
            "failed_runs": int(failed_runs),
            "projects": int(projects),
        }
    finally:
        connection.close()
