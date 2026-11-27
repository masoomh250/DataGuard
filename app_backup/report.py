from pathlib import Path
from datetime import datetime


def generate_html_report(
    profile: dict,
    results: list,
    score: float,
    output_file: str,
) -> None:
    """Generate a human-readable HTML report."""

    status_rows = ""

    for result in results:

        status = result["status"]

        if status == "PASS":
            status_display = "PASS"
        elif status == "FAIL":
            status_display = "FAIL"
        else:
            status_display = "SKIP"

        status_rows += f"""
        <tr>
            <td>{result["name"]}</td>
            <td>{status_display}</td>
            <td>{result["details"]}</td>
        </tr>
        """

    column_rows = ""

    for column in profile["columns"]:
        column_rows += f"""
        <tr>
            <td>{column["name"]}</td>
            <td>{column["dtype"]}</td>
            <td>{column["null_count"]}</td>
            <td>{column["null_percentage"]}%</td>
            <td>{column["unique_count"]}</td>
        </tr>
        """

    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>DataGuard Quality Report</title>

    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 40px;
            background: #f5f7fa;
        }}

        .container {{
            max-width: 1100px;
            margin: auto;
            background: white;
            padding: 30px;
            border-radius: 10px;
        }}

        h1 {{
            margin-bottom: 5px;
        }}

        .score {{
            font-size: 42px;
            font-weight: bold;
            margin: 20px 0;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }}

        th, td {{
            border: 1px solid #ddd;
            padding: 10px;
            text-align: left;
        }}

        th {{
            background: #f0f2f5;
        }}
    </style>
</head>

<body>

<div class="container">

    <h1>DataGuard</h1>

    <p>Data Quality Report</p>

    <hr>

    <h2>Dataset Summary</h2>

    <p>
        <strong>Rows:</strong>
        {profile["row_count"]}
    </p>

    <p>
        <strong>Columns:</strong>
        {profile["column_count"]}
    </p>

    <p>
        <strong>Generated:</strong>
        {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    </p>

    <div class="score">
        Quality Score: {score}%
    </div>

    <h2>Validation Results</h2>

    <table>

        <tr>
            <th>Check</th>
            <th>Status</th>
            <th>Details</th>
        </tr>

        {status_rows}

    </table>

    <h2>Column Profile</h2>

    <table>

        <tr>
            <th>Column</th>
            <th>Data Type</th>
            <th>Null Count</th>
            <th>Null %</th>
            <th>Unique Count</th>
        </tr>

        {column_rows}

    </table>

</div>

</body>
</html>
"""

    output_path = Path(output_file)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        html,
        encoding="utf-8",
    )