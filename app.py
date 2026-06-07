from __future__ import annotations

import html
import hashlib
import hmac
import json
import re
import secrets
import string
import sqlite3
import time
from datetime import datetime
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
SESSION_TTL_SECONDS = 8 * 60 * 60
PASSWORD_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 180_000
DEMO_PASSWORD = "Demo123!"
LEGACY_DEMO_PASSWORD_HASH = hashlib.sha256(DEMO_PASSWORD.encode()).hexdigest()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SESSIONS: dict[str, dict[str, float | int]] = {}


def event_requests_table_sql(table_name: str = "Event_Requests") -> str:
    return f"""
    CREATE TABLE {table_name} (
        request_id INTEGER PRIMARY KEY,
        requester_id INTEGER NOT NULL,
        room_id INTEGER NOT NULL,
        event_title TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type IN ('Workshop', 'Club', 'Makeup', 'Exam', 'Seminar')),
        requested_start TEXT NOT NULL,
        requested_end TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Cancelled')),
        approved_by INTEGER,
        decision_at TEXT,
        rejection_reason TEXT,
        decision_note TEXT,
        request_note TEXT,
        FOREIGN KEY (requester_id) REFERENCES Users(user_id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,
        FOREIGN KEY (room_id) REFERENCES Classrooms(room_id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,
        FOREIGN KEY (approved_by) REFERENCES Users(user_id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,
        CHECK (datetime(requested_start) IS NOT NULL),
        CHECK (datetime(requested_end) IS NOT NULL),
        CHECK (datetime(requested_end) > datetime(requested_start)),
        CHECK (
            (status = 'Pending' AND approved_by IS NULL AND decision_at IS NULL)
            OR (status = 'Approved' AND approved_by IS NOT NULL AND decision_at IS NOT NULL)
            OR (status = 'Rejected' AND approved_by IS NOT NULL AND decision_at IS NOT NULL)
            OR (status = 'Cancelled' AND approved_by IS NULL AND decision_at IS NOT NULL)
        )
    );
    """


REQUEST_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS Request_History (
    history_id INTEGER PRIMARY KEY,
    request_id INTEGER NOT NULL,
    actor_id INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('Created', 'Updated', 'Cancelled', 'Approved', 'Rejected')),
    previous_status TEXT CHECK (previous_status IS NULL OR previous_status IN ('Pending', 'Approved', 'Rejected', 'Cancelled')),
    new_status TEXT NOT NULL CHECK (new_status IN ('Pending', 'Approved', 'Rejected', 'Cancelled')),
    action_note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES Event_Requests(request_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    FOREIGN KEY (actor_id) REFERENCES Users(user_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);
"""


def migrate_database() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        table_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'Event_Requests'"
        ).fetchone()
        if table_row is not None:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(Event_Requests)").fetchall()
            }
            table_sql = table_row["sql"] or ""
            if "Cancelled" not in table_sql or "decision_note" not in columns:
                triggers = conn.execute(
                    """
                    SELECT name, sql
                    FROM sqlite_master
                    WHERE type = 'trigger'
                      AND tbl_name = 'Event_Requests'
                      AND sql IS NOT NULL
                    """
                ).fetchall()
                indexes = conn.execute(
                    """
                    SELECT name, sql
                    FROM sqlite_master
                    WHERE type = 'index'
                      AND tbl_name = 'Event_Requests'
                      AND sql IS NOT NULL
                    """
                ).fetchall()
                views = conn.execute(
                    """
                    SELECT name, sql
                    FROM sqlite_master
                    WHERE type = 'view'
                      AND sql LIKE '%Event_Requests%'
                      AND sql IS NOT NULL
                    """
                ).fetchall()
                for view in views:
                    conn.execute(f'DROP VIEW IF EXISTS "{view["name"]}"')
                conn.execute("DROP TABLE IF EXISTS Event_Requests_new")
                conn.execute(event_requests_table_sql("Event_Requests_new"))
                decision_note_select = "decision_note" if "decision_note" in columns else "rejection_reason"
                conn.execute(
                    f"""
                    INSERT INTO Event_Requests_new (
                        request_id, requester_id, room_id, event_title, event_type,
                        requested_start, requested_end, status, approved_by, decision_at,
                        rejection_reason, decision_note, request_note
                    )
                    SELECT
                        request_id, requester_id, room_id, event_title, event_type,
                        requested_start, requested_end, status, approved_by, decision_at,
                        rejection_reason, {decision_note_select}, request_note
                    FROM Event_Requests
                    """
                )
                for trigger in triggers:
                    conn.execute(f'DROP TRIGGER IF EXISTS "{trigger["name"]}"')
                for index in indexes:
                    conn.execute(f'DROP INDEX IF EXISTS "{index["name"]}"')
                conn.execute("DROP TABLE Event_Requests")
                conn.execute("ALTER TABLE Event_Requests_new RENAME TO Event_Requests")
                for index in indexes:
                    conn.execute(index["sql"])
                for trigger in triggers:
                    conn.execute(trigger["sql"])
                for view in views:
                    conn.execute(view["sql"])

        conn.execute(REQUEST_HISTORY_SQL)
        user_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(Users)").fetchall()
        }
        if "password_hash" not in user_columns:
            conn.execute("ALTER TABLE Users ADD COLUMN password_hash TEXT")
            conn.execute(
                """
                UPDATE Users
                SET password_hash = ?
                WHERE password_hash IS NULL OR password_hash = ''
                """,
                (LEGACY_DEMO_PASSWORD_HASH,),
            )
        users_to_upgrade = conn.execute(
            """
            SELECT user_id
            FROM Users
            WHERE password_hash IS NULL
               OR password_hash = ''
               OR password_hash = ?
            """,
            (LEGACY_DEMO_PASSWORD_HASH,),
        ).fetchall()
        for row in users_to_upgrade:
            conn.execute(
                """
                UPDATE Users
                SET password_hash = ?
                WHERE user_id = ?
                """,
                (demo_password_hash_for_user(row["user_id"]), row["user_id"]),
            )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
                ON Users (email)
            """
        )
        duplicate_email_rows = conn.execute(
            """
            SELECT LOWER(email)
            FROM Users
            GROUP BY LOWER(email)
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if duplicate_email_rows is None:
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower_unique
                    ON Users (LOWER(email))
                """
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_request_history_request_created
                ON Request_History (request_id, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE VIEW IF NOT EXISTS v_room_utilization_summary AS
            SELECT
                c.room_id,
                c.room_code,
                c.block,
                c.floor,
                c.capacity,
                COALESCE(ROUND(AVG(100.0 * ul.occupancy_count / c.capacity), 2), 0) AS average_occupancy_rate,
                COUNT(ul.log_id) AS observation_count,
                (
                    SELECT x.status
                    FROM Usage_Logs x
                    WHERE x.room_id = c.room_id
                    ORDER BY datetime(x.observed_at) DESC
                    LIMIT 1
                ) AS latest_status,
                (
                    SELECT x.observed_at
                    FROM Usage_Logs x
                    WHERE x.room_id = c.room_id
                    ORDER BY datetime(x.observed_at) DESC
                    LIMIT 1
                ) AS last_observed_at
            FROM Classrooms c
            LEFT JOIN Usage_Logs ul ON ul.room_id = c.room_id
            WHERE c.is_active = 1
            GROUP BY c.room_id, c.room_code, c.block, c.floor, c.capacity
            """
        )
        history_count = conn.execute("SELECT COUNT(*) FROM Request_History").fetchone()[0]
        if history_count == 0:
            conn.execute(
                """
                INSERT INTO Request_History (
                    request_id, actor_id, action, previous_status, new_status, action_note, created_at
                )
                SELECT
                    request_id, requester_id, 'Created', NULL, 'Pending', request_note,
                    COALESCE(datetime(decision_at, '-1 minute'), CURRENT_TIMESTAMP)
                FROM Event_Requests
                """
            )
            conn.execute(
                """
                INSERT INTO Request_History (
                    request_id, actor_id, action, previous_status, new_status, action_note, created_at
                )
                SELECT
                    request_id,
                    COALESCE(approved_by, requester_id),
                    status,
                    'Pending',
                    status,
                    COALESCE(decision_note, rejection_reason),
                    COALESCE(decision_at, CURRENT_TIMESTAMP)
                FROM Event_Requests
                WHERE status IN ('Approved', 'Rejected', 'Cancelled')
                """
            )
        conn.commit()
    finally:
        conn.close()


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

    migrate_database()


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


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"{PASSWORD_ALGORITHM}${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith(f"{PASSWORD_ALGORITHM}$"):
        try:
            algorithm, iterations, salt, digest = stored_hash.split("$", 3)
            if algorithm != PASSWORD_ALGORITHM:
                return False
            candidate = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations),
            ).hex()
            return hmac.compare_digest(candidate, digest)
        except (TypeError, ValueError):
            return False

    legacy_hash = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(legacy_hash, stored_hash)


def should_upgrade_password_hash(stored_hash: str | None) -> bool:
    return not stored_hash or not stored_hash.startswith(f"{PASSWORD_ALGORITHM}$")


def demo_password_hash_for_user(user_id: int) -> str:
    return hash_password(DEMO_PASSWORD, f"kmf-demo-user-{user_id:02d}")


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def now_ts() -> float:
    return time.time()


def create_session(user_id: int) -> str:
    session_id = secrets.token_urlsafe(32)
    SESSIONS[session_id] = {
        "user_id": user_id,
        "expires_at": now_ts() + SESSION_TTL_SECONDS,
    }
    return session_id


def get_session_user_id(handler: BaseHTTPRequestHandler) -> int | None:
    cookie_header = handler.headers.get("Cookie")
    if not cookie_header:
        return None

    jar = cookies.SimpleCookie()
    jar.load(cookie_header)
    session_cookie = jar.get(SESSION_COOKIE)
    if session_cookie is None:
        return None

    session = SESSIONS.get(session_cookie.value)
    if session is None:
        return None

    expires_at = float(session.get("expires_at", 0))
    if expires_at < now_ts():
        SESSIONS.pop(session_cookie.value, None)
        return None

    session["expires_at"] = now_ts() + SESSION_TTL_SECONDS
    return int(session["user_id"])


def build_session_cookie(session_id: str) -> cookies.SimpleCookie:
    cookie = cookies.SimpleCookie()
    cookie[SESSION_COOKIE] = session_id
    cookie[SESSION_COOKIE]["path"] = "/"
    cookie[SESSION_COOKIE]["httponly"] = True
    cookie[SESSION_COOKIE]["samesite"] = "Lax"
    cookie[SESSION_COOKIE]["max-age"] = str(SESSION_TTL_SECONDS)
    return cookie


def user_initials(name: str) -> str:
    parts = [part for part in name.split() if part]
    if not parts:
        return "U"
    return "".join(part[0].upper() for part in parts[:2])


def gear_icon_svg() -> str:
    return """
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"></path>
      <path d="M19.4 15a1.8 1.8 0 0 0 .36 1.98l.05.05a2.15 2.15 0 1 1-3.04 3.04l-.05-.05A1.8 1.8 0 0 0 14.74 19a1.8 1.8 0 0 0-1.1 1.65V21a2.15 2.15 0 1 1-4.3 0v-.08A1.8 1.8 0 0 0 8.26 19a1.8 1.8 0 0 0-1.98.36l-.05.05a2.15 2.15 0 1 1-3.04-3.04l.05-.05A1.8 1.8 0 0 0 3.6 15a1.8 1.8 0 0 0-1.65-1.1H2a2.15 2.15 0 1 1 0-4.3h.08A1.8 1.8 0 0 0 3.6 8a1.8 1.8 0 0 0-.36-1.98l-.05-.05a2.15 2.15 0 1 1 3.04-3.04l.05.05A1.8 1.8 0 0 0 8.26 3.6 1.8 1.8 0 0 0 9.36 2V2a2.15 2.15 0 1 1 4.3 0v.08A1.8 1.8 0 0 0 14.74 3.6a1.8 1.8 0 0 0 1.98-.36l.05-.05a2.15 2.15 0 1 1 3.04 3.04l-.05.05A1.8 1.8 0 0 0 19.4 8c.19.52.7.94 1.65 1.1H21a2.15 2.15 0 1 1 0 4.3h-.08A1.8 1.8 0 0 0 19.4 15Z"></path>
    </svg>
    """


def password_policy_error(password: str) -> str | None:
    if len(password) < 6:
        return "Password must be at least 6 characters"
    if not any(c.isupper() for c in password):
        return "Password must contain at least one uppercase letter"
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one digit"
    if not any(c in string.punctuation for c in password):
        return "Password must contain at least one punctuation character"
    return None


LANG_COOKIE = "kmf_lang"
DEFAULT_LANGUAGE = "tr"
SUPPORTED_LANGUAGES = {"en", "tr"}
THEME_COOKIE = "kmf_theme"
DEFAULT_THEME = "light"
SUPPORTED_THEMES = {"light", "dark"}

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
       "account_role_mismatch": "This account does not match the selected login type",
       "academic_accounts_notice": "Academic accounts are created by the system administrator.",
       "choose_login_type": "Choose Login Type",
        "choose_login_description": "Please select how you want to enter the KMF Smart Classroom system.",
        "student_login": "Student Login",
        "academic_login": "Academic Login",
        "student_sign_in": "Student Sign In",
        "academic_sign_in": "Academic Sign In",
        "student_sign_in_description": "Sign in with your student account to discover classrooms and create reservation requests.",
        "academic_sign_in_description": "Sign in with your academic account to review and manage reservation requests.",
        "brand_eyebrow": "YTU Mathematical Engineering • Applied SQL",
        "brand_title": "KMF Smart Classroom & Event Management System",
        "brand_subtitle": "Yildiz-inspired classroom intelligence dashboard",
        "logo_click_hint": "Click the logo anytime to return to your home screen.",
        "signed_in_as": "Signed in as",
        "sign_out": "Sign out",
        "sqlite_note": "SQLite-backed demo application",
        "settings": "Settings",
        "open_settings": "Open settings",
        "account": "Account",
        "language": "Language",
        "theme_label": "Theme",
        "theme_light": "Light",
        "theme_dark": "Dark",
        "sign_in_title": "Sign In to Your Account",
        "sign_in_description": "Access the classroom management system for students and academics. Sign in with your email and password.",
        "no_account": "Don't have an account?",
        "sign_up_as_student": "Sign up as a student",
        "create_account_title": "Create Your Student Account",
        "create_account_description": "Join the classroom management system as a student. Fill in your details to sign up.",
        "already_account": "Already have an account?",
        "sign_in_link": "Sign in",
        "full_name": "Full Name",
        "email": "Email",
        "department": "Department",
        "password": "Password",
        "confirm_password": "Confirm Password",
        "password_requirements": "Password must include at least one uppercase letter, one digit, and one punctuation mark.",
        "select_department": "Select your department",
        "sign_up_button": "Sign Up",
        "sign_in_button": "Sign In",
        "student_dashboard": "Student Dashboard",
        "welcome_back": "Welcome back, {name}",
        "find_room_text": "Find the right room in a few seconds. The interface stays tightly connected to SQLite, so filters, live room cards, and reservation history all reflect current database values.",
        "live_map_info": "Monitor classroom occupancy and availability in real time to find the most suitable room.",
        "pill_heatmap": "Heatmap occupancy",
        "pill_smart_filter": "Smart equipment filter",
        "quick_filter": "Quick Filter",
        "visible_rooms": "visible rooms after current filters",
        "rooms_ready": "Rooms currently calm or ready to use",
        "approved_requests": "Approved requests for this student",
        "block_label": "Block",
        "floor": "Floor",
        "seats": "Seats",
        "all_blocks": "All blocks",
        "minimum_power_outlets": "Minimum power outlets",
        "no_preference": "No preference",
        "projector_label": "Projector",
        "smart_board_label": "Smart board",
        "any": "Any",
        "required": "Required",
        "apply_filters": "Apply Filters",
        "reset_filters": "Reset Filters",
        "live_room_heatmap": "Live Room Heatmap",
        "no_rooms_match": "No room matches the current filter.",
        "try_removing_constraints": "Try removing one or more constraints.",
        "create_reservation_request": "Create Reservation Request",
        "request_note_hint": "Need projector and 20+ power outlets",
        "submit_request": "Submit Request",
        "my_requests": "My Requests",
        "title": "Title",
        "type": "Type",
        "room": "Room",
        "start": "Start",
        "time": "Time",
        "status": "Status",
        "note": "Note",
        "details": "Details",
        "actions": "Actions",
        "edit": "Edit",
        "edit_request": "Edit request",
        "update_request": "Update",
        "cancel_request": "Delete",
        "confirm_update_request": "Update this reservation request?",
        "confirm_cancel_request": "Delete this reservation request?",
        "decision_note": "Decision note",
        "open_calendar": "Open calendar",
        "room_calendar": "Room Calendar",
        "week": "Week",
        "previous_week": "Previous week",
        "next_week": "Next week",
        "today": "Today",
        "close": "Close",
        "busy": "Busy",
        "selected_time": "Selected time",
        "select_time": "Select time",
        "reservation_details": "Reservation Details",
        "reserve_selected_time": "Reserve selected time",
        "calendar_busy_academic": "Academic booking",
        "calendar_busy_request": "Reservation",
        "calendar_pending": "Pending",
        "calendar_approved": "Approved",
        "calendar_unavailable_range": "Selected range contains a busy block.",
        "calendar_select_end": "Select an end time.",
        "calendar_no_selection": "No time selected",
        "event_title_placeholder": "Event title (e.g. Project Presentation)",
        "review_note": "Review note",
        "review_note_hint": "Optional approval note, required rejection reason",
        "request_history": "Request History",
        "no_history_yet": "No request history has been recorded yet.",
        "alternative_rooms": "Alternative rooms",
        "try_alternative_rooms": "Conflict detected. Try these rooms: {rooms}",
        "no_alternative_rooms": "Conflict detected. No alternative room is available for this time range.",
        "created_by": "by {actor}",
        "action_created": "Created",
        "action_updated": "Updated",
        "action_cancelled": "Cancelled",
        "action_approved": "Approved",
        "action_rejected": "Rejected",
        "percent_full": "{percent}% full",
        "no_requests_yet": "No reservation request has been created by this student yet.",
        "end": "End",
        "event_type_workshop": "Workshop",
        "event_type_club": "Club",
        "event_type_makeup": "Make-up exam",
        "date_format": "dd.mm.yyyy",
        "event_type_exam": "Exam",
        "event_type_seminar": "Seminar",
        "academic_dashboard": "Academic Panel",
        "welcome_back_dr": "Welcome back, Dr. {name}",
        "academic_description": "Manage classroom reservations, review student requests, and monitor scheduling conflicts from a single academic panel.",
        "schedule_optimizer": "Schedule Management",
        "conflict_detection": "Conflict Analysis",
        "approval_workflow": "Request Management",
        "conflict_logic_summary": "Dashboard Summary",
        "conflict_summary_text": "The system automatically detects and prevents overlapping reservations to ensure efficient classroom utilization.",
        "pending_requests_waiting": "Pending Requests",
        "exam_coordination_records": "Exam Schedules",
        "active_overlaps": "Active Conflicts",
        "my_schedule": "My Schedule",
        "exam_coordination": "Exam Coordination",
        "pending_requests_title": "Pending Requests",
        "requester": "Requester",
        "decision": "Decision",
        "approve": "Approve",
        "reject": "Reject",
        "no_pending_requests": "There is no pending request right now.",
        "conflict_detection_feed": "Conflict Detection Feed",
        "current_state": "Current state",
        "no_active_conflict": "No active conflict is detected in pending or approved requests.",
        "clear": "Clear",
        "conflict": "Conflict",
        "schedule_type_lecture": "Lecture",
        "schedule_type_exam": "Exam",
        "schedule_type_seminar": "Seminar",
        "no_schedule_assigned": "No schedule assigned to this academic yet.",
        "no_exam_records": "No exam records are available.",
        "occupancy_trend": "Occupancy trend",
        "overlapping_requests": "Overlapping requests",
        "room_utilization_snapshot": "Room Utilization Snapshot",
        "average_occupancy": "Average occupancy",
        "observations": "Observations",
        "last_seen": "Last seen",
        "overlaps_with": "{room} overlaps with {schedule}",
        "request_submitted_success": "Request submitted successfully",
        "request_updated_success": "Request updated successfully",
        "request_cancelled_success": "Request cancelled successfully",
        "only_students_submit": "Only students can submit requests",
        "only_academics_review": "Only academic users can review requests",
        "unsupported_decision": "Unsupported decision",
        "request_approved_success": "Request approved successfully",
        "request_rejected_success": "Request rejected successfully",
        "pending_request_required": "Only pending requests can be changed.",
        "rejection_reason_required": "Rejection reason is required.",
        "all_fields_required": "All fields are required",
        "email_password_required": "Email and password are required",
        "invalid_credentials": "Invalid email or password",
        "please_sign_in_first": "Please sign in first",
        "invalid_email": "Invalid email address",
        "passwords_do_not_match": "Passwords do not match",
        "password_min_length": "Password must be at least 6 characters",
        "password_uppercase": "Password must contain at least one uppercase letter",
        "password_digit": "Password must contain at least one digit",
        "password_punctuation": "Password must contain at least one punctuation character",
        "account_created": "Account created successfully. Please sign in.",
        "email_exists": "Email already exists",
        "academic_approval_required": "Only academic users can approve event requests.",
        "occupancy_capacity_error": "Occupancy cannot exceed classroom capacity.",
        "recurring_schedule_conflict": "This schedule conflicts with an existing recurring classroom booking.",
        "event_recurring_schedule_conflict": "This request conflicts with an academic schedule for the selected classroom.",
        "approved_request_conflict": "This approved request conflicts with another approved request.",
        "event_request_conflict": "This request overlaps with another pending or approved reservation for the selected classroom.",
        "request_time_invalid": "End time must be after start time.",
        "room_or_user_invalid": "Selected room or user is invalid.",
        "form_data_invalid": "Please check the form values and try again.",
        "yes": "Yes",
        "no": "No",
    },
    "tr": {
       "account_role_mismatch": "Bu hesap seçilen giriş türüyle eşleşmiyor",
       "academic_accounts_notice": "Akademik hesaplar sistem yöneticisi tarafından oluşturulur.",
       "choose_login_type": "Giriş Türünü Seç",
        "choose_login_description": "KMF Akıllı Sınıf sistemine nasıl giriş yapmak istediğinizi seçin.",
        "student_login": "Öğrenci Girişi",
        "academic_login": "Akademisyen Girişi",
        "student_sign_in": "Öğrenci Girişi",
        "academic_sign_in": "Akademisyen Girişi",
        "student_sign_in_description": "Sınıfları keşfetmek ve rezervasyon talebi oluşturmak için öğrenci hesabınızla giriş yapın.",
        "academic_sign_in_description": "Rezervasyon taleplerini incelemek ve yönetmek için akademisyen hesabınızla giriş yapın.",
        "brand_eyebrow": "YTÜ Matematik Mühendisliği • Uygulamalı SQL",
        "brand_title": "KMF Akıllı Sınıf ve Etkinlik Yönetim Sistemi",
        "brand_subtitle": "Yıldız ilhamlı sınıf zeka panosu",
        "logo_click_hint": "Ev ekranına geri dönmek için her zaman logoya tıklayabilirsiniz.",
        "signed_in_as": "Giriş yapan",
        "sign_out": "Çıkış yap",
        "sqlite_note": "SQLite destekli demo uygulama",
        "settings": "Ayarlar",
        "open_settings": "Ayarları aç",
        "account": "Hesap",
        "language": "Dil",
        "theme_label": "Tema",
        "theme_light": "Açık",
        "theme_dark": "Koyu",
        "sign_in_title": "Hesabınıza Giriş Yapın",
        "sign_in_description": "Öğrenciler ve akademisyenler için sınıf yönetim sistemine erişin. E-posta ve parolanızla giriş yapın.",
        "no_account": "Hesabın yok mu?",
        "sign_up_as_student": "Öğrenci olarak kaydol",
        "create_account_title": "Öğrenci Hesabınızı Oluşturun",
        "create_account_description": "Sınıf yönetim sistemine öğrenci olarak katılın. Kayıt olmak için bilgilerinizi doldurun.",
        "already_account": "Zaten hesabınız var mı?",
        "sign_in_link": "Giriş yap",
        "full_name": "Ad Soyad",
        "email": "E-posta",
        "department": "Bölüm",
        "password": "Parola",
        "confirm_password": "Parola Onayı",
        "password_requirements": "Parola en az bir büyük harf, bir rakam ve bir noktalama işareti içermelidir.",
        "select_department": "Bölümünüzü seçin",
        "sign_up_button": "Kayıt Ol",
        "sign_in_button": "Giriş Yap",
        "student_dashboard": "Öğrenci Paneli",
        "welcome_back": "Tekrar hoş geldin, {name}",
        "find_room_text": "Doğru sınıfı birkaç saniye içinde bulun. Arayüz SQLite ile sıkı şekilde bağlı kaldığı için filtreler, canlı sınıf kartları ve rezervasyon geçmişi güncel verileri yansıtır.",
        "live_map_info": "Canlı harita `v_student_live_status` tarafından oluşturulur; hassas akademik detayları gizler ve öğrencilere sadece gerekli oda keşfi bilgilerini sağlar.",
        "pill_live_availability": "Canlı kullanılabilirlik",
        "pill_heatmap": "Doluluk haritası",
        "pill_smart_filter": "Akıllı ekipman filtresi",
        "quick_filter": "Hızlı Filtre",
        "visible_rooms": "mevcut filtrelerden sonra görünen odalar",
        "rooms_ready": "şu anda sakin veya kullanıma hazır odalar",
        "approved_requests": "bu öğrenci için onaylanmış istekler",
        "block_label": "Blok",
        "floor": "Kat",
        "seats": "Koltuk",
        "all_blocks": "Tüm bloklar",
        "minimum_power_outlets": "Minimum priz sayısı",
        "no_preference": "Tercih yok",
        "projector_label": "Projeksiyon",
        "smart_board_label": "Akıllı tahta",
        "any": "Herhangi",
        "required": "Zorunlu",
        "apply_filters": "Filtreleri Uygula",
        "reset_filters": "Filtreleri Temizle",
        "live_room_heatmap": "Canlı Oda Haritası",
        "no_rooms_match": "Mevcut filtreye uygun oda yok.",
        "try_removing_constraints": "Bir veya daha fazla kısıtı kaldırmayı deneyin.",
        "create_reservation_request": "Rezervasyon Talebi Oluştur",
        "request_note_hint": "Projeksiyon ve 20+ priz gerekiyor",
        "submit_request": "Talebi Gönder",
        "my_requests": "Taleplerim",
        "title": "Başlık",
        "type": "Tür",
        "room": "Oda",
        "start": "Başlangıç",
        "time": "Zaman",
        "status": "Durum",
        "note": "Not",
        "details": "Detaylar",
        "actions": "İşlemler",
        "edit": "Düzenle",
        "edit_request": "Talebi düzenle",
        "update_request": "Güncelle",
        "cancel_request": "Sil",
        "confirm_update_request": "Bu rezervasyon talebini güncellemek istediğine emin misin?",
        "confirm_cancel_request": "Bu rezervasyon talebini silmek istediğine emin misin?",
        "decision_note": "Karar notu",
        "open_calendar": "Takvimi aç",
        "room_calendar": "Oda Takvimi",
        "week": "Hafta",
        "previous_week": "Önceki hafta",
        "next_week": "Sonraki hafta",
        "today": "Bugün",
        "close": "Kapat",
        "busy": "Dolu",
        "selected_time": "Seçilen zaman",
        "select_time": "Zaman seç",
        "reservation_details": "Rezervasyon Detayları",
        "reserve_selected_time": "Seçilen zamanı rezerve et",
        "calendar_busy_academic": "Akademik kullanım",
        "calendar_busy_request": "Rezervasyon",
        "calendar_pending": "Beklemede",
        "calendar_approved": "Onaylandı",
        "calendar_unavailable_range": "Seçilen aralıkta dolu blok var.",
        "calendar_select_end": "Bitiş zamanını seçin.",
        "calendar_no_selection": "Zaman seçilmedi",
        "event_title_placeholder": "Etkinlik başlığı (örn: Proje Sunumu)",
        "review_note": "İnceleme notu",
        "review_note_hint": "Onay notu isteğe bağlı, ret nedeni zorunlu",
        "request_history": "Talep Geçmişi",
        "no_history_yet": "Henüz talep geçmişi kaydı yok.",
        "alternative_rooms": "Alternatif odalar",
        "try_alternative_rooms": "Çakışma tespit edildi. Bu odaları deneyin: {rooms}",
        "no_alternative_rooms": "Çakışma tespit edildi. Bu zaman aralığı için uygun alternatif oda yok.",
        "created_by": "{actor} tarafından",
        "action_created": "Oluşturuldu",
        "action_updated": "Güncellendi",
        "action_cancelled": "İptal edildi",
        "action_approved": "Onaylandı",
        "action_rejected": "Reddedildi",
        "percent_full": "%{percent} dolu",
        "no_requests_yet": "Bu öğrenci tarafından henüz rezervasyon talebi oluşturulmadı.",
        "end": "Bitiş",
        "event_type_workshop": "Atölye",
        "event_type_club": "Kulüp",
        "event_type_makeup": "Mazaret Sınavı",
        "date_format": "gg.aa.yyyy",
        "event_type_exam": "Sınav",
        "event_type_seminar": "Seminer",
        "academic_dashboard": "Akademik Panel",
        "welcome_back_dr": "Tekrar hoş geldin, Dr. {name}",
        "academic_description": "Akademik panel üzerinden sınıf rezervasyonlarını inceleyebilir, çakışmaları kontrol edebilir ve talepleri onaylayabilirsiniz.",
        "schedule_optimizer": "Akıllı Programlama",
        "conflict_detection": "Çakışma Tespiti",
        "approval_workflow": "Onay süreci",
        "conflict_logic_summary": "Güncel Durum",
        "conflict_summary_text": "Sistem, aynı zaman dilimindeki çakışan rezervasyonları otomatik olarak tespit eder ve engeller.",
        "pending_requests_waiting": "Akademik inceleme bekleyen talepler",
        "exam_coordination_records": "Sınav Programları",
        "active_overlaps": "Çakışma beslemesinde gösterilen aktif çakışmalar",
        "my_schedule": "Programım",
        "exam_coordination": "Sınav Koordinasyonu",
        "pending_requests_title": "Bekleyen Talepler",
        "requester": "Talep Eden",
        "decision": "Karar",
        "approve": "Onayla",
        "reject": "Reddet",
        "no_pending_requests": "Şu anda bekleyen talep yok.",
        "conflict_detection_feed": "Çakışma Tespit Beslemesi",
        "current_state": "Mevcut durum",
        "no_active_conflict": "Bekleyen veya onaylanmış taleplerde aktif çakışma tespit edilmedi.",
        "clear": "Temiz",
        "conflict": "Çakışma",
        "schedule_type_lecture": "Ders",
        "schedule_type_exam": "Sınav",
        "schedule_type_seminar": "Seminer",
        "no_schedule_assigned": "Bu akademisyene atanmış program henüz yok.",
        "no_exam_records": "Sınav kaydı bulunmuyor.",
        "occupancy_trend": "Doluluk eğilimi",
        "overlapping_requests": "Çakışan talepler",
        "room_utilization_snapshot": "Oda Kullanım Özeti",
        "average_occupancy": "Ortalama doluluk",
        "observations": "gözlem",
        "last_seen": "Son görülme",
        "overlaps_with": "{room}, {schedule} ile çakışıyor",
        "request_submitted_success": "Talep başarıyla gönderildi",
        "request_updated_success": "Talep başarıyla güncellendi",
        "request_cancelled_success": "Talep başarıyla iptal edildi",
        "only_students_submit": "Sadece öğrenciler talep gönderebilir",
        "only_academics_review": "Sadece akademik kullanıcılar talepleri inceleyebilir",
        "unsupported_decision": "Desteklenmeyen karar",
        "request_approved_success": "Talep başarıyla onaylandı",
        "request_rejected_success": "Talep başarıyla reddedildi",
        "pending_request_required": "Sadece bekleyen talepler değiştirilebilir.",
        "rejection_reason_required": "Ret nedeni zorunludur.",
        "all_fields_required": "Tüm alanlar zorunludur",
        "email_password_required": "E-posta ve parola zorunludur",
        "invalid_credentials": "E-posta veya parola hatalı",
        "please_sign_in_first": "Lütfen önce giriş yapın",
        "invalid_email": "Geçersiz e-posta adresi",
        "passwords_do_not_match": "Parolalar eşleşmiyor",
        "password_min_length": "Parola en az 6 karakter olmalıdır",
        "password_uppercase": "Parola en az bir büyük harf içermelidir",
        "password_digit": "Parola en az bir rakam içermelidir",
        "password_punctuation": "Parola en az bir noktalama işareti içermelidir",
        "account_created": "Hesap başarıyla oluşturuldu. Lütfen giriş yapın.",
        "email_exists": "E-posta zaten mevcut",
        "academic_approval_required": "Etkinlik taleplerini yalnızca akademik kullanıcılar onaylayabilir.",
        "occupancy_capacity_error": "Doluluk sayısı sınıf kapasitesini aşamaz.",
        "recurring_schedule_conflict": "Bu program, seçilen sınıftaki mevcut tekrarlayan programla çakışıyor.",
        "event_recurring_schedule_conflict": "Bu talep, seçilen sınıftaki akademik programla çakışıyor.",
        "approved_request_conflict": "Bu onaylı talep başka bir onaylı taleple çakışıyor.",
        "event_request_conflict": "Bu talep, seçilen sınıftaki bekleyen veya onaylı başka bir rezervasyonla çakışıyor.",
        "request_time_invalid": "Bitiş zamanı başlangıç zamanından sonra olmalıdır.",
        "room_or_user_invalid": "Seçilen oda veya kullanıcı geçersiz.",
        "form_data_invalid": "Lütfen form değerlerini kontrol edip tekrar deneyin.",
        "yes": "Evet",
        "no": "Hayır",
    },
}


def t(key: str, lang: str) -> str:
    return TRANSLATIONS.get(lang, TRANSLATIONS[DEFAULT_LANGUAGE]).get(key, TRANSLATIONS[DEFAULT_LANGUAGE].get(key, key))


def translate_status(status: str, lang: str) -> str:
    status_map = {
        "Pending": {"en": "Pending", "tr": "Beklemede"},
        "Approved": {"en": "Approved", "tr": "Onaylandı"},
        "Rejected": {"en": "Rejected", "tr": "Reddedildi"},
        "Cancelled": {"en": "Cancelled", "tr": "İptal edildi"},
        "Available": {"en": "Available", "tr": "Mevcut"},
    "Reserved": {"en": "Reserved", "tr": "Rezerve"},
    "Occupied": {"en": "Occupied", "tr": "Dolu"},
    "Maintenance": {"en": "Maintenance", "tr": "Bakım"},
    "maintenance": {"en": "Maintenance", "tr": "Bakım"},
        "Unknown": {"en": "Unknown", "tr": "Bilinmiyor"},
    }
    return status_map.get(status, {"en": status, "tr": status}).get(lang, status)


def translate_history_action(action: str, lang: str) -> str:
    return t(f"action_{action.strip().lower()}", lang)


def yes_no(value: bool, lang: str) -> str:
    return t("yes", lang) if value else t("no", lang)


def build_language_cookie(lang: str) -> cookies.SimpleCookie:
    cookie = cookies.SimpleCookie()
    cookie[LANG_COOKIE] = lang
    cookie[LANG_COOKIE]["path"] = "/"
    cookie[LANG_COOKIE]["samesite"] = "Lax"
    cookie[LANG_COOKIE]["max-age"] = str(30 * 24 * 60 * 60)
    return cookie


def get_language(handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> str | None:
    requested = params.get("lang", [""])[0].lower()
    if requested in SUPPORTED_LANGUAGES:
        return requested
    cookie_header = handler.headers.get("Cookie")
    if cookie_header:
        jar = cookies.SimpleCookie()
        jar.load(cookie_header)
        lang_cookie = jar.get(LANG_COOKIE)
        if lang_cookie and lang_cookie.value in SUPPORTED_LANGUAGES:
            return lang_cookie.value
    return None


def get_theme(handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> str:
    requested = params.get("theme", [""])[0].lower()
    if requested in SUPPORTED_THEMES:
        return requested
    cookie_header = handler.headers.get("Cookie")
    if cookie_header:
        jar = cookies.SimpleCookie()
        jar.load(cookie_header)
        theme_cookie = jar.get(THEME_COOKIE)
        if theme_cookie and theme_cookie.value in SUPPORTED_THEMES:
            return theme_cookie.value
    return DEFAULT_THEME


def translate_event_type(event_type: str | None, lang: str) -> str:
    if not event_type:
        return ""
    key = f"event_type_{event_type.strip().lower()}"
    # fallback to raw value when translation key missing
    return TRANSLATIONS.get(lang, TRANSLATIONS[DEFAULT_LANGUAGE]).get(key, event_type)


def translate_schedule_type(schedule_type: str | None, lang: str) -> str:
    if not schedule_type:
        return ""
    key = f"schedule_type_{schedule_type.strip().lower()}"
    return TRANSLATIONS.get(lang, TRANSLATIONS[DEFAULT_LANGUAGE]).get(key, schedule_type)


EN_MONTHS = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}


def format_datetime(value: object, lang: str) -> str:
    if value is None:
        return ""
    raw = str(value)
    parsed: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return raw
    if lang == "tr":
        return f"{parsed.day:02d}.{parsed.month:02d}.{parsed.year} {parsed.hour:02d}:{parsed.minute:02d}"
    return f"{EN_MONTHS[parsed.month]} {parsed.day}, {parsed.year} {parsed.hour:02d}:{parsed.minute:02d}"


def normalize_datetime_input(value: str) -> str:
    normalized = value.strip().replace("T", " ")
    if len(normalized) == 16:
        return f"{normalized}:00"
    return normalized


def datetime_local_value(value: object) -> str:
    if value is None:
        return ""
    raw = str(value).replace(" ", "T")
    return raw[:16]


def datetime_iso_value(value: object) -> str:
    return datetime_local_value(value) + ":00" if datetime_local_value(value) else ""


def room_options_html(rooms: list[sqlite3.Row], selected_room_id: object | None = None) -> str:
    selected = "" if selected_room_id is None else str(selected_room_id)
    return "".join(
        f'<option value="{h(room["room_id"])}" {"selected" if str(room["room_id"]) == selected else ""}>{h(room["room_code"])}</option>'
        for room in rooms
    )


def event_type_options_html(selected_event_type: str, lang: str) -> str:
    event_types = ("Workshop", "Club", "Makeup", "Exam", "Seminar")
    return "".join(
        f'<option value="{h(event_type)}" {"selected" if event_type == selected_event_type else ""}>{h(translate_event_type(event_type, lang))}</option>'
        for event_type in event_types
    )


EVENT_REQUEST_CONFLICT_MESSAGE = "Event request conflicts with another pending or approved request."


def find_conflicting_event_request(
    conn: sqlite3.Connection,
    room_id: int,
    requested_start: str,
    requested_end: str,
    current_request_id: int | None = None,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT request_id, event_title, status
        FROM Event_Requests
        WHERE room_id = ?
          AND status IN ('Pending', 'Approved')
          AND (? IS NULL OR request_id <> ?)
          AND datetime(?) < datetime(requested_end)
          AND datetime(?) > datetime(requested_start)
        LIMIT 1
        """,
        (
            room_id,
            current_request_id,
            current_request_id,
            requested_start,
            requested_end,
        ),
    ).fetchone()


def find_alternative_rooms(
    conn: sqlite3.Connection,
    requested_start: str,
    requested_end: str,
    current_room_id: int | None = None,
    current_request_id: int | None = None,
    limit: int = 3,
) -> list[sqlite3.Row]:
    params = {
        "requested_start": requested_start,
        "requested_end": requested_end,
        "current_room_id": current_room_id,
        "current_request_id": current_request_id,
        "limit": limit,
    }
    return conn.execute(
        """
        SELECT c.room_id, c.room_code, c.capacity
        FROM Classrooms c
        WHERE c.is_active = 1
          AND (:current_room_id IS NULL OR c.room_id <> :current_room_id)
          AND NOT EXISTS (
            SELECT 1
            FROM Academic_Schedules s
            WHERE s.room_id = c.room_id
              AND (
                (
                  date(:requested_start) = date(s.start_at)
                  AND datetime(:requested_start) < datetime(s.end_at)
                  AND datetime(:requested_end) > datetime(s.start_at)
                )
                OR (
                  s.recurrence_pattern IN ('Weekly', 'Biweekly')
                  AND (
                    CASE strftime('%w', :requested_start)
                      WHEN '0' THEN 7
                      ELSE CAST(strftime('%w', :requested_start) AS INTEGER)
                    END
                  ) = s.weekday
                  AND date(:requested_start) >= date(s.start_at)
                  AND time(:requested_start) < time(s.end_at)
                  AND time(:requested_end) > time(s.start_at)
                  AND (
                    s.recurrence_pattern = 'Weekly'
                    OR ABS(CAST(julianday(date(:requested_start)) - julianday(date(s.start_at)) AS INTEGER)) % 14 = 0
                  )
                )
              )
          )
          AND NOT EXISTS (
            SELECT 1
            FROM Event_Requests e
            WHERE e.room_id = c.room_id
              AND e.status IN ('Pending', 'Approved')
              AND (:current_request_id IS NULL OR e.request_id <> :current_request_id)
              AND datetime(:requested_start) < datetime(e.requested_end)
              AND datetime(:requested_end) > datetime(e.requested_start)
          )
        ORDER BY c.block, c.floor, c.room_code
        LIMIT :limit
        """,
        params,
    ).fetchall()


def conflict_feedback(message: str, alternatives: list[sqlite3.Row], lang: str) -> str:
    if alternatives:
        rooms = ", ".join(row["room_code"] for row in alternatives)
        return t("try_alternative_rooms", lang).format(rooms=rooms)
    translated = translate_flash_message(message, lang)
    if translated == message:
        return t("no_alternative_rooms", lang)
    return f"{translated} {t('no_alternative_rooms', lang)}"


FLASH_MESSAGE_KEYS = {
  
    "Account created successfully. Please sign in.": "account_created",
    "All fields are required": "all_fields_required",
    "Approved event request conflicts with another approved request.": "approved_request_conflict",
    "Email already exists": "email_exists",
    "Email and password are required": "email_password_required",
    "Event request conflicts with a recurring academic schedule.": "event_recurring_schedule_conflict",
    "Event request conflicts with an academic schedule.": "event_recurring_schedule_conflict",
    EVENT_REQUEST_CONFLICT_MESSAGE: "event_request_conflict",
    "FOREIGN KEY constraint failed": "room_or_user_invalid",
    "Invalid email address": "invalid_email",
    "Invalid email or password": "invalid_credentials",
    "Occupancy count cannot exceed classroom capacity.": "occupancy_capacity_error",
    "Only Academic users can approve event requests.": "academic_approval_required",
    "Only academic users can review requests": "only_academics_review",
    "Only students can submit requests": "only_students_submit",
    "Password must be at least 6 characters": "password_min_length",
    "Password must contain at least one digit": "password_digit",
    "Password must contain at least one punctuation character": "password_punctuation",
    "Password must contain at least one uppercase letter": "password_uppercase",
    "Passwords do not match": "passwords_do_not_match",
    "Please sign in first": "please_sign_in_first",
    "Recurring schedule conflict detected for the selected classroom.": "recurring_schedule_conflict",
    "Request approved successfully": "request_approved_success",
    "Request cancelled successfully": "request_cancelled_success",
    "Request rejected successfully": "request_rejected_success",
    "Request submitted successfully": "request_submitted_success",
    "Request updated successfully": "request_updated_success",
    "Only pending requests can be changed.": "pending_request_required",
    "Rejection reason is required.": "rejection_reason_required",
    "Schedule conflict detected for the selected classroom.": "recurring_schedule_conflict",
    "Unsupported decision": "unsupported_decision",
    "CHECK constraint failed: datetime(requested_end) > datetime(requested_start)": "request_time_invalid",
    "CHECK constraint failed: datetime(end_at) > datetime(start_at)": "request_time_invalid",
}


def translate_flash_message(message: str, lang: str) -> str:
    clean_message = message.strip()
    key = FLASH_MESSAGE_KEYS.get(clean_message)
    if key is not None:
        return t(key, lang)
    if clean_message.startswith("CHECK constraint failed"):
        return t("form_data_invalid", lang)
    return clean_message


def render_flash(message: str, error: bool, lang: str) -> str:
    if not message:
        return ""
    flash_class = "error" if error else "success"
    return f'<div class="flash {flash_class}">{h(translate_flash_message(message, lang))}</div>'


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
        color-scheme: light;
        --bg: #f4efe6;
        --surface: rgba(255, 250, 241, 0.92);
        --surface-strong: #fffaf2;
        --surface-soft: rgba(255,255,255,0.76);
        --field-bg: rgba(255,255,255,0.88);
        --link-bg: rgba(255,255,255,0.82);
        --flash-bg: rgba(255,255,255,0.70);
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
        --card-shadow: 0 10px 28px rgba(21, 35, 33, 0.05);
        --brand-shadow: 0 12px 24px rgba(21, 35, 33, 0.18);
        --body-bg:
          radial-gradient(circle at top left, rgba(212, 104, 63, 0.14), transparent 30%),
          radial-gradient(circle at right 12%, rgba(31, 90, 97, 0.15), transparent 28%),
          linear-gradient(180deg, #faf6ee 0%, #efe4d1 100%);
        --hero-bg:
          linear-gradient(140deg, rgba(255,255,255,0.70), rgba(255,255,255,0.28)),
          linear-gradient(120deg, rgba(212,104,63,0.18), rgba(31,90,97,0.10));
        --primary-button-bg: #152321;
        --primary-button-text: #ffffff;
        --secondary-button-bg: rgba(21, 35, 33, 0.08);
        --room-tag-border: rgba(255,255,255,0.20);
        --room-glow: rgba(255,255,255,0.12);
      }
      html[data-theme="dark"] {
        color-scheme: dark;
        --bg: #0e1518;
        --surface: rgba(20, 31, 34, 0.92);
        --surface-strong: #18272b;
        --surface-soft: rgba(255,255,255,0.06);
        --field-bg: rgba(255,255,255,0.08);
        --link-bg: rgba(255,255,255,0.07);
        --flash-bg: rgba(255,255,255,0.06);
        --ink: #eef5f2;
        --muted: #a5b8b3;
        --line: rgba(238, 245, 242, 0.14);
        --accent: #f08a63;
        --accent-strong: #c95845;
        --deep: #86d0d5;
        --ok: #7dd6b2;
        --warn: #e2b162;
        --danger: #f18d8d;
        --shadow: 0 18px 46px rgba(0, 0, 0, 0.32);
        --card-shadow: 0 10px 28px rgba(0, 0, 0, 0.24);
        --brand-shadow: 0 12px 26px rgba(0, 0, 0, 0.36);
        --body-bg:
          radial-gradient(circle at top left, rgba(240, 138, 99, 0.16), transparent 28%),
          radial-gradient(circle at right 12%, rgba(134, 208, 213, 0.14), transparent 26%),
          linear-gradient(180deg, #10191c 0%, #0b1114 100%);
        --hero-bg:
          linear-gradient(140deg, rgba(255,255,255,0.09), rgba(255,255,255,0.03)),
          linear-gradient(120deg, rgba(240,138,99,0.12), rgba(134,208,213,0.09));
        --primary-button-bg: #eef5f2;
        --primary-button-text: #10191c;
        --secondary-button-bg: rgba(255,255,255,0.09);
        --room-tag-border: rgba(255,255,255,0.24);
        --room-glow: rgba(255,255,255,0.10);
      }
      * { box-sizing: border-box; }
      html.theme-transition,
      html.theme-transition *,
      html.theme-transition *::before,
      html.theme-transition *::after {
        transition:
          background-color 640ms ease,
          border-color 640ms ease,
          color 640ms ease,
          box-shadow 640ms ease,
          opacity 640ms ease,
          filter 640ms ease;
      }
      body {
        margin: 0;
        font-family: "Segoe UI", Tahoma, sans-serif;
        color: var(--ink);
        background: var(--body-bg);
      }
      body.scheduler-open {
        overflow: hidden;
      }
      .theme-fade-layer {
        position: fixed;
        inset: 0;
        z-index: 999;
        pointer-events: none;
        opacity: 0;
        background: #05090b;
        transition: opacity 520ms ease;
      }
      .theme-fade-layer.active {
        opacity: 0.34;
      }
      a { color: inherit; text-decoration: none; }
      .topbar-actions,
      .locale-toggle,
      .theme-toggle {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        justify-content: flex-end;
      }
      .topbar-actions {
        align-content: flex-end;
        flex-wrap: wrap;
        gap: 8px;
        position: relative;
        margin-left: auto;
      }
      .settings-menu {
        position: relative;
      }
      .settings-menu > summary {
        list-style: none;
      }
      .settings-menu > summary::-webkit-details-marker {
        display: none;
      }
      .settings-trigger {
        min-width: 220px;
        max-width: 300px;
        display: grid;
        grid-template-columns: 42px minmax(0, 1fr) 34px;
        align-items: center;
        gap: 10px;
        padding: 9px 10px;
        border: 1px solid var(--line);
        border-radius: 999px;
        background: var(--surface-soft);
        box-shadow: var(--card-shadow);
        cursor: pointer;
        transition: transform 160ms ease, box-shadow 160ms ease, background-color 160ms ease;
      }
      .settings-trigger:hover {
        transform: translateY(-1px);
        box-shadow: var(--shadow);
      }
      .profile-avatar {
        width: 42px;
        height: 42px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        color: #fff;
        background: linear-gradient(140deg, var(--deep), var(--accent));
        font-weight: 800;
        letter-spacing: 0;
        box-shadow: 0 8px 18px rgba(21, 35, 33, 0.16);
      }
      .profile-copy {
        display: grid;
        gap: 1px;
        min-width: 0;
      }
      .profile-copy strong,
      .profile-copy span {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .profile-gear {
        width: 34px;
        height: 34px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        color: var(--deep);
        background: var(--link-bg);
        box-shadow: inset 0 0 0 1px var(--line);
      }
      .profile-gear svg {
        width: 18px;
        height: 18px;
        stroke: currentColor;
      }
      .settings-trigger strong,
      .settings-user strong,
      .settings-user span {
        overflow-wrap: anywhere;
      }
      .settings-popover {
        position: absolute;
        top: calc(100% + 10px);
        right: 0;
        z-index: 20;
        width: min(360px, calc(100vw - 44px));
        display: grid;
        gap: 14px;
        padding: 16px;
        border: 1px solid var(--line);
        border-radius: 20px;
        background: var(--surface-strong);
        box-shadow: var(--shadow);
      }
      .settings-section {
        display: grid;
        gap: 8px;
      }
      .settings-user {
        display: grid;
        grid-template-columns: 46px minmax(0, 1fr);
        gap: 12px;
        align-items: center;
        padding: 12px 14px;
        border: 1px solid var(--line);
        border-radius: 16px;
        background: var(--surface-soft);
      }
      .settings-user-copy {
        min-width: 0;
      }
      .settings-user strong,
      .settings-user span {
        display: block;
      }
      .settings-popover .locale-toggle,
      .settings-popover .theme-toggle {
        justify-content: center;
        width: 100%;
      }
      .locale-toggle a {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        min-height: 34px;
        padding: 7px 10px;
        border-radius: 999px;
        color: var(--ink);
        opacity: 0.72;
      }
      .locale-toggle a.active {
        opacity: 1;
        font-weight: 700;
        background: var(--link-bg);
        box-shadow: inset 0 0 0 1px var(--line);
      }
      .locale-toggle .divider {
        color: var(--muted);
        opacity: 0.58;
        font-weight: 700;
      }
      .locale-toggle {
        gap: 2px;
        padding: 4px;
        border: 1px solid var(--line);
        border-radius: 999px;
        background: var(--surface-soft);
      }
      .flag-icon,
      .theme-icon {
        display: inline-block;
        flex: 0 0 auto;
        width: 18px;
        height: 18px;
      }
      .flag-icon {
        position: relative;
        overflow: hidden;
        border-radius: 50%;
        box-shadow: 0 0 0 1px var(--line), 0 3px 8px rgba(0, 0, 0, 0.10);
      }
      .flag-en {
        background:
          linear-gradient(0deg, transparent 0 42%, rgba(255,255,255,0.96) 42% 58%, transparent 58%),
          linear-gradient(90deg, transparent 0 42%, rgba(255,255,255,0.96) 42% 58%, transparent 58%),
          linear-gradient(35deg, transparent 0 44%, #cf3341 44% 56%, transparent 56%),
          linear-gradient(145deg, transparent 0 44%, #cf3341 44% 56%, transparent 56%),
          #24457c;
      }
      .flag-tr {
        background: #e30a17;
      }
      .flag-tr::before {
        content: "";
        position: absolute;
        top: 4px;
        left: 4px;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: #fff;
      }
      .flag-tr::after {
        content: "";
        position: absolute;
        top: 4px;
        left: 7px;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: #e30a17;
      }
      .theme-toggle {
        gap: 4px;
        padding: 4px;
        border: 1px solid var(--line);
        border-radius: 999px;
        background: var(--surface-soft);
      }
      .theme-option {
        width: auto;
        margin-top: 0;
        min-height: 34px;
        padding: 7px 11px;
        border-radius: 999px;
        border: 0;
        color: var(--muted);
        background: transparent;
        font-size: 0.84rem;
        display: inline-flex;
        align-items: center;
        gap: 7px;
      }
      .theme-option.active {
        color: var(--ink);
        background: var(--link-bg);
        box-shadow: inset 0 0 0 1px var(--line);
      }
      .theme-icon {
        position: relative;
        width: 16px;
        height: 16px;
      }
      .theme-sun {
        border-radius: 50%;
        background: #f4b94f;
        box-shadow: 0 0 0 3px rgba(244, 185, 79, 0.18);
      }
      .theme-sun::before {
        content: "";
        position: absolute;
        inset: -3px;
        border-radius: 50%;
        background:
          linear-gradient(#f4b94f, #f4b94f) center top / 2px 3px no-repeat,
          linear-gradient(#f4b94f, #f4b94f) center bottom / 2px 3px no-repeat,
          linear-gradient(90deg, #f4b94f, #f4b94f) left center / 3px 2px no-repeat,
          linear-gradient(90deg, #f4b94f, #f4b94f) right center / 3px 2px no-repeat;
      }
      .theme-moon {
        border-radius: 50%;
        background: #6f8494;
        box-shadow: inset -4px 0 0 #dce8ee;
      }
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
        min-width: 0;
      }
      .brand-mark {
        width: 60px;
        height: 60px;
        flex: 0 0 auto;
        filter: drop-shadow(var(--brand-shadow));
      }
      .brand-copy {
        display: flex;
        flex-direction: column;
        gap: 2px;
        min-width: 0;
      }
      .brand h1 {
        margin: 4px 0 0;
        font-size: 1.5rem;
        line-height: 1.15;
        overflow-wrap: anywhere;
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
        background: var(--hero-bg);
        box-shadow: var(--shadow);
      }
      .hero-grid, .grid-2, .grid-3 {
        display: grid;
        gap: 18px;
      }
      .hero-grid > *, .grid-2 > *, .grid-3 > * {
        min-width: 0;
      }
      .hero-grid { grid-template-columns: 1.15fr 0.85fr; }
      .grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .card {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 24px;
        padding: 20px;
        box-shadow: var(--card-shadow);
        min-width: 0;
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
        background: var(--surface-soft);
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
        background: var(--surface-soft);
        border: 1px solid var(--line);
      }
      .heatmap {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
        margin-top: 14px;
        max-height: clamp(430px, 62vh, 680px);
        overflow-x: hidden;
        overflow-y: auto;
        overscroll-behavior: contain;
        padding-right: 8px;
        scrollbar-color: var(--muted) transparent;
        scrollbar-gutter: stable;
        align-content: start;
      }
      .heatmap::-webkit-scrollbar {
        width: 8px;
      }
      .heatmap::-webkit-scrollbar-track {
        background: transparent;
      }
      .heatmap::-webkit-scrollbar-thumb {
        background: var(--line);
        border-radius: 999px;
      }
      .heatmap::-webkit-scrollbar-thumb:hover {
        background: var(--muted);
      }
      .room-card {
        border-radius: 22px;
        padding: 18px;
        min-height: 168px;
        color: #fff;
        position: relative;
        overflow: hidden;
      }
      .room-card-button {
        display: block;
        width: 100%;
        margin-top: 0;
        border: 0;
        text-align: left;
        font: inherit;
        cursor: pointer;
        transition: transform 160ms ease, box-shadow 160ms ease, filter 160ms ease;
      }
      .room-card-button:hover {
        transform: translateY(-2px);
        filter: brightness(1.04);
      }
      .room-card-button:focus-visible {
        outline: 3px solid rgba(134, 208, 213, 0.72);
        outline-offset: 3px;
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
        background: var(--room-glow);
      }
      .room-head, .form-row, .toolbar {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
        min-width: 0;
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
        border: 1px solid var(--room-tag-border);
        border-radius: 999px;
        font-size: 0.78rem;
      }
      .scheduler-modal[hidden] {
        display: none;
      }
      .scheduler-modal {
        position: fixed;
        inset: 0;
        z-index: 100;
        display: grid;
        place-items: center;
        padding: 18px;
      }
      .scheduler-backdrop {
        position: absolute;
        inset: 0;
        width: auto;
        margin: 0;
        padding: 0;
        border: 0;
        border-radius: 0;
        background: rgba(3, 8, 10, 0.68);
        backdrop-filter: blur(12px);
      }
      .scheduler-shell {
        position: relative;
        z-index: 1;
        width: min(1360px, calc(100vw - 28px));
        max-height: min(880px, calc(100vh - 28px));
        display: flex;
        flex-direction: column;
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 24px;
        background: var(--surface-strong);
        box-shadow: 0 28px 80px rgba(0, 0, 0, 0.34);
      }
      .scheduler-header,
      .scheduler-toolbar,
      .scheduler-side {
        border-bottom: 1px solid var(--line);
      }
      .scheduler-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 18px 20px;
      }
      .scheduler-header h2 {
        margin: 2px 0 0;
        font-size: 1.24rem;
      }
      .icon-button {
        width: 40px;
        height: 40px;
        min-width: 40px;
        margin-top: 0;
        padding: 0;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 999px;
        color: var(--ink);
        background: var(--secondary-button-bg);
      }
      .scheduler-toolbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 12px 16px;
        flex-wrap: wrap;
      }
      .scheduler-toolbar-group,
      .scheduler-legend {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
      }
      .scheduler-toolbar button {
        width: auto;
        margin-top: 0;
        min-width: 42px;
        padding: 8px 12px;
      }
      .scheduler-range {
        font-weight: 800;
      }
      .legend-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
      }
      .legend-dot.busy { background: var(--danger); }
      .legend-dot.selected { background: var(--deep); }
      .scheduler-main {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 330px;
        min-height: 0;
      }
      .scheduler-grid-wrap {
        overflow: auto;
        min-height: 0;
        max-height: calc(min(880px, 100vh - 28px) - 150px);
        background: var(--surface);
      }
      .scheduler-grid {
        display: grid;
        grid-template-columns: 70px repeat(7, minmax(112px, 1fr));
        min-width: 854px;
      }
      .calendar-cell {
        min-height: 44px;
        border-right: 1px solid var(--line);
        border-bottom: 1px solid var(--line);
        background: var(--surface);
      }
      .calendar-corner,
      .calendar-day,
      .calendar-time {
        position: sticky;
        z-index: 3;
        background: var(--surface-strong);
      }
      .calendar-corner {
        top: 0;
        left: 0;
        z-index: 5;
      }
      .calendar-day {
        top: 0;
        min-height: 62px;
        padding: 10px;
        display: grid;
        gap: 2px;
      }
      .calendar-day span {
        color: var(--muted);
        font-size: 0.78rem;
        font-weight: 700;
        text-transform: uppercase;
      }
      .calendar-day strong {
        font-size: 0.95rem;
      }
      .calendar-time {
        left: 0;
        min-height: 38px;
        padding: 9px 10px;
        color: var(--muted);
        font-size: 0.78rem;
        text-align: right;
      }
      .calendar-slot {
        width: auto;
        margin-top: 0;
        min-height: 38px;
        padding: 5px 8px;
        border: 0;
        border-right: 1px solid var(--line);
        border-bottom: 1px solid var(--line);
        border-radius: 0;
        color: var(--ink);
        background: var(--surface);
        text-align: left;
      }
      button.calendar-slot:hover {
        background: var(--surface-soft);
      }
      .calendar-slot.busy {
        display: grid;
        align-content: start;
        gap: 2px;
        color: var(--danger);
        background: rgba(165, 55, 55, 0.11);
        box-shadow: inset 3px 0 0 var(--danger);
      }
      html[data-theme="dark"] .calendar-slot.busy {
        background: rgba(241, 141, 141, 0.12);
      }
      .calendar-slot.busy strong {
        font-size: 0.75rem;
        overflow-wrap: anywhere;
      }
      .calendar-slot.busy span {
        color: var(--muted);
        font-size: 0.72rem;
      }
      .calendar-slot.selected {
        color: var(--ink);
        background: rgba(31, 90, 97, 0.16);
        box-shadow: inset 0 0 0 2px var(--deep);
      }
      .scheduler-side {
        border-bottom: 0;
        border-left: 1px solid var(--line);
        padding: 18px;
        overflow-y: auto;
        background: var(--surface-strong);
      }
      .scheduler-side h3 {
        margin: 0 0 14px;
      }
      .selected-time-box {
        padding: 12px 14px;
        border: 1px solid var(--line);
        border-radius: 16px;
        background: var(--surface-soft);
        margin-bottom: 14px;
      }
      .selected-time-box strong {
        display: block;
        margin-bottom: 4px;
      }
      .scheduler-side button:disabled {
        cursor: not-allowed;
        opacity: 0.48;
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
        color: var(--ink);
        background: var(--field-bg);
      }
      select {
        color-scheme: light;
      }
      select option {
        color: #152321;
        background: #fffaf2;
      }
      html[data-theme="dark"] select {
        color-scheme: dark;
      }
      html[data-theme="dark"] select option {
        color: #eef5f2;
        background: #18272b;
      }
      html[data-theme="dark"] select option:checked,
      html[data-theme="dark"] select option:hover {
        color: #ffffff;
        background: #244047;
      }
      textarea {
        min-height: 96px;
        resize: vertical;
      }
      button {
        cursor: pointer;
        color: var(--primary-button-text);
        background: var(--primary-button-bg);
        border: none;
      }
      .button-secondary {
        background: var(--secondary-button-bg);
        color: var(--ink);
      }
      .button-danger {
        color: #fff;
        background: linear-gradient(140deg, var(--danger), #8e2f2f);
        box-shadow: 0 10px 22px rgba(165, 55, 55, 0.18);
      }
      .button-accent {
        color: #fff;
        background: linear-gradient(140deg, var(--accent), var(--accent-strong));
      }
      .button-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 12px 14px;
        border-radius: 14px;
        border: 1px solid var(--line);
        background: var(--link-bg);
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
        background: var(--flash-bg);
      }
      .flash.error { border-color: rgba(165, 55, 55, 0.18); color: var(--danger); }
      .flash.success { border-color: rgba(31, 122, 93, 0.20); color: var(--ok); }
      .login-cards {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 28px;
        margin-top: 10px;
      }
      .login-choice-panel {
        align-items: center;
      }
      .login-choice-intro {
        padding: 22px 0;
      }
      .title-underline {
        width: 58px;
        height: 4px;
        margin: 14px 0 28px;
        border-radius: 999px;
        background: linear-gradient(90deg, #d9a650, var(--accent));
      }
      .role-card {
        position: relative;
        display: grid;
        justify-items: center;
        gap: 18px;
        min-height: 430px;
        padding: 34px 28px;
        border-radius: 28px;
        border: 1px solid var(--line);
        background:
          radial-gradient(circle at 50% 8%, rgba(255,255,255,0.88), transparent 34%),
          var(--surface);
        box-shadow: var(--card-shadow);
        text-align: center;
        overflow: hidden;
        transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
      }
      .role-card:hover {
        transform: translateY(-6px);
        box-shadow: var(--shadow);
      }
      .role-card.student-card {
        border-color: rgba(31, 122, 93, 0.22);
      }
      .role-card.academic-card {
        border-color: rgba(118, 75, 190, 0.26);
      }
      .role-icon {
        width: 126px;
        height: 126px;
        display: grid;
        place-items: center;
        border-radius: 50%;
        background: rgba(255,255,255,0.58);
        box-shadow: inset 0 0 0 1px var(--line), 0 16px 28px rgba(21, 35, 33, 0.08);
      }
      .student-card .role-icon {
        color: #1f9a59;
        background: rgba(31, 154, 89, 0.10);
      }
      .academic-card .role-icon {
        color: #764bbe;
        background: rgba(118, 75, 190, 0.10);
      }
      .role-icon svg {
        width: 70px;
        height: 70px;
        stroke: currentColor;
      }
      .role-card h3 {
        margin: 0;
        font-size: 1.65rem;
        line-height: 1.1;
      }
      .student-card h3 {
        color: #23985c;
      }
      .academic-card h3 {
        color: #7547bf;
      }
      .role-mini-line {
        width: 150px;
        height: 3px;
        border-radius: 999px;
        opacity: 0.28;
        background: currentColor;
      }
      .role-card p {
        max-width: 310px;
        margin: 0;
        font-size: 1.02rem;
        line-height: 1.55;
      }
      .role-button {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 14px;
        width: 100%;
        min-height: 72px;
        margin-top: auto;
        padding: 18px 24px;
        border-radius: 16px;
        color: #fff;
        font-size: 1.12rem;
        font-weight: 800;
        box-shadow: 0 14px 26px rgba(21, 35, 33, 0.14);
      }
      .role-button.student {
        background: linear-gradient(135deg, #43bd72, #23864d);
      }
      .role-button.academic {
        background: linear-gradient(135deg, #8f5be6, #6c3fbd);
      }
      .role-arrow {
        margin-left: auto;
        font-size: 2rem;
        line-height: 1;
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
      .table-wrap {
        width: 100%;
        max-width: 100%;
        overflow-x: auto;
        overflow-y: hidden;
      }
      .table-wrap .table-lite {
        min-width: 680px;
      }
      .table-lite.pending-table {
        min-width: 980px;
        table-layout: fixed;
      }
      .table-lite.request-table {
        min-width: 980px;
        table-layout: fixed;
      }
      .request-table th:nth-child(1),
      .request-table td:nth-child(1) { width: 18%; }
      .request-table th:nth-child(2),
      .request-table td:nth-child(2) { width: 12%; }
      .request-table th:nth-child(3),
      .request-table td:nth-child(3) { width: 10%; }
      .request-table th:nth-child(4),
      .request-table td:nth-child(4) { width: 17%; }
      .request-table th:nth-child(5),
      .request-table td:nth-child(5) { width: 11%; }
      .request-table th:nth-child(6),
      .request-table td:nth-child(6) { width: 17%; }
      .request-table th:nth-child(7),
      .request-table td:nth-child(7) { width: 15%; }
      .pending-table th:nth-child(1),
      .pending-table td:nth-child(1) { width: 18%; }
      .pending-table th:nth-child(2),
      .pending-table td:nth-child(2) { width: 14%; }
      .pending-table th:nth-child(3),
      .pending-table td:nth-child(3) { width: 9%; }
      .pending-table th:nth-child(4),
      .pending-table td:nth-child(4),
      .pending-table th:nth-child(5),
      .pending-table td:nth-child(5) { width: 13%; }
      .pending-table th:nth-child(6),
      .pending-table td:nth-child(6) { width: 33%; }
      .table-lite th, .table-lite td {
        padding: 12px 10px;
        border-bottom: 1px solid var(--line);
        text-align: left;
        vertical-align: top;
        overflow-wrap: anywhere;
      }
      .actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }
      .actions button {
        width: auto;
        min-width: 82px;
        padding: 10px 12px;
      }
      .request-edit-form,
      .review-form {
        display: grid;
        gap: 8px;
      }
      .request-edit-panel {
        display: grid;
        gap: 10px;
      }
      .request-edit-panel:not([open]) > .request-edit-form {
        display: none;
      }
      .request-edit-panel > summary {
        width: max-content;
        min-width: 82px;
        margin-top: 0;
        padding: 10px 14px;
        border-radius: 14px;
        color: var(--primary-button-text);
        background: var(--primary-button-bg);
        cursor: pointer;
        list-style: none;
        text-align: center;
        font-weight: 700;
        user-select: none;
      }
      .request-edit-panel > summary::-webkit-details-marker {
        display: none;
      }
      .request-edit-panel > summary:focus-visible {
        outline: 3px solid rgba(134, 208, 213, 0.72);
        outline-offset: 3px;
      }
      .request-edit-panel[open] > summary {
        color: var(--ink);
        background: var(--secondary-button-bg);
      }
      .request-edit-form label,
      .review-form label {
        margin-bottom: 0;
      }
      .request-edit-form input,
      .request-edit-form select,
      .request-edit-form textarea,
      .review-form textarea {
        margin-top: 4px;
        padding: 9px 10px;
        border-radius: 12px;
        font-size: 0.88rem;
      }
      .request-edit-form textarea,
      .review-form textarea {
        min-height: 64px;
      }
      .history-list {
        display: grid;
        gap: 10px;
      }
      .history-entry {
        padding: 12px 14px;
        border: 1px solid var(--line);
        border-radius: 16px;
        background: var(--surface-soft);
      }
      .history-entry strong,
      .history-entry span {
        overflow-wrap: anywhere;
      }
      .note-box,
      .suggestion-list {
        margin-top: 8px;
      }
      .suggestion-list {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      @media (max-width: 720px) {
        .heatmap {
          max-height: 520px;
          padding-right: 4px;
        }
        .scheduler-modal {
          padding: 8px;
        }
        .scheduler-shell {
          width: calc(100vw - 16px);
          max-height: calc(100vh - 16px);
          border-radius: 18px;
        }
        .scheduler-header,
        .scheduler-toolbar {
          padding: 12px 14px;
        }
        .scheduler-main {
          grid-template-columns: 1fr;
          overflow-y: auto;
        }
        .scheduler-grid-wrap {
          max-height: 430px;
        }
        .scheduler-side {
          border-left: 0;
          border-top: 1px solid var(--line);
        }
        .scheduler-grid {
          grid-template-columns: 64px repeat(7, minmax(112px, 1fr));
          min-width: 848px;
        }
        .table-wrap {
          overflow-x: visible;
        }
        .table-wrap .table-lite,
        .table-lite.pending-table {
          display: block;
          min-width: 0;
          width: 100%;
        }
        .table-lite thead {
          display: none;
        }
        .table-lite tbody {
          display: grid;
          gap: 12px;
        }
        .table-lite tr {
          display: block;
          padding: 12px 14px;
          border: 1px solid var(--line);
          border-radius: 18px;
          background: var(--surface-soft);
        }
        .table-lite td {
          display: grid;
          grid-template-columns: minmax(92px, 0.36fr) 1fr;
          gap: 12px;
          align-items: start;
          padding: 8px 0;
          border-bottom: 1px solid var(--line);
        }
        .table-lite td:last-child {
          border-bottom: 0;
        }
        .table-lite td::before {
          content: attr(data-label);
          color: var(--muted);
          font-weight: 700;
        }
        .table-lite td[colspan] {
          display: block;
        }
        .table-lite td[colspan]::before {
          content: "";
        }
        .pending-table th:nth-child(1),
        .pending-table td:nth-child(1),
        .pending-table th:nth-child(2),
        .pending-table td:nth-child(2),
        .pending-table th:nth-child(3),
        .pending-table td:nth-child(3),
        .pending-table th:nth-child(4),
        .pending-table td:nth-child(4),
        .pending-table th:nth-child(5),
        .pending-table td:nth-child(5),
        .pending-table th:nth-child(6),
        .pending-table td:nth-child(6),
        .request-table th,
        .request-table td {
          width: auto;
        }
        .actions {
          align-items: stretch;
        }
        .actions,
        .inline-form,
        .actions button,
        .request-edit-panel > summary {
          width: 100%;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        html.theme-transition,
        html.theme-transition *,
        html.theme-transition *::before,
        html.theme-transition *::after,
        .theme-fade-layer {
          transition: none;
        }
      }
      @media (max-width: 960px) {
        .hero-grid, .grid-2, .grid-3, .login-cards, .heatmap {
          grid-template-columns: 1fr;
        }
        .topbar, .toolbar, .form-row, .item-row {
          flex-direction: column;
          align-items: stretch;
        }
        .topbar-actions {
          width: 100%;
          justify-content: center;
        }
        .settings-menu,
        .settings-trigger {
          width: 100%;
          max-width: none;
        }
        .settings-popover {
          width: 100%;
          right: 0;
          left: 0;
        }
        .locale-toggle,
        .theme-toggle {
          justify-content: center;
        }
        .brand-link {
          width: 100%;
        }
      }


      /* Modern student dashboard redesign */
      .student-modern-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 18px;
        margin: 18px 0 18px;
      }
      .student-modern-title h2 {
        margin: 0 0 6px;
        font-size: clamp(1.35rem, 2vw, 2rem);
        line-height: 1.15;
      }
      .student-modern-title p { margin: 0; max-width: 720px; }
      .student-modern-actions {
        display: flex;
        gap: 10px;
        align-items: center;
        flex-wrap: wrap;
        justify-content: flex-end;
      }
      .dashboard-stat-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 18px;
        margin-bottom: 20px;
      }
      .dashboard-stat-card {
        display: grid;
        grid-template-columns: 64px minmax(0, 1fr);
        align-items: center;
        gap: 16px;
        padding: 22px;
        border: 1px solid var(--line);
        border-radius: 20px;
        background: var(--surface);
        box-shadow: var(--card-shadow);
      }
      .dashboard-stat-icon {
        width: 58px;
        height: 58px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        font-size: 1.8rem;
        background: var(--surface-soft);
        box-shadow: inset 0 0 0 1px var(--line);
      }
      .dashboard-stat-card strong {
        display: block;
        margin-top: 3px;
        font-size: 2rem;
        line-height: 1;
      }
      .dashboard-stat-card small { color: var(--muted); }
      .dashboard-stat-card.pending .dashboard-stat-icon,
      .dashboard-stat-card.pending strong { color: #6d35d6; }
      .dashboard-stat-card.approved .dashboard-stat-icon,
      .dashboard-stat-card.approved strong { color: var(--ok); }
      .dashboard-stat-card.ready .dashboard-stat-icon,
      .dashboard-stat-card.ready strong { color: #d58a18; }
      .student-dashboard-grid {
        display: grid;
        grid-template-columns: minmax(0, 1.15fr) minmax(420px, 0.85fr);
        gap: 20px;
        align-items: start;
      }
      .modern-panel {
        padding: 22px;
        border-radius: 22px;
      }
      .panel-title-row {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 14px;
        margin-bottom: 18px;
      }
      .panel-title-left {
        display: grid;
        grid-template-columns: 44px minmax(0, 1fr);
        gap: 12px;
        align-items: center;
      }
      .panel-icon {
        width: 40px;
        height: 40px;
        border-radius: 12px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: rgba(31, 90, 97, 0.10);
        color: var(--deep);
        font-size: 1.25rem;
      }
      .panel-title-row h3 { margin: 0; font-size: 1.35rem; }
      .panel-title-row p { margin: 3px 0 0; }
      .room-legend {
        display: inline-flex;
        align-items: center;
        gap: 12px;
        flex-wrap: wrap;
        font-size: 0.84rem;
        color: var(--muted);
        white-space: nowrap;
      }
      .legend-dot-color {
        width: 10px;
        height: 10px;
        display: inline-block;
        border-radius: 50%;
        margin-right: 5px;
        vertical-align: middle;
      }
      .legend-high { background: #bd2534; }
      .legend-medium { background: #d28a0e; }
      .legend-low { background: #17834e; }
      .heatmap.modern-room-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 16px;
        max-height: none;
        overflow: visible;
      }
      .heatmap.modern-room-grid .room-card {
        min-height: 230px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        gap: 12px;
        padding: 20px;
        border-radius: 16px;
        border: 1px solid transparent;
        text-align: center;
        color: #fff;
        box-shadow: 0 12px 28px rgba(0,0,0,0.12);
        transition: transform 160ms ease, box-shadow 160ms ease;
      }
      .heatmap.modern-room-grid .room-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 18px 34px rgba(0,0,0,0.18);
      }
      .heatmap.modern-room-grid .room-card.high {
        background: linear-gradient(145deg, #c72737, #8f1625);
      }
      .heatmap.modern-room-grid .room-card.medium {
        background: linear-gradient(145deg, #d99813, #9c6700);
      }
      .heatmap.modern-room-grid .room-card.low {
        background: linear-gradient(145deg, #2f9b67, #075939);
      }
      .heatmap.modern-room-grid .room-head {
        display: grid;
        gap: 8px;
        justify-items: center;
      }
      .heatmap.modern-room-grid .room-head strong {
        font-size: 1.05rem;
      }
      .heatmap.modern-room-grid .room-head span {
        font-size: 1.8rem;
        font-weight: 900;
        line-height: 1.05;
      }
      .heatmap.modern-room-grid .room-tags {
        display: grid;
        gap: 8px;
        text-align: left;
        margin-top: 8px;
      }
      .heatmap.modern-room-grid .room-tags span {
        border-radius: 999px;
        padding: 7px 9px;
        background: rgba(255,255,255,0.12);
        border: 1px solid rgba(255,255,255,0.18);
        font-size: 0.78rem;
      }
      .panel-footer-button {
        width: 100%;
        margin-top: 16px;
        min-height: 46px;
        border-radius: 12px;
      }
      .reservation-stats {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
        margin: 14px 0 18px;
      }
      .reservation-mini-stat {
        display: grid;
        grid-template-columns: 42px minmax(0, 1fr);
        gap: 12px;
        align-items: center;
        padding: 14px;
        border-radius: 16px;
        border: 1px solid var(--line);
        background: var(--surface-soft);
      }
      .reservation-mini-stat strong {
        display: block;
        font-size: 1.45rem;
      }
      .reservation-form-modern {
        display: grid;
        gap: 12px;
      }
      .reservation-form-modern .form-row-2 {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
      }
      .reservation-form-modern label { margin: 0; }
      .reservation-form-actions {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 180px;
        gap: 12px;
        margin-top: 6px;
      }
      .compact-filter {
        margin-bottom: 18px;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: var(--surface);
        box-shadow: var(--card-shadow);
      }
      .compact-filter summary {
        cursor: pointer;
        padding: 14px 18px;
        font-weight: 800;
      }
      .compact-filter form {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        padding: 0 18px 18px;
      }
      .compact-filter .filter-actions {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        align-items: end;
      }
      @media (max-width: 1080px) {
        .student-dashboard-grid,
        .dashboard-stat-grid { grid-template-columns: 1fr; }
        .heatmap.modern-room-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .student-modern-header { flex-direction: column; }
        .compact-filter form { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      }
      @media (max-width: 720px) {
        .heatmap.modern-room-grid,
        .reservation-form-modern .form-row-2,
        .reservation-form-actions,
        .reservation-stats,
        .compact-filter form { grid-template-columns: 1fr; }
      }

      .academic-page {
        display: grid;
        gap: 18px;
      }
      .academic-top {
        display: grid;
        grid-template-columns: 1.25fr 0.75fr;
        gap: 18px;
        align-items: stretch;
      }
      .academic-hero-card,
      .academic-side-card,
      .academic-section {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 26px;
        box-shadow: var(--shadow);
      }
      .academic-hero-card {
        padding: 28px;
        min-height: 220px;
        background:
          radial-gradient(circle at 12% 15%, rgba(212,104,63,.14), transparent 34%),
          radial-gradient(circle at 88% 18%, rgba(31,90,97,.16), transparent 36%),
          var(--surface);
      }
      .academic-side-card {
        padding: 22px;
      }
      .academic-title-row {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
      }
      .academic-title-row h2 {
        margin: 6px 0 8px;
        font-size: clamp(1.45rem, 2.4vw, 2.25rem);
        line-height: 1.08;
      }
      .academic-badge {
        white-space: nowrap;
        align-self: flex-start;
      }
      .academic-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 18px;
      }
      .academic-pill {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 10px 14px;
        border-radius: 999px;
        border: 1px solid var(--line);
        background: var(--surface-soft);
        font-weight: 700;
        font-size: .86rem;
      }
      .academic-pill::before {
        content: "✓";
        width: 18px;
        height: 18px;
        display: inline-grid;
        place-items: center;
        border-radius: 50%;
        color: #fff;
        background: linear-gradient(135deg, var(--deep), var(--accent));
        font-size: .72rem;
      }
      .academic-metric-grid {
        display: grid;
        gap: 12px;
      }
      .academic-metric {
        display: grid;
        grid-template-columns: 46px 1fr;
        gap: 12px;
        align-items: center;
        padding: 15px;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: var(--surface-soft);
      }
      .academic-metric-icon {
        width: 46px;
        height: 46px;
        display: grid;
        place-items: center;
        border-radius: 16px;
        background: rgba(31,90,97,.12);
        font-size: 1.35rem;
      }
      .academic-metric strong {
        display: block;
        font-size: 1.45rem;
        line-height: 1;
        margin-bottom: 4px;
      }
      .academic-main-grid {
        display: grid;
        grid-template-columns: minmax(0, .95fr) minmax(0, 1.05fr);
        gap: 18px;
      }
      .academic-section {
        padding: 22px;
      }
      .academic-section-head {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 12px;
        margin-bottom: 14px;
      }
      .academic-section h3 {
        margin: 0 0 4px;
        font-size: 1.15rem;
      }
      .academic-list {
        display: grid;
        gap: 12px;
      }
      .academic-item {
        padding: 15px;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: var(--surface-soft);
      }
      .academic-item-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 6px;
      }
      .academic-util-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
      }
      .academic-util-card {
        padding: 16px;
        border-radius: 18px;
        border: 1px solid var(--line);
        background:
          linear-gradient(145deg, rgba(255,255,255,.08), rgba(255,255,255,0)),
          var(--surface-soft);
      }
      .academic-util-card strong {
        font-size: 1.05rem;
      }
      .academic-progress {
        height: 8px;
        overflow: hidden;
        border-radius: 999px;
        background: var(--secondary-button-bg);
        margin: 12px 0 8px;
      }
      .academic-progress span {
        display: block;
        height: 100%;
        width: var(--value, 0%);
        border-radius: inherit;
        background: linear-gradient(90deg, var(--deep), var(--accent));
      }
      .academic-pending-grid {
        display: grid;
        gap: 14px;
      }
      .academic-request-card {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(280px, .8fr);
        gap: 16px;
        padding: 16px;
        border: 1px solid var(--line);
        border-radius: 20px;
        background: var(--surface-soft);
      }
      .academic-request-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 10px;
      }
      .academic-request-card textarea {
        min-height: 82px;
      }
      .academic-request-card .actions {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
      }
      html[data-theme="dark"] .academic-hero-card,
      html[data-theme="dark"] .academic-side-card,
      html[data-theme="dark"] .academic-section {
        background: rgba(13, 31, 31, .92);
      }
      html[data-theme="dark"] .academic-item,
      html[data-theme="dark"] .academic-metric,
      html[data-theme="dark"] .academic-util-card,
      html[data-theme="dark"] .academic-request-card {
        background: rgba(255,255,255,.055);
      }
      @media (max-width: 980px) {
        .academic-top,
        .academic-main-grid,
        .academic-request-card {
          grid-template-columns: 1fr;
        }
        .academic-util-grid {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }
      @media (max-width: 620px) {
        .academic-hero-card,
        .academic-side-card,
        .academic-section {
          padding: 16px;
        }
        .academic-util-grid {
          grid-template-columns: 1fr;
        }
      }

    </style>
    """


def render_layout(title: str, content: str, user: sqlite3.Row | None = None, lang: str = DEFAULT_LANGUAGE, theme: str = DEFAULT_THEME) -> str:
    home_link = "/dashboard" if user is not None else "/"
    active_theme = theme if theme in SUPPORTED_THEMES else DEFAULT_THEME
    center_html = "" if user is not None else f'<div class="muted small">{h(t("sqlite_note", lang))}</div>'

    locale_links = f"""
      <div class="locale-toggle" aria-label="Language">
        <a href="?lang=en" class="{'active' if lang == 'en' else ''}" aria-label="English">
          <span class="flag-icon flag-en" aria-hidden="true"></span>
          <span>EN</span>
        </a>
        <span class="divider" aria-hidden="true">|</span>
        <a href="?lang=tr" class="{'active' if lang == 'tr' else ''}" aria-label="Türkçe">
          <span class="flag-icon flag-tr" aria-hidden="true"></span>
          <span>TR</span>
        </a>
      </div>
    """
    theme_controls = f"""
      <div class="theme-toggle" aria-label="{h(t('theme_label', lang))}">
        <button type="button" class="theme-option {'active' if active_theme == 'light' else ''}" data-theme-option="light" aria-pressed="{'true' if active_theme == 'light' else 'false'}">
          <span class="theme-icon theme-sun" aria-hidden="true"></span>
          <span>{h(t('theme_light', lang))}</span>
        </button>
        <button type="button" class="theme-option {'active' if active_theme == 'dark' else ''}" data-theme-option="dark" aria-pressed="{'true' if active_theme == 'dark' else 'false'}">
          <span class="theme-icon theme-moon" aria-hidden="true"></span>
          <span>{h(t('theme_dark', lang))}</span>
        </button>
      </div>
    """
    if user is not None:
        initials = h(user_initials(user["name"]))
        gear_icon = gear_icon_svg()
        topbar_actions = f"""
          <details class="settings-menu">
            <summary class="settings-trigger" aria-label="{h(t('open_settings', lang))}">
              <span class="profile-avatar" aria-hidden="true">{initials}</span>
              <span class="profile-copy">
                <strong>{h(user['name'])}</strong>
                <span class="small muted">{h(t('settings', lang))}</span>
              </span>
              <span class="profile-gear" aria-hidden="true">{gear_icon}</span>
            </summary>
            <div class="settings-popover">
              <section class="settings-section">
                <div class="small muted">{h(t('account', lang))}</div>
                <div class="settings-user">
                  <span class="profile-avatar" aria-hidden="true">{initials}</span>
                  <span class="settings-user-copy">
                    <strong>{h(user['name'])}</strong>
                    <span class="small muted">{h(user['role'])}</span>
                    <span class="small muted">{h(user['email'])}</span>
                  </span>
                </div>
              </section>
              <section class="settings-section">
                <div class="small muted">{h(t('language', lang))}</div>
                {locale_links}
              </section>
              <section class="settings-section">
                <div class="small muted">{h(t('theme_label', lang))}</div>
                {theme_controls}
              </section>
              <form method="post" action="/logout">
                <button class="button-secondary" type="submit">{h(t('sign_out', lang))}</button>
              </form>
            </div>
          </details>
        """
    else:
        topbar_actions = f"""
          {locale_links}
          {theme_controls}
        """

    return f"""<!DOCTYPE html>
<html lang="{h(lang)}" data-theme="{h(active_theme)}">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{h(title)}</title>
    {style_block()}
  </head>
  <body>
    <main class="app-shell">
      <section class="topbar">
        <a class="brand-link" href="{home_link}" title="{h(t('brand_title', lang))}">
          <div class="brand-mark">{logo_svg()}</div>
          <div class="brand-copy brand">
            <div class="eyebrow">{h(t('brand_eyebrow', lang))}</div>
            <h1>{h(t('brand_title', lang))}</h1>
            <div class="small muted">{h(t('brand_subtitle', lang))}</div>
          </div>
        </a>
        {center_html}
        <div class="topbar-actions">
          {topbar_actions}
        </div>
      </section>
      {content}
    </main>
    <script>
      (() => {{
        const root = document.documentElement;
        const buttons = Array.from(document.querySelectorAll("[data-theme-option]"));
        let fadeTimer = 0;
        let transitionTimer = 0;

        const getFadeLayer = () => {{
          let layer = document.querySelector(".theme-fade-layer");
          if (!layer) {{
            layer = document.createElement("div");
            layer.className = "theme-fade-layer";
            layer.setAttribute("aria-hidden", "true");
            document.body.append(layer);
          }}
          return layer;
        }};

        const commitTheme = (theme) => {{
          root.dataset.theme = theme;
          document.cookie = "{THEME_COOKIE}=" + theme + "; path=/; max-age=31536000; SameSite=Lax";
          buttons.forEach((button) => {{
            const isActive = button.dataset.themeOption === theme;
            button.classList.toggle("active", isActive);
            button.setAttribute("aria-pressed", isActive ? "true" : "false");
          }});
        }};

        const applyTheme = (theme) => {{
          if (root.dataset.theme === theme) return;
          window.clearTimeout(fadeTimer);
          window.clearTimeout(transitionTimer);

          const layer = getFadeLayer();
          const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
          layer.style.background = theme === "dark" ? "#05090b" : "#fffaf2";

          if (prefersReducedMotion) {{
            commitTheme(theme);
            return;
          }}

          root.classList.add("theme-transition");
          layer.classList.add("active");
          fadeTimer = window.setTimeout(() => {{
            commitTheme(theme);
            layer.classList.remove("active");
          }}, 180);
          transitionTimer = window.setTimeout(() => {{
            root.classList.remove("theme-transition");
          }}, 820);
        }};

        buttons.forEach((button) => {{
          button.addEventListener("click", () => applyTheme(button.dataset.themeOption));
        }});

        const settingsMenu = document.querySelector(".settings-menu");
        if (settingsMenu) {{
          document.addEventListener("click", (event) => {{
            if (!settingsMenu.contains(event.target)) {{
              settingsMenu.open = false;
            }}
          }});
          document.addEventListener("keydown", (event) => {{
            if (event.key === "Escape") {{
              settingsMenu.open = false;
            }}
          }});
        }}

        document.querySelectorAll("form[data-confirm]").forEach((form) => {{
          form.addEventListener("submit", (event) => {{
            const message = form.dataset.confirm;
            if (message && !window.confirm(message)) {{
              event.preventDefault();
            }}
          }});
        }});
      }})();
    </script>
  </body>
</html>
"""


def room_scheduler_script() -> str:
    return """
    <script>
      (() => {
        const dataElement = document.getElementById("room-calendar-data");
        const modal = document.querySelector("[data-room-scheduler]");
        if (!dataElement || !modal) return;

        const payload = JSON.parse(dataElement.textContent);
        const labels = payload.labels || {};
        const locale = payload.lang === "tr" ? "tr-TR" : "en-US";
        const dayNames = payload.lang === "tr"
          ? ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
          : ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
        const slotStartHour = 8;
        const slotEndHour = 20;
        const slotMinutes = 30;

        const titleElement = modal.querySelector("[data-calendar-title]");
        const roomMetaElement = modal.querySelector("[data-calendar-room-meta]");
        const rangeElement = modal.querySelector("[data-calendar-range]");
        const gridElement = modal.querySelector("[data-calendar-grid]");
        const selectedElement = modal.querySelector("[data-selected-time]");
        const statusElement = modal.querySelector("[data-selection-status]");
        const form = modal.querySelector("[data-calendar-form]");
        const roomInput = modal.querySelector("[data-calendar-room-input]");
        const startInput = modal.querySelector("[data-calendar-start-input]");
        const endInput = modal.querySelector("[data-calendar-end-input]");
        const submitButton = modal.querySelector("[data-calendar-submit]");
        const closeButton = modal.querySelector("[data-close-scheduler-main]");

        let activeRoom = null;
        let weekStart = startOfWeek(new Date());
        let selection = null;

        function parseLocal(value) {
          const [datePart, timePart = "00:00:00"] = value.split("T");
          const [year, month, day] = datePart.split("-").map(Number);
          const [hour, minute, second = 0] = timePart.split(":").map(Number);
          return new Date(year, month - 1, day, hour || 0, minute || 0, second || 0);
        }

        function toInputValue(date) {
          return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}T${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
        }

        function addDays(date, days) {
          const next = new Date(date);
          next.setDate(next.getDate() + days);
          return next;
        }

        function addMinutes(date, minutes) {
          return new Date(date.getTime() + minutes * 60000);
        }

        function startOfWeek(date) {
          const next = new Date(date.getFullYear(), date.getMonth(), date.getDate());
          const jsDay = next.getDay() || 7;
          next.setDate(next.getDate() - jsDay + 1);
          next.setHours(0, 0, 0, 0);
          return next;
        }

        function dayNumber(date) {
          return date.getDay() === 0 ? 7 : date.getDay();
        }

        function daysBetween(a, b) {
          const first = new Date(a.getFullYear(), a.getMonth(), a.getDate());
          const second = new Date(b.getFullYear(), b.getMonth(), b.getDate());
          return Math.round((first - second) / 86400000);
        }

        function sameTimeOnDay(day, source) {
          return new Date(day.getFullYear(), day.getMonth(), day.getDate(), source.getHours(), source.getMinutes(), 0, 0);
        }

        function formatShortDate(date) {
          return new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" }).format(date);
        }

        function formatFullDate(date) {
          return new Intl.DateTimeFormat(locale, { weekday: "short", month: "short", day: "numeric" }).format(date);
        }

        function formatTime(date) {
          return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(date);
        }

        function formatRange(start, end) {
          return `${formatFullDate(start)} · ${formatTime(start)} - ${formatTime(end)}`;
        }

        function escapeHtml(value) {
          return String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
        }

        function getRawEventsForRoom(roomId) {
          return payload.events.filter((event) => Number(event.roomId) === Number(roomId));
        }

        function expandEventsForWeek(roomId) {
          const weekEnd = addDays(weekStart, 7);
          const instances = [];

          getRawEventsForRoom(roomId).forEach((event) => {
            const baseStart = parseLocal(event.start);
            const baseEnd = parseLocal(event.end);
            const duration = baseEnd - baseStart;

            if (event.kind !== "schedule" || event.recurrence === "Once") {
              if (baseStart < weekEnd && baseEnd > weekStart) {
                instances.push({ ...event, startDate: baseStart, endDate: baseEnd });
              }
              return;
            }

            for (let index = 0; index < 7; index += 1) {
              const day = addDays(weekStart, index);
              if (dayNumber(day) !== Number(event.weekday)) continue;
              const diff = daysBetween(day, baseStart);
              if (diff < 0) continue;
              if (event.recurrence === "Biweekly" && diff % 14 !== 0) continue;
              const startDate = sameTimeOnDay(day, baseStart);
              const endDate = new Date(startDate.getTime() + duration);
              instances.push({ ...event, startDate, endDate });
            }
          });

          return instances.sort((a, b) => a.startDate - b.startDate);
        }

        function eventOverlaps(start, end, event) {
          return start < event.endDate && end > event.startDate;
        }

        function selectedOverlaps(start, end) {
          return selection && start < selection.end && end > selection.start;
        }

        function rangeHasBusy(start, end) {
          return expandEventsForWeek(activeRoom.id).some((event) => eventOverlaps(start, end, event));
        }

        function defaultWeekForRoom(roomId) {
          const roomEvents = getRawEventsForRoom(roomId);
          const hasRecurring = roomEvents.some((event) => event.kind === "schedule" && event.recurrence !== "Once");
          if (hasRecurring) return startOfWeek(new Date());
          if (roomEvents.length) {
            return startOfWeek(parseLocal(roomEvents.sort((a, b) => parseLocal(a.start) - parseLocal(b.start))[0].start));
          }
          return startOfWeek(new Date());
        }

        function renderSelection(message) {
          if (!selection) {
            selectedElement.textContent = labels.calendarNoSelection;
            statusElement.textContent = message || "";
            roomInput.value = activeRoom ? activeRoom.id : "";
            startInput.value = "";
            endInput.value = "";
            submitButton.disabled = true;
            return;
          }
          selectedElement.textContent = formatRange(selection.start, selection.end);
          statusElement.textContent = message || "";
          roomInput.value = activeRoom.id;
          startInput.value = toInputValue(selection.start);
          endInput.value = toInputValue(selection.end);
          submitButton.disabled = false;
        }

        function renderGrid() {
          if (!activeRoom) return;
          const weekEnd = addDays(weekStart, 6);
          const events = expandEventsForWeek(activeRoom.id);
          rangeElement.textContent = `${labels.week} · ${formatShortDate(weekStart)} - ${formatShortDate(weekEnd)}`;
          gridElement.innerHTML = "";

          const corner = document.createElement("div");
          corner.className = "calendar-cell calendar-corner";
          gridElement.append(corner);

          for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
            const day = addDays(weekStart, dayIndex);
            const header = document.createElement("div");
            header.className = "calendar-cell calendar-day";
            header.innerHTML = `<span>${dayNames[dayIndex]}</span><strong>${formatShortDate(day)}</strong>`;
            gridElement.append(header);
          }

          for (let hour = slotStartHour; hour < slotEndHour; hour += 1) {
            for (let minuteStep = 0; minuteStep < 60; minuteStep += slotMinutes) {
              const timeDate = new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate(), hour, minuteStep, 0, 0);
              const timeCell = document.createElement("div");
              timeCell.className = "calendar-cell calendar-time";
              timeCell.textContent = formatTime(timeDate);
              gridElement.append(timeCell);

              for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
                const day = addDays(weekStart, dayIndex);
                const slotStart = new Date(day.getFullYear(), day.getMonth(), day.getDate(), hour, minuteStep, 0, 0);
                const slotEnd = addMinutes(slotStart, slotMinutes);
                const busyEvent = events.find((event) => eventOverlaps(slotStart, slotEnd, event));
                const cell = document.createElement(busyEvent ? "div" : "button");
                cell.className = "calendar-cell calendar-slot";

                if (busyEvent) {
                  cell.classList.add("busy");
                  cell.innerHTML = `<strong>${escapeHtml(busyEvent.label)}</strong><span>${escapeHtml(formatTime(busyEvent.startDate))} - ${escapeHtml(formatTime(busyEvent.endDate))}</span>`;
                } else {
                  cell.type = "button";
                  cell.dataset.start = toInputValue(slotStart);
                  cell.dataset.end = toInputValue(slotEnd);
                  cell.setAttribute("aria-label", `${labels.selectTime}: ${formatRange(slotStart, slotEnd)}`);
                  cell.addEventListener("click", () => selectSlot(slotStart, slotEnd));
                }

                if (!busyEvent && selectedOverlaps(slotStart, slotEnd)) {
                  cell.classList.add("selected");
                }
                gridElement.append(cell);
              }
            }
          }
        }

        function selectSlot(slotStart, slotEnd) {
          if (!selection || slotStart < selection.start) {
            const next = { start: slotStart, end: slotEnd };
            if (rangeHasBusy(next.start, next.end)) {
              renderSelection(labels.calendarUnavailableRange);
              return;
            }
            selection = next;
            renderGrid();
            renderSelection();
            return;
          }

          const next = { start: selection.start, end: slotEnd };
          if (rangeHasBusy(next.start, next.end)) {
            renderSelection(labels.calendarUnavailableRange);
            return;
          }
          selection = next;
          renderGrid();
          renderSelection();
        }

        function openScheduler(roomId) {
          activeRoom = payload.rooms.find((room) => Number(room.id) === Number(roomId));
          if (!activeRoom) return;
          weekStart = defaultWeekForRoom(activeRoom.id);
          selection = null;
          titleElement.textContent = `${activeRoom.code} · ${labels.roomCalendar}`;
          roomMetaElement.textContent = `${labels.blockLabel} ${activeRoom.block} · ${labels.floor} ${activeRoom.floor} · ${activeRoom.capacity} ${labels.seats}`;
          renderGrid();
          renderSelection();
          modal.hidden = false;
          modal.setAttribute("aria-hidden", "false");
          document.body.classList.add("scheduler-open");
          closeButton.focus();
        }

        function closeScheduler() {
          modal.hidden = true;
          modal.setAttribute("aria-hidden", "true");
          document.body.classList.remove("scheduler-open");
        }

        document.querySelectorAll("[data-room-card]").forEach((card) => {
          card.addEventListener("click", () => openScheduler(card.dataset.roomId));
        });

        modal.querySelectorAll("[data-close-scheduler]").forEach((button) => {
          button.addEventListener("click", closeScheduler);
        });
        modal.querySelector("[data-calendar-prev]").addEventListener("click", () => {
          weekStart = addDays(weekStart, -7);
          selection = null;
          renderGrid();
          renderSelection();
        });
        modal.querySelector("[data-calendar-next]").addEventListener("click", () => {
          weekStart = addDays(weekStart, 7);
          selection = null;
          renderGrid();
          renderSelection();
        });
        modal.querySelector("[data-calendar-today]").addEventListener("click", () => {
          weekStart = startOfWeek(new Date());
          selection = null;
          renderGrid();
          renderSelection();
        });
        document.addEventListener("keydown", (event) => {
          if (event.key === "Escape" && !modal.hidden) closeScheduler();
        });
      })();
    </script>
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
    user_id = get_session_user_id(handler)
    if user_id is None:
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


def signin_page(message: str = "", error: bool = False, lang: str = DEFAULT_LANGUAGE, theme: str = DEFAULT_THEME, expected_role: str = "Student") -> str:
    flash = render_flash(message, error, lang)

    role_title = t("academic_sign_in", lang) if expected_role == "Academic" else t("student_sign_in", lang)
    role_description = (
        t("academic_sign_in_description", lang)
        if expected_role == "Academic"
        else t("student_sign_in_description", lang)
    )

    flash = render_flash(message, error, lang)

    content = f"""
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">{h(t('brand_eyebrow', lang))}</div>
          <h2>{h(role_title)}</h2>
          <p class="muted">{h(role_description)}</p>
          {flash}
          <div class="pill-row">
            <a class="pill" href="/student-login">{h(t('student_login', lang))}</a>
            <a class="pill" href="/academic-login">{h(t('academic_login', lang))}</a>
          </div>
          <div class="spaced">
{
    f'<p class="small muted">{h(t("no_account", lang))} <a href="/signup">{h(t("sign_up_as_student", lang))}</a></p>'
    if expected_role == "Student"
    else f'<p class="small muted">{h(t("academic_accounts_notice", lang))}</p>'
}          </div>
        </div>
        <div class="card">
<form method="post" action="/login?lang={h(lang)}&theme={h(theme)}">            <input type="hidden" name="expected_role" value="{h(expected_role)}">
            <label for="email">{h(t('email', lang))}</label>
            <input type="email" id="email" name="email" required autofocus>
            <label for="password">{h(t('password', lang))}</label>
            <input type="password" id="password" name="password" required>
            <button class="button-accent" type="submit">{h(t('sign_in_button', lang))}</button>
          </form>
        </div>
      </div>
    </section>
    """
    return render_layout(role_title, content, None, lang, theme)


def signup_page(message: str = "", error: bool = False, lang: str = DEFAULT_LANGUAGE, theme: str = DEFAULT_THEME) -> str:
    with get_connection() as conn:
        departments = conn.execute(
            "SELECT department_id, department_name FROM Departments ORDER BY department_name"
        ).fetchall()

    flash = render_flash(message, error, lang)

    dept_options = "".join(
        f'<option value="{row["department_id"]}">{h(row["department_name"])}</option>'
        for row in departments
    )

    content = f"""
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">{h(t('brand_eyebrow', lang))}</div>
          <h2>{h(t('create_account_title', lang))}</h2>
          <p class="muted">
            {h(t('create_account_description', lang))}
          </p>
          {flash}
          <div class="spaced">
            <p class="small muted">{h(t('already_account', lang))} <a href="/signin">{h(t('sign_in_link', lang))}</a></p>
          </div>
        </div>
        <div class="card">
          <form method="post" action="/register">
            <label for="name">{h(t('full_name', lang))}</label>
            <input type="text" id="name" name="name" required>
            <label for="email">{h(t('email', lang))}</label>
            <input type="email" id="email" name="email" required>
            <label for="department">{h(t('department', lang))}</label>
            <select id="department" name="department_id" required>
              <option value="">{h(t('select_department', lang))}</option>
              {dept_options}
            </select>
            <label for="password">{h(t('password', lang))}</label>
            <input type="password" id="password" name="password" required minlength="6">
            <p class="small muted">{h(t('password_requirements', lang))}</p>
            <label for="confirm_password">{h(t('confirm_password', lang))}</label>
            <input type="password" id="confirm_password" name="confirm_password" required>
            <button class="button-accent" type="submit">{h(t('sign_up_button', lang))}</button>
          </form>
        </div>
      </div>
    </section>
    """
    return render_layout(t('create_account_title', lang), content, None, lang, theme)


def student_dashboard(user: sqlite3.Row, params: dict[str, list[str]], message: str = "", error: bool = False, lang: str = DEFAULT_LANGUAGE, theme: str = DEFAULT_THEME) -> str:
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
                   er.room_id, er.requested_start, er.requested_end, er.request_note,
                   er.rejection_reason, er.decision_note, er.decision_at,
                   c.room_code, reviewer.name AS reviewer_name
            FROM Event_Requests er
            JOIN Classrooms c ON c.room_id = er.room_id
            LEFT JOIN Users reviewer ON reviewer.user_id = er.approved_by
            WHERE er.requester_id = ?
            ORDER BY datetime(er.requested_start) DESC
            """,
            (user["user_id"],),
        ).fetchall()
        room_options = conn.execute(
            "SELECT room_id, room_code FROM Classrooms WHERE is_active = 1 ORDER BY room_code"
        ).fetchall()
        history = conn.execute(
            """
            SELECT rh.action, rh.previous_status, rh.new_status, rh.action_note, rh.created_at,
                   er.event_title, actor.name AS actor_name
            FROM Request_History rh
            JOIN Event_Requests er ON er.request_id = rh.request_id
            JOIN Users actor ON actor.user_id = rh.actor_id
            WHERE er.requester_id = ?
            ORDER BY datetime(rh.created_at) DESC, rh.history_id DESC
            LIMIT 10
            """,
            (user["user_id"],),
        ).fetchall()
        calendar_schedules = conn.execute(
            """
            SELECT room_id, schedule_type, title, start_at, end_at, weekday, recurrence_pattern
            FROM Academic_Schedules
            ORDER BY datetime(start_at)
            """
        ).fetchall()
        calendar_requests = conn.execute(
            """
            SELECT room_id, event_type, status, requested_start, requested_end
            FROM Event_Requests
            WHERE status IN ('Pending', 'Approved')
            ORDER BY datetime(requested_start)
            """
        ).fetchall()

    pending_count = sum(1 for row in requests if row["status"] == "Pending")
    approved_count = sum(1 for row in requests if row["status"] == "Approved")
    available_now = sum(1 for row in rooms if row["live_status"] in ("Available", "Reserved"))

    flash = render_flash(message, error, lang)

    room_cards = []
    for row in rooms:
        ratio = occupancy_percentage(row)
        percent = round(ratio * 100)
        tag_class = room_badge(ratio)
        status_text = translate_status(row["live_status"] or "Unknown", lang)
        room_cards.append(
            f"""
            <button type="button" class="room-card room-card-button {tag_class}" data-room-card data-room-id="{h(row["room_id"])}" data-room-code="{h(row["room_code"])}" aria-label="{h(t('open_calendar', lang))}: {h(row["room_code"])}">
              <div class="room-head">
                <strong>{h(row["room_code"])}</strong>
                <span>{h(t('percent_full', lang).format(percent=percent))}</span>
              </div>
              <div class="small">{h(t('block_label', lang))} {h(row["block"])} • {h(t('floor', lang))} {h(row["floor"])} • {h(row["capacity"])} {h(t('seats', lang))}</div>
              <div class="room-tags">
                <span>{h(t('status', lang))}: {h(status_text)}</span>
                <span>{h(t('projector_label', lang))}: {h(yes_no(bool(row['projector']), lang))}</span>
                <span>{h(t('minimum_power_outlets', lang))}: {h(row["power_outlets"])}</span>
                <span>{h(t('smart_board_label', lang))}: {h(yes_no(bool(row['smart_board']), lang))}</span>
              </div>
            </button>
            """
        )

    if not room_cards:
        room_cards.append(
            f'<div class="card"><h3>{h(t("no_rooms_match", lang))}</h3><p class="muted">{h(t("try_removing_constraints", lang))}</p></div>'
        )

    request_rows = []
    for row in requests:
        status_class = "ok" if row["status"] == "Approved" else "warn" if row["status"] == "Pending" else "info" if row["status"] == "Cancelled" else "danger"
        note_parts = []
        if row["request_note"]:
            note_parts.append(f'<div class="small muted"><strong>{h(t("note", lang))}:</strong> {h(row["request_note"])}</div>')
        decision_note = row["decision_note"] or row["rejection_reason"]
        if decision_note:
            note_parts.append(f'<div class="small muted"><strong>{h(t("decision_note", lang))}:</strong> {h(decision_note)}</div>')
        if row["reviewer_name"] and row["decision_at"]:
            note_parts.append(f'<div class="small muted">{h(t("created_by", lang).format(actor=row["reviewer_name"]))} • {h(format_datetime(row["decision_at"], lang))}</div>')
        details_html = "".join(note_parts) or '<span class="muted">-</span>'
        actions_html = '<span class="muted">-</span>'
        if row["status"] == "Pending":
            actions_html = f"""
              <details class="request-edit-panel">
                <summary>{h(t('edit', lang))}</summary>
                <form method="post" action="/requests/update" class="request-edit-form" data-confirm="{h(t('confirm_update_request', lang))}">
                  <input type="hidden" name="request_id" value="{h(row["request_id"])}">
                  <label>{h(t('title', lang))}
                    <input type="text" name="event_title" value="{h(row["event_title"])}" required>
                  </label>
                  <label>{h(t('type', lang))}
                    <select name="event_type" required>
                      {event_type_options_html(row["event_type"], lang)}
                    </select>
                  </label>
                  <label>{h(t('room', lang))}
                    <select name="room_id" required>
                      {room_options_html(room_options, row["room_id"])}
                    </select>
                  </label>
                  <label>{h(t('start', lang))}
                    <input type="datetime-local" name="requested_start" value="{h(datetime_local_value(row["requested_start"]))}" required>
                  </label>
                  <label>{h(t('end', lang))}
                    <input type="datetime-local" name="requested_end" value="{h(datetime_local_value(row["requested_end"]))}" required>
                  </label>
                  <label>{h(t('note', lang))}
                    <textarea name="request_note">{h(row["request_note"] or "")}</textarea>
                  </label>
                  <div class="actions">
                    <button type="submit">{h(t('update_request', lang))}</button>
                  </div>
                </form>
              </details>
              <form method="post" action="/requests/cancel" class="inline-form spaced" data-confirm="{h(t('confirm_cancel_request', lang))}">
                <input type="hidden" name="request_id" value="{h(row["request_id"])}">
                <button class="button-danger" type="submit">{h(t('cancel_request', lang))}</button>
              </form>
            """
        request_rows.append(
            f"""
            <tr>
              <td data-label="{h(t('title', lang))}">{h(row["event_title"])}</td>
              <td data-label="{h(t('type', lang))}">{h(translate_event_type(row["event_type"], lang))}</td>
              <td data-label="{h(t('room', lang))}">{h(row["room_code"])}</td>
              <td data-label="{h(t('time', lang))}">{h(format_datetime(row["requested_start"], lang))}<br><span class="muted small">{h(format_datetime(row["requested_end"], lang))}</span></td>
              <td data-label="{h(t('status', lang))}"><span class="badge {status_class}">{h(translate_status(row['status'], lang))}</span></td>
              <td data-label="{h(t('details', lang))}">{details_html}</td>
              <td data-label="{h(t('actions', lang))}">{actions_html}</td>
            </tr>
            """
        )

    request_rows_html = "".join(request_rows) or f'<tr><td colspan="7" class="muted">{h(t("no_requests_yet", lang))}</td></tr>'

    history_rows = "".join(
        f"""
        <div class="history-entry">
          <div class="stat-row">
            <strong>{h(row["event_title"])}</strong>
            <span class="badge info">{h(translate_history_action(row["action"], lang))}</span>
          </div>
          <div class="small muted">{h(t("created_by", lang).format(actor=row["actor_name"]))} • {h(format_datetime(row["created_at"], lang))}</div>
          {f'<div class="small muted note-box">{h(row["action_note"])}</div>' if row["action_note"] else ''}
        </div>
        """
        for row in history
    ) or f'<div class="history-entry muted">{h(t("no_history_yet", lang))}</div>'

    room_select = room_options_html(room_options)
    block_options = "".join(
        f'<option value="{h(row["block"])}" {"selected" if row["block"] == block else ""}>{h(row["block"])}</option>'
        for row in blocks
    )
    calendar_payload = {
        "lang": lang,
        "rooms": [
            {
                "id": row["room_id"],
                "code": row["room_code"],
                "block": row["block"],
                "floor": row["floor"],
                "capacity": row["capacity"],
            }
            for row in rooms
        ],
        "events": [
            {
                "roomId": row["room_id"],
                "kind": "schedule",
                "label": t("calendar_busy_academic", lang),
                "start": datetime_iso_value(row["start_at"]),
                "end": datetime_iso_value(row["end_at"]),
                "weekday": row["weekday"],
                "recurrence": row["recurrence_pattern"],
            }
            for row in calendar_schedules
        ]
        + [
            {
                "roomId": row["room_id"],
                "kind": "request",
                "label": f"{t('calendar_busy_request', lang)} · {t('calendar_pending' if row['status'] == 'Pending' else 'calendar_approved', lang)}",
                "start": datetime_iso_value(row["requested_start"]),
                "end": datetime_iso_value(row["requested_end"]),
                "weekday": None,
                "recurrence": "Once",
            }
            for row in calendar_requests
        ],
        "eventTypes": [
            {"value": event_type, "label": translate_event_type(event_type, lang)}
            for event_type in ("Workshop", "Club", "Makeup", "Exam", "Seminar")
        ],
        "labels": {
            "roomCalendar": t("room_calendar", lang),
            "week": t("week", lang),
            "previousWeek": t("previous_week", lang),
            "nextWeek": t("next_week", lang),
            "today": t("today", lang),
            "close": t("close", lang),
            "busy": t("busy", lang),
            "selectedTime": t("selected_time", lang),
            "selectTime": t("select_time", lang),
            "reservationDetails": t("reservation_details", lang),
            "reserveSelectedTime": t("reserve_selected_time", lang),
            "calendarUnavailableRange": t("calendar_unavailable_range", lang),
            "calendarNoSelection": t("calendar_no_selection", lang),
            "blockLabel": t("block_label", lang),
            "floor": t("floor", lang),
            "seats": t("seats", lang),
        },
    }
    calendar_payload_json = json.dumps(calendar_payload, ensure_ascii=False).replace("</", "<\\/")

    content = f"""
    <section class="student-modern-header">
      <div class="student-modern-title">
        <h2>{h(t('welcome_back', lang).format(name=user['name'].split()[0]))} 👋</h2>
        <p class="muted">{h(t('find_room_text', lang))}</p>
      </div>
      <div class="student-modern-actions">
        <a class="pill" href="/dashboard?lang={h(lang)}&theme=light">☀️ {h(t('theme_light', lang))}</a>
        <a class="pill" href="/dashboard?lang=tr&theme={h(theme)}">🇹🇷 TR</a>
        <span class="pill">🏛️ {h(user['department_name'])}</span>
      </div>
    </section>

    {flash}

    <section class="dashboard-stat-grid">
      <article class="dashboard-stat-card pending">
        <div class="dashboard-stat-icon">🗓️</div>
        <div>
          <div>{h(t('pending_requests_title', lang))}</div>
          <strong>{pending_count}</strong>
          <small>{h(t('calendar_pending', lang))}</small>
        </div>
      </article>
      <article class="dashboard-stat-card approved">
        <div class="dashboard-stat-icon">✅</div>
        <div>
          <div>{h(t('approved_requests', lang))}</div>
          <strong>{approved_count}</strong>
          <small>{h(t('calendar_approved', lang))}</small>
        </div>
      </article>
      <article class="dashboard-stat-card ready">
        <div class="dashboard-stat-icon">🔑</div>
        <div>
          <div>{h(t('rooms_ready', lang))}</div>
          <strong>{available_now}</strong>
          <small>{h(t('visible_rooms', lang))}: {len(rooms)}</small>
        </div>
      </article>
    </section>

    <details class="compact-filter">
      <summary>⚙️ {h(t('quick_filter', lang))}</summary>
      <form method="get" action="/dashboard">
        <label>{h(t('block_label', lang))}
          <select name="block">
            <option value="">{h(t('all_blocks', lang))}</option>
            {block_options}
          </select>
        </label>
        <label>{h(t('minimum_power_outlets', lang))}
          <select name="min_outlets">
            <option value="">{h(t('no_preference', lang))}</option>
            <option value="10" {"selected" if min_outlets == "10" else ""}>10+</option>
            <option value="20" {"selected" if min_outlets == "20" else ""}>20+</option>
            <option value="40" {"selected" if min_outlets == "40" else ""}>40+</option>
          </select>
        </label>
        <label>{h(t('projector_label', lang))}
          <select name="projector">
            <option value="">{h(t('any', lang))}</option>
            <option value="1" {"selected" if params.get("projector", [""])[0] == "1" else ""}>{h(t('required', lang))}</option>
          </select>
        </label>
        <label>{h(t('smart_board_label', lang))}
          <select name="smart_board">
            <option value="">{h(t('any', lang))}</option>
            <option value="1" {"selected" if params.get("smart_board", [""])[0] == "1" else ""}>{h(t('required', lang))}</option>
          </select>
        </label>
        <div class="filter-actions">
          <button class="button-accent" type="submit">{h(t('apply_filters', lang))}</button>
          <a class="button-link" href="/dashboard">{h(t('reset_filters', lang))}</a>
        </div>
      </form>
    </details>

    <section class="student-dashboard-grid spaced">
      <article class="card modern-panel">
        <div class="panel-title-row">
          <div class="panel-title-left">
            <span class="panel-icon">📡</span>
            <div>
              <h3>{h(t('live_room_heatmap', lang))}</h3>
              <p class="muted small">{h(t('live_map_info', lang))}</p>
            </div>
          </div>
          <div class="room-legend">
            <span><i class="legend-dot-color legend-high"></i>{h(t('high', lang) if 'high' in TRANSLATIONS.get(lang, {}) else ('Yüksek' if lang == 'tr' else 'High'))}</span>
            <span><i class="legend-dot-color legend-medium"></i>{h('Orta' if lang == 'tr' else 'Medium')}</span>
            <span><i class="legend-dot-color legend-low"></i>{h('Düşük' if lang == 'tr' else 'Low')}</span>
          </div>
        </div>
        <div class="heatmap modern-room-grid">
          {"".join(room_cards)}
        </div>
        <a class="button-link panel-footer-button" href="/dashboard">↻ {h('Tüm odaları yenile' if lang == 'tr' else 'Refresh all rooms')}</a>
      </article>

      <aside class="card modern-panel">
        <div class="panel-title-row">
          <div class="panel-title-left">
            <span class="panel-icon">🗓️</span>
            <div>
              <h3>{h(t('create_reservation_request', lang))}</h3>
              <p class="muted small">{h('İhtiyacınıza uygun sınıf için rezervasyon talebinde bulunun.' if lang == 'tr' else 'Create a reservation request for the classroom you need.')}</p>
            </div>
          </div>
        </div>

        <div class="reservation-stats">
          <div class="reservation-mini-stat">
            <span class="panel-icon">🗓️</span>
            <div><small class="muted">{h(t('pending_requests_title', lang))}</small><strong>{pending_count}</strong></div>
          </div>
          <div class="reservation-mini-stat">
            <span class="panel-icon">✅</span>
            <div><small class="muted">{h(t('approved_requests', lang))}</small><strong>{approved_count}</strong></div>
          </div>
        </div>

        <form method="post" action="/requests/new" class="reservation-form-modern">
          <label>{h(t('room', lang))}
            <select name="room_id" required>{room_select}</select>
          </label>
          <label>{h(t('title', lang))}
            <input type="text" name="event_title" placeholder="{h(t('event_title_placeholder', lang))}" required>
          </label>
          <label>{h(t('type', lang))}
            <select name="event_type" required>{event_type_options_html("Workshop", lang)}</select>
          </label>
          <div class="form-row-2">
            <label>{h(t('start', lang))}
              <input type="datetime-local" name="requested_start" required>
            </label>
            <label>{h(t('end', lang))}
              <input type="datetime-local" name="requested_end" required>
            </label>
          </div>
          <div class="form-row-2">
            <label>{h(t('projector_label', lang))}
              <select name="projector_preference">
                <option value="">{h(t('any', lang))}</option>
                <option value="1">{h(t('required', lang))}</option>
              </select>
            </label>
            <label>{h(t('smart_board_label', lang))}
              <select name="smart_board_preference">
                <option value="">{h(t('any', lang))}</option>
                <option value="1">{h(t('required', lang))}</option>
              </select>
            </label>
          </div>
          <label>{h(t('minimum_power_outlets', lang))}
            <select name="power_outlet_preference">
              <option value="">{h(t('no_preference', lang))}</option>
              <option value="10">10+</option>
              <option value="20">20+</option>
              <option value="40">40+</option>
            </select>
          </label>
          <label>{h(t('note', lang))}
            <textarea name="request_note" placeholder="{h('Talebiniz hakkında ek bilgi yazabilirsiniz...' if lang == 'tr' else 'You can add extra details about your request...')}"></textarea>
          </label>
          <div class="reservation-form-actions">
            <button class="button-accent" type="submit">✈️ {h(t('submit_request', lang))}</button>
            <a class="button-link" href="/dashboard">🗑️ {h(t('clear', lang))}</a>
          </div>
        </form>
      </aside>
    </section>

    <script type="application/json" id="room-calendar-data">{calendar_payload_json}</script>
    <div class="scheduler-modal" data-room-scheduler hidden aria-hidden="true">
      <button type="button" class="scheduler-backdrop" data-close-scheduler aria-label="{h(t('close', lang))}"></button>
      <section class="scheduler-shell" role="dialog" aria-modal="true" aria-labelledby="room-calendar-title">
        <header class="scheduler-header">
          <div>
            <div class="eyebrow">{h(t('room_calendar', lang))}</div>
            <h2 id="room-calendar-title" data-calendar-title>{h(t('room_calendar', lang))}</h2>
            <div class="small muted" data-calendar-room-meta></div>
          </div>
          <button type="button" class="icon-button" data-close-scheduler data-close-scheduler-main aria-label="{h(t('close', lang))}">×</button>
        </header>
        <div class="scheduler-toolbar">
          <div class="scheduler-toolbar-group">
            <button type="button" class="button-secondary" data-calendar-prev aria-label="{h(t('previous_week', lang))}">‹</button>
            <button type="button" class="button-secondary" data-calendar-today>{h(t('today', lang))}</button>
            <button type="button" class="button-secondary" data-calendar-next aria-label="{h(t('next_week', lang))}">›</button>
            <span class="scheduler-range" data-calendar-range></span>
          </div>
          <div class="scheduler-legend small muted">
            <span><i class="legend-dot busy"></i> {h(t('busy', lang))}</span>
            <span><i class="legend-dot selected"></i> {h(t('selected_time', lang))}</span>
          </div>
        </div>
        <div class="scheduler-main">
          <div class="scheduler-grid-wrap">
            <div class="scheduler-grid" data-calendar-grid></div>
          </div>
          <aside class="scheduler-side">
            <h3>{h(t('reservation_details', lang))}</h3>
            <div class="selected-time-box">
              <strong>{h(t('selected_time', lang))}</strong>
              <div class="small" data-selected-time>{h(t('calendar_no_selection', lang))}</div>
              <div class="small muted" data-selection-status></div>
            </div>
            <form method="post" action="/requests/new" data-calendar-form>
              <input type="hidden" name="room_id" data-calendar-room-input>
              <input type="hidden" name="requested_start" data-calendar-start-input>
              <input type="hidden" name="requested_end" data-calendar-end-input>
              <label>{h(t('title', lang))}
                <input type="text" name="event_title" placeholder="{h(t('event_title_placeholder', lang))}" required>
              </label>
              <label>{h(t('type', lang))}
                <select name="event_type" required>
                  {event_type_options_html("Workshop", lang)}
                </select>
              </label>
              <label>{h(t('note', lang))}
                <textarea name="request_note" placeholder="{h(t('request_note_hint', lang))}"></textarea>
              </label>
              <button class="button-accent" type="submit" data-calendar-submit disabled>{h(t('reserve_selected_time', lang))}</button>
            </form>
          </aside>
        </div>
      </section>
    </div>
    {room_scheduler_script()}

    <section class="card spaced">
      <h3>{h(t('my_requests', lang))}</h3>
      <div class="table-wrap">
        <table class="table-lite request-table">
          <thead>
            <tr>
              <th>{h(t('title', lang))}</th>
              <th>{h(t('type', lang))}</th>
              <th>{h(t('room', lang))}</th>
              <th>{h(t('time', lang))}</th>
              <th>{h(t('status', lang))}</th>
              <th>{h(t('details', lang))}</th>
              <th>{h(t('actions', lang))}</th>
            </tr>
          </thead>
          <tbody>{request_rows_html}</tbody>
        </table>
      </div>
    </section>

    <section class="card spaced">
      <h3>{h(t('request_history', lang))}</h3>
      <div class="history-list">{history_rows}</div>
    </section>
    """
    return render_layout(t('student_dashboard', lang), content, user, lang, theme)


def academic_dashboard(user: sqlite3.Row, message: str = "", error: bool = False, lang: str = DEFAULT_LANGUAGE, theme: str = DEFAULT_THEME) -> str:
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
                   er.room_id, er.request_note, c.room_code, u.name AS requester_name
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
             AND (
                (
                  date(er.requested_start) = date(s.start_at)
                  AND datetime(er.requested_start) < datetime(s.end_at)
                  AND datetime(er.requested_end) > datetime(s.start_at)
                )
                OR (
                  s.recurrence_pattern IN ('Weekly', 'Biweekly')
                  AND (
                    CASE strftime('%w', er.requested_start)
                      WHEN '0' THEN 7
                      ELSE CAST(strftime('%w', er.requested_start) AS INTEGER)
                    END
                  ) = s.weekday
                  AND date(er.requested_start) >= date(s.start_at)
                  AND time(er.requested_start) < time(s.end_at)
                  AND time(er.requested_end) > time(s.start_at)
                  AND (
                    s.recurrence_pattern = 'Weekly'
                    OR ABS(CAST(julianday(date(er.requested_start)) - julianday(date(s.start_at)) AS INTEGER)) % 14 = 0
                  )
                )
             )
            JOIN Classrooms c ON c.room_id = er.room_id
            WHERE er.status IN ('Pending', 'Approved')
            ORDER BY datetime(er.requested_start)
            """
        ).fetchall()
        history = conn.execute(
            """
            SELECT rh.action, rh.previous_status, rh.new_status, rh.action_note, rh.created_at,
                   er.event_title, actor.name AS actor_name
            FROM Request_History rh
            JOIN Event_Requests er ON er.request_id = rh.request_id
            JOIN Users actor ON actor.user_id = rh.actor_id
            ORDER BY datetime(rh.created_at) DESC, rh.history_id DESC
            LIMIT 12
            """
        ).fetchall()
        utilization = conn.execute(
            """
            SELECT room_code, average_occupancy_rate, observation_count, latest_status, last_observed_at
            FROM v_room_utilization_summary
            ORDER BY average_occupancy_rate DESC, room_code
            LIMIT 6
            """
        ).fetchall()
        pending_alternatives = {
            row["request_id"]: find_alternative_rooms(
                conn,
                row["requested_start"],
                row["requested_end"],
                row["room_id"],
                row["request_id"],
            )
            for row in pending
        }

    flash = render_flash(message, error, lang)

    schedule_rows = "".join(
        f"""
        <div class="academic-item">
          <div class="academic-item-top">
            <strong>{h(row["title"])}</strong>
            <span class="badge info">{h(format_datetime(row["start_at"], lang))}</span>
          </div>
          <div class="small muted">{h(translate_schedule_type(row["schedule_type"], lang))} • {h(row["room_code"])} • {h(format_datetime(row["end_at"], lang))}</div>
        </div>
        """
        for row in my_schedule
    ) or f'<div class="academic-item muted">{h(t("no_schedule_assigned", lang))}</div>'

    coordination_rows = "".join(
        f"""
        <div class="academic-item">
          <div class="academic-item-top">
            <strong>{h(row["title"])}</strong>
            <span class="badge {'ok' if row['overlapping_event_requests'] == 0 else 'warn'}">{h(row["room_code"])}</span>
          </div>
          <div class="small muted">{h(t('occupancy_trend', lang))}: {h(row["prior_week_occupancy_rate"])}% • {h(t('overlapping_requests', lang))}: {h(row["overlapping_event_requests"])}</div>
          <div class="small muted">{h(format_datetime(row["start_at"], lang))}</div>
        </div>
        """
        for row in coordination
    ) or f'<div class="academic-item muted">{h(t("no_exam_records", lang))}</div>'

    pending_rows = []
    for row in pending:
        alternatives = pending_alternatives.get(row["request_id"], [])
        alternative_html = ""
        if alternatives:
            alternative_html = f"""
              <div class="academic-request-meta small muted">
                <strong>{h(t('alternative_rooms', lang))}:</strong>
                {"".join(f'<span class="badge info">{h(alt["room_code"])}</span>' for alt in alternatives)}
              </div>
            """
        note_html = f'<div class="small muted note-box">{h(row["request_note"])}</div>' if row["request_note"] else ""
        pending_rows.append(
            f"""
            <div class="academic-request-card">
              <div>
                <div class="academic-item-top">
                  <strong>{h(row["event_title"])}</strong>
                  <span class="badge warn">{h(translate_event_type(row["event_type"], lang))}</span>
                </div>
                <div class="academic-request-meta">
                  <span class="badge info">{h(row["requester_name"])}</span>
                  <span class="badge info">{h(row["room_code"])}</span>
                  <span class="badge info">{h(format_datetime(row["requested_start"], lang))}</span>
                  <span class="badge info">{h(format_datetime(row["requested_end"], lang))}</span>
                </div>
                {note_html}
                {alternative_html}
              </div>
              <form method="post" action="/requests/review" class="review-form">
                <input type="hidden" name="request_id" value="{h(row["request_id"])}">
                <label>{h(t('review_note', lang))}
                  <textarea name="decision_note" placeholder="{h(t('review_note_hint', lang))}"></textarea>
                </label>
                <div class="actions">
                  <button type="submit" name="decision" value="Approved">{h(t('approve', lang))}</button>
                  <button class="button-secondary" type="submit" name="decision" value="Rejected">{h(t('reject', lang))}</button>
                </div>
              </form>
            </div>
            """
        )

    conflict_list = "".join(
        f"""
        <div class="academic-item">
          <div class="academic-item-top">
            <strong>{h(row["event_title"])}</strong>
            <span class="badge danger">{h(t('conflict', lang))}</span>
          </div>
          <div class="small muted">{h(t('overlaps_with', lang).format(room=row["room_code"], schedule=row["schedule_title"]))}</div>
        </div>
        """
        for row in conflict_rows
    ) or f'<div class="academic-item"><div class="academic-item-top"><strong>{h(t("current_state", lang))}</strong><span class="badge ok">{h(t("clear", lang))}</span></div><div class="small muted">{h(t("no_active_conflict", lang))}</div></div>'

    utilization_rows = "".join(
        f"""
        <div class="academic-util-card">
          <div class="academic-item-top">
            <strong>{h(row["room_code"])}</strong>
            <span class="badge info">{h(translate_status(row["latest_status"] or "Unknown", lang))}</span>
          </div>
          <div class="academic-progress" style="--value:{max(0, min(100, float(row["average_occupancy_rate"] or 0)))}%"><span></span></div>
          <div class="small muted">{h(t('average_occupancy', lang))}: {h(row["average_occupancy_rate"])}% • {h(row["observation_count"])} {h(t('observations', lang))}</div>
          <div class="small muted">{h(t('last_seen', lang))}: {h(format_datetime(row["last_observed_at"], lang)) if row["last_observed_at"] else '-'}</div>
        </div>
        """
        for row in utilization
    ) or f'<div class="academic-util-card muted">{h(t("no_exam_records", lang))}</div>'

    history_rows = "".join(
        f"""
        <div class="academic-item">
          <div class="academic-item-top">
            <strong>{h(row["event_title"])}</strong>
            <span class="badge info">{h(translate_history_action(row["action"], lang))}</span>
          </div>
          <div class="small muted">{h(t("created_by", lang).format(actor=row["actor_name"]))} • {h(format_datetime(row["created_at"], lang))}</div>
          {f'<div class="small muted note-box">{h(row["action_note"])}</div>' if row["action_note"] else ''}
        </div>
        """
        for row in history
    ) or f'<div class="academic-item muted">{h(t("no_history_yet", lang))}</div>'

    first_name = user["name"].split()[0]
    content = f"""
    <div class="academic-page">
      <section class="academic-top">
        <div class="academic-hero-card">
          <div class="eyebrow">{h(t('academic_dashboard', lang))}</div>
          <div class="academic-title-row">
            <div>
              <h2>{h(t('welcome_back_dr', lang).format(name=first_name))}</h2>
              <p class="muted">{h(t('academic_description', lang))}</p>
            </div>
            <span class="badge info academic-badge">{h(user['department_name'])}</span>
          </div>
          <div class="academic-actions">
            <span class="academic-pill">{h(t('schedule_optimizer', lang))}</span>
            <span class="academic-pill">{h(t('conflict_detection', lang))}</span>
            <span class="academic-pill">{h(t('approval_workflow', lang))}</span>
          </div>
          {flash}
        </div>

        <aside class="academic-side-card">
          <div class="academic-section-head">
            <div>
              <h3>{h(t('conflict_logic_summary', lang))}</h3>
              <div class="small muted">{h(t('conflict_summary_text', lang))}</div>
            </div>
          </div>
          <div class="academic-metric-grid">
            <div class="academic-metric">
              <span class="academic-metric-icon">📝</span>
              <div><strong>{len(pending)}</strong><div class="small muted">{h(t('pending_requests_waiting', lang))}</div></div>
            </div>
            <div class="academic-metric">
              <span class="academic-metric-icon">📚</span>
              <div><strong>{len(coordination)}</strong><div class="small muted">{h(t('exam_coordination_records', lang))}</div></div>
            </div>
            <div class="academic-metric">
              <span class="academic-metric-icon">⚠️</span>
              <div><strong>{len(conflict_rows)}</strong><div class="small muted">{h(t('active_overlaps', lang))}</div></div>
            </div>
          </div>
        </aside>
      </section>

      <section class="academic-main-grid">
        <article class="academic-section">
          <div class="academic-section-head">
            <div>
              <h3>{h(t('my_schedule', lang))}</h3>
              <div class="small muted">{h(t('schedule_optimizer', lang))}</div>
            </div>
          </div>
          <div class="academic-list">{schedule_rows}</div>
        </article>

        <article class="academic-section">
          <div class="academic-section-head">
            <div>
              <h3>{h(t('exam_coordination', lang))}</h3>
              <div class="small muted">{h(t('conflict_detection', lang))}</div>
            </div>
          </div>
          <div class="academic-list">{coordination_rows}</div>
        </article>
      </section>

      <section class="academic-section">
        <div class="academic-section-head">
          <div>
            <h3>{h(t('room_utilization_snapshot', lang))}</h3>
            <div class="small muted">{h(t('average_occupancy', lang))} / {h(t('observations', lang))}</div>
          </div>
        </div>
        <div class="academic-util-grid">{utilization_rows}</div>
      </section>

      <section class="academic-main-grid">
        <article class="academic-section">
          <div class="academic-section-head">
            <div>
              <h3>{h(t('pending_requests_title', lang))}</h3>
              <div class="small muted">{h(t('approval_workflow', lang))}</div>
            </div>
            <span class="badge warn">{len(pending)}</span>
          </div>
          <div class="academic-pending-grid">
            {"".join(pending_rows) or f'<div class="academic-item muted">{h(t("no_pending_requests", lang))}</div>'}
          </div>
        </article>

        <aside class="academic-section">
          <div class="academic-section-head">
            <div>
              <h3>{h(t('conflict_detection_feed', lang))}</h3>
              <div class="small muted">{h(t('current_state', lang))}</div>
            </div>
          </div>
          <div class="academic-list">{conflict_list}</div>
        </aside>
      </section>

      <section class="academic-section">
        <div class="academic-section-head">
          <div>
            <h3>{h(t('request_history', lang))}</h3>
            <div class="small muted">{h(t('last_seen', lang))}</div>
          </div>
        </div>
        <div class="academic-list">{history_rows}</div>
      </section>
    </div>
    """
    return render_layout(t('academic_dashboard', lang), content, user, lang, theme)


class KMFHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        ensure_database()
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        lang = get_language(self, params) or DEFAULT_LANGUAGE
        theme = get_theme(self, params)
        lang_cookie = None
        if params.get("lang", [""])[0].lower() in SUPPORTED_LANGUAGES:
            lang_cookie = build_language_cookie(lang)

        user = get_current_user(self)

        if parsed.path == "/":
            if user is not None:
                self.redirect("/dashboard", cookies_header=lang_cookie)
                return

            content = f"""
            <section class="hero">
              <div class="hero-grid login-choice-panel">
                <div class="login-choice-intro">
                  <div class="eyebrow">{h(t('brand_eyebrow', lang))}</div>
                  <h2>{h(t('choose_login_type', lang))}</h2>
                  <div class="title-underline"></div>
                  <p class="muted">{h(t('choose_login_description', lang))}</p>
                </div>
                <div class="login-cards">
                  <a class="role-card student-card" href="/student-login?lang={h(lang)}&theme={h(theme)}">
                    <span class="role-icon" aria-hidden="true">
                      <svg viewBox="0 0 64 64" fill="none" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M10 22 32 12l22 10-22 10L10 22Z"></path>
                        <path d="M20 29v9c0 6 5 11 12 11s12-5 12-11v-9"></path>
                        <path d="M18 52c3-7 8-10 14-10s11 3 14 10"></path>
                      </svg>
                    </span>
                    <h3>{h(t('student_login', lang))}</h3>
                    <span class="role-mini-line"></span>
                    <p class="muted">{h(t('student_sign_in_description', lang))}</p>
                    <span class="role-button student">
                      {h(t('student_login', lang))}
                      <span class="role-arrow">→</span>
                    </span>
                  </a>

                  <a class="role-card academic-card" href="/academic-login?lang={h(lang)}&theme={h(theme)}">
                    <span class="role-icon" aria-hidden="true">
                      <svg viewBox="0 0 64 64" fill="none" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="32" cy="21" r="10"></circle>
                        <path d="M16 52c2-11 8-17 16-17s14 6 16 17"></path>
                        <path d="M28 38l4 8 4-8"></path>
                      </svg>
                    </span>
                    <h3>{h(t('academic_login', lang))}</h3>
                    <span class="role-mini-line"></span>
                    <p class="muted">{h(t('academic_sign_in_description', lang))}</p>
                    <span class="role-button academic">
                      {h(t('academic_login', lang))}
                      <span class="role-arrow">→</span>
                    </span>
                  </a>
                </div>
              </div>
            </section>
            """
            self.respond_html(render_layout(t("choose_login_type", lang), content, None, lang, theme), cookies_header=lang_cookie)
            return

        if parsed.path == "/signin" or parsed.path == "/student-login":
            if user is not None:
                self.redirect("/dashboard", cookies_header=lang_cookie)
                return
            self.respond_html(signin_page(
                params.get("message", [""])[0],
                params.get("error", ["0"])[0] == "1",
                lang,
                theme,
                "Student"
            ), cookies_header=lang_cookie)
            return

        if parsed.path == "/academic-login":
            if user is not None:
                self.redirect("/dashboard", cookies_header=lang_cookie)
                return
            self.respond_html(signin_page(
                params.get("message", [""])[0],
                params.get("error", ["0"])[0] == "1",
                lang,
                theme,
                "Academic"
            ), cookies_header=lang_cookie)
            return

        if parsed.path == "/signup":
            if user is not None:
                self.redirect("/dashboard", cookies_header=lang_cookie)
                return
            self.respond_html(signup_page(params.get("message", [""])[0], params.get("error", ["0"])[0] == "1", lang, theme), cookies_header=lang_cookie)
            return

        if parsed.path == "/proto":
            proto_html = (BASE_DIR / "ui-prototype.html").read_text(encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if lang_cookie is not None:
                for morsel in lang_cookie.values():
                    self.send_header("Set-Cookie", morsel.OutputString())
            self.end_headers()
            self.wfile.write(proto_html.encode("utf-8"))
            return

        if parsed.path == "/dashboard":
            if user is None:
                self.redirect("/?message=Please+sign+in+first&error=1", cookies_header=lang_cookie)
                return

            message = params.get("message", [""])[0]
            error = params.get("error", ["0"])[0] == "1"
            if user["role"] == "Student":
                self.respond_html(student_dashboard(user, params, message, error, lang, theme), cookies_header=lang_cookie)
                return
            self.respond_html(academic_dashboard(user, message, error, lang, theme), cookies_header=lang_cookie)
            return

        self.respond_html(render_layout("Not Found", '<section class="hero"><h2>Page not found</h2></section>', None, lang, theme), status=404, cookies_header=lang_cookie)

    def do_POST(self) -> None:
      ensure_database()
      parsed = urlparse(self.path)
      form = parse_post_data(self)
      user = get_current_user(self)
      # preserve language selection across POST redirects
      params = parse_qs(parsed.query)
      lang = get_language(self, params) or DEFAULT_LANGUAGE
      theme = get_theme(self, params)
      lang_cookie = None
      if params.get("lang", [""])[0].lower() in SUPPORTED_LANGUAGES:
        lang_cookie = build_language_cookie(lang)

      if parsed.path == "/login":
        email = form.get("email", "").strip().lower()
        password = form.get("password", "")
        expected_role = form.get("expected_role", "").strip()
        login_page = "/academic-login" if expected_role == "Academic" else "/student-login"

        if not email or not password:
          self.redirect(f"{login_page}?message=Email+and+password+are+required&error=1", cookies_header=lang_cookie)
          return

        with get_connection() as conn:
          db_user = conn.execute(
            """
            SELECT user_id, name, email, role, password_hash
            FROM Users
            WHERE LOWER(email) = ?
            """,
            (email,),
          ).fetchone()
          if db_user is not None and verify_password(password, db_user["password_hash"]):
            if should_upgrade_password_hash(db_user["password_hash"]):
              conn.execute(
                """
                UPDATE Users
                SET password_hash = ?
                WHERE user_id = ?
                """,
                (hash_password(password), db_user["user_id"]),
              )
              conn.commit()
          else:
            db_user = None

        if db_user is None:
          self.redirect(f"{login_page}?message=Invalid+email+or+password&error=1", cookies_header=lang_cookie)
          return

        if expected_role in ("Student", "Academic") and db_user["role"] != expected_role:
          mismatch_message = quote_plus(t("account_role_mismatch", lang))
          self.redirect(
            f"{login_page}?message={mismatch_message}&error=1",
            cookies_header=lang_cookie,
          )
          return

        session_id = create_session(db_user["user_id"])
        cookie = build_session_cookie(session_id)

        self.send_response(303)
        self.send_header("Location", "/dashboard")
        for morsel in cookie.values():
          self.send_header("Set-Cookie", morsel.OutputString())
        if lang_cookie is not None:
          for morsel in lang_cookie.values():
            self.send_header("Set-Cookie", morsel.OutputString())
        self.end_headers()
        return

        session_id = create_session(db_user["user_id"])
        cookie = build_session_cookie(session_id)

        self.send_response(303)
        self.send_header("Location", "/dashboard")
        for morsel in cookie.values():
            self.send_header("Set-Cookie", morsel.OutputString())

        if lang_cookie is not None:
            for morsel in lang_cookie.values():
                self.send_header("Set-Cookie", morsel.OutputString())

        self.end_headers()
        return

        session_id = create_session(db_user["user_id"])
        cookie = build_session_cookie(session_id)

        self.send_response(303)
        self.send_header("Location", "/dashboard")
        for morsel in cookie.values():
          self.send_header("Set-Cookie", morsel.OutputString())
        if lang_cookie is not None:
          for morsel in lang_cookie.values():
            self.send_header("Set-Cookie", morsel.OutputString())
        self.end_headers()
        return

      if parsed.path == "/register":
        name = form.get("name", "").strip()
        email = form.get("email", "").strip().lower()
        department_id = form.get("department_id", "").strip()
        password = form.get("password", "")
        confirm_password = form.get("confirm_password", "")

        if not all([name, email, department_id, password, confirm_password]):
          self.redirect("/signup?message=All+fields+are+required&error=1", cookies_header=lang_cookie)
          return
        if password != confirm_password:
          self.redirect("/signup?message=Passwords+do+not+match&error=1", cookies_header=lang_cookie)
          return
        policy_error = password_policy_error(password)
        if policy_error is not None:
          self.redirect(f"/signup?message={quote_plus(policy_error)}&error=1", cookies_header=lang_cookie)
          return
        if not is_valid_email(email):
          self.redirect("/signup?message=Invalid+email+address&error=1", cookies_header=lang_cookie)
          return

        password_hash = hash_password(password)
        try:
          department_int = int(department_id)
          with get_connection() as conn:
            existing_email = conn.execute(
              """
              SELECT 1
              FROM Users
              WHERE LOWER(email) = ?
              """,
              (email,),
            ).fetchone()
            if existing_email is not None:
              self.redirect("/signup?message=Email+already+exists&error=1", cookies_header=lang_cookie)
              return
            department_exists = conn.execute(
              "SELECT 1 FROM Departments WHERE department_id = ? AND is_active = 1",
              (department_int,),
            ).fetchone()
            if department_exists is None:
              self.redirect("/signup?message=Invalid+department&error=1", cookies_header=lang_cookie)
              return
            conn.execute(
              """
              INSERT INTO Users (department_id, name, email, password_hash, role)
              VALUES (?, ?, ?, ?, 'Student')
              """,
              (department_int, name, email, password_hash),
            )
            conn.commit()
          self.redirect("/signin?message=Account+created+successfully.+Please+sign+in.", cookies_header=lang_cookie)
        except ValueError:
          self.redirect("/signup?message=Invalid+department&error=1", cookies_header=lang_cookie)
        except sqlite3.IntegrityError:
          self.redirect("/signup?message=Email+already+exists&error=1", cookies_header=lang_cookie)
        return

      if parsed.path == "/logout":
        self.clear_session_and_redirect()
        return

      if parsed.path == "/requests/new":
        if user is None or user["role"] != "Student":
          self.redirect("/?message=Only+students+can+submit+requests&error=1", cookies_header=lang_cookie)
          return

        requested_start = normalize_datetime_input(form.get("requested_start", ""))
        requested_end = normalize_datetime_input(form.get("requested_end", ""))
        room_id: int | None = None
        try:
          room_id = int(form["room_id"])
          with get_connection() as conn:
            if find_conflicting_event_request(conn, room_id, requested_start, requested_end):
              raise sqlite3.IntegrityError(EVENT_REQUEST_CONFLICT_MESSAGE)
            cursor = conn.execute(
              """
              INSERT INTO Event_Requests (
                requester_id, room_id, event_title, event_type,
                requested_start, requested_end, request_note
              ) VALUES (?, ?, ?, ?, ?, ?, ?)
              """,
              (
                user["user_id"],
                room_id,
                form["event_title"],
                form["event_type"],
                requested_start,
                requested_end,
                form.get("request_note", "").strip() or None,
              ),
            )
            conn.execute(
              """
              INSERT INTO Request_History (
                request_id, actor_id, action, previous_status, new_status, action_note
              ) VALUES (?, ?, 'Created', NULL, 'Pending', ?)
              """,
              (cursor.lastrowid, user["user_id"], form.get("request_note", "").strip() or None),
            )
            conn.commit()
          self.redirect("/dashboard?message=Request+submitted+successfully", cookies_header=lang_cookie)
        except (sqlite3.IntegrityError, sqlite3.OperationalError, KeyError, ValueError) as exc:
          alternatives: list[sqlite3.Row] = []
          if requested_start and requested_end and room_id is not None:
            with get_connection() as conn:
              alternatives = find_alternative_rooms(conn, requested_start, requested_end, room_id)
          self.redirect(f"/dashboard?message={quote_plus(conflict_feedback(str(exc), alternatives, lang))}&error=1", cookies_header=lang_cookie)
        return

      if parsed.path == "/requests/update":
        if user is None or user["role"] != "Student":
          self.redirect("/?message=Only+students+can+submit+requests&error=1", cookies_header=lang_cookie)
          return

        requested_start = normalize_datetime_input(form.get("requested_start", ""))
        requested_end = normalize_datetime_input(form.get("requested_end", ""))
        room_id: int | None = None
        request_id: int | None = None
        try:
          room_id = int(form["room_id"])
          request_id = int(form["request_id"])
          with get_connection() as conn:
            current = conn.execute(
              """
              SELECT status
              FROM Event_Requests
              WHERE request_id = ? AND requester_id = ? AND status = 'Pending'
              """,
              (request_id, user["user_id"]),
            ).fetchone()
            if current is None:
              self.redirect("/dashboard?message=Only+pending+requests+can+be+changed.&error=1", cookies_header=lang_cookie)
              return
            if find_conflicting_event_request(conn, room_id, requested_start, requested_end, request_id):
              raise sqlite3.IntegrityError(EVENT_REQUEST_CONFLICT_MESSAGE)
            conn.execute(
              """
              UPDATE Event_Requests
              SET room_id = ?,
                  event_title = ?,
                  event_type = ?,
                  requested_start = ?,
                  requested_end = ?,
                  request_note = ?
              WHERE request_id = ? AND requester_id = ? AND status = 'Pending'
              """,
              (
                room_id,
                form["event_title"].strip(),
                form["event_type"],
                requested_start,
                requested_end,
                form.get("request_note", "").strip() or None,
                request_id,
                user["user_id"],
              ),
            )
            conn.execute(
              """
              INSERT INTO Request_History (
                request_id, actor_id, action, previous_status, new_status, action_note
              ) VALUES (?, ?, 'Updated', 'Pending', 'Pending', ?)
              """,
              (request_id, user["user_id"], form.get("request_note", "").strip() or None),
            )
            conn.commit()
          self.redirect("/dashboard?message=Request+updated+successfully", cookies_header=lang_cookie)
        except (sqlite3.IntegrityError, sqlite3.OperationalError, KeyError, ValueError) as exc:
          alternatives: list[sqlite3.Row] = []
          if requested_start and requested_end and room_id is not None and request_id is not None:
            with get_connection() as conn:
              alternatives = find_alternative_rooms(conn, requested_start, requested_end, room_id, request_id)
          self.redirect(f"/dashboard?message={quote_plus(conflict_feedback(str(exc), alternatives, lang))}&error=1", cookies_header=lang_cookie)
        return

      if parsed.path == "/requests/cancel":
        if user is None or user["role"] != "Student":
          self.redirect("/?message=Only+students+can+submit+requests&error=1", cookies_header=lang_cookie)
          return

        try:
          request_id = int(form["request_id"])
          with get_connection() as conn:
            cursor = conn.execute(
              """
              UPDATE Event_Requests
              SET status = 'Cancelled',
                  decision_at = CURRENT_TIMESTAMP,
                  rejection_reason = NULL,
                  decision_note = ?
              WHERE request_id = ? AND requester_id = ? AND status = 'Pending'
              """,
              (t("request_cancelled_success", lang), request_id, user["user_id"]),
            )
            if cursor.rowcount == 0:
              self.redirect("/dashboard?message=Only+pending+requests+can+be+changed.&error=1", cookies_header=lang_cookie)
              return
            conn.execute(
              """
              INSERT INTO Request_History (
                request_id, actor_id, action, previous_status, new_status, action_note
              ) VALUES (?, ?, 'Cancelled', 'Pending', 'Cancelled', ?)
              """,
              (request_id, user["user_id"], t("request_cancelled_success", lang)),
            )
            conn.commit()
          self.redirect("/dashboard?message=Request+cancelled+successfully", cookies_header=lang_cookie)
        except (sqlite3.IntegrityError, sqlite3.OperationalError, KeyError, ValueError) as exc:
          self.redirect(f"/dashboard?message={quote_plus(str(exc))}&error=1", cookies_header=lang_cookie)
        return

      if parsed.path == "/requests/review":
        if user is None or user["role"] != "Academic":
          self.redirect("/?message=Only+academic+users+can+review+requests&error=1", cookies_header=lang_cookie)
          return

        decision = form.get("decision", "")
        if decision not in {"Approved", "Rejected"}:
          self.redirect("/dashboard?message=Unsupported+decision&error=1", cookies_header=lang_cookie)
          return
        decision_note = form.get("decision_note", "").strip()
        if decision == "Rejected" and not decision_note:
          self.redirect("/dashboard?message=Rejection+reason+is+required.&error=1", cookies_header=lang_cookie)
          return

        try:
          request_id = int(form["request_id"])
          with get_connection() as conn:
            target_request = conn.execute(
              """
              SELECT request_id, room_id, requested_start, requested_end
              FROM Event_Requests
              WHERE request_id = ? AND status = 'Pending'
              """,
              (request_id,),
            ).fetchone()
            if target_request is None:
              self.redirect("/dashboard?message=Only+pending+requests+can+be+changed.&error=1", cookies_header=lang_cookie)
              return
            if decision == "Approved":
              cursor = conn.execute(
                """
                UPDATE Event_Requests
                SET status = 'Approved',
                  approved_by = ?,
                  decision_at = CURRENT_TIMESTAMP,
                  rejection_reason = NULL,
                  decision_note = ?
                WHERE request_id = ? AND status = 'Pending'
                """,
                (user["user_id"], decision_note or None, request_id),
              )
            else:
              cursor = conn.execute(
                """
                UPDATE Event_Requests
                SET status = 'Rejected',
                  approved_by = ?,
                  decision_at = CURRENT_TIMESTAMP,
                  rejection_reason = ?,
                  decision_note = ?
                WHERE request_id = ? AND status = 'Pending'
                """,
                (user["user_id"], decision_note, decision_note, request_id),
              )
            if cursor.rowcount == 0:
              self.redirect("/dashboard?message=Only+pending+requests+can+be+changed.&error=1", cookies_header=lang_cookie)
              return
            conn.execute(
              """
              INSERT INTO Request_History (
                request_id, actor_id, action, previous_status, new_status, action_note
              ) VALUES (?, ?, ?, 'Pending', ?, ?)
              """,
              (request_id, user["user_id"], decision, decision, decision_note or None),
            )
            conn.commit()
          self.redirect(f"/dashboard?message=Request+{decision.lower()}+successfully", cookies_header=lang_cookie)
        except (sqlite3.IntegrityError, sqlite3.OperationalError, KeyError, ValueError) as exc:
          alternatives: list[sqlite3.Row] = []
          if "target_request" in locals() and target_request is not None:
            with get_connection() as conn:
              alternatives = find_alternative_rooms(
                conn,
                target_request["requested_start"],
                target_request["requested_end"],
                target_request["room_id"],
                target_request["request_id"],
              )
          self.redirect(f"/dashboard?message={quote_plus(conflict_feedback(str(exc), alternatives, lang))}&error=1", cookies_header=lang_cookie)
        return

      self.respond_html(render_layout("Not Found", '<section class="hero"><h2>Page not found</h2></section>', None, lang, theme), status=404, cookies_header=lang_cookie)

    def respond_html(self, body: str, status: int = 200, cookies_header: cookies.SimpleCookie | None = None) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if cookies_header is not None:
            for morsel in cookies_header.values():
                self.send_header("Set-Cookie", morsel.OutputString())
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location: str, cookies_header: cookies.SimpleCookie | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if cookies_header is not None:
            for morsel in cookies_header.values():
                self.send_header("Set-Cookie", morsel.OutputString())
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
        cookie[SESSION_COOKIE]["httponly"] = True
        cookie[SESSION_COOKIE]["samesite"] = "Lax"
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
