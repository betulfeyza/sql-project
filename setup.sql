PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

DROP VIEW IF EXISTS v_exam_coordination;
DROP VIEW IF EXISTS v_student_live_status;

DROP TRIGGER IF EXISTS trg_event_requests_insert_guard;
DROP TRIGGER IF EXISTS trg_event_requests_update_guard;
DROP TRIGGER IF EXISTS trg_usage_logs_time_guard;
DROP TRIGGER IF EXISTS trg_schedule_conflict_guard_insert;
DROP TRIGGER IF EXISTS trg_schedule_conflict_guard_update;
DROP TRIGGER IF EXISTS trg_event_request_conflict_guard_insert;
DROP TRIGGER IF EXISTS trg_event_request_conflict_guard_update;

DROP TABLE IF EXISTS Usage_Logs;
DROP TABLE IF EXISTS Event_Requests;
DROP TABLE IF EXISTS Academic_Schedules;
DROP TABLE IF EXISTS Classrooms;
DROP TABLE IF EXISTS Users;
DROP TABLE IF EXISTS Departments;

CREATE TABLE Departments (
    department_id INTEGER PRIMARY KEY,
    department_name TEXT NOT NULL UNIQUE,
    department_code TEXT NOT NULL UNIQUE,
    faculty_name TEXT NOT NULL DEFAULT 'Chemical and Metallurgical Engineering Faculty',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE Users (
    user_id INTEGER PRIMARY KEY,
    department_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('Student', 'Academic')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (department_id) REFERENCES Departments(department_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CHECK (instr(email, '@') > 1)
);

CREATE TABLE Classrooms (
    room_id INTEGER PRIMARY KEY,
    room_code TEXT NOT NULL UNIQUE,
    block TEXT NOT NULL CHECK (block IN ('A', 'B', 'C', 'D')),
    floor INTEGER NOT NULL CHECK (floor BETWEEN 0 AND 10),
    capacity INTEGER NOT NULL CHECK (capacity BETWEEN 5 AND 500),
    specs TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    CHECK (json_valid(specs)),
    CHECK (json_type(specs, '$.projector') IN ('true', 'false')),
    CHECK (json_type(specs, '$.power_outlets') = 'integer'),
    CHECK (json_type(specs, '$.smart_board') IN ('true', 'false')),
    CHECK (json_type(specs, '$.air_conditioning') IN ('true', 'false'))
);

CREATE TABLE Academic_Schedules (
    schedule_id INTEGER PRIMARY KEY,
    academic_id INTEGER NOT NULL,
    department_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    schedule_type TEXT NOT NULL CHECK (schedule_type IN ('Lecture', 'Exam', 'Seminar')),
    title TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    recurrence_pattern TEXT NOT NULL DEFAULT 'Weekly'
        CHECK (recurrence_pattern IN ('Once', 'Weekly', 'Biweekly')),
    term_label TEXT NOT NULL,
    notes TEXT,
    FOREIGN KEY (academic_id) REFERENCES Users(user_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    FOREIGN KEY (department_id) REFERENCES Departments(department_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    FOREIGN KEY (room_id) REFERENCES Classrooms(room_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CHECK (datetime(start_at) IS NOT NULL),
    CHECK (datetime(end_at) IS NOT NULL),
    CHECK (datetime(end_at) > datetime(start_at))
);

CREATE TABLE Event_Requests (
    request_id INTEGER PRIMARY KEY,
    requester_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    event_title TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('Workshop', 'Club', 'Makeup', 'Exam', 'Seminar')),
    requested_start TEXT NOT NULL,
    requested_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Approved', 'Rejected')),
    approved_by INTEGER,
    decision_at TEXT,
    rejection_reason TEXT,
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
        OR (status = 'Rejected' AND decision_at IS NOT NULL)
    )
);

CREATE TABLE Usage_Logs (
    log_id INTEGER PRIMARY KEY,
    room_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    occupancy_count INTEGER NOT NULL CHECK (occupancy_count >= 0),
    status TEXT NOT NULL CHECK (status IN ('Available', 'Occupied', 'Reserved', 'Maintenance')),
    source TEXT NOT NULL DEFAULT 'Sensor' CHECK (source IN ('Sensor', 'Manual', 'System')),
    FOREIGN KEY (room_id) REFERENCES Classrooms(room_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CHECK (datetime(observed_at) IS NOT NULL)
);

CREATE TRIGGER trg_event_requests_insert_guard
BEFORE INSERT ON Event_Requests
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN NEW.status = 'Approved'
                 AND (
                    NEW.approved_by IS NULL OR NOT EXISTS (
                        SELECT 1
                        FROM Users u
                        WHERE u.user_id = NEW.approved_by
                          AND u.role = 'Academic'
                    )
                 )
            THEN RAISE(ABORT, 'Only Academic users can approve event requests.')
        END;
END;

CREATE TRIGGER trg_event_requests_update_guard
BEFORE UPDATE OF status, approved_by ON Event_Requests
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN NEW.status = 'Approved'
                 AND (
                    NEW.approved_by IS NULL OR NOT EXISTS (
                        SELECT 1
                        FROM Users u
                        WHERE u.user_id = NEW.approved_by
                          AND u.role = 'Academic'
                    )
                 )
            THEN RAISE(ABORT, 'Only Academic users can approve event requests.')
        END;
END;

CREATE TRIGGER trg_usage_logs_time_guard
BEFORE INSERT ON Usage_Logs
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN NEW.occupancy_count > (
                SELECT c.capacity
                FROM Classrooms c
                WHERE c.room_id = NEW.room_id
            )
            THEN RAISE(ABORT, 'Occupancy count cannot exceed classroom capacity.')
        END;
END;

CREATE TRIGGER trg_schedule_conflict_guard_insert
BEFORE INSERT ON Academic_Schedules
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM Academic_Schedules s
                WHERE s.room_id = NEW.room_id
                  AND s.weekday = NEW.weekday
                  AND datetime(NEW.start_at) < datetime(s.end_at)
                  AND datetime(NEW.end_at) > datetime(s.start_at)
            )
            THEN RAISE(ABORT, 'Schedule conflict detected for the selected classroom.')
        END;
END;

CREATE TRIGGER trg_schedule_conflict_guard_update
BEFORE UPDATE OF room_id, weekday, start_at, end_at ON Academic_Schedules
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM Academic_Schedules s
                WHERE s.room_id = NEW.room_id
                  AND s.weekday = NEW.weekday
                  AND s.schedule_id <> NEW.schedule_id
                  AND datetime(NEW.start_at) < datetime(s.end_at)
                  AND datetime(NEW.end_at) > datetime(s.start_at)
            )
            THEN RAISE(ABORT, 'Schedule conflict detected for the selected classroom.')
        END;
END;

CREATE TRIGGER trg_event_request_conflict_guard_insert
BEFORE INSERT ON Event_Requests
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN NEW.status IN ('Pending', 'Approved')
                 AND EXISTS (
                    SELECT 1
                    FROM Academic_Schedules s
                    WHERE s.room_id = NEW.room_id
                      AND datetime(NEW.requested_start) < datetime(s.end_at)
                      AND datetime(NEW.requested_end) > datetime(s.start_at)
                 )
            THEN RAISE(ABORT, 'Event request conflicts with an academic schedule.')
        END;

    SELECT
        CASE
            WHEN NEW.status = 'Approved'
                 AND EXISTS (
                    SELECT 1
                    FROM Event_Requests e
                    WHERE e.room_id = NEW.room_id
                      AND e.status = 'Approved'
                      AND datetime(NEW.requested_start) < datetime(e.requested_end)
                      AND datetime(NEW.requested_end) > datetime(e.requested_start)
                 )
            THEN RAISE(ABORT, 'Approved event request conflicts with another approved request.')
        END;
END;

CREATE TRIGGER trg_event_request_conflict_guard_update
BEFORE UPDATE OF room_id, requested_start, requested_end, status ON Event_Requests
FOR EACH ROW
BEGIN
    SELECT
        CASE
            WHEN NEW.status IN ('Pending', 'Approved')
                 AND EXISTS (
                    SELECT 1
                    FROM Academic_Schedules s
                    WHERE s.room_id = NEW.room_id
                      AND datetime(NEW.requested_start) < datetime(s.end_at)
                      AND datetime(NEW.requested_end) > datetime(s.start_at)
                 )
            THEN RAISE(ABORT, 'Event request conflicts with an academic schedule.')
        END;

    SELECT
        CASE
            WHEN NEW.status = 'Approved'
                 AND EXISTS (
                    SELECT 1
                    FROM Event_Requests e
                    WHERE e.room_id = NEW.room_id
                      AND e.request_id <> NEW.request_id
                      AND e.status = 'Approved'
                      AND datetime(NEW.requested_start) < datetime(e.requested_end)
                      AND datetime(NEW.requested_end) > datetime(e.requested_start)
                 )
            THEN RAISE(ABORT, 'Approved event request conflicts with another approved request.')
        END;
END;

CREATE INDEX idx_usage_logs_room_observed_at
    ON Usage_Logs (room_id, observed_at DESC);

CREATE INDEX idx_usage_logs_observed_at
    ON Usage_Logs (observed_at DESC);

CREATE INDEX idx_schedules_room_time
    ON Academic_Schedules (room_id, start_at, end_at);

CREATE INDEX idx_event_requests_room_time
    ON Event_Requests (room_id, requested_start, requested_end);

CREATE VIEW v_student_live_status AS
SELECT
    c.room_id,
    c.room_code,
    c.block,
    c.floor,
    c.capacity,
    json_extract(c.specs, '$.projector') AS projector,
    json_extract(c.specs, '$.power_outlets') AS power_outlets,
    json_extract(c.specs, '$.smart_board') AS smart_board,
    json_extract(c.specs, '$.air_conditioning') AS air_conditioning,
    ul.status AS live_status,
    ul.occupancy_count,
    ul.observed_at AS last_seen_at
FROM Classrooms c
LEFT JOIN Usage_Logs ul
    ON ul.log_id = (
        SELECT x.log_id
        FROM Usage_Logs x
        WHERE x.room_id = c.room_id
        ORDER BY datetime(x.observed_at) DESC
        LIMIT 1
    )
WHERE c.is_active = 1;

CREATE VIEW v_exam_coordination AS
SELECT
    s.schedule_id,
    s.title,
    s.schedule_type,
    s.term_label,
    d.department_name,
    u.name AS academic_name,
    c.room_code,
    c.block,
    c.floor,
    c.capacity,
    s.start_at,
    s.end_at,
    COALESCE((
        SELECT ROUND(AVG(100.0 * ul.occupancy_count / c.capacity), 2)
        FROM Usage_Logs ul
        WHERE ul.room_id = s.room_id
          AND datetime(ul.observed_at) BETWEEN datetime(s.start_at, '-7 days') AND datetime(s.start_at)
    ), 0) AS prior_week_occupancy_rate,
    (
        SELECT COUNT(*)
        FROM Event_Requests er
        WHERE er.room_id = s.room_id
          AND er.status IN ('Pending', 'Approved')
          AND datetime(er.requested_start) < datetime(s.end_at)
          AND datetime(er.requested_end) > datetime(s.start_at)
    ) AS overlapping_event_requests
FROM Academic_Schedules s
JOIN Users u ON u.user_id = s.academic_id
JOIN Departments d ON d.department_id = s.department_id
JOIN Classrooms c ON c.room_id = s.room_id
WHERE s.schedule_type = 'Exam';

INSERT INTO Departments (department_id, department_name, department_code) VALUES
    (1, 'Mathematical Engineering', 'MATH'),
    (2, 'Chemical Engineering', 'CHEM'),
    (3, 'Metallurgical and Materials Engineering', 'METE'),
    (4, 'Food Engineering', 'FOOD'),
    (5, 'Bioengineering', 'BIOE');

INSERT INTO Users (user_id, department_id, name, email, password_hash, role) VALUES
    (1, 1, 'Ayse Demir', 'ayse.demir@ytu.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Academic'),
    (2, 2, 'Mehmet Kaya', 'mehmet.kaya@ytu.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Academic'),
    (3, 3, 'Selin Arslan', 'selin.arslan@ytu.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Academic'),
    (4, 4, 'Can Yilmaz', 'can.yilmaz@std.yildiz.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Student'),
    (5, 5, 'Zeynep Acar', 'zeynep.acar@std.yildiz.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Student'),
    (6, 1, 'Berk Gunes', 'berk.gunes@std.yildiz.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Student'),
    (7, 2, 'Elif Kurt', 'elif.kurt@ytu.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Academic'),
    (8, 3, 'Mert Sahin', 'mert.sahin@std.yildiz.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Student'),
    (9, 4, 'Deniz Ozturk', 'deniz.ozturk@std.yildiz.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Student'),
    (10, 5, 'Seda Inan', 'seda.inan@ytu.edu.tr', 'ef61a579c907bbed674c0dbcbcf7f7af8f851538eef7b8e58c5bee0b8', 'Academic');

INSERT INTO Classrooms (room_id, room_code, block, floor, capacity, specs) VALUES
    (101, 'A-101', 'A', 1, 40, '{"projector":true,"power_outlets":24,"smart_board":true,"air_conditioning":true}'),
    (102, 'A-102', 'A', 1, 30, '{"projector":true,"power_outlets":16,"smart_board":false,"air_conditioning":true}'),
    (201, 'B-201', 'B', 2, 60, '{"projector":true,"power_outlets":40,"smart_board":true,"air_conditioning":true}'),
    (202, 'B-202', 'B', 2, 45, '{"projector":false,"power_outlets":18,"smart_board":true,"air_conditioning":true}'),
    (301, 'C-301', 'C', 3, 80, '{"projector":true,"power_outlets":52,"smart_board":true,"air_conditioning":true}'),
    (302, 'D-010', 'D', 0, 25, '{"projector":false,"power_outlets":12,"smart_board":false,"air_conditioning":false}');

INSERT INTO Academic_Schedules (
    schedule_id, academic_id, department_id, room_id, schedule_type, title,
    start_at, end_at, weekday, recurrence_pattern, term_label, notes
) VALUES
    (1, 1, 1, 101, 'Lecture', 'Linear Algebra II', '2026-04-20 09:00:00', '2026-04-20 10:50:00', 1, 'Weekly', 'Spring 2025-2026', 'Core undergraduate course'),
    (2, 2, 2, 201, 'Lecture', 'Reaction Engineering', '2026-04-21 10:00:00', '2026-04-21 11:50:00', 2, 'Weekly', 'Spring 2025-2026', 'Lab-supported lecture'),
    (3, 3, 3, 301, 'Exam', 'Materials Science Midterm', '2026-04-22 13:00:00', '2026-04-22 15:00:00', 3, 'Once', 'Spring 2025-2026', 'Midterm session'),
    (4, 7, 4, 202, 'Lecture', 'Food Microbiology', '2026-04-23 14:00:00', '2026-04-23 15:50:00', 4, 'Weekly', 'Spring 2025-2026', 'Shared elective'),
    (5, 10, 5, 102, 'Exam', 'BioProcess Systems Quiz', '2026-04-24 09:30:00', '2026-04-24 10:30:00', 5, 'Once', 'Spring 2025-2026', 'Short quiz');

INSERT INTO Event_Requests (
    request_id, requester_id, room_id, event_title, event_type, requested_start, requested_end,
    status, approved_by, decision_at, rejection_reason, request_note
) VALUES
    (1, 4, 101, 'Math Club Problem Solving Session', 'Workshop', '2026-04-20 12:00:00', '2026-04-20 13:30:00', 'Approved', 1, '2026-04-18 10:00:00', NULL, 'Need board access'),
    (2, 5, 202, 'Food Innovation Society Meetup', 'Seminar', '2026-04-23 16:15:00', '2026-04-23 17:30:00', 'Pending', NULL, NULL, NULL, 'Expected 20 attendees'),
    (3, 6, 302, 'Bioinformatics Peer Study', 'Workshop', '2026-04-24 11:00:00', '2026-04-24 12:30:00', 'Approved', 10, '2026-04-18 10:15:00', NULL, 'Need power outlets'),
    (4, 8, 201, 'Chemical Engineering Makeup Session', 'Makeup', '2026-04-21 13:00:00', '2026-04-21 14:30:00', 'Rejected', 2, '2026-04-18 10:20:00', 'Room reserved for maintenance preparation', 'Alternative date requested'),
    (5, 9, 102, 'Career Talk with Alumni', 'Seminar', '2026-04-24 11:00:00', '2026-04-24 12:00:00', 'Pending', NULL, NULL, NULL, 'Public event announcement pending');

INSERT INTO Usage_Logs (log_id, room_id, observed_at, occupancy_count, status, source) VALUES
    (1, 101, '2026-04-18 08:00:00', 12, 'Occupied', 'Sensor'),
    (2, 101, '2026-04-18 09:00:00', 28, 'Occupied', 'Sensor'),
    (3, 102, '2026-04-18 09:10:00', 10, 'Available', 'Sensor'),
    (4, 201, '2026-04-18 10:00:00', 35, 'Occupied', 'Sensor'),
    (5, 201, '2026-04-18 11:00:00', 0, 'Reserved', 'System'),
    (6, 202, '2026-04-18 11:15:00', 18, 'Occupied', 'Sensor'),
    (7, 301, '2026-04-18 12:00:00', 52, 'Occupied', 'Sensor'),
    (8, 301, '2026-04-18 14:30:00', 0, 'Reserved', 'System'),
    (9, 302, '2026-04-18 15:00:00', 8, 'Available', 'Manual'),
    (10, 302, '2026-04-18 16:00:00', 0, 'Maintenance', 'System');

COMMIT;

-- =====================================================
-- ANALYTICAL QUERY 1: Recursive CTE for Faculty Tree
-- Blok > Kat > Oda hiyerarsisini raporlar.
-- =====================================================
WITH RECURSIVE faculty_tree(node_type, node_key, parent_key, label, depth) AS (
    SELECT
        'BLOCK' AS node_type,
        'BLOCK:' || block AS node_key,
        NULL AS parent_key,
        'Block ' || block AS label,
        0 AS depth
    FROM Classrooms
    GROUP BY block

    UNION ALL

    SELECT
        'FLOOR',
        'FLOOR:' || c.block || ':' || c.floor,
        'BLOCK:' || c.block,
        'Floor ' || c.floor || ' (' || c.block || ')',
        1
    FROM Classrooms c
    GROUP BY c.block, c.floor

    UNION ALL

    SELECT
        'ROOM',
        'ROOM:' || c.room_id,
        'FLOOR:' || c.block || ':' || c.floor,
        c.room_code || ' - Capacity ' || c.capacity,
        2
    FROM Classrooms c
)
SELECT
    printf('%s%s', substr('          ', 1, depth * 2), label) AS hierarchy_line,
    node_type,
    node_key,
    parent_key
FROM faculty_tree
ORDER BY node_key;

-- =====================================================
-- ANALYTICAL QUERY 2: Weekly Utilization Ranking
-- Her bloktaki sınıfları haftalık kullanım yoğunluğuna göre sıralar.
-- =====================================================
WITH weekly_usage AS (
    SELECT
        c.block,
        c.room_code,
        ROUND(AVG(1.0 * ul.occupancy_count / c.capacity), 4) AS avg_utilization_ratio
    FROM Classrooms c
    JOIN Usage_Logs ul ON ul.room_id = c.room_id
    WHERE datetime(ul.observed_at) >= datetime('2026-04-18 23:59:59', '-7 days')
    GROUP BY c.block, c.room_code
)
SELECT
    block,
    room_code,
    avg_utilization_ratio,
    DENSE_RANK() OVER (
        PARTITION BY block
        ORDER BY avg_utilization_ratio DESC
    ) AS utilization_rank_in_block
FROM weekly_usage
ORDER BY block, utilization_rank_in_block, room_code;
