from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "dataguard.db"


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    return connection


def initialize_database() -> None:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
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
                FOREIGN KEY (project_id)
                    REFERENCES projects(id)
            )
            """
        )

        # Existing databases created before v1.4 may not have result_id.
        columns = [
            row[1]
            for row in cursor.execute(
                "PRAGMA table_info(runs)"
            ).fetchall()
        ]

        if "result_id" not in columns:
            cursor.execute(
                "ALTER TABLE runs ADD COLUMN result_id TEXT"
            )

        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_result_id
            ON runs(result_id)
            """
        )

        connection.commit()

    finally:
        connection.close()


def create_project(
    name: str,
    description: str = "",
) -> int:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO projects (
                name,
                description,
                created_at
            )
            VALUES (?, ?, ?)
            """,
            (
                name.strip(),
                description.strip(),
                datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
            ),
        )

        connection.commit()

        return int(cursor.lastrowid)

    finally:
        connection.close()


def get_projects() -> list[sqlite3.Row]:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        return cursor.execute(
            """
            SELECT
                id,
                name,
                description,
                created_at
            FROM projects
            ORDER BY name
            """
        ).fetchall()

    finally:
        connection.close()


def get_project(project_id: int) -> sqlite3.Row | None:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        return cursor.execute(
            """
            SELECT
                id,
                name,
                description,
                created_at
            FROM projects
            WHERE id = ?
            """,
            (project_id,),
        ).fetchone()

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

        cursor.execute(
            """
            INSERT INTO runs (
                project_id,
                run_type,
                dataset_name,
                source_name,
                target_name,
                primary_key,
                score,
                status,
                passed,
                failed,
                report_path,
                created_at,
                result_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                run_type,
                dataset_name,
                source_name,
                target_name,
                primary_key,
                score,
                status,
                passed,
                failed,
                report_path,
                datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                result_id,
            ),
        )

        connection.commit()

        return int(cursor.lastrowid)

    finally:
        connection.close()


def get_runs(limit: int = 100) -> list[sqlite3.Row]:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        return cursor.execute(
            """
            SELECT
                runs.id,
                runs.project_id,
                projects.name AS project_name,
                runs.run_type,
                runs.dataset_name,
                runs.source_name,
                runs.target_name,
                runs.primary_key,
                runs.score,
                runs.status,
                runs.passed,
                runs.failed,
                runs.report_path,
                runs.created_at,
                runs.result_id
            FROM runs
            LEFT JOIN projects
                ON projects.id = runs.project_id
            ORDER BY runs.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    finally:
        connection.close()


def get_run(run_id: int) -> sqlite3.Row | None:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        return cursor.execute(
            """
            SELECT
                runs.id,
                runs.project_id,
                projects.name AS project_name,
                runs.run_type,
                runs.dataset_name,
                runs.source_name,
                runs.target_name,
                runs.primary_key,
                runs.score,
                runs.status,
                runs.passed,
                runs.failed,
                runs.report_path,
                runs.created_at,
                runs.result_id
            FROM runs
            LEFT JOIN projects
                ON projects.id = runs.project_id
            WHERE runs.id = ?
            """,
            (run_id,),
        ).fetchone()

    finally:
        connection.close()


def get_run_by_result_id(
    result_id: str,
) -> sqlite3.Row | None:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        return cursor.execute(
            """
            SELECT
                runs.id,
                runs.project_id,
                projects.name AS project_name,
                runs.run_type,
                runs.dataset_name,
                runs.source_name,
                runs.target_name,
                runs.primary_key,
                runs.score,
                runs.status,
                runs.passed,
                runs.failed,
                runs.report_path,
                runs.created_at,
                runs.result_id
            FROM runs
            LEFT JOIN projects
                ON projects.id = runs.project_id
            WHERE runs.result_id = ?
            """,
            (result_id,),
        ).fetchone()

    finally:
        connection.close()


def get_dashboard_stats() -> dict[str, int]:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        total_runs = cursor.execute(
            "SELECT COUNT(*) FROM runs"
        ).fetchone()[0]

        quality_runs = cursor.execute(
            """
            SELECT COUNT(*)
            FROM runs
            WHERE run_type = 'Data Quality'
            """
        ).fetchone()[0]

        etl_runs = cursor.execute(
            """
            SELECT COUNT(*)
            FROM runs
            WHERE run_type = 'ETL Validation'
            """
        ).fetchone()[0]

        failed_runs = cursor.execute(
            """
            SELECT COUNT(*)
            FROM runs
            WHERE status = 'FAIL'
            """
        ).fetchone()[0]

        projects = cursor.execute(
            "SELECT COUNT(*) FROM projects"
        ).fetchone()[0]

        return {
            "total_runs": int(total_runs),
            "quality_runs": int(quality_runs),
            "etl_runs": int(etl_runs),
            "failed_runs": int(failed_runs),
            "projects": int(projects),
        }

    finally:
        connection.close()