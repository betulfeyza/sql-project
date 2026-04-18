from __future__ import annotations

import html
import secrets
import sqlite3
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "kmf.db"
SETUP_PATH = BASE_DIR / "setup.sql"
HOST = "127.0.0.1"
PORT = 8000
SESSION_COOKIE = "kmf_session"
SESSIONS: dict[str, int] = {}


def ensure_database() -> None:
    initialize = not DB_PATH.exists()

    if not initialize:
        with sqlite3.connect(DB_PATH) as conn:
            existing = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='Users'"
            ).fetchone()
            initialize = existing is None

    if initialize:
        script = SETUP_PATH.read_text(encoding="utf-8")
        with sqlite3.connect(DB_PATH) as conn:
            conn.executescript(script)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def room_badge(percent: float) -> str:
    if percent >= 0.65:
        return "high"
    if percent >= 0.35:
        return "medium"
    return "low"


def h(value: object) -> str:
    return html.escape("" if value is None else str(value))


def logo_svg() -> str:
    return """
    <svg viewBox="0 0 80 80" aria-hidden="true" role="img">
      <defs>
        <linearGradient id="kmfGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#f1b25a" />
          <stop offset="100%" stop-color="#d4683f" />
        </linearGradient>
      </defs>
      <rect x="4" y="4" width="72" height="72" rx="22" fill="#17363a" />
      <path d="M40 12l5.6 13.8 14.9 1.1-11.4 9.5 3.7 14.2L40 42.4 27.2 50.6l3.7-14.2-11.4-9.5 14.9-1.1z" fill="url(#kmfGrad)" />
      <rect x="24" y="45" width="32" height="16" rx="4" fill="#fff6ea" />
      <rect x="29" y="50" width="7" height="11" rx="1.5" fill="#17363a" />
      <rect x="38" y="50" width="7" height="11" rx="1.5" fill="#17363a" />
      <rect x="47" y="50" width="4" height="11" rx="1.5" fill="#17363a" />
      <path d="M23 63h34" stroke="#fff6ea" stroke-width="3" stroke-linecap="round" />
    </svg>
    """


def sql_bool(value: str | None) -> int | None:
    if value == "1":
        return 1
    return None


def style_block() -> str:
    return """
    <style>
      :root {
        --bg: #f4efe6;
        --surface: rgba(255, 250, 241, 0.92);
        --surface-strong: #fffaf2;
        --ink: #152321;
        --muted: #60716c;
        --line: rgba(21, 35, 33, 0.10);
        --accent: #d4683f;
        --accent-strong: #9d3d2f;
        --deep: #1f5a61;
        --ok: #1f7a5d;
        --warn: #b67828;
        --danger: #a53737;
        --shadow: 0 18px 46px rgba(21, 35, 33, 0.10);
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "Segoe UI", Tahoma, sans-serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(212, 104, 63, 0.14), transparent 30%),
          radial-gradient(circle at right 12%, rgba(31, 90, 97, 0.15), transparent 28%),
          linear-gradient(180deg, #faf6ee 0%, #efe4d1 100%);
      }
      a { color: inherit; text-decoration: none; }
      .app-shell {
        width: min(1240px, calc(100% - 32px));
        margin: 24px auto 40px;
      }
      .topbar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 16px;
        padding: 18px 22px;
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 24px;
        box-shadow: var(--shadow);
        backdrop-filter: blur(10px);
      }
      .brand-link {
        display: inline-flex;
        align-items: center;
        gap: 14px;
      }
      .brand-mark {
        width: 60px;
        height: 60px;
        flex: 0 0 auto;
        filter: drop-shadow(0 12px 24px rgba(21, 35, 33, 0.18));
      }
      .brand-copy {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .brand h1 {
        margin: 4px 0 0;
        font-size: 1.5rem;
      }
      .eyebrow {
        font-size: 12px;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--deep);
      }
      .hero {
        margin-top: 18px;
        padding: 28px;
        border-radius: 28px;
        border: 1px solid var(--line);
        background:
          linear-gradient(140deg, rgba(255,255,255,0.70), rgba(255,255,255,0.28)),
          linear-gradient(120deg, rgba(212,104,63,0.18), rgba(31,90,97,0.10));
        box-shadow: var(--shadow);
      }
      .hero-grid, .grid-2, .grid-3 {
        display: grid;
        gap: 18px;
      }
      .hero-grid { grid-template-columns: 1.15fr 0.85fr; }
      .grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .card {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 24px;
        padding: 20px;
        box-shadow: 0 10px 28px rgba(21, 35, 33, 0.05);
      }
      .card h2, .card h3, .card h4, .hero h2 {
        margin-top: 0;
      }
      .muted { color: var(--muted); }
      .pill-row, .stat-row, .item-row, .list-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
      }
      .pill-row { flex-wrap: wrap; margin-top: 14px; }
      .pill {
        padding: 10px 14px;
        border: 1px solid var(--line);
        border-radius: 999px;
        background: rgba(255,255,255,0.76);
        font-size: 0.92rem;
      }
      .badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        white-space: nowrap;
        padding: 6px 10px;
        border-radius: 999px;
        font-size: 0.8rem;
        border: 1px solid transparent;
      }
      .badge.ok { color: var(--ok); background: rgba(31, 122, 93, 0.10); }
      .badge.warn { color: var(--warn); background: rgba(182, 120, 40, 0.14); }
      .badge.danger { color: var(--danger); background: rgba(165, 55, 55, 0.10); }
      .badge.info { color: var(--deep); background: rgba(31, 90, 97, 0.10); }
      .stats {
        display: grid;
        gap: 12px;
      }
      .stat-box, .list-row {
        padding: 14px 16px;
        border-radius: 18px;
        background: rgba(255,255,255,0.76);
        border: 1px solid var(--line);
      }
      .heatmap {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
        margin-top: 14px;
      }
      .room-card {
        border-radius: 22px;
        padding: 18px;
        min-height: 168px;
        color: #fff;
        position: relative;
        overflow: hidden;
      }
      .room-card.low { background: linear-gradient(140deg, #1f5a61, #12383c); }
      .room-card.medium { background: linear-gradient(140deg, #bb7b2f, #8f561e); }
      .room-card.high { background: linear-gradient(140deg, #a54534, #74232e); }
      .room-card::after {
        content: "";
        position: absolute;
        right: -22px;
        bottom: -22px;
        width: 92px;
        height: 92px;
        border-radius: 50%;
        background: rgba(255,255,255,0.12);
      }
      .room-head, .form-row, .toolbar {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
      }
      .room-tags {
        position: relative;
        z-index: 1;
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 24px;
      }
      .room-tags span {
        padding: 6px 10px;
        border: 1px solid rgba(255,255,255,0.20);
        border-radius: 999px;
        font-size: 0.78rem;
      }
      form {
        margin: 0;
      }
      label {
        display: block;
        margin-bottom: 10px;
        font-size: 0.92rem;
        color: var(--muted);
      }
      input, select, textarea, button {
        width: 100%;
        margin-top: 6px;
        border-radius: 14px;
        border: 1px solid var(--line);
        padding: 12px 14px;
        font: inherit;
        background: rgba(255,255,255,0.88);
      }
      textarea {
        min-height: 96px;
        resize: vertical;
      }
      button {
        cursor: pointer;
        color: #fff;
        background: var(--ink);
        border: none;
      }
      .button-secondary {
        background: rgba(21, 35, 33, 0.08);
        color: var(--ink);
      }
      .button-accent {
        background: linear-gradient(140deg, var(--accent), var(--accent-strong));
      }
      .button-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 12px 14px;
        border-radius: 14px;
        border: 1px solid var(--line);
        background: rgba(255,255,255,0.82);
      }
      .inline-form {
        display: inline-block;
        width: auto;
      }
      .flash {
        margin-top: 18px;
        padding: 14px 16px;
        border-radius: 18px;
        border: 1px solid var(--line);
        background: rgba(255,255,255,0.70);
      }
      .flash.error { border-color: rgba(165, 55, 55, 0.18); color: var(--danger); }
      .flash.success { border-color: rgba(31, 122, 93, 0.20); color: var(--ok); }
      .login-cards {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 18px;
        margin-top: 20px;
      }
      .small { font-size: 0.9rem; }
      .spaced { margin-top: 18px; }
      .welcome-line {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
      }
      .table-lite {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.95rem;
      }
      .table-lite th, .table-lite td {
        padding: 12px 10px;
        border-bottom: 1px solid var(--line);
        text-align: left;
        vertical-align: top;
      }
      .actions {
        display: flex;
        gap: 8px;
      }
      .actions button {
        width: auto;
        min-width: 96px;
      }
      @media (max-width: 960px) {
        .hero-grid, .grid-2, .grid-3, .login-cards, .heatmap {
          grid-template-columns: 1fr;
        }
        .topbar, .toolbar, .form-row, .item-row {
          flex-direction: column;
          align-items: stretch;
        }
      }
    </style>
    """


def render_layout(title: str, content: str, user: sqlite3.Row | None = None) -> str:
    home_link = "/dashboard" if user is not None else "/"
    user_html = ""
    if user is not None:
        user_html = f"""
        <div class="toolbar">
          <div>
            <div class="small muted">Signed in as</div>
            <strong>{h(user["name"])}</strong>
            <div class="small muted">{h(user["role"])} • {h(user["email"])}</div>
            <div class="small muted">Click the logo anytime to return to your home screen.</div>
          </div>
          <form method="post" action="/logout" class="inline-form">
            <button class="button-secondary" type="submit">Sign out</button>
          </form>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{h(title)}</title>
    {style_block()}
  </head>
  <body>
    <main class="app-shell">
      <section class="topbar">
        <a class="brand-link" href="{home_link}" title="Return to home">
          <div class="brand-mark">{logo_svg()}</div>
          <div class="brand-copy brand">
            <div class="eyebrow">YTU Mathematical Engineering • Applied SQL</div>
            <h1>KMF Smart Classroom & Event Management System</h1>
            <div class="small muted">Yildiz-inspired classroom intelligence dashboard</div>
          </div>
        </a>
        {user_html or '<div class="muted small">SQLite-backed demo application</div>'}
      </section>
      {content}
    </main>
  </body>
</html>
"""


def occupancy_percentage(row: sqlite3.Row) -> float:
    capacity = row["capacity"] or 1
    occupancy = row["occupancy_count"] or 0
    return occupancy / capacity


def parse_post_data(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length).decode("utf-8")
    parsed = parse_qs(raw)
    return {key: values[0] for key, values in parsed.items()}


def get_current_user(handler: BaseHTTPRequestHandler) -> sqlite3.Row | None:
    cookie_header = handler.headers.get("Cookie")
    if not cookie_header:
        return None

    jar = cookies.SimpleCookie()
    jar.load(cookie_header)
    session_cookie = jar.get(SESSION_COOKIE)
    if session_cookie is None:
        return None

    user_id = SESSIONS.get(session_cookie.value)
    if not user_id:
        return None

    with get_connection() as conn:
        return conn.execute(
            """
            SELECT u.user_id, u.name, u.email, u.role, d.department_name
            FROM Users u
            JOIN Departments d ON d.department_id = u.department_id
            WHERE u.user_id = ?
            """,
            (user_id,),
        ).fetchone()


def login_page(message: str = "", error: bool = False) -> str:
    with get_connection() as conn:
        students = conn.execute(
            """
            SELECT user_id, name, email
            FROM Users
            WHERE role = 'Student'
            ORDER BY name
            """
        ).fetchall()
        academics = conn.execute(
            """
            SELECT user_id, name, email
            FROM Users
            WHERE role = 'Academic'
            ORDER BY name
            """
        ).fetchall()

    flash = ""
    if message:
        flash_class = "error" if error else "success"
        flash = f'<div class="flash {flash_class}">{h(message)}</div>'

    student_options = "".join(
        f'<option value="{h(row["email"])}">{h(row["name"])} - {h(row["email"])}</option>'
        for row in students
    )
    academic_options = "".join(
        f'<option value="{h(row["email"])}">{h(row["name"])} - {h(row["email"])}</option>'
        for row in academics
    )

    content = f"""
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Course Project Demo</div>
          <h2>Database-driven campus planning experience</h2>
          <p class="muted">
            This interface is connected directly to the SQLite database prepared for the Applied SQL assignment in Mathematical Engineering.
            Students explore live room availability, while academics coordinate schedules, exams, and request approvals from the same data source.
          </p>
          <div class="pill-row">
            <div class="pill">3NF schema</div>
            <div class="pill">Foreign key integrity</div>
            <div class="pill">Trigger-based conflict detection</div>
            <div class="pill">Role-based dashboards</div>
          </div>
          {flash}
        </div>
        <div class="card">
          <h3>Demo Login</h3>
          <p class="muted small">Passwords are intentionally omitted for classroom demonstration. Select a seeded user and continue.</p>
          <div class="login-cards">
            <form method="post" action="/login" class="card">
              <h4>Student Login</h4>
              <input type="hidden" name="role" value="Student">
              <label>Email
                <select name="email" required>
                  <option value="">Choose a student</option>
                  {student_options}
                </select>
              </label>
              <button class="button-accent" type="submit">Open Student Dashboard</button>
            </form>
            <form method="post" action="/login" class="card">
              <h4>Academic Login</h4>
              <input type="hidden" name="role" value="Academic">
              <label>Email
                <select name="email" required>
                  <option value="">Choose an academic</option>
                  {academic_options}
                </select>
              </label>
              <button type="submit">Open Academic Dashboard</button>
            </form>
          </div>
        </div>
      </div>
    </section>
    """
    return render_layout("KMF Login", content)


def student_dashboard(user: sqlite3.Row, params: dict[str, list[str]], message: str = "", error: bool = False) -> str:
    projector = sql_bool(params.get("projector", [""])[0])
    smart_board = sql_bool(params.get("smart_board", [""])[0])
    min_outlets = params.get("min_outlets", [""])[0]
    block = params.get("block", [""])[0]

    conditions = ["1=1"]
    values: list[object] = []

    if projector is not None:
        conditions.append("projector = ?")
        values.append(projector)
    if smart_board is not None:
        conditions.append("smart_board = ?")
        values.append(smart_board)
    if min_outlets:
        conditions.append("power_outlets >= ?")
        values.append(int(min_outlets))
    if block:
        conditions.append("block = ?")
        values.append(block)

    query = f"""
        SELECT *
        FROM v_student_live_status
        WHERE {" AND ".join(conditions)}
        ORDER BY block, floor, room_code
    """

    with get_connection() as conn:
        rooms = conn.execute(query, values).fetchall()
        blocks = conn.execute(
            "SELECT DISTINCT block FROM Classrooms WHERE is_active = 1 ORDER BY block"
        ).fetchall()
        requests = conn.execute(
            """
            SELECT er.request_id, er.event_title, er.event_type, er.status,
                   er.requested_start, er.requested_end, c.room_code
            FROM Event_Requests er
            JOIN Classrooms c ON c.room_id = er.room_id
            WHERE er.requester_id = ?
            ORDER BY datetime(er.requested_start) DESC
            """,
            (user["user_id"],),
        ).fetchall()
        room_options = conn.execute(
            "SELECT room_id, room_code FROM Classrooms WHERE is_active = 1 ORDER BY room_code"
        ).fetchall()

    pending_count = sum(1 for row in requests if row["status"] == "Pending")
    approved_count = sum(1 for row in requests if row["status"] == "Approved")
    available_now = sum(1 for row in rooms if row["live_status"] in ("Available", "Reserved"))

    flash = ""
    if message:
        flash_class = "error" if error else "success"
        flash = f'<div class="flash {flash_class}">{h(message)}</div>'

    room_cards = []
    for row in rooms:
        ratio = occupancy_percentage(row)
        percent = round(ratio * 100)
        tag_class = room_badge(ratio)
        status_text = row["live_status"] or "Unknown"
        room_cards.append(
            f"""
            <div class="room-card {tag_class}">
              <div class="room-head">
                <strong>{h(row["room_code"])}</strong>
                <span>{percent}% full</span>
              </div>
              <div class="small">Block {h(row["block"])} • Floor {h(row["floor"])} • {h(row["capacity"])} seats</div>
              <div class="room-tags">
                <span>Status: {h(status_text)}</span>
                <span>Projector: {"Yes" if row["projector"] else "No"}</span>
                <span>Outlets: {h(row["power_outlets"])}</span>
                <span>Smart board: {"Yes" if row["smart_board"] else "No"}</span>
              </div>
            </div>
            """
        )

    if not room_cards:
        room_cards.append(
            '<div class="card"><h3>No room matches the current filter.</h3><p class="muted">Try removing one or more constraints.</p></div>'
        )

    request_rows = "".join(
        f"""
        <tr>
          <td>{h(row["event_title"])}</td>
          <td>{h(row["event_type"])}</td>
          <td>{h(row["room_code"])}</td>
          <td>{h(row["requested_start"])}</td>
          <td><span class="badge {'ok' if row['status'] == 'Approved' else 'warn' if row['status'] == 'Pending' else 'danger'}">{h(row["status"])}</span></td>
        </tr>
        """
        for row in requests
    ) or '<tr><td colspan="5" class="muted">No reservation request has been created by this student yet.</td></tr>'

    room_select = "".join(
        f'<option value="{h(room["room_id"])}">{h(room["room_code"])}</option>'
        for room in room_options
    )
    block_options = "".join(
        f'<option value="{h(row["block"])}" {"selected" if row["block"] == block else ""}>{h(row["block"])}</option>'
        for row in blocks
    )

    content = f"""
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Student Dashboard</div>
          <div class="welcome-line">
            <h2>Welcome back, {h(user["name"].split()[0])}</h2>
            <span class="badge info">{h(user["department_name"])}</span>
          </div>
          <p class="muted">
            Find the right room in a few seconds. The interface stays tightly connected to SQLite, so filters, live room cards, and reservation history all reflect current database values.
          </p>
          <p class="muted">
            The live map is powered by `v_student_live_status`, which hides sensitive academic details and exposes only what students need for room discovery.
          </p>
          <div class="pill-row">
            <div class="pill">Live availability</div>
            <div class="pill">Heatmap occupancy</div>
            <div class="pill">Smart equipment filter</div>
          </div>
          {flash}
        </div>
        <div class="card">
          <h3>Quick Filter</h3>
          <div class="stats spaced">
            <div class="stat-box">
              <strong>{len(rooms)}</strong>
              <div class="small muted">visible rooms after current filters</div>
            </div>
            <div class="stat-box">
              <strong>{available_now}</strong>
              <div class="small muted">rooms currently calm or ready to use</div>
            </div>
            <div class="stat-box">
              <strong>{approved_count}</strong>
              <div class="small muted">approved requests for this student</div>
            </div>
          </div>
          <div class="spaced"></div>
          <form method="get" action="/dashboard">
            <label>Block
              <select name="block">
                <option value="">All blocks</option>
                {block_options}
              </select>
            </label>
            <label>Minimum power outlets
              <select name="min_outlets">
                <option value="">No preference</option>
                <option value="10" {"selected" if min_outlets == "10" else ""}>10+</option>
                <option value="20" {"selected" if min_outlets == "20" else ""}>20+</option>
                <option value="40" {"selected" if min_outlets == "40" else ""}>40+</option>
              </select>
            </label>
            <label>Projector
              <select name="projector">
                <option value="">Any</option>
                <option value="1" {"selected" if params.get("projector", [""])[0] == "1" else ""}>Required</option>
              </select>
            </label>
            <label>Smart board
              <select name="smart_board">
                <option value="">Any</option>
                <option value="1" {"selected" if params.get("smart_board", [""])[0] == "1" else ""}>Required</option>
              </select>
            </label>
            <div class="grid-2">
              <button class="button-accent" type="submit">Apply Filters</button>
              <a class="button-link" href="/dashboard">Reset Filters</a>
            </div>
          </form>
        </div>
      </div>
    </section>

    <section class="grid-2 spaced">
      <article class="card">
        <h3>Live Room Heatmap</h3>
        <div class="heatmap">
          {"".join(room_cards)}
        </div>
      </article>

      <aside class="card">
        <h3>Create Reservation Request</h3>
        <p class="muted small">Students submit requests as `Pending`. Approval is controlled by academics and validated by database triggers, so frontend actions stay consistent with backend rules.</p>
        <div class="pill-row">
          <div class="pill">Pending: {pending_count}</div>
          <div class="pill">Approved: {approved_count}</div>
        </div>
        <form method="post" action="/requests/new">
          <label>Room
            <select name="room_id" required>
              {room_select}
            </select>
          </label>
          <label>Event title
            <input type="text" name="event_title" required>
          </label>
          <label>Event type
            <select name="event_type" required>
              <option value="Workshop">Workshop</option>
              <option value="Club">Club</option>
              <option value="Makeup">Makeup</option>
              <option value="Exam">Exam</option>
              <option value="Seminar">Seminar</option>
            </select>
          </label>
          <label>Start time
            <input type="datetime-local" name="requested_start" required>
          </label>
          <label>End time
            <input type="datetime-local" name="requested_end" required>
          </label>
          <label>Request note
            <textarea name="request_note" placeholder="Need projector and 20+ power outlets"></textarea>
          </label>
          <button type="submit">Submit Request</button>
        </form>
      </aside>
    </section>

    <section class="card spaced">
      <h3>My Requests</h3>
      <table class="table-lite">
        <thead>
          <tr>
            <th>Title</th>
            <th>Type</th>
            <th>Room</th>
            <th>Start</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>{request_rows}</tbody>
      </table>
    </section>
    """
    return render_layout("Student Dashboard", content, user)


def academic_dashboard(user: sqlite3.Row, message: str = "", error: bool = False) -> str:
    with get_connection() as conn:
        my_schedule = conn.execute(
            """
            SELECT s.schedule_id, s.title, s.schedule_type, s.start_at, s.end_at, c.room_code
            FROM Academic_Schedules s
            JOIN Classrooms c ON c.room_id = s.room_id
            WHERE s.academic_id = ?
            ORDER BY datetime(s.start_at)
            """,
            (user["user_id"],),
        ).fetchall()
        coordination = conn.execute(
            """
            SELECT title, room_code, prior_week_occupancy_rate, overlapping_event_requests, start_at
            FROM v_exam_coordination
            ORDER BY datetime(start_at)
            """
        ).fetchall()
        pending = conn.execute(
            """
            SELECT er.request_id, er.event_title, er.event_type, er.requested_start, er.requested_end,
                   c.room_code, u.name AS requester_name
            FROM Event_Requests er
            JOIN Classrooms c ON c.room_id = er.room_id
            JOIN Users u ON u.user_id = er.requester_id
            WHERE er.status = 'Pending'
            ORDER BY datetime(er.requested_start)
            """
        ).fetchall()
        conflict_rows = conn.execute(
            """
            SELECT er.event_title, c.room_code, s.title AS schedule_title, er.requested_start, er.requested_end
            FROM Event_Requests er
            JOIN Academic_Schedules s
              ON s.room_id = er.room_id
             AND datetime(er.requested_start) < datetime(s.end_at)
             AND datetime(er.requested_end) > datetime(s.start_at)
            JOIN Classrooms c ON c.room_id = er.room_id
            WHERE er.status IN ('Pending', 'Approved')
            ORDER BY datetime(er.requested_start)
            """
        ).fetchall()

    flash = ""
    if message:
        flash_class = "error" if error else "success"
        flash = f'<div class="flash {flash_class}">{h(message)}</div>'

    schedule_rows = "".join(
        f"""
        <div class="list-row">
          <div>
            <strong>{h(row["title"])}</strong>
            <div class="small muted">{h(row["schedule_type"])} • {h(row["room_code"])}</div>
          </div>
          <span class="badge info">{h(row["start_at"])}</span>
        </div>
        """
        for row in my_schedule
    ) or '<div class="list-row muted">No schedule assigned to this academic yet.</div>'

    coordination_rows = "".join(
        f"""
        <div class="stat-box">
          <div class="stat-row">
            <strong>{h(row["title"])}</strong>
            <span class="badge {'ok' if row['overlapping_event_requests'] == 0 else 'warn'}">{h(row["room_code"])}</span>
          </div>
          <div class="small muted">Occupancy trend: {h(row["prior_week_occupancy_rate"])}% • Overlapping requests: {h(row["overlapping_event_requests"])}</div>
        </div>
        """
        for row in coordination
    ) or '<div class="list-row muted">No exam records are available.</div>'

    pending_rows = []
    for row in pending:
        pending_rows.append(
            f"""
            <tr>
              <td>{h(row["event_title"])}</td>
              <td>{h(row["requester_name"])}</td>
              <td>{h(row["room_code"])}</td>
              <td>{h(row["requested_start"])}</td>
              <td>{h(row["requested_end"])}</td>
              <td>
                <div class="actions">
                  <form method="post" action="/requests/review" class="inline-form">
                    <input type="hidden" name="request_id" value="{h(row["request_id"])}">
                    <input type="hidden" name="decision" value="Approved">
                    <button type="submit">Approve</button>
                  </form>
                  <form method="post" action="/requests/review" class="inline-form">
                    <input type="hidden" name="request_id" value="{h(row["request_id"])}">
                    <input type="hidden" name="decision" value="Rejected">
                    <button class="button-secondary" type="submit">Reject</button>
                  </form>
                </div>
              </td>
            </tr>
            """
        )

    conflict_list = "".join(
        f"""
        <div class="list-row">
          <div>
            <strong>{h(row["event_title"])}</strong>
            <div class="small muted">{h(row["room_code"])} overlaps with {h(row["schedule_title"])}</div>
          </div>
          <span class="badge danger">Conflict</span>
        </div>
        """
        for row in conflict_rows
    ) or '<div class="list-row"><div><strong>Current state</strong><div class="small muted">No active conflict is detected in pending or approved requests.</div></div><span class="badge ok">Clear</span></div>'

    content = f"""
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">Academic Dashboard</div>
          <div class="welcome-line">
            <h2>Welcome back, Dr. {h(user["name"].split()[0])}</h2>
            <span class="badge info">{h(user["department_name"])}</span>
          </div>
          <p class="muted">
            The academic dashboard combines `Academic_Schedules`, `Event_Requests`, and `v_exam_coordination` so planning decisions remain data-driven and safe.
          </p>
          <div class="pill-row">
            <div class="pill">Schedule optimizer</div>
            <div class="pill">Conflict detection</div>
            <div class="pill">Approval workflow</div>
          </div>
          {flash}
        </div>
        <div class="card">
          <h3>Conflict Logic Summary</h3>
          <p class="muted small">
            A booking is rejected when `new.start_at < existing.end_at` and `new.end_at > existing.start_at` for the same room. This rule is enforced at database level through triggers.
          </p>
          <div class="stats">
            <div class="stat-box"><strong>{len(pending)}</strong><div class="small muted">Pending requests waiting for academic review</div></div>
            <div class="stat-box"><strong>{len(coordination)}</strong><div class="small muted">Exam coordination records in analytical view</div></div>
            <div class="stat-box"><strong>{len(conflict_rows)}</strong><div class="small muted">active overlaps shown in conflict feed</div></div>
          </div>
        </div>
      </div>
    </section>

    <section class="grid-2 spaced">
      <article class="card">
        <h3>My Schedule</h3>
        <div class="stats">{schedule_rows}</div>
      </article>
      <article class="card">
        <h3>Exam Coordination</h3>
        <div class="stats">{coordination_rows}</div>
      </article>
    </section>

    <section class="grid-2 spaced">
      <article class="card">
        <h3>Pending Requests</h3>
        <table class="table-lite">
          <thead>
            <tr>
              <th>Title</th>
              <th>Requester</th>
              <th>Room</th>
              <th>Start</th>
              <th>End</th>
              <th>Decision</th>
            </tr>
          </thead>
          <tbody>
            {"".join(pending_rows) or '<tr><td colspan="6" class="muted">There is no pending request right now.</td></tr>'}
          </tbody>
        </table>
      </article>
      <aside class="card">
        <h3>Conflict Detection Feed</h3>
        <div class="stats">{conflict_list}</div>
      </aside>
    </section>
    """
    return render_layout("Academic Dashboard", content, user)


class KMFHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        ensure_database()
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        user = get_current_user(self)

        if parsed.path == "/":
            self.respond_html(login_page(params.get("message", [""])[0], params.get("error", ["0"])[0] == "1"))
            return

        if parsed.path == "/dashboard":
            if user is None:
                self.redirect("/?message=Please+sign+in+first&error=1")
                return

            message = params.get("message", [""])[0]
            error = params.get("error", ["0"])[0] == "1"
            if user["role"] == "Student":
                self.respond_html(student_dashboard(user, params, message, error))
                return
            self.respond_html(academic_dashboard(user, message, error))
            return

        self.respond_html(render_layout("Not Found", '<section class="hero"><h2>Page not found</h2></section>'), status=404)

    def do_POST(self) -> None:
        ensure_database()
        parsed = urlparse(self.path)
        form = parse_post_data(self)
        user = get_current_user(self)

        if parsed.path == "/login":
            email = form.get("email", "").strip()
            role = form.get("role", "").strip()
            with get_connection() as conn:
                db_user = conn.execute(
                    """
                    SELECT user_id, name, email, role
                    FROM Users
                    WHERE email = ? AND role = ?
                    """,
                    (email, role),
                ).fetchone()

            if db_user is None:
                self.redirect("/?message=Selected+demo+user+could+not+be+found&error=1")
                return

            session_id = secrets.token_hex(16)
            SESSIONS[session_id] = db_user["user_id"]
            cookie = cookies.SimpleCookie()
            cookie[SESSION_COOKIE] = session_id
            cookie[SESSION_COOKIE]["path"] = "/"

            self.send_response(303)
            self.send_header("Location", "/dashboard")
            self.send_header("Set-Cookie", cookie.output(header="").strip())
            self.end_headers()
            return

        if parsed.path == "/logout":
            self.clear_session_and_redirect()
            return

        if parsed.path == "/requests/new":
            if user is None or user["role"] != "Student":
                self.redirect("/?message=Only+students+can+submit+requests&error=1")
                return

            try:
                with get_connection() as conn:
                    conn.execute(
                        """
                        INSERT INTO Event_Requests (
                            requester_id, room_id, event_title, event_type,
                            requested_start, requested_end, request_note
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user["user_id"],
                            int(form["room_id"]),
                            form["event_title"],
                            form["event_type"],
                            form["requested_start"].replace("T", " ") + ":00",
                            form["requested_end"].replace("T", " ") + ":00",
                            form.get("request_note", "").strip() or None,
                        ),
                    )
                    conn.commit()
                self.redirect("/dashboard?message=Request+submitted+successfully")
            except (sqlite3.IntegrityError, sqlite3.OperationalError, KeyError, ValueError) as exc:
                self.redirect(f"/dashboard?message={quote_plus(str(exc))}&error=1")
            return

        if parsed.path == "/requests/review":
            if user is None or user["role"] != "Academic":
                self.redirect("/?message=Only+academic+users+can+review+requests&error=1")
                return

            decision = form.get("decision", "")
            if decision not in {"Approved", "Rejected"}:
                self.redirect("/dashboard?message=Unsupported+decision&error=1")
                return

            try:
                with get_connection() as conn:
                    if decision == "Approved":
                        conn.execute(
                            """
                            UPDATE Event_Requests
                            SET status = 'Approved',
                                approved_by = ?,
                                decision_at = CURRENT_TIMESTAMP,
                                rejection_reason = NULL
                            WHERE request_id = ? AND status = 'Pending'
                            """,
                            (user["user_id"], int(form["request_id"])),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE Event_Requests
                            SET status = 'Rejected',
                                approved_by = ?,
                                decision_at = CURRENT_TIMESTAMP,
                                rejection_reason = 'Rejected from academic dashboard'
                            WHERE request_id = ? AND status = 'Pending'
                            """,
                            (user["user_id"], int(form["request_id"])),
                        )
                    conn.commit()
                self.redirect(f"/dashboard?message=Request+{decision.lower()}+successfully")
            except (sqlite3.IntegrityError, sqlite3.OperationalError, KeyError, ValueError) as exc:
                self.redirect(f"/dashboard?message={quote_plus(str(exc))}&error=1")
            return

        self.respond_html(render_layout("Not Found", '<section class="hero"><h2>Page not found</h2></section>'), status=404)

    def respond_html(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def clear_session_and_redirect(self) -> None:
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            jar = cookies.SimpleCookie()
            jar.load(cookie_header)
            session_cookie = jar.get(SESSION_COOKIE)
            if session_cookie is not None:
                SESSIONS.pop(session_cookie.value, None)

        cookie = cookies.SimpleCookie()
        cookie[SESSION_COOKIE] = ""
        cookie[SESSION_COOKIE]["path"] = "/"
        cookie[SESSION_COOKIE]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", cookie.output(header="").strip())
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def run() -> None:
    ensure_database()
    server = ThreadingHTTPServer((HOST, PORT), KMFHandler)
    print(f"KMF demo app running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()
