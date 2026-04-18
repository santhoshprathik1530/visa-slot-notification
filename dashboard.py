#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "runs.db"


def get_db_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_db() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                command TEXT NOT NULL,
                country_slug TEXT NOT NULL,
                required_city TEXT,
                alert_mode TEXT NOT NULL,
                api_status TEXT,
                alert_status TEXT,
                slots_count INTEGER NOT NULL DEFAULT 0,
                current_slots_json TEXT NOT NULL,
                changes_json TEXT NOT NULL,
                delivery_results_json TEXT NOT NULL,
                error_text TEXT,
                exit_code INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def fetch_recent_runs(limit: int = 100) -> list[sqlite3.Row]:
    initialize_db()
    with get_db_connection() as connection:
        return list(
            connection.execute(
                """
                SELECT *
                FROM runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )


def fetch_summary() -> dict[str, Any]:
    initialize_db()
    with get_db_connection() as connection:
        total_runs = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        failed_runs = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE exit_code != 0 OR alert_status = 'partial_failure'"
        ).fetchone()[0]
        last_run = connection.execute(
            "SELECT created_at, command, alert_status, api_status, slots_count FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_summary_run = connection.execute(
            """
            SELECT created_at, command, alert_status, api_status, slots_count
            FROM runs
            WHERE command = 'daily-summary'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        last_poll_run = connection.execute(
            """
            SELECT created_at, command, alert_status, api_status, slots_count
            FROM runs
            WHERE command = 'check'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        last_bangalore_slot = connection.execute(
            """
            SELECT created_at, current_slots_json
            FROM runs
            WHERE required_city = 'bangalore' AND slots_count > 0
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    return {
        "total_runs": total_runs,
        "failed_runs": failed_runs,
        "last_run": dict(last_run) if last_run else None,
        "last_summary_run": dict(last_summary_run) if last_summary_run else None,
        "last_poll_run": dict(last_poll_run) if last_poll_run else None,
        "last_bangalore_slot": dict(last_bangalore_slot) if last_bangalore_slot else None,
    }


def html_page(summary: dict[str, Any], runs: list[sqlite3.Row]) -> str:
    def row_json(value: str) -> str:
        parsed = json.loads(value)
        return escape(json.dumps(parsed, indent=2, sort_keys=True))

    last_bangalore = summary.get("last_bangalore_slot")
    bangalore_text = "Never"
    bangalore_meta = ""
    if last_bangalore:
        bangalore_slots = json.loads(last_bangalore["current_slots_json"])
        bangalore_date = bangalore_slots.get("bangalore", "unknown")
        bangalore_text = last_bangalore["created_at"]
        bangalore_meta = f"Slot date: {bangalore_date}"

    rows_html = []
    for row in runs:
        row_command = row["command"]
        row_required_city = row["required_city"] or ""
        row_type = "poll"
        if row_command == "daily-summary":
            row_type = "summary"

        rows_html.append(
            f"""
            <tr data-run-type="{escape(row_type)}">
              <td>{row["id"]}</td>
              <td class="utc-ts" data-utc="{escape(str(row["created_at"]))}">{escape(str(row["created_at"]))}</td>
              <td>{escape(str(row["command"]))}</td>
              <td>{escape(str(row_required_city or "-"))}</td>
              <td>{escape(str(row["alert_mode"]))}</td>
              <td>{row["slots_count"]}</td>
              <td>{escape(str(row["api_status"] or "-"))}</td>
              <td>{escape(str(row["alert_status"] or "-"))}</td>
              <td>{row["exit_code"]}</td>
              <td><pre>{row_json(row["delivery_results_json"])}</pre></td>
              <td><pre>{row_json(row["current_slots_json"])}</pre></td>
              <td><pre>{row_json(row["changes_json"])}</pre></td>
              <td>{escape(str(row["error_text"] or "-"))}</td>
            </tr>
            """
        )

    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Visa Slot Dashboard</title>
      <style>
        body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #0b1020; color: #e8edf7; }}
        .wrap {{ padding: 24px; }}
        .toolbar {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 16px; }}
        .toolbar label {{ font-size: 14px; color: #c7d2f7; }}
        .toolbar select {{ background: #121936; color: #e8edf7; border: 1px solid #24315f; border-radius: 8px; padding: 8px 10px; }}
        .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: #121936; border: 1px solid #24315f; border-radius: 12px; padding: 16px; }}
        .card h2 {{ margin: 0 0 8px; font-size: 14px; color: #9fb0e8; }}
        .card p {{ margin: 0; font-size: 20px; font-weight: 700; }}
        .meta {{ font-size: 12px; color: #93a3d8; margin-top: 6px; }}
        table {{ width: 100%; border-collapse: collapse; background: #121936; border: 1px solid #24315f; }}
        th, td {{ border: 1px solid #24315f; padding: 10px; vertical-align: top; text-align: left; font-size: 13px; }}
        th {{ position: sticky; top: 0; background: #172149; }}
        pre {{ margin: 0; white-space: pre-wrap; word-break: break-word; color: #d6defa; }}
        a {{ color: #8ab4ff; }}
      </style>
    </head>
    <body>
      <div class="wrap">
        <h1>Visa Slot Dashboard</h1>
        <p><a href="/api/runs">JSON API</a></p>
        <div class="toolbar">
          <label>Timezone
            <select id="timezone-select">
              <option value="Asia/Kolkata">IST</option>
              <option value="America/Chicago">CDT</option>
            </select>
          </label>
          <label>Show logs
            <select id="run-filter">
              <option value="all">All</option>
              <option value="summary">Summary only</option>
              <option value="poll">5 min alert only</option>
            </select>
          </label>
        </div>
        <div class="cards">
          <div class="card"><h2>Total Runs</h2><p>{summary["total_runs"]}</p></div>
          <div class="card"><h2>Failed Runs</h2><p>{summary["failed_runs"]}</p></div>
          <div class="card">
            <h2>Latest Summary Run</h2>
            <p class="utc-ts" data-utc="{escape(summary["last_summary_run"]["created_at"] if summary["last_summary_run"] else "")}">{escape(summary["last_summary_run"]["created_at"] if summary["last_summary_run"] else "Never")}</p>
            <div class="meta">Slots: {summary["last_summary_run"]["slots_count"] if summary["last_summary_run"] else 0}</div>
          </div>
          <div class="card">
            <h2>Latest 5 Min Alert Run</h2>
            <p class="utc-ts" data-utc="{escape(summary["last_poll_run"]["created_at"] if summary["last_poll_run"] else "")}">{escape(summary["last_poll_run"]["created_at"] if summary["last_poll_run"] else "Never")}</p>
            <div class="meta">Slots: {summary["last_poll_run"]["slots_count"] if summary["last_poll_run"] else 0}</div>
          </div>
          <div class="card">
            <h2>Last Bangalore Slot Seen</h2>
            <p class="utc-ts" data-utc="{escape(last_bangalore["created_at"] if last_bangalore else "")}">{escape(bangalore_text)}</p>
            <div class="meta">{escape(bangalore_meta or "No Bangalore slot seen yet")}</div>
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Created At</th>
              <th>Command</th>
              <th>City Filter</th>
              <th>Mode</th>
              <th>Slots</th>
              <th>API</th>
              <th>Alert</th>
              <th>Exit</th>
              <th>Delivery</th>
              <th>Current Slots</th>
              <th>Changes</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {"".join(rows_html)}
          </tbody>
        </table>
      </div>
      <script>
        const timezoneSelect = document.getElementById('timezone-select');
        const runFilter = document.getElementById('run-filter');

        function formatTimestamp(value, timezone) {{
          if (!value) return 'Never';
          const date = new Date(value);
          if (Number.isNaN(date.getTime())) return value;
          return new Intl.DateTimeFormat('en-IN', {{
            timeZone: timezone,
            year: 'numeric',
            month: 'short',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: true,
          }}).format(date) + ' ' + timezone;
        }}

        function updateTimestamps() {{
          const timezone = timezoneSelect.value;
          document.querySelectorAll('.utc-ts').forEach((node) => {{
            const raw = node.dataset.utc;
            if (!raw) return;
            node.textContent = formatTimestamp(raw, timezone);
          }});
        }}

        function applyFilter() {{
          const filter = runFilter.value;
          document.querySelectorAll('tbody tr[data-run-type]').forEach((row) => {{
            row.style.display = filter === 'all' || row.dataset.runType === filter ? '' : 'none';
          }});
        }}

        timezoneSelect.addEventListener('change', updateTimestamps);
        runFilter.addEventListener('change', applyFilter);
        updateTimestamps();
        applyFilter();
      </script>
    </body>
    </html>
    """


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/runs":
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["100"])[0])
            runs = [dict(row) for row in fetch_recent_runs(limit=limit)]
            self.respond_json({"summary": fetch_summary(), "runs": runs})
            return

        if parsed.path == "/":
            summary = fetch_summary()
            runs = fetch_recent_runs()
            html_doc = html_page(summary, runs)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_doc.encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not found")

    def log_message(self, format: str, *args: object) -> None:
        return

    def respond_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    initialize_db()
    host = "0.0.0.0"
    port = int((__import__("os")).getenv("DASHBOARD_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard listening on http://{host}:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
