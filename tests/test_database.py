from app.database import (
    get_connection,
    initialize_database,
)


def test_database_initialization(
    tmp_path,
    monkeypatch,
):
    import app.database as database

    test_db = tmp_path / "test.db"

    monkeypatch.setattr(
        database,
        "DB_PATH",
        test_db,
    )

    monkeypatch.setattr(
        database,
        "DATA_DIR",
        tmp_path,
    )

    initialize_database()

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        )

        tables = {
            row["name"]
            for row in cursor.fetchall()
        }

    finally:
        connection.close()

    assert "projects" in tables
    assert "runs" in tables