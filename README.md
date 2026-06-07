# KMF Smart Classroom & Event Management System

This repository contains an Applied SQL course project prepared for the Mathematical Engineering Department at Yildiz Technical University. The project models a smart classroom and event coordination platform for the Chemical and Metallurgical Engineering Faculty (KMF).

The system demonstrates how database design, SQL integrity rules, analytical queries, authentication, conflict detection, and a role-based web interface can work together in one academic assignment.

## Course Context

* University: Yildiz Technical University
* Department: Mathematical Engineering
* Course: Applied SQL
* Project Type: Database design and implementation assignment

The main academic goal is not only to store data, but also to show how SQL can actively control workflow, prevent invalid operations, support analytics, and feed a usable interface.

## Project Overview

KMF Smart Classroom & Event Management System is a role-based classroom reservation and academic coordination platform.

The system includes two main user roles:

* Student
* Academic Staff

Students can discover classrooms, filter rooms by equipment, create reservation requests, and track their request history.

Academic staff can review student requests, approve or reject reservations, monitor conflicts, view exam coordination records, and analyze classroom utilization.

## Latest Updates

The latest version focuses on improving the user interface, strengthening authentication, improving language support, and making the dashboard experience more professional.

### Backend and Database

* Added automatic local migration support for older `kmf.db` files.
* Added `Users.password_hash` migration for older databases.
* Password handling now uses salted PBKDF2 hashes.
* Legacy SHA-256 demo hashes are still accepted and upgraded after successful login.
* Session cookies use `HttpOnly`, `SameSite=Lax`, `max-age`, and in-memory expiration.
* Added case-insensitive email uniqueness support.
* Added room utilization analytics through database views.
* Preserved request history migration behavior for older database versions.

### Frontend and User Experience

* Redesigned the login type selection screen.
* Added separate Student Login and Academic Login flows.
* Improved the Student Registration page.
* Added a modern student dashboard.
* Added a modern academic dashboard.
* Added light and dark theme support.
* Added English and Turkish language support.
* Added a compact profile/settings menu.
* Added dashboard summary cards.
* Improved live room heatmap cards.
* Improved reservation request form design.
* Improved academic panel texts and dashboard labels.
* Improved responsive layout and visual consistency.

## Project Files

* `setup.sql`
  SQLite setup script with schema, constraints, triggers, indexes, views, seed data, recursive CTE, and analytical SQL.

* `app.py`
  Python standard-library web application connected directly to the SQLite database.

* `ui-prototype.html`
  Earlier static interface prototype kept as a presentation asset.

* `SQL-PROJECT.pdf`
  Original project/report material.

* `.gitignore`
  Prevents local database files from being committed accidentally.

## Problem Definition

The system is designed for faculty-level classroom and event management. It supports:

* classroom discovery for students
* smart filtering by building block, projector, smart board, and outlet count
* live occupancy monitoring
* reservation request submission
* academic approval and rejection workflow
* conflict detection between academic schedules and reservations
* exam coordination support
* room utilization analytics
* request history tracking

This creates a realistic scenario where SQL is used as both a data storage layer and a business-rule enforcement layer.

## Database Design

The schema is normalized around the following main entities:

* `Departments`
* `Users`
* `Classrooms`
* `Academic_Schedules`
* `Event_Requests`
* `Request_History`
* `Usage_Logs`

The database follows 3NF principles:

* each table represents a single subject
* non-key fields depend on the full key
* transitive dependencies are avoided
* master data and operational data are separated

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

This project demonstrates several course-relevant SQL concepts:

* normalized relational schema design
* referential integrity with `FOREIGN KEY`
* domain validation with `CHECK`
* rule enforcement with `TRIGGER`
* analytical reporting with `VIEW`
* recursive CTE usage
* utilization analysis with window functions
* query performance support with indexes

## Business Rules

The database and application enforce important system rules:

* only valid departments, users, classrooms, schedules, requests, and logs can exist
* occupancy count cannot exceed classroom capacity
* academic schedules cannot overlap in the same room
* pending or approved requests cannot conflict with academic schedules
* approved requests cannot overlap with another approved request
* only academic users can approve or reject reservation requests
* rejection requires a reason
* students can update or delete only their own pending requests
* request history is recorded for important workflow actions

The database remains the final protection layer, while the application gives earlier and more user-friendly feedback.

## Analytical Layer

The project includes important SQL views:

* `v_student_live_status`
  Provides privacy-safe room visibility for students, including room status, equipment, and occupancy information.

* `v_exam_coordination`
  Supports academic decision-making by combining exam records, room capacity, occupancy trends, and overlapping request counts.

* `v_room_utilization_summary`
  Provides room-level average occupancy, observation count, latest status, and last observation time.

Advanced SQL features also include:

* recursive CTE for `Block > Floor > Room`
* room utilization ranking
* indexed access for room and timestamp-based queries

## Authentication and Accounts

The application supports role-based authentication.

Features:

* Student Login
* Academic Login
* Student Registration
* PBKDF2 password hashing
* Legacy password migration
* Session-based authentication
* Secure cookie management
* Role-based dashboard routing

For classroom-demo simplicity, seeded users use:

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

## Student Dashboard

The student dashboard is designed to help students quickly find and reserve suitable classrooms.

Features:

* dashboard summary cards
* pending request count
* approved request count
* available room count
* smart filtering system
* live room heatmap
* reservation request form
* request status tracking
* request history timeline
* language and theme controls

## Smart Classroom Filtering

Students can filter classrooms using:

* building block
* minimum power outlet requirement
* projector availability
* smart board availability

The filtering system updates visible rooms based on current SQLite-backed classroom data.

## Live Room Heatmap

The live room heatmap provides a real-time overview of classroom occupancy and availability.

It shows:

* room code
* occupancy percentage
* block and floor
* capacity
* live room status
* projector availability
* smart board availability
* power outlet count

Occupancy cards are color-coded:

* red for high occupancy
* orange for medium occupancy
* green for low occupancy

## Reservation Request System

Students can create reservation requests by selecting:

* classroom
* event title
* event type
* start date and time
* end date and time
* projector preference
* smart board preference
* minimum outlet preference
* optional note

Submitted requests are saved into the database and become visible in the student request list and academic approval workflow.

## Request History

The system records important request actions such as:

* created
* updated
* cancelled
* approved
* rejected

This makes the reservation workflow traceable and easier to audit.

## Academic Dashboard

The academic dashboard focuses on coordination and decision support.

Features:

* schedule management
* request management
* conflict analysis
* exam coordination
* pending request review
* room utilization snapshot
* dashboard summary cards

The academic dashboard allows academic staff to review reservation requests, approve or reject them, and monitor scheduling conflicts.

## Conflict Detection

One of the key real-world problems in classroom management is schedule collision.

For one-time records, the system checks whether:

```text
new.start_at < existing.end_at AND new.end_at > existing.start_at
```

If this condition is true for the same room, the intervals overlap.

For recurring schedules, the logic also compares:

* same classroom
* same weekday
* overlapping time of day
* weekly recurrence
* biweekly recurrence

Conflicts are checked against:

* `Academic_Schedules`
* pending `Event_Requests`
* approved `Event_Requests`

When a conflict is detected, the system can suggest alternative rooms for the same time range.

## Exam Coordination

The academic dashboard includes an exam coordination section.

It helps academic staff review:

* exam records
* assigned classrooms
* occupancy trends
* overlapping request counts
* room suitability

## Room Utilization Snapshot

The room utilization snapshot shows:

* average occupancy
* observation count
* latest room status
* last seen timestamp
* room-level usage information

This section is powered by the analytical SQL view `v_room_utilization_summary`.

## Multi-Language Support

The application supports:

* English
* Turkish

Users can switch languages from the profile/settings menu.

The interface includes localized:

* dashboards
* forms
* buttons
* status labels
* validation messages
* workflow messages

## Theme System

The application includes:

* Light Theme
* Dark Theme

Users can switch themes from the profile/settings menu. The selected theme is applied across login, registration, student dashboard, and academic dashboard pages.

## Frontend and Database Harmony

The frontend stays connected to the SQLite backend:

* sign-in checks the `Users` table
* room cards read from `v_student_live_status`
* request submissions write into `Event_Requests`
* request actions are recorded in `Request_History`
* academic approval updates the same reservation workflow
* exam coordination reads from `v_exam_coordination`
* utilization cards read from `v_room_utilization_summary`

## Running the Project

### 1. Start the application

```bash
python app.py
```

or:

```bash
python3 app.py
```

Then open:

```text
http://127.0.0.1:8000
```

On first run, the application automatically creates `kmf.db` from `setup.sql` if the database file does not already exist.

On later runs, lightweight migrations update older local databases so the backend and frontend remain compatible.

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
SELECT * FROM v_room_utilization_summary ORDER BY room_code;
```

## Suggested UI Test Flow

For a presentation or manual test, use this flow:

1. Start the app.
2. Switch between English and Turkish.
3. Switch between light and dark theme.
4. Open the Student Login page.
5. Sign in as a student.
6. Use the smart filters.
7. Review the live room heatmap.
8. Create a reservation request.
9. Review request status and request history.
10. Sign out.
11. Sign in as an academic.
12. Review the academic dashboard.
13. Check exam coordination records.
14. Review room utilization snapshot.
15. Approve or reject a pending request.

## Suggested Screenshots

Recommended screenshot order for GitHub README:

1. Login Type Selection
2. Student Login
3. Student Registration
4. Student Dashboard
5. Smart Filtering System
6. Live Room Heatmap
7. Reservation Request Form
8. Request History
9. Academic Dashboard
10. Exam Coordination
11. Room Utilization Snapshot
12. Dark Mode View

## Suggested Milestone Commit Messages

* `feat(init): bootstrap SQLite schema for KMF smart classroom management`
* `feat(integrity): add foreign keys, checks, and conflict prevention triggers`
* `feat(analytics): introduce reporting views and room utilization analytics`
* `feat(auth): add role-based authentication and password hashing`
* `feat(ui): add modern student and academic dashboards`
* `feat(ui): add dark mode and Turkish-English localization`
* `feat(rooms): replace generic rooms with KMF classroom seed data`
* `feat(workflow): add request history and academic approval workflow`
* `feat(filters): add smart classroom filtering`
* `feat(dashboard): add live room heatmap and utilization snapshot`

## Summary

This project connects Applied SQL theory with a practical classroom reservation system.

It demonstrates that a well-designed SQLite database can support:

* normalized relational modeling
* strong data integrity
* secure authentication
* analytical SQL reporting
* reservation workflows
* conflict detection
* academic approval processes
* multilingual dashboards
* and a modern role-based web interface
