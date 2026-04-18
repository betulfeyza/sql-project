# KMF Smart Classroom & Event Management System

SQLite-based term project for Yildiz Technical University MTM4692. The project models a smart classroom and event coordination platform for the Chemical and Metallurgical Engineering Faculty with a focus on 3NF normalization, referential integrity, analytical SQL, and role-based user experience.

## Project Scope

The system manages:

- faculty departments
- student and academic users
- classrooms with technical equipment metadata in JSON
- fixed academic schedules and exam planning
- event reservation requests and approval workflow
- live classroom occupancy logs

The design is intended for both database evaluation and product-level presentation. It combines a reliable transactional schema with a lightweight analytics layer for reporting, coordination, and conflict prevention.

## Core Deliverables

- [setup.sql](./setup.sql)
  Complete SQLite setup script including schema, constraints, triggers, views, indexes, test data, recursive CTE, and window function examples.
- [ui-prototype.html](./ui-prototype.html)
  A presentation-friendly role-based UI/UX prototype for Student and Academic journeys.
- [.gitignore](./.gitignore)
  Keeps local database artifacts and editor noise out of version control.

## Database Architecture

The schema follows 3NF principles:

- `Departments` stores faculty department master data.
- `Users` stores people and their role as `Student` or `Academic`.
- `Classrooms` stores physical room attributes and smart equipment metadata.
- `Academic_Schedules` stores lectures, exams, and seminars.
- `Event_Requests` stores reservation requests, approval workflow, and decision metadata.
- `Usage_Logs` stores live occupancy observations for smart monitoring.

Each table is modeled so that:

- non-key attributes depend only on the key
- repeating technical equipment fields are grouped into structured JSON
- foreign key dependencies are explicit
- update and delete behaviors are controlled

## Integrity and Security Logic

The system includes multiple SQL-level controls:

- `FOREIGN KEY` constraints for referential integrity
- `CHECK` constraints for roles, statuses, date validity, capacity, and JSON structure
- triggers to block overlapping room reservations
- triggers to block classroom schedule conflicts
- triggers to ensure occupancy cannot exceed classroom capacity
- triggers to enforce that only users with role `Academic` can approve requests

This makes the database responsible not only for storage, but also for core business rules.

## Analytical Layer

Two reporting views are included:

- `v_student_live_status`
  Hides academic identity and only exposes room availability, capacity, and smart equipment details for students.
- `v_exam_coordination`
  Supports academic planning by showing exam allocations, prior occupancy rates, and overlapping requests.

Advanced SQL requirements are also covered:

- recursive CTE for `Block > Floor > Room` hierarchy reporting
- `DENSE_RANK()` based weekly room utilization ranking by block
- performance indexes on room and timestamp columns

## UI/UX Concept

The product experience is split by role at login:

### Student Experience

- `Live Map / Heatmap`
  Students see room occupancy intensity and current availability.
- `Smart Filter`
  Students can filter rooms by projector, power outlets, smart board, and floor.
- `Privacy by Design`
  Sensitive academic planning details are hidden behind the student-facing view.

### Academic Experience

- `Schedule Optimizer`
  Academics can compare room capacity and historic occupancy before assigning an exam or lecture.
- `Conflict Detection`
  The system checks `Academic_Schedules` and `Event_Requests` together to reject overlapping bookings before they are approved.
- `Decision Support`
  Occupancy trends and request overlap counts improve planning quality for exams and faculty events.

## How Conflict Detection Works

The conflict problem is solved at SQL level using overlapping time interval checks.

For a new academic schedule:

- the trigger checks whether another row already uses the same room
- it compares time windows with the condition:
  `new.start_at < existing.end_at AND new.end_at > existing.start_at`
- if overlap exists, insertion or update is aborted

For a new event request:

- the trigger checks both `Academic_Schedules` and approved `Event_Requests`
- if the requested time intersects with an existing room allocation, the request is blocked
- this prevents last-minute exam planning conflicts and double booking

This approach is strong because it protects data integrity even if the application layer is bypassed.

## Running the Project

If `sqlite3` is installed, create the database with:

```powershell
sqlite3 kmf.db ".read 'setup.sql'"
```

To inspect student-facing room status:

```sql
SELECT * FROM v_student_live_status ORDER BY room_code;
```

To inspect exam coordination:

```sql
SELECT * FROM v_exam_coordination ORDER BY start_at;
```

To present the interface concept, open `ui-prototype.html` in a browser and switch between Student and Academic roles.

## Example Presentation Story

This project can be presented in four milestones:

1. Initialization
   Normalized schema, entities, and faculty structure are created.
2. Integrity
   Foreign keys, checks, and triggers enforce business rules.
3. Analytics
   Views, ranking, and hierarchy reporting enable decision support.
4. Security
   Role-based access logic and privacy-aware views protect sensitive data.

## Suggested Commit Messages

- `feat(init): bootstrap SQLite schema for KMF smart classroom management`
- `feat(integrity): add foreign keys, checks, and conflict prevention triggers`
- `feat(analytics): introduce reporting views, recursive hierarchy query, and usage ranking`
- `feat(security): enforce academic-only approval workflow and privacy-safe student visibility`

## Author Note

This repository is designed as a course milestone that balances database theory with a realistic campus operations scenario. It is intentionally presentation-ready, so both technical implementation and user experience are visible in one place.
