# KMF Smart Classroom & Event Management System

A standard-library Python + SQLite demo for the MTM4692 Applied SQL course. The project models a smart classroom and event coordination platform for the Chemical and Metallurgical Engineering Faculty (KMF), while keeping the original academic purpose: SQL is used for relational modeling, data integrity, workflow control, analytics, and a simple role-based interface.

## What was improved in this version

### Backend

- Added stronger password handling with salted PBKDF2 hashes.
- Kept backward compatibility for older SHA-256 demo hashes.
- Added session expiration and safer cookies with `HttpOnly`, `SameSite=Lax`, and `max-age`.
- Added safer form validation for registration, login, reservation creation, and request review.
- Added active-room and active-department checks before writing new records.
- Added `Request_Audit_Log` to record reservation workflow history.
- Added `v_room_utilization_summary` for room-level occupancy analytics.
- Added indexes for request audit lookup and user role/department filtering.
- Improved database bootstrap logic so an older `kmf.db` is rebuilt when required objects are missing.

### Frontend

- Added visible demo credentials on the login page.
- Improved student room cards with localized status labels.
- Improved academic dashboard with a room utilization snapshot.
- Expanded Turkish/English translations for newly visible UI labels.
- Kept the original warm Yildiz-inspired visual style and role-based dashboard structure.

### Documentation

- Updated this README to match the current code and database behavior.
- Added an updated PDF project summary: `SQL-PROJECT-UPDATED.pdf`.
- Added `CHANGELOG.md` for a concise technical change record.

## Project Files

- `setup.sql` - SQLite schema, triggers, views, indexes, seed data, and analytical SQL examples.
- `app.py` - Python standard-library web application connected directly to SQLite.
- `ui-prototype.html` - Static visual prototype kept as a presentation asset.
- `SQL-PROJECT-UPDATED.pdf` - Updated project summary matching the revised schema and application.
- `CHANGELOG.md` - Summary of implemented frontend, backend, SQL, and documentation changes.
- `.gitignore` - Ignores generated local files such as `kmf.db`, caches, and virtual environments.

## Demo Credentials

Use any seeded email with the shared demo password below.

```text
Password: Demo123!
```

Example users:

```text
Student:    can.yilmaz@std.yildiz.edu.tr
Academic:   ayse.demir@ytu.edu.tr
```

Additional seeded users are defined in `setup.sql`.

## Running the Project

The project has no external Python dependency. It uses only the Python standard library and SQLite.

```powershell
python app.py
```

Then open:

```text
http://127.0.0.1:8000
```

On first run, the application creates `kmf.db` from `setup.sql`. If an older database is present and does not contain the newer audit table or utilization view, the app rebuilds the demo database automatically.

## Optional Manual Database Initialization

```powershell
sqlite3 kmf.db ".read 'setup.sql'"
```

If the `sqlite3` command-line tool is not installed, simply run `python app.py`; the app initializes the database itself.

## Database Design

The schema is normalized around operational and analytical entities:

- `Departments`
- `Users`
- `Classrooms`
- `Academic_Schedules`
- `Event_Requests`
- `Request_Audit_Log`
- `Usage_Logs`

The design follows 3NF principles: each table models one subject, non-key fields depend on the key, and operational data is separated from master data.

## SQL Rules and Integrity

The database enforces important business rules directly:

- valid user roles: `Student` or `Academic`
- valid classroom equipment JSON in `Classrooms.specs`
- valid schedule and request time ranges
- occupancy count cannot exceed classroom capacity
- academic schedules cannot overlap in the same room
- pending or approved event requests cannot conflict with academic schedules
- approved event requests cannot overlap with other approved requests
- only academic users can approve requests
- request status changes are written to `Request_Audit_Log`

This keeps the database as a protection layer even if the application sends invalid data.

## Analytical SQL Layer

The current version includes three views:

### `v_student_live_status`

Privacy-safe student-facing room status. It exposes room code, block, floor, capacity, equipment flags, latest status, latest occupancy, and last observation time.

### `v_exam_coordination`

Academic-facing exam coordination view. It joins schedules, academics, departments, classrooms, prior occupancy trend, and overlapping request counts.

### `v_room_utilization_summary`

Room-level usage summary for the academic dashboard. It reports average occupancy rate, observation count, latest status, and last observation time.

The setup script also includes:

- recursive CTE for `Block > Floor > Room` hierarchy
- window-function ranking for weekly utilization by block
- audit-trail query for request workflow history

## Application Flow

### Student

1. Sign in with a seeded student account or register a new student account.
2. Filter rooms by block, projector, smart board, and power outlet count.
3. Submit a reservation request.
4. Track personal requests and approval states.

### Academic

1. Sign in with a seeded academic account.
2. Review personal schedule and exam coordination records.
3. Inspect room utilization summary.
4. Approve or reject pending reservation requests.
5. Let SQL triggers block invalid approval attempts or conflicts.

## Security Notes for the Demo

This is still a course demo, not a production deployment. The current version improves the original implementation by using PBKDF2 password hashing, safer cookies, session expiration, and server-side validation. A production system would still need HTTPS, persistent server-side sessions, CSRF tokens, structured logging, rate limiting, and a full authentication/authorization framework.

## Suggested Demo Flow

1. Run `python app.py`.
2. Open `http://127.0.0.1:8000`.
3. Sign in as `can.yilmaz@std.yildiz.edu.tr` with `Demo123!`.
4. Filter rooms from the student dashboard.
5. Submit a non-conflicting request.
6. Sign out and sign in as `ayse.demir@ytu.edu.tr` with `Demo123!`.
7. Approve or reject the pending request.
8. Explain how triggers, views, and audit logging keep the workflow consistent.

## Summary

The project now better connects the frontend, backend, and SQL layer without changing the original purpose. It remains an Applied SQL assignment focused on relational design, integrity rules, analytical SQL, and a practical role-based interface.
