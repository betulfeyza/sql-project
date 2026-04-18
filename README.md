# KMF Smart Classroom & Event Management System

This repository contains an Applied SQL course project prepared for the Mathematical Engineering department at Yildiz Technical University. The project models a smart classroom and event coordination platform for the Chemical and Metallurgical Engineering Faculty and demonstrates how database design, SQL integrity rules, analytical queries, and a simple role-based interface can work together in one academic assignment.

## Course Context

- University: Yildiz Technical University
- Department: Mathematical Engineering
- Course: Applied SQL
- Project Type: Database design and implementation assignment

The main academic goal of the project is not only to store data, but to show how SQL can actively control workflow, prevent invalid operations, support analytics, and feed a usable interface.

## Project Files

- [setup.sql](./setup.sql)
  Full SQLite setup script with schema, foreign keys, checks, triggers, indexes, views, seed data, recursive CTE, and window-function analytics.
- [app.py](./app.py)
  Python standard-library web application connected directly to the SQLite database.
- [ui-prototype.html](./ui-prototype.html)
  Earlier static concept draft kept as a visual presentation asset.
- [.gitignore](./.gitignore)
  Prevents local database files from being committed accidentally.

## Problem Definition

The system is designed for faculty-level classroom and event management. It supports:

- classroom discovery for students
- smart filtering by technical equipment
- occupancy monitoring
- lecture and exam planning
- event request submission
- academic approval workflow
- conflict detection between schedules and requests

This creates a realistic scenario where SQL is used both as a data storage language and as a business-rule enforcement layer.

## Database Design

The schema is normalized around the following main entities:

- `Departments`
- `Users`
- `Classrooms`
- `Academic_Schedules`
- `Event_Requests`
- `Usage_Logs`

The structure is designed according to 3NF:

- each table represents a single subject
- non-key fields depend on the whole key
- transitive dependencies are avoided
- master data and operational data are separated

### Why the design is suitable for Applied SQL

This project demonstrates several course-relevant SQL concepts in one coherent system:

- normalized relational schema design
- referential integrity with `FOREIGN KEY`
- domain validation with `CHECK`
- rule enforcement with `TRIGGER`
- analytical reporting with `VIEW`
- hierarchical reporting with recursive CTE
- ranking and utilization analysis with window functions
- query performance support with indexes

## Business Rules Implemented in SQL

The database enforces important system rules directly:

- only valid departments, users, classrooms, schedules, requests, and logs can exist
- occupancy count cannot exceed room capacity
- academic schedules cannot overlap in the same room
- pending or approved requests cannot conflict with existing academic schedules
- approved requests cannot overlap with another approved request
- only users with role `Academic` can approve a reservation request

This means the database itself acts as a protection layer even if the application sends invalid data.

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

The repository now includes a working web demo connected to the SQLite database.

### Login Experience

The application starts with a role-separated login screen:

- Student Login
- Academic Login

For classroom-demo simplicity, login uses seeded users from the database and does not require passwords.

### Student Dashboard

The student experience is designed to be more user friendly and task-oriented:

- live heatmap-like room cards
- quick filter for block, projector, smart board, and outlet count
- direct reservation request form
- personal request history
- a clickable Yildiz-inspired logo that returns the signed-in student to the main dashboard

All room cards are read from `v_student_live_status`, so the UI stays aligned with privacy requirements.

### Academic Dashboard

The academic experience focuses on coordination and decision support:

- personal teaching or exam schedule
- exam coordination summary from `v_exam_coordination`
- pending request approval table
- conflict feed based on schedule and reservation overlap logic
- the same logo-driven return-to-home behavior after login

This interface demonstrates how SQL outputs can be turned into practical operational screens.

### Frontend and Database Harmony

The frontend is intentionally designed to stay in sync with the SQLite backend:

- login options are read from seeded `Users` data
- student room cards are read from `v_student_live_status`
- request submission writes directly into `Event_Requests`
- academic approval actions update the same table and immediately reflect SQL trigger rules
- the top-left logo acts as a stable navigation anchor and always returns the active user to their own home screen

## How Conflict Detection Works

One of the key real-world problems in classroom management is exam and event collision.

This project solves that problem at SQL level by comparing time intervals in the same room.

For a new academic schedule, the trigger checks whether:

`new.start_at < existing.end_at AND new.end_at > existing.start_at`

If this condition is true for the same room, the new row is rejected.

For event requests, the same logic is applied against:

- `Academic_Schedules`
- approved `Event_Requests`

This guarantees that room conflicts are stopped before invalid data enters the system.

## Running the Project

### 1. Start the application

```powershell
python app.py
```

Then open:

```text
http://127.0.0.1:8000
```

On first run, the application automatically creates `kmf.db` from `setup.sql` if the database file does not already exist.

### 2. Optional direct SQLite usage

If you want to initialize the database manually:

```powershell
sqlite3 kmf.db ".read 'setup.sql'"
```

### 3. Sample analytics queries

Student-facing room view:

```sql
SELECT * FROM v_student_live_status ORDER BY room_code;
```

Exam coordination view:

```sql
SELECT * FROM v_exam_coordination ORDER BY start_at;
```

## Example Demo Flow

For presentation, the project can be demonstrated in this order:

1. Show the login page and explain role-based entry.
2. Enter as a student and filter rooms by equipment.
3. Create a reservation request from the student panel.
4. Sign in as an academic and approve or reject pending requests.
5. Explain that approval and conflict logic are protected by SQL triggers.

## Suggested Milestone Commit Messages

- `feat(init): bootstrap SQLite schema for KMF smart classroom management`
- `feat(integrity): add foreign keys, checks, and conflict prevention triggers`
- `feat(analytics): introduce reporting views, recursive hierarchy query, and usage ranking`
- `feat(ui): add database-connected role-based demo interface`

## Summary

This project is a Mathematical Engineering Applied SQL assignment that connects theory and practice. It demonstrates that a well-designed SQLite database can support:

- normalized relational modeling
- strong integrity enforcement
- analytical SQL reporting
- secure approval logic
- and a user-friendly interface built directly on top of database outputs
