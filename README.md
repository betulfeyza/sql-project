# KMF Smart Classroom & Event Management System

This repository contains an Applied SQL course project prepared for the Mathematical Engineering department at Yildiz Technical University. The project models a smart classroom and event coordination platform for the Chemical and Metallurgical Engineering Faculty (KMF) and demonstrates how database design, SQL integrity rules, analytical queries, and a role-based web interface can work together in one academic assignment.

## Course Context

- University: Yildiz Technical University
- Department: Mathematical Engineering
- Course: Applied SQL
- Project Type: Database design and implementation assignment

The main academic goal is not only to store data, but to show how SQL can actively control workflow, prevent invalid operations, support analytics, and feed a usable interface.

## Project Files

- [setup.sql](./setup.sql)  
  Full SQLite setup script with schema, foreign keys, checks, triggers, indexes, views, seed data, recursive CTE, and window-function analytics.
- [app.py](./app.py)  
  Python standard-library web application connected directly to the SQLite database.
- [ui-prototype.html](./ui-prototype.html)  
  Earlier static concept draft kept as a visual presentation asset.
- [SQL-PROJECT.pdf](./SQL-PROJECT.pdf)  
  Original project/report material.
- [.gitignore](./.gitignore)  
  Prevents local database files from being committed accidentally.

## Problem Definition

The system is designed for faculty-level classroom and event management. It supports:

- classroom discovery for students
- smart filtering by building block, technical equipment, and outlet count
- live occupancy monitoring
- lecture and exam planning
- student reservation request submission
- academic approval and rejection workflow
- conflict detection between academic schedules and reservation requests
- student-side conflict warnings before requests reach academic approval

This creates a realistic scenario where SQL is used both as a data storage language and as a business-rule enforcement layer.

## Database Design

The schema is normalized around the following main entities:

- `Departments`
- `Users`
- `Classrooms`
- `Academic_Schedules`
- `Event_Requests`
- `Request_History`
- `Usage_Logs`

The structure is designed according to 3NF:

- each table represents a single subject
- non-key fields depend on the whole key
- transitive dependencies are avoided
- master data and operational data are separated

## Real KMF Classroom Seed Data

The classroom seed data uses KMF room names instead of generic demo rooms:

```text
KMB-202, KMB-203, KMB-210, KMB-211, KMB-212, KMB-213, KMB-213-A,
KMB-214, KMB-215, KMB-216, KMB-217, KMB-224, KMB-227, KMB-228,
KMB-305, KMB-312, KMB-314, KMB-315, KMB-316, KMB-317, KMB-318,
KMB-320, KMB-321, KMB-322, KMB-327, KMB-328, KMB-329, KMB-329-A,
KME-208, KME-304, KME-305, KME-306, KMF SNL-002, KMF SNL-021
```

Capacity and equipment values are demo estimates so the interface can show filters, heatmap states, and reservation examples.

## Applied SQL Concepts

This project demonstrates several course-relevant SQL concepts in one coherent system:

- normalized relational schema design
- referential integrity with `FOREIGN KEY`
- domain validation with `CHECK`
- rule enforcement with `TRIGGER`
- analytical reporting with `VIEW`
- hierarchical reporting with recursive CTE
- ranking and utilization analysis with window functions
- query performance support with indexes

## Business Rules

The database and application enforce important system rules:

- only valid departments, users, classrooms, schedules, requests, and logs can exist
- occupancy count cannot exceed classroom capacity
- academic schedules cannot overlap in the same room
- pending or approved requests cannot conflict with academic schedules
- student-created requests are checked against existing pending and approved reservations before they are saved
- approved requests cannot overlap with another approved request
- only users with role `Academic` can approve or reject reservation requests
- rejection requires a reason
- students can update or delete only their own pending requests

The database remains the final protection layer, while the application also gives earlier, friendlier feedback to the user.

## Analytical Layer

Two important views are included:

- `v_student_live_status`  
  Provides privacy-safe room visibility for students, including status, equipment, and occupancy information.
- `v_exam_coordination`  
  Supports academic decision-making by combining exam records, room capacity, prior occupancy trend, and overlapping request counts.

Advanced SQL features are also included:

- recursive CTE for `Block > Floor > Room`
- `DENSE_RANK()` ranking of weekly room utilization by block
- indexes on room and timestamp related fields for faster access

## UI and Application Layer

The repository includes a working web demo connected to SQLite. It uses only Python standard-library modules, so no external web framework is required.

### Login and Accounts

The application starts with a shared email/password sign-in screen:

- seeded academic and student users can sign in with the demo password
- new users can register from the sign-up page
- self-registration creates `Student` accounts only
- language can be switched between English and Turkish
- light/dark mode is available with an animated fade transition
- signed-in users access account details, language, theme, and sign-out from a compact profile/settings menu in the top bar

For classroom-demo simplicity, seeded setup users use:

```text
Password: Demo123!
```

Example academic account:

```text
Email: ayse.demir@ytu.edu.tr
Password: Demo123!
```

Example student accounts:

```text
Email: can.yilmaz@std.yildiz.edu.tr
Password: Demo123!
```

```text
Email: zeynep.acar@std.yildiz.edu.tr
Password: Demo123!
```

If you created the extra live academic demo account during local testing, it may also exist in your current `kmf.db`:

```text
Email: akademik.demo@ytu.edu.tr
Password: Academic123!
```

That extra account is not required by `setup.sql`; it depends on the local database state.

### Backend Compatibility

The application includes a small local migration layer for existing `kmf.db` files. If a database was created before password-based login was added, startup adds the missing `Users.password_hash` column, fills seeded demo users with the `Demo123!` hash, creates the user email uniqueness index, and keeps the newer request-history fields in sync.

This lets teammates pull the latest code and continue using an older local SQLite file without deleting their local demo data.

### Student Dashboard

The student dashboard is task-oriented:

- live room heatmap cards
- internal scroll for the heatmap so many classrooms do not stretch the page
- quick filters for block, projector, smart board, and outlet count
- direct reservation request form
- clickable room cards that open a Teams-style weekly room calendar
- busy academic and reservation slots shown inside the calendar
- empty slots selectable directly from the calendar
- selected calendar time automatically fills the reservation request
- personal request list
- request history
- pending request editing behind an `Edit` button
- red `Delete` button for pending requests
- confirmation prompts before update and delete actions

### Academic Dashboard

The academic dashboard focuses on coordination and decision support:

- personal teaching or exam schedule
- exam coordination summary from `v_exam_coordination`
- pending request approval table
- approval notes
- rejection reason requirement
- alternative room suggestions when conflicts exist
- conflict detection feed based on schedule and reservation overlap logic

### Frontend and Database Harmony

The frontend stays in sync with the SQLite backend:

- sign-in checks email and password against the `Users` table
- student room cards are read from `v_student_live_status`
- calendar busy slots are built from `Academic_Schedules` and `Event_Requests`
- request submission writes directly into `Event_Requests`
- request updates and delete actions record history in `Request_History`
- academic approval actions update the same table and still rely on SQL trigger protection
- the academic conflict feed uses the same recurring schedule overlap logic as the database trigger layer
- the authenticated top bar keeps frontend controls compact by grouping profile information, language, theme, and logout inside one settings popover

## How Conflict Detection Works

One of the key real-world problems in classroom management is exam and event collision.

For one-time records, the system checks whether:

```text
new.start_at < existing.end_at AND new.end_at > existing.start_at
```

If this condition is true for the same room, the intervals overlap.

For recurring schedules, the logic also compares:

- same classroom
- same weekday
- overlapping time of day
- `Weekly` recurrence on every matching weekday after the schedule start date
- `Biweekly` recurrence only on matching 14-day intervals from the schedule start date

For reservation requests, conflicts are checked against:

- `Academic_Schedules`
- existing pending `Event_Requests`
- existing approved `Event_Requests`

The student side now warns immediately when a request overlaps with another pending or approved reservation. The academic approval screen still has SQL trigger protection as a final safety layer.

When a conflict is detected, the application tries to suggest alternative rooms for the same time range.

## Running the Project

### 1. Start the application

```bash
python3 app.py
```

Then open:

```text
http://127.0.0.1:8000
```

On first run, the application automatically creates `kmf.db` from `setup.sql` if the database file does not already exist. On later runs, lightweight migrations update older local databases so the backend and frontend stay compatible after pulling new changes.

### 2. Sign in with a demo account

Academic:

```text
ayse.demir@ytu.edu.tr
Demo123!
```

Student:

```text
can.yilmaz@std.yildiz.edu.tr
Demo123!
```

### 3. Optional direct SQLite usage

If you want to initialize the database manually:

```bash
sqlite3 kmf.db ".read 'setup.sql'"
```

Sample queries:

```sql
SELECT * FROM v_student_live_status ORDER BY room_code;
```

```sql
SELECT * FROM v_exam_coordination ORDER BY start_at;
```

```sql
SELECT room_code, live_status, occupancy_count, capacity
FROM v_student_live_status
ORDER BY block, floor, room_code;
```

## Suggested UI Test Flow

For a presentation or manual test, use this flow:

1. Start the app and sign in as a student.
2. Open the profile/settings menu and switch between English/Turkish and light/dark mode.
3. Filter rooms in the heatmap.
4. Click a room card and open the weekly room calendar.
5. Select an empty time range and create a reservation request.
6. Try to create another request for the same room/time.
7. Confirm that the student dashboard shows a conflict warning immediately.
8. Edit a pending request and test the update confirmation prompt.
9. Use the red Delete button and test the delete confirmation prompt.
10. Sign in as an academic and approve or reject a pending request.
11. Check that rejection requires a note.
12. Review request history and conflict feed behavior.

## Suggested Milestone Commit Messages

- `feat(init): bootstrap SQLite schema for KMF smart classroom management`
- `feat(integrity): add foreign keys, checks, and conflict prevention triggers`
- `feat(analytics): introduce reporting views, recursive hierarchy query, and usage ranking`
- `feat(ui): add database-connected role-based demo interface`
- `feat(ui): add dark mode, localization, and responsive request tables`
- `feat(rooms): replace generic rooms with KMF classroom seed data`
- `feat(calendar): add room calendar modal and student-side booking selection`
- `feat(workflow): add request history, edit/delete confirmations, and early conflict feedback`

## Summary

This project is a Mathematical Engineering Applied SQL assignment that connects theory and practice. It demonstrates that a well-designed SQLite database can support:

- normalized relational modeling
- strong integrity enforcement
- analytical SQL reporting
- role-based approval workflow
- student-friendly conflict feedback
- and a usable interface built directly on top of database outputs
