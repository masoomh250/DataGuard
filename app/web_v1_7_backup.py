from __future__ import annotations

import csv
import html
import urllib.error
import urllib.request
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from app.database import (
    authenticate_user,
    claim_orphan_projects,
    create_project,
    create_session,
    create_user,
    delete_session,
    add_workspace_member,
    create_workspace,
    create_workspace_invitation,
    mark_invitation_email_failed,
    mark_invitation_email_sent,
    get_dashboard_stats,
    get_default_workspace_id,
    get_invitation_by_token,
    get_project,
    get_projects,
    get_workspace,
    get_workspace_invitations,
    get_workspace_members,
    get_workspaces,
    get_run_by_report_path,
    get_run_by_result_id,
    get_runs,
    get_user_by_email,
    get_user_by_session,
    accept_workspace_invitation,
    initialize_database,
    save_run,
    update_project,
)

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
SESSION_COOKIE = "dataguard_session"
WORKSPACE_COOKIE = "dataguard_workspace"
SESSION_SECRET = os.getenv(
    "DATAGUARD_SESSION_SECRET",
    "dataguard-development-secret-change-before-production",
)
COOKIE_SECURE = os.getenv("DATAGUARD_COOKIE_SECURE", "0") == "1"
EMAIL_PROVIDER = os.getenv("DATAGUARD_EMAIL_PROVIDER", "resend").strip().lower()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
FROM_EMAIL = os.getenv("DATAGUARD_FROM_EMAIL", "").strip()
FROM_NAME = os.getenv("DATAGUARD_FROM_NAME", "DataGuard").strip() or "DataGuard"
APP_BASE_URL = os.getenv("DATAGUARD_APP_BASE_URL", "").strip().rstrip("/")


app = FastAPI(
    title="DataGuard",
    description="Data Quality and ETL Validation Platform",
    version="1.8.0",
)


@app.on_event("startup")
def startup() -> None:
    initialize_database()
    migrate_history_to_database()


def get_current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    return get_user_by_session(token)


def get_active_workspace(request: Request, user):
    if user is None:
        return None
    try:
        workspace_id = int(request.cookies.get(WORKSPACE_COOKIE, ""))
    except (TypeError, ValueError):
        workspace_id = 0
    if workspace_id:
        workspace = get_workspace(workspace_id, int(user["id"]))
        if workspace is not None:
            return workspace
    default_id = get_default_workspace_id(int(user["id"]))
    return get_workspace(default_id, int(user["id"])) if default_id else None


def workspace_id_for_request(request: Request, user) -> int | None:
    workspace = get_active_workspace(request, user)
    return int(workspace["id"]) if workspace else None


def auth_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def email_is_valid(email: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()))


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


def migrate_history_to_database() -> None:
    """
    One-way compatibility migration from the old JSON history to SQLite.

    Existing result IDs are used as the idempotency key. This keeps the old
    result pages usable while moving dashboard/run history into SQLite.
    """
    history = load_history()
    if not history:
        return

    projects = get_projects()
    project_map = {row["name"]: int(row["id"]) for row in projects}

    default_project_id: int | None = None
    if projects:
        default_project_id = int(projects[0]["id"])
    else:
        default_project_id = create_project(
            "Default Project",
            "Automatically created during DataGuard migration.",
        )
        project_map["Default Project"] = default_project_id

    existing = {
        row["result_id"]
        for row in get_runs(10000)
        if row["result_id"]
    }

    for item in history:
        result_id = str(item.get("run_id") or "").strip()
        if not result_id or result_id in existing:
            continue

        project_name = str(item.get("project") or "Default Project").strip()
        project_id = project_map.get(project_name, default_project_id)

        run_type = str(item.get("type") or "Data Quality")
        dataset = item.get("dataset")
        score = item.get("score")

        save_run(
            project_id=project_id,
            run_type=run_type,
            dataset_name=dataset if run_type == "Data Quality" else None,
            source_name=(
                str(dataset).split(" -> ", 1)[0]
                if run_type == "ETL Validation" and dataset
                else None
            ),
            target_name=(
                str(dataset).split(" -> ", 1)[1]
                if run_type == "ETL Validation" and dataset and " -> " in str(dataset)
                else None
            ),
            primary_key=item.get("primary_key"),
            score=float(score) if score is not None else None,
            status=str(item.get("status") or "FAIL"),
            passed=int(item.get("passed") or 0),
            failed=int(item.get("failed") or 0),
            report_path=item.get("report"),
            result_id=result_id,
        )
        existing.add(result_id)


def write_result_csv(path: Path, results: list[dict[str, str]]) -> None:
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
    user=None,
) -> Path:
    rendered = page(title, body, active, user)
    result_path = RESULT_ROOT / f"{run_id}.html"
    result_path.write_text(rendered, encoding="utf-8")
    return result_path


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
            check_name = f"{check.strip()} | {column.strip()}"

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


def project_options(owner_user_id: int, selected_id: int | None = None, workspace_id: int | None = None) -> str:
    projects = get_projects(owner_user_id, workspace_id=workspace_id)
    if not projects:
        return '<option value="">No projects available</option>'

    options = []
    for project in projects:
        selected = " selected" if selected_id == int(project["id"]) else ""
        options.append(
            f'<option value="{int(project["id"])}"{selected} data-primary-key="{esc(project["default_primary_key"] or "")}">'
            f'{esc(project["name"])}'
            "</option>"
        )
    return "".join(options)


def get_default_project_id(owner_user_id: int, workspace_id: int | None = None) -> int | None:
    projects = get_projects(owner_user_id, workspace_id=workspace_id)
    return int(projects[0]["id"]) if projects else None


def render_history_table(runs: list) -> str:
    if not runs:
        return """
        <div class="empty">
          No runs yet. Run a Data Quality or ETL validation to create your first history entry.
        </div>
        """

    rows = []
    for item in runs[:20]:
        status = item["status"] or "FAIL"
        status_class = "pass" if status == "PASS" else "fail"
        score = item["score"]
        score_text = f"{float(score):.2f}%" if score is not None else "N/A"
        result_id = item["result_id"] or ""
        dataset = (
            item["dataset_name"]
            if item["run_type"] == "Data Quality"
            else f"{item['source_name'] or ''} -> {item['target_name'] or ''}"
        )

        rows.append(
            f"""
            <tr class="row-{status_class}">
              <td>{esc(item["created_at"] or "")}</td>
              <td><strong>{esc(item["run_type"] or "")}</strong></td>
              <td>{esc(item["project_name"] or "Unassigned")}</td>
              <td>{esc(dataset)}</td>
              <td>{score_text}</td>
              <td>{status_badge(status)}</td>
              <td>
                <a class="small-link" href="/runs/{esc(result_id)}">View</a>
              </td>
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
            <th>Project</th>
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


def build_app_url(request: Request, path: str) -> str:
    base = APP_BASE_URL or str(request.base_url).rstrip("/")
    return f"{base}{path}"


def send_resend_email(to_email: str, subject: str, html_body: str) -> None:
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured.")
    if not FROM_EMAIL:
        raise RuntimeError("DATAGUARD_FROM_EMAIL is not configured.")

    payload = json.dumps({
        "from": f"{FROM_NAME} <{FROM_EMAIL}>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "DataGuard/1.8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Resend returned HTTP {response.status}.")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"Resend email failed: HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Resend: {exc.reason}") from exc


def send_workspace_invitation_email(
    request: Request,
    recipient: str,
    workspace_name: str,
    role: str,
    token: str,
) -> str:
    invite_url = build_app_url(request, f"/invitations/{token}")
    subject = f"You are invited to join {workspace_name} on DataGuard"
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;padding:32px;color:#172033">
      <h1 style="margin:0 0 12px">You have been invited to DataGuard</h1>
      <p style="font-size:16px;line-height:1.6">You have been invited to join <strong>{esc(workspace_name)}</strong> as a <strong>{esc(role)}</strong>.</p>
      <p style="margin:28px 0"><a href="{esc(invite_url)}" style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:13px 20px;border-radius:8px;font-weight:700">Accept Invitation</a></p>
      <p style="font-size:13px;color:#667085;line-height:1.6">This invitation expires in 7 days. Sign in using <strong>{esc(recipient)}</strong> before accepting it.</p>
      <p style="font-size:12px;color:#98a2b3;word-break:break-all">{esc(invite_url)}</p>
    </div>
    """
    if EMAIL_PROVIDER != "resend":
        raise RuntimeError(f"Unsupported email provider: {EMAIL_PROVIDER or 'none'}. v1.8 supports resend.")
    send_resend_email(recipient, subject, html_body)
    return invite_url


def page(title: str, body: str, active: str = "Dashboard", user=None) -> str:
    nav_items = [
        ("Dashboard", "/"),
        ("Projects", "/projects"),
        ("Data Quality", "/#quality"),
        ("ETL Validation", "/#etl"),
        ("Workspace", "/workspace"),
    ] if user else []

    nav_html = "".join(
        f'<a class="nav-link {"active" if active == name else ""}" href="{url}">{name}</a>'
        for name, url in nav_items
    )
    if user:
        nav_html += (
            f'<span class="nav-spacer"></span>'
            f'<span class="nav-user">{esc(user["email"])}</span>'
            f'<form action="/logout" method="post" class="nav-logout"><button type="submit" class="link-button">Sign out</button></form>'
        )
    else:
        nav_html = '<span class="nav-spacer"></span><a class="nav-link" href="/login">Sign in</a><a class="nav-link" href="/register">Create account</a>'


    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} | DataGuard</title>
<style>
:root {{
  --bg:#f4f7fb; --card:#ffffff; --text:#172033; --muted:#667085;
  --border:#e4e7ec; --primary:#2563eb; --primary-dark:#1d4ed8;
  --success:#087443; --success-bg:#ecfdf3; --danger:#b42318;
  --danger-bg:#fef3f2; --warning:#b54708; --warning-bg:#fffaeb;
  --shadow:0 8px 24px rgba(16,24,40,.06);
}}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{ margin:0; font-family:Inter,Segoe UI,Arial,sans-serif; background:var(--bg); color:var(--text); }}
a {{ color:inherit; }}
.container {{ max-width:1180px; margin:0 auto; padding:28px 20px 64px; }}
.header {{ display:flex; justify-content:space-between; align-items:center; gap:20px; margin-bottom:22px; }}
.brand {{ display:flex; align-items:center; gap:11px; }}
.logo {{ font-size:29px; font-weight:850; letter-spacing:-.8px; }}
.logo-mark {{ width:34px; height:34px; border-radius:10px; display:grid; place-items:center; color:white; background:linear-gradient(135deg,#2563eb,#7c3aed); font-weight:900; }}
.badge {{ background:#eaf1ff; color:var(--primary); padding:7px 12px; border-radius:999px; font-size:13px; font-weight:750; }}
.nav {{ display:flex; gap:6px; padding:6px; border:1px solid var(--border); background:#fff; border-radius:12px; margin-bottom:30px; box-shadow:0 2px 8px rgba(16,24,40,.03); }}
.nav-spacer {{ flex:1; }}
.nav-user {{ color:var(--muted); font-size:13px; font-weight:700; padding:0 8px; white-space:nowrap; }}
.nav-logout {{ margin:0; }}
.link-button {{ border:0; background:transparent; color:var(--primary); font-weight:750; cursor:pointer; padding:10px 8px; font-size:14px; }}
.auth-card {{ max-width:520px; margin:60px auto 0; }}
.auth-meta {{ color:var(--muted); font-size:14px; margin-top:8px; }}
.nav-link {{ text-decoration:none; padding:10px 14px; border-radius:8px; color:var(--muted); font-weight:700; font-size:14px; }}
.nav-link:hover,.nav-link.active {{ background:#eef4ff; color:var(--primary); }}
.hero {{ margin:8px 0 28px; }}
.hero h1 {{ font-size:40px; line-height:1.1; margin:0 0 9px; letter-spacing:-1px; }}
.hero p {{ color:var(--muted); font-size:17px; margin:0; max-width:780px; }}
.section-title {{ display:flex; justify-content:space-between; align-items:end; gap:15px; margin:30px 0 14px; }}
.section-title h2 {{ margin:0; font-size:22px; }}
.section-title p {{ margin:0; color:var(--muted); font-size:14px; }}
.score-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }}
.score-card {{ background:var(--card); border:1px solid var(--border); border-radius:18px; padding:23px; text-decoration:none; box-shadow:var(--shadow); }}
.score-card-top {{ display:flex; justify-content:space-between; color:var(--muted); font-weight:750; }}
.mini-arrow {{ color:var(--primary); font-size:20px; }}
.score {{ font-size:42px; font-weight:850; letter-spacing:-1px; margin:12px 0 2px; }}
.score-state {{ display:inline-block; font-size:12px; font-weight:850; border-radius:999px; padding:5px 9px; }}
.score-state.pass {{ background:var(--success-bg); color:var(--success); }}
.score-state.fail {{ background:var(--danger-bg); color:var(--danger); }}
.mini-stats {{ display:flex; gap:18px; margin-top:18px; color:var(--muted); font-size:13px; }}
.mini-stats strong {{ color:var(--text); }}
.grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px; }}
.card {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:24px; box-shadow:var(--shadow); }}
.card h2 {{ margin:0 0 7px; font-size:20px; }}
.card-subtitle {{ color:var(--muted); margin:0 0 18px; font-size:14px; }}
.full {{ grid-column:1/-1; }}
label {{ display:block; font-weight:750; margin:14px 0 7px; }}
input[type=file], input[type=text], input[type=email], input[readonly], select {{ width:100%; padding:12px; border:1px solid #cfd4dc; border-radius:9px; background:#fff; font:inherit; }}
input:focus,select:focus {{ outline:3px solid #dbeafe; border-color:#60a5fa; }}
button,.button {{ display:inline-block; border:0; border-radius:9px; padding:12px 18px; background:var(--primary); color:white; font-weight:750; cursor:pointer; text-decoration:none; font:inherit; }}
button:hover,.button:hover {{ background:var(--primary-dark); }}
.secondary {{ background:#eef2f6; color:var(--text); }}
.secondary:hover {{ background:#e2e8f0; }}
.help {{ color:var(--muted); font-size:13px; margin-top:6px; line-height:1.5; }}
.notice {{ padding:14px 16px; border-radius:10px; margin-bottom:18px; background:var(--warning-bg); color:var(--warning); }}
.notice.ok {{ background:var(--success-bg); color:var(--success); }}
.notice.error {{ background:var(--danger-bg); color:var(--danger); }}
.result-summary {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:18px 0; }}
.metric {{ border:1px solid var(--border); border-radius:12px; padding:15px; background:#fbfcfe; }}
.metric .label {{ color:var(--muted); font-size:12px; font-weight:750; }}
.metric .value {{ font-size:24px; font-weight:850; margin-top:5px; }}
.results {{ overflow:auto; border:1px solid var(--border); border-radius:12px; }}
table {{ width:100%; border-collapse:collapse; min-width:720px; }}
th,td {{ padding:13px 14px; text-align:left; border-bottom:1px solid var(--border); font-size:14px; vertical-align:top; }}
th {{ background:#f8fafc; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
tr:last-child td {{ border-bottom:0; }}
.status {{ display:inline-block; min-width:52px; text-align:center; padding:4px 8px; border-radius:999px; font-size:11px; font-weight:850; }}
.status.pass {{ background:var(--success-bg); color:var(--success); }}
.status.fail {{ background:var(--danger-bg); color:var(--danger); }}
.status.warn {{ background:var(--warning-bg); color:var(--warning); }}
.row-fail {{ background:#fff8f7; }}
.row-pass {{ background:#fbfffd; }}
pre {{ white-space:pre-wrap; word-break:break-word; background:#0f172a; color:#e5e7eb; border-radius:12px; padding:18px; overflow:auto; line-height:1.5; font-size:13px; }}
.footer {{ margin-top:38px; color:var(--muted); text-align:center; font-size:13px; }}
.empty {{ padding:26px; text-align:center; color:var(--muted); border:1px dashed var(--border); border-radius:12px; }}
.small-link {{ color:var(--primary); font-weight:750; text-decoration:none; }}
.small-link:hover {{ text-decoration:underline; }}
.dashboard-stats {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:14px; margin-bottom:20px; }}
.dashboard-stat {{ background:var(--card); border:1px solid var(--border); border-radius:14px; padding:18px; box-shadow:var(--shadow); }}
.dashboard-stat .number {{ font-size:28px; font-weight:850; margin-top:4px; }}
.dashboard-stat .label {{ color:var(--muted); font-size:12px; font-weight:750; }}
.action-row {{ display:flex; flex-wrap:wrap; gap:9px; margin-top:18px; }}
.project-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }}
.project-card {{ background:#fff; border:1px solid var(--border); border-radius:14px; padding:20px; box-shadow:var(--shadow); }}
.project-card h3 {{ margin:0 0 7px; }}
.project-card p {{ color:var(--muted); min-height:42px; }}
@media(max-width:900px) {{
  .dashboard-stats {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
  .project-grid {{ grid-template-columns:1fr 1fr; }}
}}
@media(max-width:800px) {{
  .score-grid,.grid {{ grid-template-columns:1fr; }}
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
  .project-grid {{ grid-template-columns:1fr; }}
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
    <div class="badge">v1.7 SaaS</div>
  </div>
  <nav class="nav">{nav_html}</nav>
  {body}
  <div class="footer">DataGuard | Data Quality & ETL Testing Platform</div>
</div>
</body>
</html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user is not None:
        return RedirectResponse("/", status_code=303)
    body = """
    <div class="auth-card card">
      <div class="hero">
        <h1>Sign in to DataGuard</h1>
        <p>Access your private projects, validation runs, and reports.</p>
      </div>
      <form action="/login" method="post">
        <label>Email</label>
        <input type="email" name="email" autocomplete="email" required>
        <label>Password</label>
        <input type="password" name="password" autocomplete="current-password" required>
        <button type="submit" style="margin-top:18px">Sign in</button>
      </form>
      <div class="auth-meta">New to DataGuard? <a class="small-link" href="/register">Create an account</a></div>
    </div>
    """
    return page("Sign in", body)


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    normalized = email.strip().lower()
    user = authenticate_user(normalized, password)
    if user is None:
        body = """
        <div class="auth-card card">
          <div class="notice error"><strong>Sign in failed.</strong> Check your email and password.</div>
          <div class="action-row"><a class="button" href="/login">Try again</a><a class="button secondary" href="/register">Create account</a></div>
        </div>
        """
        return page("Sign in failed", body)

    user_id = int(user["id"])
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, create_session(user_id), httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=60 * 60 * 24 * 7)
    workspace_id = get_default_workspace_id(user_id)
    if workspace_id:
        response.set_cookie(WORKSPACE_COOKIE, str(workspace_id), httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = get_current_user(request)
    if user is not None:
        return RedirectResponse("/", status_code=303)
    body = """
    <div class="auth-card card">
      <div class="hero">
        <h1>Create your DataGuard account</h1>
        <p>Your projects and validation history will belong to this account.</p>
      </div>
      <form action="/register" method="post">
        <label>Email</label>
        <input type="email" name="email" autocomplete="email" required>
        <label>Password</label>
        <input type="password" name="password" autocomplete="new-password" minlength="8" required>
        <label>Confirm Password</label>
        <input type="password" name="confirm_password" autocomplete="new-password" minlength="8" required>
        <button type="submit" style="margin-top:18px">Create account</button>
      </form>
      <div class="auth-meta">Already have an account? <a class="small-link" href="/login">Sign in</a></div>
    </div>
    """
    return page("Create account", body)


@app.post("/register", response_class=HTMLResponse)
async def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    if get_current_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    normalized = email.strip().lower()
    if not email_is_valid(normalized):
        return page("Registration Error", '<div class="auth-card card"><div class="notice error"><strong>Enter a valid email address.</strong></div><a class="button" href="/register">Back to registration</a></div>')
    if len(password) < 8:
        return page("Registration Error", '<div class="auth-card card"><div class="notice error"><strong>Password must be at least 8 characters.</strong></div><a class="button" href="/register">Back to registration</a></div>')
    if password != confirm_password:
        return page("Registration Error", '<div class="auth-card card"><div class="notice error"><strong>Passwords do not match.</strong></div><a class="button" href="/register">Back to registration</a></div>')
    if get_user_by_email(normalized) is not None:
        return page("Registration Error", '<div class="auth-card card"><div class="notice error"><strong>An account with this email already exists.</strong></div><div class="action-row"><a class="button" href="/login">Sign in</a><a class="button secondary" href="/register">Use another email</a></div></div>')

    try:
        user_id = create_user(normalized, password)
        claimed = claim_orphan_projects(user_id)
        workspace_id = get_default_workspace_id(user_id)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(SESSION_COOKIE, create_session(user_id), httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=60 * 60 * 24 * 7)
        if workspace_id:
            response.set_cookie(WORKSPACE_COOKIE, str(workspace_id), httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=60 * 60 * 24 * 30)
        return response
    except Exception as exc:
        return page("Registration Error", f'<div class="auth-card card"><div class="notice error"><strong>Could not create account:</strong> {esc(exc)}</div><a class="button" href="/register">Back to registration</a></div>')


@app.post("/logout")
async def logout(request: Request):
    delete_session(request.cookies.get(SESSION_COOKIE, ""))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(WORKSPACE_COOKIE)
    return response


@app.get("/workspace", response_class=HTMLResponse)
async def workspace_page(request: Request):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    user_id = int(user["id"])
    active = get_active_workspace(request, user)
    workspaces = get_workspaces(user_id)
    if active is None:
        return page("Workspace", '<div class="notice error"><strong>No workspace is available.</strong></div>', "Workspace", user)
    workspace_id = int(active["id"])
    members = get_workspace_members(workspace_id, user_id)
    invitations = get_workspace_invitations(workspace_id, user_id)
    role = str(active["member_role"] or "MEMBER").upper()
    can_manage = role in {"OWNER", "ADMIN"}
    workspace_options = "".join(
        f'<option value="{int(ws["id"])}"{" selected" if int(ws["id"]) == workspace_id else ""}>{esc(ws["name"])} ({esc(ws["member_role"])})</option>'
        for ws in workspaces
    )
    member_rows = "".join(
        f'<tr><td>{esc(member["email"])}</td><td><strong>{esc(member["role"])}</strong></td><td>{esc(member["created_at"])}</td></tr>'
        for member in members
    )
    def invitation_status(inv) -> str:
        if inv["accepted_at"]:
            return "Accepted"
        delivery = str(inv["email_status"] or "PENDING").upper()
        if delivery == "SENT":
            return "Email Sent"
        if delivery == "FAILED":
            return "Email Failed"
        return "Pending"

    invite_rows = "".join(
        f'<tr><td>{esc(inv["email"])}</td><td>{esc(inv["role"])}</td><td>{esc(invitation_status(inv))}</td></tr>'
        for inv in invitations[:20]
    )
    body = f"""
    <div class="hero">
      <h1>Workspace</h1>
      <p>Collaborate on DataGuard projects and share validation history with authorized team members.</p>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Active Workspace</h2>
        <p class="card-subtitle">Switch between workspaces you belong to.</p>
        <form action="/workspace/switch" method="post">
          <label>Workspace</label>
          <select name="workspace_id" required>{workspace_options}</select>
          <button type="submit" style="margin-top:18px">Switch Workspace</button>
        </form>
        <div class="help">Current role: <strong>{esc(role)}</strong></div>
      </div>

      <div class="card">
        <h2>Create Workspace</h2>
        <p class="card-subtitle">Create a separate workspace for another company, team, or client.</p>
        <form action="/workspace/create" method="post">
          <label>Workspace Name</label>
          <input type="text" name="name" placeholder="ABC Technologies" required>
          <button type="submit" style="margin-top:18px">Create Workspace</button>
        </form>
      </div>

      <div class="card full">
        <div class="section-title" style="margin-top:0"><div><h2>Team Members</h2><p>{len(members)} member(s) in {esc(active["name"])}</p></div></div>
        <div class="results"><table><thead><tr><th>Email</th><th>Role</th><th>Joined</th></tr></thead><tbody>{member_rows}</tbody></table></div>
      </div>

      <div class="card full">
        <h2>Invite Team Member</h2>
        <p class="card-subtitle">Invitations are valid for 7 days. v1.8 sends a secure invitation link by email through Resend.</p>
        {('<form action="/workspace/invite" method="post"><label>Email</label><input type="email" name="email" placeholder="teammate@company.com" required><label>Role</label><select name="role"><option value="MEMBER">Member</option><option value="ADMIN">Admin</option></select><button type="submit" style="margin-top:18px">Create Invitation</button></form>' if can_manage else '<div class="notice">Only Workspace Owners and Admins can invite team members.</div>')}
      </div>

      <div class="card full">
        <div class="section-title" style="margin-top:0"><div><h2>Invitations</h2><p>Recent workspace invitations.</p></div></div>
        <div class="results"><table><thead><tr><th>Email</th><th>Role</th><th>Status</th></tr></thead><tbody>{invite_rows or '<tr><td colspan="3">No invitations yet.</td></tr>'}</tbody></table></div>
      </div>
    </div>
    """
    return page("Workspace", body, "Workspace", user)


@app.post("/workspace/switch", response_class=HTMLResponse)
async def workspace_switch(request: Request, workspace_id: int = Form(...)):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    workspace = get_workspace(workspace_id, int(user["id"]))
    if workspace is None:
        return page("Workspace Error", '<div class="notice error"><strong>Workspace was not found or you are not a member.</strong></div><a class="button" href="/workspace">Back to Workspace</a>', "Workspace", user)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(WORKSPACE_COOKIE, str(workspace_id), httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


@app.post("/workspace/create", response_class=HTMLResponse)
async def workspace_create(request: Request, name: str = Form(...)):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    try:
        workspace_id = create_workspace(name, int(user["id"]))
        response = RedirectResponse("/workspace", status_code=303)
        response.set_cookie(WORKSPACE_COOKIE, str(workspace_id), httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=60 * 60 * 24 * 30)
        return response
    except Exception as exc:
        return page("Workspace Error", f'<div class="notice error"><strong>Could not create workspace:</strong> {esc(exc)}</div><a class="button" href="/workspace">Back to Workspace</a>', "Workspace", user)


@app.post("/workspace/invite", response_class=HTMLResponse)
async def workspace_invite(request: Request, email: str = Form(...), role: str = Form("MEMBER")):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    active = get_active_workspace(request, user)
    if active is None or str(active["member_role"]).upper() not in {"OWNER", "ADMIN"}:
        return page("Workspace Error", '<div class="notice error"><strong>You do not have permission to invite members.</strong></div><a class="button" href="/workspace">Back to Workspace</a>', "Workspace", user)

    try:
        invitation_id, token = create_workspace_invitation(int(active["id"]), email, role, int(user["id"]))
        invite_url = build_app_url(request, f"/invitations/{token}")
        try:
            send_workspace_invitation_email(
                request,
                email.strip().lower(),
                str(active["name"]),
                role.upper(),
                token,
            )
            mark_invitation_email_sent(invitation_id)
            delivery = '<div class="notice ok"><strong>Invitation created and email sent successfully.</strong></div>'
            status_note = 'The invitation email has been sent through Resend.'
        except Exception as email_exc:
            mark_invitation_email_failed(invitation_id, str(email_exc))
            delivery = f'<div class="notice error"><strong>Invitation created, but email delivery failed.</strong> {esc(email_exc)}</div>'
            status_note = 'You can still use the secure link below for testing. Fix the email configuration and create a new invitation before production use.'

        body = f"""
        {delivery}
        <div class="card">
          <h2>Invitation Link</h2>
          <input type="text" value="{esc(invite_url)}" readonly>
          <div class="help">{esc(status_note)}</div>
          <div class="action-row"><a class="button" href="/workspace">Back to Workspace</a></div>
        </div>
        """
        return page("Invitation Created", body, "Workspace", user)
    except Exception as exc:
        return page("Workspace Error", f'<div class="notice error"><strong>Could not create invitation:</strong> {esc(exc)}</div><a class="button" href="/workspace">Back to Workspace</a>', "Workspace", user)


@app.get("/invitations/{token}", response_class=HTMLResponse)
async def invitation_page(request: Request, token: str):
    invitation = get_invitation_by_token(token)
    if invitation is None:
        return page("Invitation Error", '<div class="notice error"><strong>This invitation is invalid or expired.</strong></div><a class="button" href="/">Go to DataGuard</a>')
    user = get_current_user(request)
    if user is None:
        body = f'<div class="card"><div class="hero"><h1>Workspace Invitation</h1><p>You have been invited to <strong>{esc(invitation["workspace_name"])}</strong> as <strong>{esc(invitation["role"])}</strong>.</p></div><div class="notice">Sign in with <strong>{esc(invitation["email"])}</strong>, then open this invitation link again.</div><a class="button" href="/login">Sign in</a></div>'
        return page("Workspace Invitation", body)
    if user["email"].strip().lower() != invitation["email"].strip().lower():
        return page("Invitation Error", f'<div class="notice error"><strong>This invitation was issued for {esc(invitation["email"])}.</strong> Sign in with that email address to accept it.</div><a class="button" href="/workspace">Back to Workspace</a>', "Workspace", user)
    body = f"""
    <div class="card auth-card">
      <div class="hero"><h1>Join Workspace</h1><p>Join <strong>{esc(invitation["workspace_name"])}</strong> as <strong>{esc(invitation["role"])}</strong>.</p></div>
      <form action="/invitations/{esc(token)}/accept" method="post"><button type="submit">Accept Invitation</button></form>
    </div>"""
    return page("Join Workspace", body, "Workspace", user)


@app.post("/invitations/{token}/accept", response_class=HTMLResponse)
async def invitation_accept(request: Request, token: str):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    try:
        workspace_id = accept_workspace_invitation(token, int(user["id"]))
        response = RedirectResponse("/workspace", status_code=303)
        response.set_cookie(WORKSPACE_COOKIE, str(workspace_id), httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=60 * 60 * 24 * 30)
        return response
    except Exception as exc:
        return page("Invitation Error", f'<div class="notice error"><strong>Could not accept invitation:</strong> {esc(exc)}</div><a class="button" href="/workspace">Back to Workspace</a>', "Workspace", user)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()

    owner_id = int(user["id"])
    workspace = get_active_workspace(request, user)
    workspace_id = int(workspace["id"]) if workspace else None
    stats = get_dashboard_stats(owner_id, workspace_id=workspace_id)
    runs = get_runs(100, owner_id, workspace_id=workspace_id)

    default_project_id = get_default_project_id(owner_id, workspace_id)
    default_project = get_project(default_project_id, owner_id, workspace_id=workspace_id) if default_project_id else None
    default_key = default_project["default_primary_key"] if default_project else "customer_id"

    body = f"""
    <div class="hero">
      <h1>Data Quality & ETL Testing</h1>
      <p>
        Validate datasets, compare source and target pipelines, organize runs by project,
        and keep a private validation history for your account.
      </p>
      <div class="notice" style="margin-top:14px;margin-bottom:0">Active workspace: <strong>{esc(workspace["name"] if workspace else "No workspace")}</strong></div>
    </div>

    <div class="dashboard-stats">
      <div class="dashboard-stat"><div class="label">Total Runs</div><div class="number">{stats["total_runs"]}</div></div>
      <div class="dashboard-stat"><div class="label">Data Quality Runs</div><div class="number">{stats["quality_runs"]}</div></div>
      <div class="dashboard-stat"><div class="label">ETL Runs</div><div class="number">{stats["etl_runs"]}</div></div>
      <div class="dashboard-stat"><div class="label">Failed Runs</div><div class="number" style="color:var(--danger)">{stats["failed_runs"]}</div></div>
      <div class="dashboard-stat"><div class="label">Projects</div><div class="number">{stats["projects"]}</div></div>
    </div>

    <div class="section-title">
      <div><h2>Run a validation</h2><p>Choose the validation workflow that matches your testing need.</p></div>
    </div>

    <div class="grid">
      <div class="card" id="quality">
        <h2>Data Quality</h2>
        <p class="card-subtitle">Profile a dataset and validate it against configurable JSON rules.</p>
        <form action="/validate" method="post" enctype="multipart/form-data">
          <label>Project</label>
          <select id="quality-project" name="project_id" required>{project_options(owner_id, default_project_id, workspace_id)}</select>

          <label>Dataset</label>
          <input type="file" name="dataset" accept=".csv,.xlsx,.parquet" required>

          <label>Rules Override</label>
          <input type="file" name="rules" accept=".json">
          <div class="help">Optional. If omitted, DataGuard uses the selected project's saved rules.</div>

          <button type="submit" style="margin-top:18px">Run Quality Validation</button>
        </form>
      </div>

      <div class="card" id="etl">
        <h2>Source vs Target ETL</h2>
        <p class="card-subtitle">Compare structure, counts, keys, records, values, and hashes.</p>
        <form action="/compare" method="post" enctype="multipart/form-data">
          <label>Project</label>
          <select id="etl-project" name="project_id" required>{project_options(owner_id, default_project_id, workspace_id)}</select>

          <label>Source Dataset</label>
          <input type="file" name="source" accept=".csv,.xlsx,.parquet" required>

          <label>Target Dataset</label>
          <input type="file" name="target" accept=".csv,.xlsx,.parquet" required>

          <label>Primary Key</label>
          <input type="text" name="primary_key" value="{esc(default_key)}" required>
          <div class="help">Defaults to the selected project's configured primary key.</div>

          <button type="submit" style="margin-top:18px">Run ETL Validation</button>
        </form>
      </div>

      <div class="card full">
        <div class="section-title" style="margin-top:0">
          <div><h2>Run History</h2><p>Your account's validation history is stored in SQLite.</p></div>
          <a class="button secondary" href="/projects">Manage Projects</a>
        </div>
        {render_history_table(runs)}
      </div>
    </div>

    <script>
      const etlProject = document.getElementById("etl-project");
      const etlPrimaryKey = document.querySelector('input[name="primary_key"]');
      if (etlProject && etlPrimaryKey) {{
        const syncPrimaryKey = () => {{
          const selected = etlProject.options[etlProject.selectedIndex];
          if (selected && selected.dataset.primaryKey) {{
            etlPrimaryKey.value = selected.dataset.primaryKey;
          }}
        }};
        etlProject.addEventListener("change", syncPrimaryKey);
      }}
    </script>
    """

    return page("Dashboard", body, "Dashboard", user)


@app.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    owner_id = int(user["id"])
    workspace = get_active_workspace(request, user)
    workspace_id = int(workspace["id"]) if workspace else None
    projects = get_projects(owner_id, workspace_id=workspace_id)

    cards = []
    for project in projects:
        rules_json = project["rules_json"] or "{}"
        try:
            rules_data = json.loads(rules_json)
            column_count = len(rules_data.get("columns", {})) if isinstance(rules_data, dict) else 0
        except json.JSONDecodeError:
            column_count = 0

        cards.append(
            f"""
            <div class="project-card">
              <h3>{esc(project["name"])}</h3>
              <p>{esc(project["description"] or "No description provided.")}</p>
              <div class="help">Primary key: <strong>{esc(project["default_primary_key"] or "Not configured")}</strong></div>
              <div class="help">Configured rule columns: <strong>{column_count}</strong></div>
              <div class="help">Created: {esc(project["created_at"])}</div>
              <div class="action-row" style="margin-top:14px">
                <a class="button secondary" href="/projects/{int(project["id"])}">Project Settings</a>
              </div>
            </div>
            """
        )

    project_cards = (
        '<div class="project-grid">' + "".join(cards) + "</div>"
        if cards
        else '<div class="empty">No projects yet. Create your first project below.</div>'
    )

    body = f"""
    <div class="hero">
      <h1>Projects</h1>
      <p>Organize validations and keep project-specific testing configuration in the active workspace.</p>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Create Project</h2>
        <p class="card-subtitle">New projects are shared with authorized members of the active workspace.</p>
        <form action="/projects/create" method="post">
          <label>Project Name</label>
          <input type="text" name="name" placeholder="Customer Data Platform" required>

          <label>Description</label>
          <input type="text" name="description" placeholder="DQ and ETL checks for customer pipelines">

          <button type="submit" style="margin-top:18px">Create Project</button>
        </form>
      </div>

      <div class="card">
        <h2>Account Isolation</h2>
        <p class="card-subtitle">Projects, runs, and result permalinks are scoped to your signed-in account.</p>
        <div class="notice ok">Your validation history is private to your account.</div>
      </div>

      <div class="card full">
        <div class="section-title" style="margin-top:0">
          <div><h2>My Projects</h2><p>{len(projects)} project(s)</p></div>
        </div>
        {project_cards}
      </div>
    </div>
    """

    return page("Projects", body, "Projects", user)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_settings_page(request: Request, project_id: int):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    owner_id = int(user["id"])
    workspace = get_active_workspace(request, user)
    workspace_id = int(workspace["id"]) if workspace else None
    project = get_project(project_id, owner_id, workspace_id=workspace_id)
    if project is None:
        return page(
            "Project Error",
            '<div class="notice error"><strong>Project was not found.</strong></div>'
            '<a class="button" href="/projects">Back to Projects</a>',
            "Projects",
            user,
        )

    body = f"""
    <div class="hero">
      <h1>Project Settings</h1>
      <p>Configure project-specific validation behavior for <strong>{esc(project["name"])}</strong>.</p>
    </div>

    <div class="card">
      <form action="/projects/{project_id}/update" method="post">
        <label>Project Name</label>
        <input type="text" name="name" value="{esc(project["name"])}" required>

        <label>Description</label>
        <input type="text" name="description" value="{esc(project["description"] or "")}">

        <label>Default Primary Key</label>
        <input type="text" name="default_primary_key" value="{esc(project["default_primary_key"] or "customer_id")}" required>
        <div class="help">Used automatically by ETL Validation unless you enter an override on the dashboard.</div>

        <label>Data Quality Rules JSON</label>
        <textarea name="rules_json" rows="24" style="width:100%;font-family:monospace;resize:vertical">{esc(project["rules_json"] or "{}")}</textarea>
        <div class="help">Edit the project's required fields, unique checks, types, ranges, allowed values, and other supported rules.</div>

        <div class="action-row" style="margin-top:18px">
          <button type="submit">Save Project Settings</button>
          <a class="button secondary" href="/projects">Back to Projects</a>
          <a class="button secondary" href="/">Back to Dashboard</a>
        </div>
      </form>
    </div>
    """
    return page("Project Settings", body, "Projects", user)


@app.post("/projects/create", response_class=HTMLResponse)
async def create_project_route(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    owner_id = int(user["id"])
    workspace = get_active_workspace(request, user)
    workspace_id = int(workspace["id"]) if workspace else None
    cleaned_name = name.strip()
    if not cleaned_name:
        return page(
            "Project Error",
            '<div class="notice error"><strong>Project name is required.</strong></div>'
            '<a class="button" href="/projects">Back to Projects</a>',
            "Projects",
            user,
        )

    try:
        if workspace_id is None:
            raise ValueError("No active workspace is available.")
        create_project(cleaned_name, description, user_id=owner_id, workspace_id=workspace_id)
        return HTMLResponse(
            page(
                "Project Created",
                f"""
                <div class="notice ok">
                  <strong>Project created successfully:</strong> {esc(cleaned_name)}
                </div>
                <div class="action-row">
                  <a class="button" href="/projects">View Projects</a>
                  <a class="button secondary" href="/">Go to Dashboard</a>
                </div>
                """,
                "Projects",
                user,
            )
        )
    except Exception as exc:
        return page(
            "Project Error",
            f"""
            <div class="notice error"><strong>Could not create project:</strong> {esc(exc)}</div>
            <a class="button" href="/projects">Back to Projects</a>
            """,
            "Projects",
            user,
        )


@app.post("/projects/{project_id}/update", response_class=HTMLResponse)
async def update_project_route(
    request: Request,
    project_id: int,
    name: str = Form(...),
    description: str = Form(""),
    default_primary_key: str = Form(...),
    rules_json: str = Form(...),
):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    owner_id = int(user["id"])
    workspace = get_active_workspace(request, user)
    workspace_id = int(workspace["id"]) if workspace else None
    project = get_project(project_id, owner_id, workspace_id=workspace_id)
    if project is None:
        return page(
            "Project Error",
            '<div class="notice error"><strong>Project was not found.</strong></div>'
            '<a class="button" href="/projects">Back to Projects</a>',
            "Projects",
            user,
        )

    cleaned_name = name.strip()
    cleaned_key = default_primary_key.strip()
    if not cleaned_name:
        return page("Project Error", '<div class="notice error"><strong>Project name is required.</strong></div><a class="button" href="/projects">Back to Projects</a>', "Projects", user)
    if not cleaned_key:
        return page("Project Error", '<div class="notice error"><strong>Default primary key is required.</strong></div><a class="button" href="/projects/%d">Back to Settings</a>' % project_id, "Projects", user)

    try:
        parsed_rules = json.loads(rules_json)
        if not isinstance(parsed_rules, dict):
            raise ValueError("Rules JSON must contain a JSON object.")
        if "columns" not in parsed_rules or not isinstance(parsed_rules["columns"], dict):
            raise ValueError("Rules JSON must contain a 'columns' object.")
        normalized_rules = json.dumps(parsed_rules, indent=4, ensure_ascii=False)

        update_project(
            project_id=project_id,
            name=cleaned_name,
            description=description,
            default_primary_key=cleaned_key,
            rules_json=normalized_rules,
            user_id=owner_id,
            workspace_id=workspace_id,
        )
        return HTMLResponse(
            page(
                "Project Updated",
                f"""
                <div class="notice ok">
                  <strong>Project settings saved:</strong> {esc(cleaned_name)}
                </div>
                <div class="action-row">
                  <a class="button" href="/projects/{project_id}">Back to Project Settings</a>
                  <a class="button secondary" href="/">Go to Dashboard</a>
                </div>
                """,
                "Projects",
                user,
            )
        )
    except json.JSONDecodeError as exc:
        return page(
            "Project Settings Error",
            f"""
            <div class="notice error"><strong>Invalid JSON:</strong> {esc(exc)}</div>
            <a class="button" href="/projects/{project_id}">Back to Project Settings</a>
            """,
            "Projects",
            user,
        )
    except Exception as exc:
        return page(
            "Project Settings Error",
            f"""
            <div class="notice error"><strong>Could not save project settings:</strong> {esc(exc)}</div>
            <a class="button" href="/projects/{project_id}">Back to Project Settings</a>
            """,
            "Projects",
            user,
        )


@app.post("/validate", response_class=HTMLResponse)
async def validate(
    request: Request,
    dataset: UploadFile = File(...),
    rules: UploadFile | None = File(None),
    project_id: int = Form(...),
):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    owner_id = int(user["id"])
    workspace = get_active_workspace(request, user)
    workspace_id = int(workspace["id"]) if workspace else None
    project = get_project(project_id, owner_id, workspace_id=workspace_id)
    if project is None:
        return page(
            "Validation Error",
            '<div class="notice error"><strong>Selected project was not found.</strong></div>'
            '<a class="button" href="/">Back to Dashboard</a>',
            "Data Quality",
            user,
        )

    session = UPLOAD_ROOT / uuid.uuid4().hex
    session.mkdir(parents=True, exist_ok=True)

    try:
        dataset_path = save_upload(dataset, session)

        if rules and rules.filename:
            rules_path = save_upload(rules, session)
        else:
            rules_path = session / "project_rules.json"
            project_rules = project["rules_json"] or "{}"
            rules_path.write_text(project_rules, encoding="utf-8")

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
        write_result_csv(RESULT_ROOT / csv_name, results)

        save_run(
            project_id=project_id,
            run_type="Data Quality",
            dataset_name=dataset.filename or "dataset",
            score=score,
            status=overall,
            passed=passed,
            failed=failed,
            report_path=report_name,
            result_id=run_id,
        )

        state_class = "ok" if overall == "PASS" else "error"

        body = f"""
        <div class="hero">
          <h1>Data Quality Result</h1>
          <p>Project: <strong>{esc(project["name"])}</strong> | Dataset: {esc(dataset.filename)}</p>
        </div>

        <div class="notice {state_class}">
          <strong>{'Validation completed successfully' if overall == 'PASS' else 'Validation completed with failures'}</strong>
        </div>

        <div class="result-summary">
          <div class="metric"><div class="label">Quality Score</div><div class="value">{f"{score:.2f}%" if score is not None else "N/A"}</div></div>
          <div class="metric"><div class="label">Total Checks</div><div class="value">{len(results)}</div></div>
          <div class="metric"><div class="label">Passed</div><div class="value" style="color:var(--success)">{passed}</div></div>
          <div class="metric"><div class="label">Failed</div><div class="value" style="color:var(--danger)">{failed}</div></div>
        </div>

        <div class="card">
          <h2>Validation Results</h2>
          <p class="card-subtitle">Structured results from the DataGuard quality engine.</p>
          {render_results_table(results)}
          <div class="action-row">
            <a class="button" href="/reports/{esc(report_name)}" target="_blank">Open HTML Report</a>
            <a class="button secondary" href="/downloads/{esc(csv_name)}">Download Results CSV</a>
            <a class="button secondary" href="/runs/{esc(run_id)}">Permalink</a>
            <a class="button secondary" href="/">Back to Dashboard</a>
          </div>
        </div>

        <div class="card" style="margin-top:20px">
          <h2>Raw Engine Output</h2>
          <p class="card-subtitle">Original CLI output retained for troubleshooting.</p>
          <pre>{esc(output)}</pre>
        </div>
        """

        save_result_page(run_id, "Data Quality Result", body, "Data Quality", user)
        return HTMLResponse(page("Data Quality Result", body, "Data Quality", user))

    except Exception as exc:
        return page(
            "Validation Error",
            f"""
            <div class="notice error"><strong>Validation Error:</strong> {esc(exc)}</div>
            <a class="button" href="/">Back to Dashboard</a>
            """,
            "Data Quality",
            user,
        )


@app.post("/compare", response_class=HTMLResponse)
async def compare(
    request: Request,
    source: UploadFile = File(...),
    target: UploadFile = File(...),
    primary_key: str = Form(""),
    project_id: int = Form(...),
):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    owner_id = int(user["id"])
    workspace = get_active_workspace(request, user)
    workspace_id = int(workspace["id"]) if workspace else None
    project = get_project(project_id, owner_id, workspace_id=workspace_id)
    if project is None:
        return page(
            "ETL Error",
            '<div class="notice error"><strong>Selected project was not found.</strong></div>'
            '<a class="button" href="/">Back to Dashboard</a>',
            "ETL Validation",
            user,
        )

    session = UPLOAD_ROOT / uuid.uuid4().hex
    session.mkdir(parents=True, exist_ok=True)

    try:
        source_path = save_upload(source, session)
        target_path = save_upload(target, session)

        key = primary_key.strip() or str(project["default_primary_key"] or "").strip()
        if not key:
            raise ValueError("Primary key cannot be empty. Configure a project default primary key first.")

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
        write_result_csv(RESULT_ROOT / csv_name, results)

        save_run(
            project_id=project_id,
            run_type="ETL Validation",
            source_name=source.filename or "source",
            target_name=target.filename or "target",
            primary_key=key,
            score=score,
            status=overall,
            passed=passed,
            failed=failed,
            report_path=report_name,
            result_id=run_id,
        )

        state_class = "ok" if overall == "PASS" else "error"

        body = f"""
        <div class="hero">
          <h1>ETL Validation Result</h1>
          <p>Project: <strong>{esc(project["name"])}</strong> | {esc(source.filename)} -> {esc(target.filename)}</p>
        </div>

        <div class="notice {state_class}">
          <strong>{'ETL validation completed successfully' if overall == 'PASS' else 'ETL validation completed with failures'}</strong>
        </div>

        <div class="result-summary">
          <div class="metric"><div class="label">ETL Score</div><div class="value">{f"{score:.2f}%" if score is not None else "N/A"}</div></div>
          <div class="metric"><div class="label">Total Checks</div><div class="value">{len(results)}</div></div>
          <div class="metric"><div class="label">Passed</div><div class="value" style="color:var(--success)">{passed}</div></div>
          <div class="metric"><div class="label">Failed</div><div class="value" style="color:var(--danger)">{failed}</div></div>
        </div>

        <div class="card">
          <h2>ETL Comparison Results</h2>
          <p class="card-subtitle">Source -> Target | Primary key: <strong>{esc(key)}</strong></p>
          {render_results_table(results)}
          <div class="action-row">
            <a class="button" href="/reports/{esc(report_name)}" target="_blank">Open HTML Report</a>
            <a class="button secondary" href="/downloads/{esc(csv_name)}">Download Results CSV</a>
            <a class="button secondary" href="/runs/{esc(run_id)}">Permalink</a>
            <a class="button secondary" href="/">Back to Dashboard</a>
          </div>
        </div>

        <div class="card" style="margin-top:20px">
          <h2>Raw Engine Output</h2>
          <p class="card-subtitle">Original CLI output retained for troubleshooting.</p>
          <pre>{esc(output)}</pre>
        </div>
        """

        save_result_page(run_id, "ETL Validation Result", body, "ETL Validation", user)
        return HTMLResponse(page("ETL Validation Result", body, "ETL Validation", user))

    except Exception as exc:
        return page(
            "ETL Error",
            f"""
            <div class="notice error"><strong>ETL Error:</strong> {esc(exc)}</div>
            <a class="button" href="/">Back to Dashboard</a>
            """,
            "ETL Validation",
            user,
        )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
async def get_run(request: Request, run_id: str):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        return HTMLResponse("Invalid run ID.", status_code=400)

    run = get_run_by_result_id(run_id, int(user["id"]), workspace_id=workspace_id_for_request(request, user))
    if run is None:
        return HTMLResponse("Run not found.", status_code=404)

    path = RESULT_ROOT / f"{run_id}.html"
    if not path.exists():
        return HTMLResponse("Run result page not found.", status_code=404)

    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/downloads/{filename}")
async def download_result(request: Request, filename: str):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    safe = safe_filename(filename)
    path = RESULT_ROOT / safe

    if not path.exists() or path.suffix.lower() != ".csv":
        return HTMLResponse("Download not found.", status_code=404)

    run_id = safe.split("_", 1)[0]
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        return HTMLResponse("Download not found.", status_code=404)
    if get_run_by_result_id(run_id, int(user["id"]), workspace_id=workspace_id_for_request(request, user)) is None:
        return HTMLResponse("Download not found.", status_code=404)

    return FileResponse(path, media_type="text/csv", filename=path.name)


@app.get("/reports/{filename}")
async def get_report(request: Request, filename: str):
    user = get_current_user(request)
    if user is None:
        return auth_redirect()
    safe = safe_filename(filename)
    path = REPORT_ROOT / safe

    if not path.exists() or path.suffix.lower() != ".html":
        return HTMLResponse("Report not found.", status_code=404)
    if get_run_by_report_path(safe, int(user["id"]), workspace_id=workspace_id_for_request(request, user)) is None:
        return HTMLResponse("Report not found.", status_code=404)

    return FileResponse(path, media_type="text/html")
