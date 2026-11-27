from __future__ import annotations

import csv
import html
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent
WEB_ROOT = PROJECT_ROOT / "web"
UPLOAD_ROOT = WEB_ROOT / "uploads"
REPORT_ROOT = PROJECT_ROOT / "reports"
RESULT_ROOT = WEB_ROOT / "results"
HISTORY_FILE = WEB_ROOT / "history.json"

UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
REPORT_ROOT.mkdir(parents=True, exist_ok=True)
RESULT_ROOT.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".parquet", ".json"}

app = FastAPI(
    title="DataGuard",
    description="Data Quality and ETL Validation Platform",
    version="1.3.0",
)


def safe_filename(filename: str) -> str:
    name = Path(filename or "upload").name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "upload"


def save_upload(upload: UploadFile, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(upload.filename)
    suffix = Path(filename).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {suffix or 'none'}. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    path = destination / filename
    with path.open("wb") as output:
        shutil.copyfileobj(upload.file, output)

    return path


def run_dataguard(args: list[str]) -> tuple[int, str]:
    command = [sys.executable, "-m", "app.cli", *args]

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    output = completed.stdout
    if completed.stderr:
        output += "\n" + completed.stderr

    return completed.returncode, output.strip()


def esc(value: object) -> str:
    return html.escape(str(value))



def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []

    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_history(history: list[dict]) -> None:
    WEB_ROOT.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history[:100], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def add_history_entry(entry: dict) -> None:
    history = load_history()
    history.insert(0, entry)
    save_history(history)


def write_result_csv(
    path: Path,
    results: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["status", "check", "details"],
        )
        writer.writeheader()
        writer.writerows(results)


def save_result_page(
    run_id: str,
    title: str,
    body: str,
    active: str,
) -> Path:
    rendered = page(title, body, active)
    result_path = RESULT_ROOT / f"{run_id}.html"
    result_path.write_text(rendered, encoding="utf-8")
    return result_path


def history_stats(history: list[dict]) -> dict[str, int]:
    return {
        "total": len(history),
        "quality": sum(item.get("type") == "Data Quality" for item in history),
        "etl": sum(item.get("type") == "ETL Validation" for item in history),
        "failed": sum(item.get("status") == "FAIL" for item in history),
    }


def render_history_table(history: list[dict]) -> str:
    if not history:
        return """
        <div class="empty">
          No runs yet. Run a Data Quality or ETL validation to create your first history entry.
        </div>
        """

    rows = []
    for item in history[:20]:
        status = item.get("status", "FAIL")
        status_class = "pass" if status == "PASS" else "fail"
        score = item.get("score")
        score_text = f"{float(score):.2f}%" if score is not None else "N/A"
        run_id = item.get("run_id", "")
        run_href = f"/runs/{esc(run_id)}"
        rows.append(
            f"""
            <tr class="row-{status_class}">
              <td>{esc(item.get("timestamp", ""))}</td>
              <td><strong>{esc(item.get("type", ""))}</strong></td>
              <td>{esc(item.get("dataset", ""))}</td>
              <td>{score_text}</td>
              <td>{status_badge(status)}</td>
              <td><a class="small-link" href="{run_href}">View</a></td>
            </tr>
            """
        )

    return f"""
    <div class="results">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Run Type</th>
            <th>Dataset</th>
            <th>Score</th>
            <th>Status</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </div>
    """


def parse_score(output: str, label: str) -> float | None:
    match = re.search(
        rf"{re.escape(label)}\s*:\s*([0-9]+(?:\.[0-9]+)?)%",
        output,
        re.IGNORECASE,
    )
    return float(match.group(1)) if match else None


def parse_quality_results(output: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    pattern = re.compile(
        r"^(PASS|FAIL|WARN(?:ING)?)\s+(.+?)\s*:\s*(.*)$",
        re.IGNORECASE,
    )

    for line in output.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue

        status = match.group(1).upper()
        if status == "WARNING":
            status = "WARN"

        check_name = match.group(2).strip()
        details = match.group(3).strip()

        if ":" in check_name:
            column, check = check_name.split(":", 1)
            check_name = f"{check.strip()} • {column.strip()}"

        results.append(
            {
                "status": status,
                "check": check_name,
                "details": details,
            }
        )

    return results


def parse_etl_results(output: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    pattern = re.compile(
        r"^(PASS|FAIL|WARN(?:ING)?)\s+(.+?)\s*:\s*(.*)$",
        re.IGNORECASE,
    )

    for line in output.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue

        status = match.group(1).upper()
        if status == "WARNING":
            status = "WARN"

        results.append(
            {
                "status": status,
                "check": match.group(2).strip(),
                "details": match.group(3).strip(),
            }
        )

    return results


def count_statuses(results: list[dict[str, str]]) -> tuple[int, int, int]:
    passed = sum(item["status"] == "PASS" for item in results)
    failed = sum(item["status"] == "FAIL" for item in results)
    warnings = sum(item["status"] == "WARN" for item in results)
    return passed, failed, warnings


def status_badge(status: str) -> str:
    normalized = status.upper()

    if normalized == "PASS":
        return '<span class="status pass">PASS</span>'
    if normalized == "FAIL":
        return '<span class="status fail">FAIL</span>'
    return '<span class="status warn">WARN</span>'


def score_card(
    title: str,
    score: float | None,
    passed: int,
    failed: int,
    warnings: int,
    href: str,
) -> str:
    score_text = f"{score:.2f}%" if score is not None else "N/A"
    state = "PASS" if score is not None and score >= 100 else "FAIL"
    state_class = "pass" if state == "PASS" else "fail"

    return f"""
    <a class="score-card" href="{esc(href)}">
      <div class="score-card-top">
        <span>{esc(title)}</span>
        <span class="mini-arrow">→</span>
      </div>
      <div class="score">{score_text}</div>
      <div class="score-state {state_class}">{state}</div>
      <div class="mini-stats">
        <span><strong>{passed}</strong> Passed</span>
        <span><strong>{failed}</strong> Failed</span>
        <span><strong>{warnings}</strong> Warnings</span>
      </div>
    </a>
    """


def page(title: str, body: str, active: str = "Dashboard") -> str:
    nav_items = [
        ("Dashboard", "/"),
        ("Data Quality", "/#quality"),
        ("ETL Validation", "/#etl"),
    ]

    nav_html = "".join(
        f'<a class="nav-link {"active" if active == name else ""}" href="{url}">{name}</a>'
        for name, url in nav_items
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} | DataGuard</title>
<style>
:root {{
  --bg:#f4f7fb;
  --card:#ffffff;
  --text:#172033;
  --muted:#667085;
  --border:#e4e7ec;
  --primary:#2563eb;
  --primary-dark:#1d4ed8;
  --success:#087443;
  --success-bg:#ecfdf3;
  --danger:#b42318;
  --danger-bg:#fef3f2;
  --warning:#b54708;
  --warning-bg:#fffaeb;
  --nav:#0f172a;
  --shadow:0 8px 24px rgba(16,24,40,.06);
}}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{
  margin:0;
  font-family:Inter,Segoe UI,Arial,sans-serif;
  background:var(--bg);
  color:var(--text);
}}
a {{ color:inherit; }}
.container {{ max-width:1180px; margin:0 auto; padding:28px 20px 64px; }}
.header {{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:20px;
  margin-bottom:22px;
}}
.brand {{
  display:flex;
  align-items:center;
  gap:11px;
}}
.logo {{
  font-size:29px;
  font-weight:850;
  letter-spacing:-.8px;
}}
.logo-mark {{
  width:34px;
  height:34px;
  border-radius:10px;
  display:grid;
  place-items:center;
  color:white;
  background:linear-gradient(135deg,#2563eb,#7c3aed);
  font-weight:900;
}}
.badge {{
  background:#eaf1ff;
  color:var(--primary);
  padding:7px 12px;
  border-radius:999px;
  font-size:13px;
  font-weight:750;
}}
.nav {{
  display:flex;
  gap:6px;
  padding:6px;
  border:1px solid var(--border);
  background:#fff;
  border-radius:12px;
  margin-bottom:30px;
  box-shadow:0 2px 8px rgba(16,24,40,.03);
}}
.nav-link {{
  text-decoration:none;
  padding:10px 14px;
  border-radius:8px;
  color:var(--muted);
  font-weight:700;
  font-size:14px;
}}
.nav-link:hover,.nav-link.active {{
  background:#eef4ff;
  color:var(--primary);
}}
.hero {{
  margin:8px 0 28px;
}}
.hero h1 {{
  font-size:40px;
  line-height:1.1;
  margin:0 0 9px;
  letter-spacing:-1px;
}}
.hero p {{
  color:var(--muted);
  font-size:17px;
  margin:0;
  max-width:780px;
}}
.section-title {{
  display:flex;
  justify-content:space-between;
  align-items:end;
  gap:15px;
  margin:30px 0 14px;
}}
.section-title h2 {{
  margin:0;
  font-size:22px;
}}
.section-title p {{
  margin:0;
  color:var(--muted);
  font-size:14px;
}}
.score-grid {{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:18px;
}}
.score-card {{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:18px;
  padding:23px;
  text-decoration:none;
  box-shadow:var(--shadow);
  transition:transform .15s ease, box-shadow .15s ease;
}}
.score-card:hover {{
  transform:translateY(-2px);
  box-shadow:0 12px 28px rgba(16,24,40,.09);
}}
.score-card-top {{
  display:flex;
  justify-content:space-between;
  color:var(--muted);
  font-weight:750;
}}
.mini-arrow {{ color:var(--primary); font-size:20px; }}
.score {{
  font-size:42px;
  font-weight:850;
  letter-spacing:-1px;
  margin:12px 0 2px;
}}
.score-state {{
  display:inline-block;
  font-size:12px;
  font-weight:850;
  border-radius:999px;
  padding:5px 9px;
}}
.score-state.pass {{ background:var(--success-bg); color:var(--success); }}
.score-state.fail {{ background:var(--danger-bg); color:var(--danger); }}
.mini-stats {{
  display:flex;
  gap:18px;
  margin-top:18px;
  color:var(--muted);
  font-size:13px;
}}
.mini-stats strong {{ color:var(--text); }}
.grid {{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  gap:20px;
}}
.card {{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:16px;
  padding:24px;
  box-shadow:var(--shadow);
}}
.card h2 {{ margin:0 0 7px; font-size:20px; }}
.card-subtitle {{ color:var(--muted); margin:0 0 18px; font-size:14px; }}
.full {{ grid-column:1/-1; }}
label {{ display:block; font-weight:750; margin:14px 0 7px; }}
input[type=file], input[type=text], select {{
  width:100%;
  padding:12px;
  border:1px solid #cfd4dc;
  border-radius:9px;
  background:#fff;
  font:inherit;
}}
input:focus {{ outline:3px solid #dbeafe; border-color:#60a5fa; }}
button,.button {{
  display:inline-block;
  border:0;
  border-radius:9px;
  padding:12px 18px;
  background:var(--primary);
  color:white;
  font-weight:750;
  cursor:pointer;
  text-decoration:none;
  font:inherit;
}}
button:hover,.button:hover {{ background:var(--primary-dark); }}
.secondary {{ background:#eef2f6; color:var(--text); }}
.secondary:hover {{ background:#e2e8f0; }}
.help {{ color:var(--muted); font-size:13px; margin-top:6px; line-height:1.5; }}
.notice {{
  padding:14px 16px;
  border-radius:10px;
  margin-bottom:18px;
  background:var(--warning-bg);
  color:var(--warning);
}}
.notice.ok {{ background:var(--success-bg); color:var(--success); }}
.notice.error {{ background:var(--danger-bg); color:var(--danger); }}
.result-summary {{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:12px;
  margin:18px 0;
}}
.metric {{
  border:1px solid var(--border);
  border-radius:12px;
  padding:15px;
  background:#fbfcfe;
}}
.metric .label {{ color:var(--muted); font-size:12px; font-weight:750; }}
.metric .value {{ font-size:24px; font-weight:850; margin-top:5px; }}
.results {{
  overflow:auto;
  border:1px solid var(--border);
  border-radius:12px;
}}
table {{
  width:100%;
  border-collapse:collapse;
  min-width:680px;
}}
th,td {{
  padding:13px 14px;
  text-align:left;
  border-bottom:1px solid var(--border);
  font-size:14px;
  vertical-align:top;
}}
th {{ background:#f8fafc; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
tr:last-child td {{ border-bottom:0; }}
.status {{
  display:inline-block;
  min-width:52px;
  text-align:center;
  padding:4px 8px;
  border-radius:999px;
  font-size:11px;
  font-weight:850;
}}
.status.pass {{ background:var(--success-bg); color:var(--success); }}
.status.fail {{ background:var(--danger-bg); color:var(--danger); }}
.status.warn {{ background:var(--warning-bg); color:var(--warning); }}
.row-fail {{ background:#fff8f7; }}
.row-pass {{ background:#fbfffd; }}
pre {{
  white-space:pre-wrap;
  word-break:break-word;
  background:#0f172a;
  color:#e5e7eb;
  border-radius:12px;
  padding:18px;
  overflow:auto;
  line-height:1.5;
  font-size:13px;
}}
.footer {{
  margin-top:38px;
  color:var(--muted);
  text-align:center;
  font-size:13px;
}}
.empty {{
  padding:26px;
  text-align:center;
  color:var(--muted);
  border:1px dashed var(--border);
  border-radius:12px;
}}

.small-link {{
  color:var(--primary);
  font-weight:750;
  text-decoration:none;
}}
.small-link:hover {{ text-decoration:underline; }}
.dashboard-stats {{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:14px;
  margin-bottom:20px;
}}
.dashboard-stat {{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:14px;
  padding:18px;
  box-shadow:var(--shadow);
}}
.dashboard-stat .number {{
  font-size:28px;
  font-weight:850;
  margin-top:4px;
}}
.dashboard-stat .label {{
  color:var(--muted);
  font-size:12px;
  font-weight:750;
}}
.action-row {{
  display:flex;
  flex-wrap:wrap;
  gap:9px;
  margin-top:18px;
}}

@media(max-width:800px) {{
  .score-grid,.grid,.dashboard-stats {{ grid-template-columns:1fr; }}
  .full {{ grid-column:auto; }}
  .result-summary {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .dashboard-stats {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  .nav {{ overflow:auto; }}
  .hero h1 {{ font-size:32px; }}
}}
@media(max-width:500px) {{
  .container {{ padding:20px 14px 45px; }}
  .header {{ align-items:flex-start; }}
  .mini-stats {{ flex-wrap:wrap; gap:8px 14px; }}
}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="brand">
      <div class="logo-mark">D</div>
      <div class="logo">DataGuard</div>
    </div>
    <div class="badge">v1.3 SaaS</div>
  </div>

  <nav class="nav">
    {nav_html}
  </nav>

  {body}

  <div class="footer">
    DataGuard • Data Quality & ETL Testing Platform
  </div>
</div>
</body>
</html>"""


def render_results_table(results: list[dict[str, str]]) -> str:
    if not results:
        return '<div class="empty">No structured validation results were detected.</div>'

    rows = []
    for item in results:
        row_class = "row-fail" if item["status"] == "FAIL" else "row-pass"
        rows.append(
            f"""
            <tr class="{row_class}">
              <td>{status_badge(item["status"])}</td>
              <td><strong>{esc(item["check"])}</strong></td>
              <td>{esc(item["details"])}</td>
            </tr>
            """
        )

    return f"""
    <div class="results">
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Check</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </div>
    """


@app.get("/", response_class=HTMLResponse)
async def home():
    history = load_history()
    stats = history_stats(history)

    body = f"""
    <div class="hero">
      <h1>Data Quality & ETL Testing</h1>
      <p>
        Validate datasets, compare source and target pipelines, review previous runs,
        and download results from one DataGuard workspace.
      </p>
    </div>

    <div class="dashboard-stats">
      <div class="dashboard-stat">
        <div class="label">Total Runs</div>
        <div class="number">{stats["total"]}</div>
      </div>
      <div class="dashboard-stat">
        <div class="label">Data Quality Runs</div>
        <div class="number">{stats["quality"]}</div>
      </div>
      <div class="dashboard-stat">
        <div class="label">ETL Runs</div>
        <div class="number">{stats["etl"]}</div>
      </div>
      <div class="dashboard-stat">
        <div class="label">Failed Runs</div>
        <div class="number" style="color:var(--danger)">{stats["failed"]}</div>
      </div>
    </div>

    <div class="section-title">
      <div>
        <h2>Run a validation</h2>
        <p>Choose the validation workflow that matches your testing need.</p>
      </div>
    </div>

    <div class="grid">
      <div class="card" id="quality">
        <h2>Data Quality</h2>
        <p class="card-subtitle">
          Profile a dataset and validate it against configurable JSON rules.
        </p>

        <form action="/validate" method="post" enctype="multipart/form-data">
          <label>Dataset</label>
          <input type="file" name="dataset" accept=".csv,.xlsx,.parquet" required>

          <label>Rules</label>
          <input type="file" name="rules" accept=".json">
          <div class="help">
            Optional. If omitted, DataGuard uses the project's rules.json.
          </div>

          <button type="submit" style="margin-top:18px">
            Run Quality Validation
          </button>
        </form>
      </div>

      <div class="card" id="etl">
        <h2>Source vs Target ETL</h2>
        <p class="card-subtitle">
          Compare structure, counts, keys, records, values, and hashes.
        </p>

        <form action="/compare" method="post" enctype="multipart/form-data">
          <label>Source Dataset</label>
          <input type="file" name="source" accept=".csv,.xlsx,.parquet" required>

          <label>Target Dataset</label>
          <input type="file" name="target" accept=".csv,.xlsx,.parquet" required>

          <label>Primary Key</label>
          <input type="text" name="primary_key" value="customer_id" required>

          <button type="submit" style="margin-top:18px">
            Run ETL Validation
          </button>
        </form>
      </div>

      <div class="card full">
        <div class="section-title" style="margin-top:0">
          <div>
            <h2>Run History</h2>
            <p>Last 20 DataGuard validations are retained locally.</p>
          </div>
        </div>
        {render_history_table(history)}
      </div>
    </div>
    """

    return page("Dashboard", body)


@app.post("/validate", response_class=HTMLResponse)
async def validate(
    dataset: UploadFile = File(...),
    rules: UploadFile | None = File(None),
):
    session = UPLOAD_ROOT / uuid.uuid4().hex
    session.mkdir(parents=True, exist_ok=True)

    try:
        dataset_path = save_upload(dataset, session)

        if rules and rules.filename:
            rules_path = save_upload(rules, session)
        else:
            rules_path = PROJECT_ROOT / "rules.json"

        report_name = f"web_{uuid.uuid4().hex}_quality.html"
        report_path = REPORT_ROOT / report_name

        code, output = run_dataguard(
            [
                "validate",
                str(dataset_path),
                "--rules",
                str(rules_path),
                "--report",
                str(report_path),
            ]
        )

        results = parse_quality_results(output)
        passed, failed, warnings = count_statuses(results)
        score = parse_score(output, "DATA QUALITY SCORE")
        overall = "PASS" if code == 0 else "FAIL"
        run_id = uuid.uuid4().hex
        csv_name = f"{run_id}_quality_results.csv"
        csv_path = RESULT_ROOT / csv_name
        write_result_csv(csv_path, results)

        state_class = "ok" if overall == "PASS" else "error"

        body = f"""
        <div class="hero">
          <h1>Data Quality Result</h1>
          <p>{esc(dataset.filename)}</p>
        </div>

        <div class="notice {state_class}">
          <strong>{'Validation completed successfully' if overall == 'PASS' else 'Validation completed with failures'}</strong>
        </div>

        <div class="result-summary">
          <div class="metric">
            <div class="label">Quality Score</div>
            <div class="value">{f"{score:.2f}%" if score is not None else "N/A"}</div>
          </div>
          <div class="metric">
            <div class="label">Total Checks</div>
            <div class="value">{len(results)}</div>
          </div>
          <div class="metric">
            <div class="label">Passed</div>
            <div class="value" style="color:var(--success)">{passed}</div>
          </div>
          <div class="metric">
            <div class="label">Failed</div>
            <div class="value" style="color:var(--danger)">{failed}</div>
          </div>
        </div>

        <div class="card">
          <h2>Validation Results</h2>
          <p class="card-subtitle">
            Structured results from the DataGuard quality engine.
          </p>
          {render_results_table(results)}

          <div class="action-row">
            <a class="button" href="/reports/{esc(report_name)}" target="_blank">
              Open HTML Report
            </a>
            <a class="button secondary" href="/downloads/{esc(csv_name)}">
              Download Results CSV
            </a>
            <a class="button secondary" href="/runs/{esc(run_id)}">
              Permalink
            </a>
            <a class="button secondary" href="/">
              Back to Dashboard
            </a>
          </div>
        </div>

        <div class="card" style="margin-top:20px">
          <h2>Raw Engine Output</h2>
          <p class="card-subtitle">Original CLI output retained for troubleshooting.</p>
          <pre>{esc(output)}</pre>
        </div>
        """

        save_result_page(run_id, "Data Quality Result", body, "Data Quality")
        add_history_entry(
            {
                "run_id": run_id,
                "timestamp": __import__("datetime").datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "Data Quality",
                "dataset": dataset.filename or "dataset",
                "score": score,
                "status": overall,
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "report": report_name,
                "csv": csv_name,
            }
        )
        return HTMLResponse(page("Data Quality Result", body, "Data Quality"))

    except Exception as exc:
        return page(
            "Validation Error",
            f"""
            <div class="notice error">
              <strong>Validation Error:</strong> {esc(exc)}
            </div>
            <a class="button" href="/">Back to Dashboard</a>
            """,
            "Data Quality",
        )


@app.post("/compare", response_class=HTMLResponse)
async def compare(
    source: UploadFile = File(...),
    target: UploadFile = File(...),
    primary_key: str = Form(...),
):
    session = UPLOAD_ROOT / uuid.uuid4().hex
    session.mkdir(parents=True, exist_ok=True)

    try:
        source_path = save_upload(source, session)
        target_path = save_upload(target, session)

        key = primary_key.strip()
        if not key:
            raise ValueError("Primary key cannot be empty.")

        report_name = f"web_{uuid.uuid4().hex}_etl.html"
        report_path = REPORT_ROOT / report_name

        code, output = run_dataguard(
            [
                "compare",
                str(source_path),
                str(target_path),
                "--primary-key",
                key,
                "--report",
                str(report_path),
            ]
        )

        results = parse_etl_results(output)
        passed, failed, warnings = count_statuses(results)
        score = parse_score(output, "ETL VALIDATION SCORE")
        overall = "PASS" if code == 0 else "FAIL"
        run_id = uuid.uuid4().hex
        csv_name = f"{run_id}_etl_results.csv"
        csv_path = RESULT_ROOT / csv_name
        write_result_csv(csv_path, results)

        state_class = "ok" if overall == "PASS" else "error"

        body = f"""
        <div class="hero">
          <h1>ETL Validation Result</h1>
          <p>{esc(source.filename)} → {esc(target.filename)}</p>
        </div>

        <div class="notice {state_class}">
          <strong>{'ETL validation completed successfully' if overall == 'PASS' else 'ETL validation completed with failures'}</strong>
        </div>

        <div class="result-summary">
          <div class="metric">
            <div class="label">ETL Score</div>
            <div class="value">{f"{score:.2f}%" if score is not None else "N/A"}</div>
          </div>
          <div class="metric">
            <div class="label">Total Checks</div>
            <div class="value">{len(results)}</div>
          </div>
          <div class="metric">
            <div class="label">Passed</div>
            <div class="value" style="color:var(--success)">{passed}</div>
          </div>
          <div class="metric">
            <div class="label">Failed</div>
            <div class="value" style="color:var(--danger)">{failed}</div>
          </div>
        </div>

        <div class="card">
          <h2>ETL Comparison Results</h2>
          <p class="card-subtitle">
            Source → Target • Primary key: <strong>{esc(key)}</strong>
          </p>
          {render_results_table(results)}

          <div class="action-row">
            <a class="button" href="/reports/{esc(report_name)}" target="_blank">
              Open HTML Report
            </a>
            <a class="button secondary" href="/downloads/{esc(csv_name)}">
              Download Results CSV
            </a>
            <a class="button secondary" href="/runs/{esc(run_id)}">
              Permalink
            </a>
            <a class="button secondary" href="/">
              Back to Dashboard
            </a>
          </div>
        </div>

        <div class="card" style="margin-top:20px">
          <h2>Raw Engine Output</h2>
          <p class="card-subtitle">Original CLI output retained for troubleshooting.</p>
          <pre>{esc(output)}</pre>
        </div>
        """

        save_result_page(run_id, "ETL Validation Result", body, "ETL Validation")
        add_history_entry(
            {
                "run_id": run_id,
                "timestamp": __import__("datetime").datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                "type": "ETL Validation",
                "dataset": f"{source.filename or 'source'} → {target.filename or 'target'}",
                "score": score,
                "status": overall,
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "report": report_name,
                "csv": csv_name,
                "primary_key": key,
            }
        )
        return HTMLResponse(page("ETL Validation Result", body, "ETL Validation"))

    except Exception as exc:
        return page(
            "ETL Error",
            f"""
            <div class="notice error">
              <strong>ETL Error:</strong> {esc(exc)}
            </div>
            <a class="button" href="/">Back to Dashboard</a>
            """,
            "ETL Validation",
        )



@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def get_run(run_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        return HTMLResponse("Invalid run ID.", status_code=400)

    path = RESULT_ROOT / f"{run_id}.html"
    if not path.exists():
        return HTMLResponse("Run not found.", status_code=404)

    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/downloads/{filename}")
async def download_result(filename: str):
    safe = safe_filename(filename)
    path = RESULT_ROOT / safe

    if not path.exists() or path.suffix.lower() != ".csv":
        return HTMLResponse("Download not found.", status_code=404)

    return FileResponse(
        path,
        media_type="text/csv",
        filename=path.name,
    )


@app.get("/reports/{filename}")
async def get_report(filename: str):
    safe = safe_filename(filename)
    path = REPORT_ROOT / safe

    if not path.exists() or path.suffix.lower() != ".html":
        return HTMLResponse("Report not found.", status_code=404)

    return FileResponse(path, media_type="text/html")
