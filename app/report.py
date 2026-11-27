from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from .models import CheckResult
from .scoring import DEFAULT_WEIGHTS, status_counts


def _value(value: Any) -> str:
    if isinstance(value, dict):
        return "<br>".join(f"<b>{escape(str(k))}</b>: {escape(str(v))}" for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return escape(", ".join(str(v) for v in value))
    return escape(str(value))


def _result_row(result: CheckResult) -> str:
    cls = result.status.lower()
    return (
        f"<tr class='{cls}'><td><span class='badge {cls}'>{escape(result.status)}</span></td>"
        f"<td><span class='severity {escape(result.severity.lower())}'>{escape(result.severity)}</span></td>"
        f"<td>{escape(result.name)}</td><td>{escape(result.details)}</td></tr>"
    )


def _mismatch_table(result: CheckResult) -> str:
    if not result.mismatches:
        return ""
    items = result.mismatches[:500]
    headers: list[str] = []
    for item in items:
        for key in item.keys():
            if key not in headers:
                headers.append(key)

    rows = []
    for item in items:
        cells = []
        for header in headers:
            cells.append(f"<td>{_value(item.get(header, ''))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    shown = len(items)
    total = len(result.mismatches)
    note = f"Showing {shown:,} of {total:,} issue(s)." if total > shown else f"{total:,} issue(s)."
    return (
        f"<section class='detail-section'><h3>{escape(result.name)}</h3><p class='muted'>{note}</p>"
        f"<table><thead><tr>{''.join('<th>' + escape(h.replace('_', ' ').title()) + '</th>' for h in headers)}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _score_breakdown(results: list[CheckResult]) -> str:
    rows = []
    for severity, weight in DEFAULT_WEIGHTS.items():
        if weight <= 0:
            continue
        severity_results = [r for r in results if r.severity.upper() == severity and r.status != "SKIP"]
        if not severity_results:
            continue
        passed = sum(r.status == "PASS" for r in severity_results)
        failed = sum(r.status == "FAIL" for r in severity_results)
        contribution = passed * weight
        rows.append(
            f"<tr><td>{escape(severity)}</td><td>{weight:g}</td><td>{len(severity_results)}</td>"
            f"<td>{passed}</td><td>{failed}</td><td>{contribution:g}</td></tr>"
        )
    return (
        "<section><h2>Score Breakdown</h2>"
        "<p class='muted'>The score is weighted by validation severity. "
        "Higher severity checks contribute more to the overall score.</p>"
        "<table><thead><tr><th>Severity</th><th>Weight</th><th>Total</th><th>Passed</th><th>Failed</th><th>Earned Weight</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def generate_html_report(
    output_path: str | Path,
    title: str,
    results: list[CheckResult],
    score: float,
    profile: dict[str, Any] | None = None,
    source: str | None = None,
    target: str | None = None,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    counts = status_counts(results)
    overall = "PASS" if counts["FAIL"] == 0 else "FAIL"

    profile_html = ""
    if profile:
        profile_html = (
            "<div class='cards'>"
            f"<div><b>Rows</b><span>{profile.get('row_count', 0):,}</span></div>"
            f"<div><b>Columns</b><span>{profile.get('column_count', 0):,}</span></div>"
            f"<div><b>Duplicates</b><span>{profile.get('duplicate_rows', 0):,}</span></div>"
            "</div>"
        )

    summary_cards = (
        "<div class='cards summary'>"
        f"<div><b>Total Checks</b><span>{len([r for r in results if r.status != 'SKIP']):,}</span></div>"
        f"<div><b>Passed</b><span class='pass-text'>{counts['PASS']:,}</span></div>"
        f"<div><b>Failed</b><span class='fail-text'>{counts['FAIL']:,}</span></div>"
        f"<div><b>Warnings</b><span class='warn-text'>{counts['WARN']:,}</span></div>"
        "</div>"
    )

    detail_sections = "".join(_mismatch_table(r) for r in results if r.mismatches)

    html = f"""<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{escape(title)}</title>
<style>
body{{font-family:Arial,sans-serif;background:#f5f7fb;color:#172033;margin:0;padding:32px}}
main{{max-width:1250px;margin:auto}}
h1{{margin-bottom:4px;font-size:32px}}
h2{{margin-top:32px}}
h3{{margin-bottom:6px}}
.muted{{color:#667085}}
.score{{font-size:52px;font-weight:700;margin:18px 0 6px}}
.status{{font-weight:700;font-size:18px;margin-bottom:20px}}
.PASS,.pass-text{{color:#087443}}
.FAIL,.fail-text{{color:#b42318}}
.WARN,.warn-text{{color:#b54708}}
.cards{{display:flex;gap:14px;margin:24px 0;flex-wrap:wrap}}
.cards div{{background:white;border-radius:10px;padding:18px;min-width:130px;box-shadow:0 1px 4px #0001}}
.cards span{{display:block;font-size:24px;font-weight:700;margin-top:8px}}
.summary div{{min-width:150px}}
table{{width:100%;border-collapse:collapse;background:white;margin:16px 0 30px;box-shadow:0 1px 3px #0000000a}}
th,td{{padding:11px;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:top}}
th{{background:#f9fafb}}
tr.fail{{background:#fff5f5}}
tr.pass{{background:#f6fffa}}
tr.warn{{background:#fffaeb}}
.badge,.severity{{display:inline-block;padding:4px 8px;border-radius:999px;font-size:12px;font-weight:700}}
.badge.pass{{background:#ecfdf3;color:#027a48}}
.badge.fail{{background:#fef3f2;color:#b42318}}
.badge.warn{{background:#fffaeb;color:#b54708}}
.severity.critical{{background:#fef3f2;color:#b42318}}
.severity.high{{background:#fff4e5;color:#b54708}}
.severity.medium{{background:#eff8ff;color:#175cd3}}
.severity.low{{background:#f2f4f7;color:#475467}}
.detail-section{{margin-top:28px}}
.detail-section table{{font-size:14px}}
code{{background:#eef2f6;padding:2px 5px;border-radius:4px}}
</style>
</head>
<body>
<main>
<h1>{escape(title)}</h1>
<div class='muted'>Generated {escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</div>
<div class='score'>{score:.2f}%</div>
<div class='status {overall}'>Overall: {overall}</div>
{summary_cards}
{profile_html}
<p><b>Source:</b> {escape(source or 'N/A')}<br><b>Target:</b> {escape(target or 'N/A')}</p>
{_score_breakdown(results)}
<section><h2>Validation Results</h2>
<table><thead><tr><th>Status</th><th>Severity</th><th>Check</th><th>Details</th></tr></thead>
<tbody>{''.join(_result_row(r) for r in results)}</tbody></table></section>
{detail_sections}
</main>
</body>
</html>"""
    output.write_text(html, encoding="utf-8")
    return output
