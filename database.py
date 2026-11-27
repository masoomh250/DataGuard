import sqlite3
from pathlib import Path
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "dataguard.db"


def get_connection():
    """Return a SQLite database connection."""

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = sqlite3.Row

    return connection


def initialize_database():
    """Create DataGuard database tables."""

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
                FOREIGN KEY (
                    project_id
                )
                REFERENCES projects(id)
            )
            """
        )

        connection.commit()

    finally:
        connection.close()


def create_project(
    name: str,
    description: str = "",
):
    """Create a new DataGuard project."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        created_at = datetime.now().isoformat(
            timespec="seconds"
        )

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
                name,
                description,
                created_at,
            ),
        )

        connection.commit()

        return cursor.lastrowid

    finally:
        connection.close()


def get_projects():
    """Return all DataGuard projects."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                name,
                description,
                created_at
            FROM projects
            ORDER BY id DESC
            """
        )

        return [
            dict(row)
            for row in cursor.fetchall()
        ]

    finally:
        connection.close()


def get_project(
    project_id: int,
):
    """Return one project."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
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
        )

        row = cursor.fetchone()

        if row is None:
            return None

        return dict(row)

    finally:
        connection.close()


def save_run(
    project_id,
    run_type,
    dataset_name=None,
    source_name=None,
    target_name=None,
    primary_key=None,
    score=None,
    status="FAIL",
    passed=0,
    failed=0,
    report_path=None,
):
    """Persist a DataGuard validation run."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        created_at = datetime.now().isoformat(
            timespec="seconds"
        )

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
                created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
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
                created_at,
            ),
        )

        connection.commit()

        return cursor.lastrowid

    finally:
        connection.close()


def get_runs(
    limit=20,
):
    """Return recent DataGuard runs."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
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
                runs.created_at
            FROM runs
            LEFT JOIN projects
                ON runs.project_id = projects.id
            ORDER BY runs.id DESC
            LIMIT ?
            """,
            (limit,),
        )

        return [
            dict(row)
            for row in cursor.fetchall()
        ]

    finally:
        connection.close()


def get_run(
    run_id: int,
):
    """Return one validation run."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
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
                runs.created_at
            FROM runs
            LEFT JOIN projects
                ON runs.project_id = projects.id
            WHERE runs.id = ?
            """,
            (run_id,),
        )

        row = cursor.fetchone()

        if row is None:
            return None

        return dict(row)

    finally:
        connection.close()


def get_dashboard_stats():
    """Return dashboard summary statistics."""

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM runs
            """
        )

        total_runs = cursor.fetchone()["total"]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM runs
            WHERE run_type = 'Data Quality'
            """
        )

        quality_runs = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM runs
            WHERE run_type = 'ETL Validation'
            """
        )

        etl_runs = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM runs
            WHERE status = 'FAIL'
            """
        )

        failed_runs = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM projects
            """
        )

        projects = cursor.fetchone()[0]

        return {
            "total_runs": total_runs,
            "quality_runs": quality_runs,
            "etl_runs": etl_runs,
            "failed_runs": failed_runs,
            "projects": projects,
        }

    finally:
        connection.close()