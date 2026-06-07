from __future__ import annotations

import html
import hashlib
import hmac
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
ALLOWED_EVENT_TYPES = {"Workshop", "Club", "Makeup", "Exam", "Seminar"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SESSIONS: dict[str, dict[str, object]] = {}


def ensure_database() -> None:
    required_objects = {
        ("table", "Users"),
        ("table", "Request_Audit_Log"),
        ("view", "v_student_live_status"),
        ("view", "v_exam_coordination"),
        ("view", "v_room_utilization_summary"),
    }
    initialize = not DB_PATH.exists()

    if not initialize:
        with sqlite3.connect(DB_PATH) as conn:
            existing_objects = {
                tuple(row)
                for row in conn.execute(
                    """
                    SELECT type, name
                    FROM sqlite_master
                    WHERE (type = 'table' OR type = 'view')
                      AND name IN (
                        'Users',
                        'Request_Audit_Log',
                        'v_student_live_status',
                        'v_exam_coordination',
                        'v_room_utilization_summary'
                      )
                    """
                ).fetchall()
            }
            initialize = not required_objects.issubset(existing_objects)

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


def hash_password(password: str, salt: str | None = None) -> str:
    """Return a salted PBKDF2 hash suitable for this standard-library demo."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"{PASSWORD_ALGORITHM}${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify new PBKDF2 hashes and keep old SHA-256 demo hashes usable."""
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
        except (ValueError, TypeError):
            return False

    # Backward compatibility for databases created before the security pass.
    legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy_hash, stored_hash)


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def parse_datetime_local(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def safe_int(value: str, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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

    # Sliding expiration keeps active demo users signed in while still expiring stale sessions.
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
DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = {"en", "tr"}

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "brand_eyebrow": "YTU Mathematical Engineering • Applied SQL",
        "brand_title": "KMF Smart Classroom & Event Management System",
        "brand_subtitle": "Yildiz-inspired classroom intelligence dashboard",
        "logo_click_hint": "Click the logo anytime to return to your home screen.",
        "signed_in_as": "Signed in as",
        "sign_out": "Sign out",
        "sqlite_note": "SQLite-backed demo application",
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
        "live_map_info": "The live map is powered by `v_student_live_status`, which hides sensitive academic details and exposes only what students need for room discovery.",
        "pill_live_availability": "Live availability",
        "pill_heatmap": "Heatmap occupancy",
        "pill_smart_filter": "Smart equipment filter",
        "quick_filter": "Quick Filter",
        "visible_rooms": "visible rooms after current filters",
        "rooms_ready": "rooms currently calm or ready to use",
        "approved_requests": "approved requests for this student",
        "block_label": "Block",
        "floor": "Floor",
        "seats": "seats",
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
        "status": "Status",
        "no_requests_yet": "No reservation request has been created by this student yet.",
        "end": "End",
        "event_type_workshop": "Workshop",
        "event_type_club": "Club",
        "event_type_makeup": "Make-up exam",
        "date_format": "dd.mm.yyyy",
        "event_type_exam": "Exam",
        "event_type_seminar": "Seminar",
        "academic_dashboard": "Academic Dashboard",
        "welcome_back_dr": "Welcome back, Dr. {name}",
        "academic_description": "The academic dashboard combines `Academic_Schedules`, `Event_Requests`, and `v_exam_coordination` so planning decisions remain data-driven and safe.",
        "schedule_optimizer": "Schedule optimizer",
        "conflict_detection": "Conflict detection",
        "approval_workflow": "Approval workflow",
        "conflict_logic_summary": "Conflict Logic Summary",
        "conflict_summary_text": "A booking is rejected when `new.start_at < existing.end_at` and `new.end_at > existing.start_at` for the same room. This rule is enforced at database level through triggers.",
        "pending_requests_waiting": "Pending requests waiting for academic review",
        "exam_coordination_records": "Exam coordination records in analytical view",
        "active_overlaps": "active overlaps shown in conflict feed",
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
        "request_submitted_success": "Request submitted successfully",
        "only_students_submit": "Only students can submit requests",
        "only_academics_review": "Only academic users can review requests",
        "unsupported_decision": "Unsupported decision",
        "request_approved_success": "Request approved successfully",
        "request_rejected_success": "Request rejected successfully",
        "all_fields_required": "All fields are required",
        "invalid_email": "Invalid email address",
        "passwords_do_not_match": "Passwords do not match",
        "account_created": "Account created successfully. Please sign in.",
        "email_exists": "Email already exists",
        "demo_credentials_title": "Demo credentials",
        "demo_credentials_text": "Use any seeded email below with password Demo123!",
        "demo_student": "Student",
        "demo_academic": "Academic",
        "status": "Status",
        "percent_full": "{percent}% full",
        "no_schedule_assigned": "No schedule assigned to this academic yet.",
        "no_exam_records": "No exam records are available.",
        "occupancy_trend": "Occupancy trend",
        "overlapping_requests": "Overlapping requests",
        "conflict": "Conflict",
        "overlaps_with": "overlaps with",
        "room_utilization_snapshot": "Room Utilization Snapshot",
        "average_occupancy": "Average occupancy",
        "observations": "observations",
        "last_seen": "Last seen",
        "yes": "Yes",
        "no": "No",
    },
    "tr": {
        "brand_eyebrow": "YTÜ Matematik Mühendisliği • Uygulamalı SQL",
        "brand_title": "KMF Akıllı Sınıf ve Etkinlik Yönetim Sistemi",
        "brand_subtitle": "Yıldız ilhamlı sınıf zeka panosu",
        "logo_click_hint": "Ev ekranına geri dönmek için her zaman logoya tıklayabilirsiniz.",
        "signed_in_as": "Giriş yapan",
        "sign_out": "Çıkış yap",
        "sqlite_note": "SQLite destekli demo uygulama",
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
        "status": "Durum",
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
        "academic_description": "Akademik panel, planlama kararlarının veri tabanlı ve güvenli kalması için `Academic_Schedules`, `Event_Requests` ve `v_exam_coordination` kaynaklarını birleştirir.",
        "schedule_optimizer": "Program optimize edici",
        "conflict_detection": "Çakışma tespiti",
        "approval_workflow": "Onay süreci",
        "conflict_logic_summary": "Çakışma Mantığı Özeti",
        "conflict_summary_text": "Aynı oda için `new.start_at < existing.end_at` ve `new.end_at > existing.start_at` olduğunda rezervasyon reddedilir. Bu kural veritabanı tetikleyicileriyle uygulanır.",
        "pending_requests_waiting": "Akademik inceleme bekleyen talepler",
        "exam_coordination_records": "Analitik görünümdeki sınav koordinasyon kayıtları",
        "active_overlaps": "çakışma beslemesinde gösterilen aktif çakışmalar",
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
        "request_submitted_success": "Talep başarıyla gönderildi",
        "only_students_submit": "Sadece öğrenciler talep gönderebilir",
        "only_academics_review": "Sadece akademik kullanıcılar talepleri inceleyebilir",
        "unsupported_decision": "Desteklenmeyen karar",
        "request_approved_success": "Talep başarıyla onaylandı",
        "request_rejected_success": "Talep başarıyla reddedildi",
        "all_fields_required": "Tüm alanlar zorunludur",
        "invalid_email": "Geçersiz e-posta adresi",
        "passwords_do_not_match": "Parolalar eşleşmiyor",
        "account_created": "Hesap başarıyla oluşturuldu. Lütfen giriş yapın.",
        "email_exists": "E-posta zaten mevcut",
        "demo_credentials_title": "Demo giriş bilgileri",
        "demo_credentials_text": "Aşağıdaki örnek e-postalardan birini Demo123! parolasıyla kullanın",
        "demo_student": "Öğrenci",
        "demo_academic": "Akademisyen",
        "status": "Durum",
        "percent_full": "%{percent} dolu",
        "no_schedule_assigned": "Bu akademisyene henüz program atanmamış.",
        "no_exam_records": "Sınav kaydı bulunmuyor.",
        "occupancy_trend": "Doluluk eğilimi",
        "overlapping_requests": "Çakışan talepler",
        "conflict": "Çakışma",
        "overlaps_with": "ile çakışıyor",
        "room_utilization_snapshot": "Oda Kullanım Özeti",
        "average_occupancy": "Ortalama doluluk",
        "observations": "gözlem",
        "last_seen": "Son görülme",
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
        "Available": {"en": "Available", "tr": "Mevcut"},
    "Reserved": {"en": "Reserved", "tr": "Rezerve"},
    "Occupied": {"en": "Occupied", "tr": "Dolu"},
    "Maintenance": {"en": "Maintenance", "tr": "Bakım"},
    "maintenance": {"en": "Maintenance", "tr": "Bakım"},
        "Unknown": {"en": "Unknown", "tr": "Bilinmiyor"},
    }
    return status_map.get(status, {"en": status, "tr": status}).get(lang, status)


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


def translate_event_type(event_type: str | None, lang: str) -> str:
    if not event_type:
        return ""
    key = f"event_type_{event_type.strip().lower()}"
    # fallback to raw value when translation key missing
    return TRANSLATIONS.get(lang, TRANSLATIONS[DEFAULT_LANGUAGE]).get(key, event_type)


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
      .locale-toggle {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        justify-content: flex-end;
      }
      .locale-toggle a {
        color: var(--ink);
        opacity: 0.7;
      }
      .locale-toggle a.active {
        opacity: 1;
        font-weight: 700;
      }
      .locale-toggle span {
        color: var(--muted);
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


def render_layout(title: str, content: str, user: sqlite3.Row | None = None, lang: str = DEFAULT_LANGUAGE) -> str:
    home_link = "/dashboard" if user is not None else "/"
    user_html = ""
    if user is not None:
        user_html = f"""
        <div class="toolbar">
          <div>
            <div class="small muted">{h(t('signed_in_as', lang))}</div>
            <strong>{h(user['name'])}</strong>
            <div class="small muted">{h(user['role'])} • {h(user['email'])}</div>
            <div class="small muted">{h(t('logo_click_hint', lang))}</div>
          </div>
          <form method="post" action="/logout" class="inline-form">
            <button class="button-secondary" type="submit">{h(t('sign_out', lang))}</button>
          </form>
        </div>
        """

    locale_links = f"""
      <div class="locale-toggle">
        <a href="?lang=en" class="{'active' if lang == 'en' else ''}">EN</a>
        <span>|</span>
        <a href="?lang=tr" class="{'active' if lang == 'tr' else ''}">TR</a>
      </div>
    """

    return f"""<!DOCTYPE html>
<html lang="{h(lang)}">
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
        {user_html or f'<div class="muted small">{h(t("sqlite_note", lang))}</div>'}
        {locale_links}
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


def signin_page(message: str = "", error: bool = False, lang: str = DEFAULT_LANGUAGE) -> str:
    flash = ""
    if message:
        flash_class = "error" if error else "success"
        flash = f'<div class="flash {flash_class}">{h(message)}</div>'

    utilization_rows = "".join(
        f"""
        <div class="stat-box">
          <div class="stat-row">
            <strong>{h(row["room_code"])}</strong>
            <span class="badge info">{h(translate_status(row["latest_status"] or "Unknown", lang))}</span>
          </div>
          <div class="small muted">{h(t('average_occupancy', lang))}: {h(row["average_occupancy_rate"])}% • {h(row["observation_count"])} {h(t('observations', lang))}</div>
          <div class="small muted">{h(t('last_seen', lang))}: {h(row["last_observed_at"] or '-')}</div>
        </div>
        """
        for row in utilization
    )

    content = f"""
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">{h(t('brand_eyebrow', lang))}</div>
          <h2>{h(t('sign_in_title', lang))}</h2>
          <p class="muted">
            {h(t('sign_in_description', lang))}
          </p>
          {flash}
          <div class="spaced">
            <p class="small muted">{h(t('no_account', lang))} <a href="/signup">{h(t('sign_up_as_student', lang))}</a></p>
          </div>
          <div class="card spaced">
            <h3>{h(t('demo_credentials_title', lang))}</h3>
            <p class="small muted">{h(t('demo_credentials_text', lang))}</p>
            <div class="stats">
              <div class="list-row"><strong>{h(t('demo_student', lang))}</strong><span class="small">can.yilmaz@std.yildiz.edu.tr</span></div>
              <div class="list-row"><strong>{h(t('demo_academic', lang))}</strong><span class="small">ayse.demir@ytu.edu.tr</span></div>
            </div>
          </div>
        </div>
        <div class="card">
          <form method="post" action="/login">
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
    return render_layout(t('sign_in_title', lang), content, None, lang)


def signup_page(message: str = "", error: bool = False, lang: str = DEFAULT_LANGUAGE) -> str:
    with get_connection() as conn:
        departments = conn.execute(
            "SELECT department_id, department_name FROM Departments ORDER BY department_name"
        ).fetchall()

    flash = ""
    if message:
        flash_class = "error" if error else "success"
        flash = f'<div class="flash {flash_class}">{h(message)}</div>'

    dept_options = "".join(
        f'<option value="{row["department_id"]}">{h(row["department_name"])}</option>'
        for row in departments
    )

    utilization_rows = "".join(
        f"""
        <div class="stat-box">
          <div class="stat-row">
            <strong>{h(row["room_code"])}</strong>
            <span class="badge info">{h(translate_status(row["latest_status"] or "Unknown", lang))}</span>
          </div>
          <div class="small muted">{h(t('average_occupancy', lang))}: {h(row["average_occupancy_rate"])}% • {h(row["observation_count"])} {h(t('observations', lang))}</div>
          <div class="small muted">{h(t('last_seen', lang))}: {h(row["last_observed_at"] or '-')}</div>
        </div>
        """
        for row in utilization
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
    return render_layout(t('create_account_title', lang), content, None, lang)


def student_dashboard(user: sqlite3.Row, params: dict[str, list[str]], message: str = "", error: bool = False, lang: str = DEFAULT_LANGUAGE) -> str:
    projector = sql_bool(params.get("projector", [""])[0])
    smart_board = sql_bool(params.get("smart_board", [""])[0])
    min_outlets = params.get("min_outlets", [""])[0]
    min_outlets_value = safe_int(min_outlets) if min_outlets else None
    if min_outlets and min_outlets_value is None:
        min_outlets = ""
    block = params.get("block", [""])[0]

    conditions = ["1=1"]
    values: list[object] = []

    if projector is not None:
        conditions.append("projector = ?")
        values.append(projector)
    if smart_board is not None:
        conditions.append("smart_board = ?")
        values.append(smart_board)
    if min_outlets_value is not None:
        conditions.append("power_outlets >= ?")
        values.append(min_outlets_value)
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
        status_text = translate_status(row["live_status"] or "Unknown", lang)
        room_cards.append(
            f"""
            <div class="room-card {tag_class}">
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
            </div>
            """
        )

    if not room_cards:
        room_cards.append(
            f'<div class="card"><h3>{h(t("no_rooms_match", lang))}</h3><p class="muted">{h(t("try_removing_constraints", lang))}</p></div>'
        )

    request_rows = "".join(
        f"""
        <tr>
          <td>{h(row["event_title"])}</td>
          <td>{h(translate_event_type(row["event_type"], lang))}</td>
          <td>{h(row["room_code"])}</td>
          <td>{h(row["requested_start"])}</td>
          <td><span class="badge {'ok' if row['status'] == 'Approved' else 'warn' if row['status'] == 'Pending' else 'danger'}">{h(translate_status(row['status'], lang))}</span></td>
        </tr>
        """
        for row in requests
    ) or f'<tr><td colspan="5" class="muted">{h(t("no_requests_yet", lang))}</td></tr>'

    room_select = "".join(
        f'<option value="{h(room["room_id"])}">{h(room["room_code"])}</option>'
        for room in room_options
    )
    block_options = "".join(
        f'<option value="{h(row["block"])}" {"selected" if row["block"] == block else ""}>{h(row["block"])}</option>'
        for row in blocks
    )

    utilization_rows = "".join(
        f"""
        <div class="stat-box">
          <div class="stat-row">
            <strong>{h(row["room_code"])}</strong>
            <span class="badge info">{h(translate_status(row["latest_status"] or "Unknown", lang))}</span>
          </div>
          <div class="small muted">{h(t('average_occupancy', lang))}: {h(row["average_occupancy_rate"])}% • {h(row["observation_count"])} {h(t('observations', lang))}</div>
          <div class="small muted">{h(t('last_seen', lang))}: {h(row["last_observed_at"] or '-')}</div>
        </div>
        """
        for row in utilization
    )

    content = f"""
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">{h(t('student_dashboard', lang))}</div>
          <div class="welcome-line">
            <h2>{h(t('welcome_back', lang).format(name=user['name'].split()[0]))}</h2>
            <span class="badge info">{h(user['department_name'])}</span>
          </div>
          <p class="muted">
            {h(t('find_room_text', lang))}
          </p>
          <p class="muted">
            {h(t('live_map_info', lang))}
          </p>
          <div class="pill-row">
            <div class="pill">{h(t('pill_live_availability', lang))}</div>
            <div class="pill">{h(t('pill_heatmap', lang))}</div>
            <div class="pill">{h(t('pill_smart_filter', lang))}</div>
          </div>
          {flash}
        </div>
        <div class="card">
          <h3>{h(t('quick_filter', lang))}</h3>
          <div class="stats spaced">
            <div class="stat-box">
              <strong>{len(rooms)}</strong>
              <div class="small muted">{h(t('visible_rooms', lang))}</div>
            </div>
            <div class="stat-box">
              <strong>{available_now}</strong>
              <div class="small muted">{h(t('rooms_ready', lang))}</div>
            </div>
            <div class="stat-box">
              <strong>{approved_count}</strong>
              <div class="small muted">{h(t('approved_requests', lang))}</div>
            </div>
          </div>
          <div class="spaced"></div>
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
            <div class="grid-2">
              <button class="button-accent" type="submit">{h(t('apply_filters', lang))}</button>
              <a class="button-link" href="/dashboard">{h(t('reset_filters', lang))}</a>
            </div>
          </form>
        </div>
      </div>
    </section>

    <section class="grid-2 spaced">
      <article class="card">
        <h3>{h(t('live_room_heatmap', lang))}</h3>
        <div class="heatmap">
          {"".join(room_cards)}
        </div>
      </article>

      <aside class="card">
        <h3>{h(t('create_reservation_request', lang))}</h3>
        <p class="muted small">{h(t('live_map_info', lang))}</p>
        <div class="pill-row">
          <div class="pill">{h(t('pending_requests_title', lang))}: {pending_count}</div>
          <div class="pill">{h(t('approved_requests', lang))}: {approved_count}</div>
        </div>
        <form method="post" action="/requests/new">
          <label>{h(t('room', lang))}
            <select name="room_id" required>
              {room_select}
            </select>
          </label>
          <label>{h(t('title', lang))}
            <input type="text" name="event_title" required>
          </label>
          <label>{h(t('type', lang))}
            <select name="event_type" required>
              <option value="Workshop">{h(t('event_type_workshop', lang))}</option>
              <option value="Club">{h(t('event_type_club', lang))}</option>
              <option value="Makeup">{h(t('event_type_makeup', lang))}</option>
              <option value="Exam">{h(t('event_type_exam', lang))}</option>
              <option value="Seminar">{h(t('event_type_seminar', lang))}</option>
            </select>
          </label>
          <label>{h(t('start', lang))}
            <input type="datetime-local" name="requested_start" required placeholder="{h(t('date_format', lang))}">
          </label>
          <label>{h(t('end', lang))}
            <input type="datetime-local" name="requested_end" required placeholder="{h(t('date_format', lang))}">
          </label>
          <label>{h(t('request_note_hint', lang))}
            <textarea name="request_note" placeholder="{h(t('request_note_hint', lang))}"></textarea>
          </label>
          <button type="submit">{h(t('submit_request', lang))}</button>
        </form>
      </aside>
    </section>

    <section class="card spaced">
      <h3>{h(t('my_requests', lang))}</h3>
      <table class="table-lite">
        <thead>
          <tr>
            <th>{h(t('title', lang))}</th>
            <th>{h(t('type', lang))}</th>
            <th>{h(t('room', lang))}</th>
            <th>{h(t('start', lang))}</th>
            <th>{h(t('status', lang))}</th>
          </tr>
        </thead>
        <tbody>{request_rows}</tbody>
      </table>
    </section>
    """
    return render_layout(t('student_dashboard', lang), content, user, lang)


def academic_dashboard(user: sqlite3.Row, message: str = "", error: bool = False, lang: str = DEFAULT_LANGUAGE) -> str:
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
        utilization = conn.execute(
            """
            SELECT room_code, average_occupancy_rate, observation_count, latest_status, last_observed_at
            FROM v_room_utilization_summary
            ORDER BY average_occupancy_rate DESC, room_code
            LIMIT 6
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
    ) or f'<div class="list-row muted">{h(t("no_schedule_assigned", lang))}</div>'

    coordination_rows = "".join(
        f"""
        <div class="stat-box">
          <div class="stat-row">
            <strong>{h(row["title"])}</strong>
            <span class="badge {'ok' if row['overlapping_event_requests'] == 0 else 'warn'}">{h(row["room_code"])}</span>
          </div>
          <div class="small muted">{h(t('occupancy_trend', lang))}: {h(row["prior_week_occupancy_rate"])}% • {h(t('overlapping_requests', lang))}: {h(row["overlapping_event_requests"])}</div>
        </div>
        """
        for row in coordination
    ) or f'<div class="list-row muted">{h(t("no_exam_records", lang))}</div>'

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
                    <button type="submit">{h(t('approve', lang))}</button>
                  </form>
                  <form method="post" action="/requests/review" class="inline-form">
                    <input type="hidden" name="request_id" value="{h(row["request_id"])}">
                    <input type="hidden" name="decision" value="Rejected">
                    <button class="button-secondary" type="submit">{h(t('reject', lang))}</button>
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
            <div class="small muted">{h(row["room_code"])} {h(t('overlaps_with', lang))} {h(row["schedule_title"])}</div>
          </div>
          <span class="badge danger">{h(t('conflict', lang))}</span>
        </div>
        """
        for row in conflict_rows
    ) or f'<div class="list-row"><div><strong>{h(t("current_state", lang))}</strong><div class="small muted">{h(t("no_active_conflict", lang))}</div></div><span class="badge ok">{h(t("clear", lang))}</span></div>'

    utilization_rows = "".join(
        f"""
        <div class="stat-box">
          <div class="stat-row">
            <strong>{h(row["room_code"])}</strong>
            <span class="badge info">{h(translate_status(row["latest_status"] or "Unknown", lang))}</span>
          </div>
          <div class="small muted">{h(t('average_occupancy', lang))}: {h(row["average_occupancy_rate"])}% • {h(row["observation_count"])} {h(t('observations', lang))}</div>
          <div class="small muted">{h(t('last_seen', lang))}: {h(row["last_observed_at"] or '-')}</div>
        </div>
        """
        for row in utilization
    )

    content = f"""
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">{h(t('academic_dashboard', lang))}</div>
          <div class="welcome-line">
            <h2>{h(t('welcome_back_dr', lang).format(name=user['name'].split()[0]))}</h2>
            <span class="badge info">{h(user['department_name'])}</span>
          </div>
          <p class="muted">
            {h(t('academic_description', lang))}
          </p>
          <div class="pill-row">
            <div class="pill">{h(t('schedule_optimizer', lang))}</div>
            <div class="pill">{h(t('conflict_detection', lang))}</div>
            <div class="pill">{h(t('approval_workflow', lang))}</div>
          </div>
          {flash}
        </div>
        <div class="card">
          <h3>{h(t('conflict_logic_summary', lang))}</h3>
          <p class="muted small">
            {h(t('conflict_summary_text', lang))}
          </p>
          <div class="stats">
            <div class="stat-box"><strong>{len(pending)}</strong><div class="small muted">{h(t('pending_requests_waiting', lang))}</div></div>
            <div class="stat-box"><strong>{len(coordination)}</strong><div class="small muted">{h(t('exam_coordination_records', lang))}</div></div>
            <div class="stat-box"><strong>{len(conflict_rows)}</strong><div class="small muted">{h(t('active_overlaps', lang))}</div></div>
          </div>
        </div>
      </div>
    </section>

    <section class="grid-2 spaced">
      <article class="card">
        <h3>{h(t('my_schedule', lang))}</h3>
        <div class="stats">{schedule_rows}</div>
      </article>
      <article class="card">
        <h3>{h(t('exam_coordination', lang))}</h3>
        <div class="stats">{coordination_rows}</div>
      </article>
    </section>

    <section class="card spaced">
      <h3>{h(t('room_utilization_snapshot', lang))}</h3>
      <div class="grid-3">{utilization_rows}</div>
    </section>

    <section class="grid-2 spaced">
      <article class="card">
        <h3>{h(t('pending_requests_title', lang))}</h3>
        <table class="table-lite">
          <thead>
            <tr>
              <th>{h(t('title', lang))}</th>
              <th>{h(t('requester', lang))}</th>
              <th>{h(t('room', lang))}</th>
              <th>{h(t('start', lang))}</th>
              <th>{h(t('end', lang))}</th>
              <th>{h(t('decision', lang))}</th>
            </tr>
          </thead>
          <tbody>
            {"".join(pending_rows) or f'<tr><td colspan="6" class="muted">{h(t("no_pending_requests", lang))}</td></tr>'}
          </tbody>
        </table>
      </article>
      <aside class="card">
        <h3>{h(t('conflict_detection_feed', lang))}</h3>
        <div class="stats">{conflict_list}</div>
      </aside>
    </section>
    """
    return render_layout(t('academic_dashboard', lang), content, user, lang)


class KMFHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        ensure_database()
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        lang = get_language(self, params) or DEFAULT_LANGUAGE
        lang_cookie = None
        if params.get("lang", [""])[0].lower() in SUPPORTED_LANGUAGES:
            lang_cookie = build_language_cookie(lang)

        user = get_current_user(self)

        if parsed.path == "/":
            if user is not None:
                self.redirect("/dashboard", cookies_header=lang_cookie)
                return
            self.respond_html(signin_page(params.get("message", [""])[0], params.get("error", ["0"])[0] == "1", lang), cookies_header=lang_cookie)
            return

        if parsed.path == "/signin":
            if user is not None:
                self.redirect("/dashboard", cookies_header=lang_cookie)
                return
            self.respond_html(signin_page(params.get("message", [""])[0], params.get("error", ["0"])[0] == "1", lang), cookies_header=lang_cookie)
            return

        if parsed.path == "/signup":
            if user is not None:
                self.redirect("/dashboard", cookies_header=lang_cookie)
                return
            self.respond_html(signup_page(params.get("message", [""])[0], params.get("error", ["0"])[0] == "1", lang), cookies_header=lang_cookie)
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
                self.respond_html(student_dashboard(user, params, message, error, lang), cookies_header=lang_cookie)
                return
            self.respond_html(academic_dashboard(user, message, error, lang), cookies_header=lang_cookie)
            return

        self.respond_html(render_layout("Not Found", '<section class="hero"><h2>Page not found</h2></section>', None, lang), status=404, cookies_header=lang_cookie)

    def do_POST(self) -> None:
      ensure_database()
      parsed = urlparse(self.path)
      form = parse_post_data(self)
      user = get_current_user(self)
      # preserve language selection across POST redirects
      params = parse_qs(parsed.query)
      lang = get_language(self, params) or DEFAULT_LANGUAGE
      lang_cookie = None
      if params.get("lang", [""])[0].lower() in SUPPORTED_LANGUAGES:
        lang_cookie = build_language_cookie(lang)

      if parsed.path == "/login":
        email = form.get("email", "").strip().lower()
        password = form.get("password", "")
        if not email or not password:
          self.redirect("/signin?message=Email+and+password+are+required&error=1", cookies_header=lang_cookie)
          return

        with get_connection() as conn:
          db_user = conn.execute(
            """
            SELECT user_id, name, email, role, password_hash
            FROM Users
            WHERE email = ?
            """,
            (email,),
          ).fetchone()

        if db_user is None or not verify_password(password, db_user["password_hash"]):
          self.redirect("/signin?message=Invalid+email+or+password&error=1", cookies_header=lang_cookie)
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
        email = form.get("email", "").strip()
        department_id = form.get("department_id", "").strip()
        password = form.get("password", "").strip()
        confirm_password = form.get("confirm_password", "").strip()

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
        email = email.lower()
        if not is_valid_email(email):
          self.redirect("/signup?message=Invalid+email+address&error=1", cookies_header=lang_cookie)
          return

        password_hash = hash_password(password)
        try:
          department_int = int(department_id)
          with get_connection() as conn:
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

        try:
          room_id = int(form.get("room_id", ""))
          event_title = form.get("event_title", "").strip()
          event_type = form.get("event_type", "").strip()
          requested_start = parse_datetime_local(form.get("requested_start", ""))
          requested_end = parse_datetime_local(form.get("requested_end", ""))
          if not event_title or len(event_title) > 120:
            raise ValueError("Event title must be between 1 and 120 characters")
          if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError("Unsupported event type")
          if datetime.fromisoformat(requested_end) <= datetime.fromisoformat(requested_start):
            raise ValueError("End time must be after start time")

          with get_connection() as conn:
            active_room = conn.execute(
              "SELECT 1 FROM Classrooms WHERE room_id = ? AND is_active = 1",
              (room_id,),
            ).fetchone()
            if active_room is None:
              raise ValueError("Selected room is not active")
            conn.execute(
              """
              INSERT INTO Event_Requests (
                requester_id, room_id, event_title, event_type,
                requested_start, requested_end, request_note
              ) VALUES (?, ?, ?, ?, ?, ?, ?)
              """,
              (
                user["user_id"],
                room_id,
                event_title,
                event_type,
                requested_start,
                requested_end,
                form.get("request_note", "").strip()[:500] or None,
              ),
            )
            conn.commit()
          self.redirect("/dashboard?message=Request+submitted+successfully", cookies_header=lang_cookie)
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

        try:
          with get_connection() as conn:
            request_id = int(form.get("request_id", ""))
            if decision == "Approved":
              cursor = conn.execute(
                """
                UPDATE Event_Requests
                SET status = 'Approved',
                  approved_by = ?,
                  decision_at = CURRENT_TIMESTAMP,
                  rejection_reason = NULL
                WHERE request_id = ? AND status = 'Pending'
                """,
                (user["user_id"], request_id),
              )
            else:
              cursor = conn.execute(
                """
                UPDATE Event_Requests
                SET status = 'Rejected',
                  approved_by = ?,
                  decision_at = CURRENT_TIMESTAMP,
                  rejection_reason = 'Rejected from academic dashboard'
                WHERE request_id = ? AND status = 'Pending'
                """,
                (user["user_id"], request_id),
              )
            if cursor.rowcount == 0:
              raise ValueError("Request is not pending or does not exist")
            conn.commit()
          self.redirect(f"/dashboard?message=Request+{decision.lower()}+successfully", cookies_header=lang_cookie)
        except (sqlite3.IntegrityError, sqlite3.OperationalError, KeyError, ValueError) as exc:
          self.redirect(f"/dashboard?message={quote_plus(str(exc))}&error=1", cookies_header=lang_cookie)
        return

      self.respond_html(render_layout("Not Found", '<section class="hero"><h2>Page not found</h2></section>'), status=404, cookies_header=lang_cookie)

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
