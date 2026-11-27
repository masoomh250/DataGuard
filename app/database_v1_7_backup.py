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
            CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                owner_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workspace_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'MEMBER',
                created_at TEXT NOT NULL,
                UNIQUE(workspace_id, user_id),
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_workspace_members_workspace ON workspace_members(workspace_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workspace_invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'MEMBER',
                token_hash TEXT NOT NULL UNIQUE,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                accepted_at TEXT,
                email_status TEXT NOT NULL DEFAULT 'PENDING',
                email_sent_at TEXT,
                email_error TEXT,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        invite_columns = [row[1] for row in cursor.execute("PRAGMA table_info(workspace_invitations)").fetchall()]
        if "email_status" not in invite_columns:
            cursor.execute("ALTER TABLE workspace_invitations ADD COLUMN email_status TEXT NOT NULL DEFAULT 'PENDING'")
        if "email_sent_at" not in invite_columns:
            cursor.execute("ALTER TABLE workspace_invitations ADD COLUMN email_sent_at TEXT")
        if "email_error" not in invite_columns:
            cursor.execute("ALTER TABLE workspace_invitations ADD COLUMN email_error TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_workspace_invites_workspace ON workspace_invitations(workspace_id)")

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
                workspace_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL
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
        if "workspace_id" not in project_columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN workspace_id INTEGER")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_projects_workspace_id ON projects(workspace_id)")

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

        # v1.7 migration: give every existing user a personal workspace and
        # attach existing projects to that workspace. Orphan projects go to
        # the first available workspace.
        users = cursor.execute("SELECT id, email FROM users ORDER BY id").fetchall()
        for user in users:
            existing_ws = cursor.execute(
                """SELECT w.id FROM workspaces w
                   JOIN workspace_members wm ON wm.workspace_id = w.id
                  WHERE wm.user_id = ?
                  ORDER BY w.id LIMIT 1""",
                (int(user["id"]),),
            ).fetchone()
            if existing_ws is None:
                name_base = (str(user["email"]).split("@", 1)[0] or "User").strip()
                workspace_name = f"{name_base}'s Workspace"
                cursor.execute(
                    "INSERT INTO workspaces (name, owner_user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (workspace_name, int(user["id"]), _now(), _now()),
                )
                ws_id = int(cursor.lastrowid)
                cursor.execute(
                    "INSERT INTO workspace_members (workspace_id, user_id, role, created_at) VALUES (?, ?, 'OWNER', ?)",
                    (ws_id, int(user["id"]), _now()),
                )

        cursor.execute("""
            UPDATE projects
               SET workspace_id = (
                   SELECT wm.workspace_id FROM workspace_members wm
                    WHERE wm.user_id = projects.user_id
                    ORDER BY wm.workspace_id LIMIT 1
               )
             WHERE workspace_id IS NULL AND user_id IS NOT NULL
        """)
        first_workspace = cursor.execute("SELECT id FROM workspaces ORDER BY id LIMIT 1").fetchone()
        if first_workspace is not None:
            cursor.execute(
                "UPDATE projects SET workspace_id = ? WHERE workspace_id IS NULL",
                (int(first_workspace[0]),),
            )

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
        workspace = cursor.execute(
            "SELECT id FROM workspaces WHERE owner_user_id = ? ORDER BY id LIMIT 1",
            (user_id,),
        ).fetchone()
        if workspace is None:
            now = _now()
            email_row = cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
            name_base = (str(email_row[0]).split("@", 1)[0] if email_row else "User").strip() or "User"
            cursor.execute(
                "INSERT INTO workspaces (name, owner_user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (f"{name_base}'s Workspace", user_id, now, now),
            )
            workspace_id = int(cursor.lastrowid)
            cursor.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role, created_at) VALUES (?, ?, 'OWNER', ?)",
                (workspace_id, user_id, now),
            )
        else:
            workspace_id = int(workspace[0])
        cursor.execute(
            "UPDATE projects SET user_id = ?, workspace_id = COALESCE(workspace_id, ?) WHERE user_id IS NULL",
            (user_id, workspace_id),
        )
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
    workspace_id: int | None = None,
) -> int:
    connection = get_connection()
    try:
        cursor = connection.cursor()
        now = _now()
        rules = rules_json if rules_json is not None else _default_rules_json()
        cursor.execute("""
            INSERT INTO projects (
                name, description, default_primary_key, rules_json,
                created_at, updated_at, user_id, workspace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name.strip(), description.strip(), default_primary_key.strip() or "customer_id",
            rules, now, now, user_id, workspace_id,
        ))
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def get_workspaces(user_id: int) -> list[sqlite3.Row]:
    connection = get_connection()
    try:
        return connection.execute("""
            SELECT w.id, w.name, w.owner_user_id, w.created_at, w.updated_at,
                   wm.role AS member_role
            FROM workspaces w
            JOIN workspace_members wm ON wm.workspace_id = w.id
            WHERE wm.user_id = ?
            ORDER BY w.name
        """, (user_id,)).fetchall()
    finally:
        connection.close()


def get_workspace(workspace_id: int, user_id: int | None = None) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        query = """SELECT w.id, w.name, w.owner_user_id, w.created_at, w.updated_at, wm.role AS member_role
                   FROM workspaces w JOIN workspace_members wm ON wm.workspace_id = w.id
                  WHERE w.id = ?"""
        params: tuple = (workspace_id,)
        if user_id is not None:
            query += " AND wm.user_id = ?"
            params += (user_id,)
        return connection.execute(query, params).fetchone()
    finally:
        connection.close()


def get_default_workspace_id(user_id: int) -> int | None:
    workspaces = get_workspaces(user_id)
    return int(workspaces[0]["id"]) if workspaces else None


def create_workspace(name: str, owner_user_id: int) -> int:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Workspace name is required.")
    connection = get_connection()
    try:
        cursor = connection.cursor()
        now = _now()
        cursor.execute("INSERT INTO workspaces (name, owner_user_id, created_at, updated_at) VALUES (?, ?, ?, ?)", (cleaned, owner_user_id, now, now))
        workspace_id = int(cursor.lastrowid)
        cursor.execute("INSERT INTO workspace_members (workspace_id, user_id, role, created_at) VALUES (?, ?, 'OWNER', ?)", (workspace_id, owner_user_id, now))
        connection.commit()
        return workspace_id
    finally:
        connection.close()


def get_workspace_members(workspace_id: int, user_id: int | None = None) -> list[sqlite3.Row]:
    connection = get_connection()
    try:
        if user_id is not None and get_workspace(workspace_id, user_id) is None:
            return []
        return connection.execute("""
            SELECT u.id, u.email, wm.role, wm.created_at
            FROM workspace_members wm JOIN users u ON u.id = wm.user_id
            WHERE wm.workspace_id = ? ORDER BY CASE wm.role WHEN 'OWNER' THEN 0 WHEN 'ADMIN' THEN 1 ELSE 2 END, u.email
        """, (workspace_id,)).fetchall()
    finally:
        connection.close()


def add_workspace_member(workspace_id: int, user_id: int, role: str = "MEMBER") -> None:
    role = role.upper().strip()
    if role not in {"OWNER", "ADMIN", "MEMBER"}:
        raise ValueError("Invalid workspace role.")
    connection = get_connection()
    try:
        connection.execute("INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role, created_at) VALUES (?, ?, ?, ?)", (workspace_id, user_id, role, _now()))
        connection.commit()
    finally:
        connection.close()


def remove_workspace_member(workspace_id: int, user_id: int) -> None:
    connection = get_connection()
    try:
        connection.execute("DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?", (workspace_id, user_id))
        connection.commit()
    finally:
        connection.close()


def create_workspace_invitation(workspace_id: int, email: str, role: str, created_by: int) -> tuple[int, str]:
    role = role.upper().strip()
    if role not in {"ADMIN", "MEMBER"}:
        raise ValueError("Invitation role must be ADMIN or MEMBER.")
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Enter a valid invitation email address.")
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    from datetime import timedelta, timezone
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=7)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO workspace_invitations (workspace_id, email, role, token_hash, created_by, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (workspace_id, email, role, token_hash, created_by, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")))
        connection.commit()
        return int(cursor.lastrowid), raw_token
    finally:
        connection.close()


def get_invitation_by_token(token: str) -> sqlite3.Row | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    from datetime import timezone
    connection = get_connection()
    try:
        return connection.execute("""
            SELECT wi.*, w.name AS workspace_name
            FROM workspace_invitations wi JOIN workspaces w ON w.id = wi.workspace_id
            WHERE wi.token_hash = ? AND wi.accepted_at IS NULL AND wi.expires_at > ?
        """, (token_hash, datetime.now(timezone.utc).isoformat(timespec="seconds"))).fetchone()
    finally:
        connection.close()


def accept_workspace_invitation(token: str, user_id: int) -> int:
    invitation = get_invitation_by_token(token)
    if invitation is None:
        raise ValueError("This invitation is invalid or has expired.")
    connection = get_connection()
    try:
        user = connection.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None or user["email"].strip().lower() != invitation["email"].strip().lower():
            raise ValueError("The invitation email does not match the signed-in account.")
        connection.execute("INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role, created_at) VALUES (?, ?, ?, ?)", (int(invitation["workspace_id"]), user_id, invitation["role"], _now()))
        connection.execute("UPDATE workspace_invitations SET accepted_at = ? WHERE id = ?", (_now(), int(invitation["id"])))
        connection.commit()
        return int(invitation["workspace_id"])
    finally:
        connection.close()


def mark_invitation_email_sent(invitation_id: int) -> None:
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE workspace_invitations SET email_status = 'SENT', email_sent_at = ?, email_error = NULL WHERE id = ?",
            (_now(), invitation_id),
        )
        connection.commit()
    finally:
        connection.close()


def mark_invitation_email_failed(invitation_id: int, error: str) -> None:
    connection = get_connection()
    try:
        connection.execute(
            "UPDATE workspace_invitations SET email_status = 'FAILED', email_error = ? WHERE id = ?",
            (error[:1000], invitation_id),
        )
        connection.commit()
    finally:
        connection.close()


def get_workspace_invitations(workspace_id: int, user_id: int | None = None) -> list[sqlite3.Row]:
    connection = get_connection()
    try:
        if user_id is not None and get_workspace(workspace_id, user_id) is None:
            return []
        return connection.execute("""
            SELECT id, email, role, created_at, expires_at, accepted_at,
                   email_status, email_sent_at, email_error
            FROM workspace_invitations WHERE workspace_id = ? ORDER BY id DESC
        """, (workspace_id,)).fetchall()
    finally:
        connection.close()


def get_projects(user_id: int | None = None, workspace_id: int | None = None) -> list[sqlite3.Row]:
    connection = get_connection()
    try:
        query = """SELECT id, name, description, default_primary_key, rules_json, created_at, updated_at, user_id, workspace_id FROM projects"""
        conditions = []
        params: list = []
        if workspace_id is not None:
            conditions.append("workspace_id = ?")
            params.append(workspace_id)
        elif user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY name"
        return connection.execute(query, tuple(params)).fetchall()
    finally:
        connection.close()


def get_project(project_id: int, user_id: int | None = None, workspace_id: int | None = None) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        query = "SELECT id, name, description, default_primary_key, rules_json, created_at, updated_at, user_id, workspace_id FROM projects WHERE id = ?"
        params: list = [project_id]
        if workspace_id is not None:
            query += " AND workspace_id = ?"
            params.append(workspace_id)
        elif user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        return connection.execute(query, tuple(params)).fetchone()
    finally:
        connection.close()


def update_project(
    project_id: int, name: str, description: str, default_primary_key: str,
    rules_json: str, user_id: int | None = None, workspace_id: int | None = None,
) -> None:
    connection = get_connection()
    try:
        query = """UPDATE projects SET name=?, description=?, default_primary_key=?, rules_json=?, updated_at=? WHERE id=?"""
        params: list = [name.strip(), description.strip(), default_primary_key.strip() or "customer_id", rules_json, _now(), project_id]
        if workspace_id is not None:
            query += " AND workspace_id=?"
            params.append(workspace_id)
        elif user_id is not None:
            query += " AND user_id=?"
            params.append(user_id)
        cursor = connection.execute(query, tuple(params))
        if cursor.rowcount == 0:
            raise ValueError("Project was not found or is not accessible to the current user.")
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


def get_runs(limit: int = 100, user_id: int | None = None, workspace_id: int | None = None) -> list[sqlite3.Row]:
    connection = get_connection()
    try:
        query = """
            SELECT runs.id, runs.project_id, projects.name AS project_name,
                   runs.run_type, runs.dataset_name, runs.source_name, runs.target_name,
                   runs.primary_key, runs.score, runs.status, runs.passed, runs.failed,
                   runs.report_path, runs.created_at, runs.result_id
            FROM runs LEFT JOIN projects ON projects.id = runs.project_id
        """
        conditions = []
        params: list = []
        if workspace_id is not None:
            conditions.append("projects.workspace_id = ?")
            params.append(workspace_id)
        elif user_id is not None:
            conditions.append("projects.user_id = ?")
            params.append(user_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY runs.id DESC LIMIT ?"
        params.append(limit)
        return connection.execute(query, tuple(params)).fetchall()
    finally:
        connection.close()


def get_run(run_id: int, user_id: int | None = None, workspace_id: int | None = None) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        query = "SELECT runs.*, projects.name AS project_name FROM runs LEFT JOIN projects ON projects.id = runs.project_id WHERE runs.id = ?"
        params: list = [run_id]
        if workspace_id is not None:
            query += " AND projects.workspace_id = ?"
            params.append(workspace_id)
        elif user_id is not None:
            query += " AND projects.user_id = ?"
            params.append(user_id)
        return connection.execute(query, tuple(params)).fetchone()
    finally:
        connection.close()


def get_run_by_result_id(result_id: str, user_id: int | None = None, workspace_id: int | None = None) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        query = "SELECT runs.*, projects.name AS project_name FROM runs LEFT JOIN projects ON projects.id = runs.project_id WHERE runs.result_id = ?"
        params: list = [result_id]
        if workspace_id is not None:
            query += " AND projects.workspace_id = ?"
            params.append(workspace_id)
        elif user_id is not None:
            query += " AND projects.user_id = ?"
            params.append(user_id)
        return connection.execute(query, tuple(params)).fetchone()
    finally:
        connection.close()


def get_run_by_report_path(report_path: str, user_id: int | None = None, workspace_id: int | None = None) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        query = "SELECT runs.*, projects.name AS project_name FROM runs LEFT JOIN projects ON projects.id = runs.project_id WHERE runs.report_path = ?"
        params: list = [report_path]
        if workspace_id is not None:
            query += " AND projects.workspace_id = ?"
            params.append(workspace_id)
        elif user_id is not None:
            query += " AND projects.user_id = ?"
            params.append(user_id)
        return connection.execute(query, tuple(params)).fetchone()
    finally:
        connection.close()


def get_dashboard_stats(user_id: int | None = None, workspace_id: int | None = None) -> dict[str, int]:
    connection = get_connection()
    try:
        join = " LEFT JOIN projects ON projects.id = runs.project_id"
        conditions = []
        params: list = []
        if workspace_id is not None:
            conditions.append("projects.workspace_id = ?")
            params.append(workspace_id)
        elif user_id is not None:
            conditions.append("projects.user_id = ?")
            params.append(user_id)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        def count(extra: str = "") -> int:
            clause = where
            local_params = list(params)
            if extra:
                clause += (" AND " if clause else " WHERE ") + extra
            return int(connection.execute(f"SELECT COUNT(*) FROM runs{join}{clause}", tuple(local_params)).fetchone()[0])

        if workspace_id is not None:
            projects = int(connection.execute("SELECT COUNT(*) FROM projects WHERE workspace_id = ?", (workspace_id,)).fetchone()[0])
        elif user_id is not None:
            projects = int(connection.execute("SELECT COUNT(*) FROM projects WHERE user_id = ?", (user_id,)).fetchone()[0])
        else:
            projects = int(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0])

        return {
            "total_runs": count(),
            "quality_runs": count("runs.run_type = 'Data Quality'"),
            "etl_runs": count("runs.run_type = 'ETL Validation'"),
            "failed_runs": count("runs.status = 'FAIL'"),
            "projects": projects,
        }
    finally:
        connection.close()
