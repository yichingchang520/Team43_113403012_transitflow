-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data
--    2. Vector      → policy documents for RAG (do not modify)
-- ============================================================

-- ============================================================
--  RELATIONAL SCHEMA
-- ============================================================

-- ── Users ─────────────────────────────────────────────────────

CREATE TABLE users (
    -- PK is a natural key from the mock data (e.g. "RU01"), not SERIAL/UUID:
    -- the IDs are externally meaningful, referenced across tables, and stable.
    -- New runtime registrations get IDs from user_id_seq below.
    user_id         VARCHAR(10)  PRIMARY KEY,
    first_name      VARCHAR(50)  NOT NULL,
    surname         VARCHAR(50)  NOT NULL,
    full_name       VARCHAR(100) GENERATED ALWAYS AS (first_name || ' ' || surname) STORED,
    email           VARCHAR(100) NOT NULL UNIQUE CHECK (email LIKE '%@%'),
    phone           VARCHAR(20),
    date_of_birth   DATE,
    registered_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);

-- Sequence for atomic, race-condition-free user ID generation
CREATE SEQUENCE user_id_seq;

CREATE TABLE user_credentials (
    -- PK = FK to users: one credential row per user (shared-PK 1:1 relationship).
    -- CASCADE: deleting a user removes their credentials (they have no meaning alone).
    user_id           VARCHAR(10)  PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash     VARCHAR(255) NOT NULL,
    secret_question   VARCHAR(255),
    secret_answer     VARCHAR(255),
    hashing_algorithm VARCHAR(50)  NOT NULL DEFAULT 'argon2id',
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Stations ──────────────────────────────────────────────────

CREATE TABLE metro_stations (
    -- Natural key "MS01".."MS20" from mock data — externally meaningful, no SERIAL needed.
    station_id                   VARCHAR(10)  PRIMARY KEY,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_metro         BOOLEAN      NOT NULL DEFAULT FALSE,
    is_interchange_national_rail BOOLEAN      NOT NULL DEFAULT FALSE,
    interchange_nr_station_id    VARCHAR(10)
);

CREATE TABLE metro_station_lines (
    -- CASCADE: lines belong to a station; deleting the station removes them.
    station_id  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE CASCADE,
    line        VARCHAR(5)  NOT NULL,
    -- Composite natural PK: a station+line pair is unique on its own.
    PRIMARY KEY (station_id, line)
);

CREATE TABLE national_rail_stations (
    -- Natural key "NR01".."NR10" from mock data.
    station_id                   VARCHAR(10)  PRIMARY KEY,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN      NOT NULL DEFAULT FALSE,
    is_interchange_metro         BOOLEAN      NOT NULL DEFAULT FALSE,
    -- SET NULL: if the linked metro station is removed, this just loses its
    -- interchange pointer rather than blocking the delete or cascading.
    interchange_metro_station_id VARCHAR(10)  REFERENCES metro_stations(station_id) ON DELETE SET NULL
);

CREATE TABLE national_rail_station_lines (
    -- CASCADE: lines belong to a station.
    station_id  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE CASCADE,
    line        VARCHAR(10) NOT NULL,
    PRIMARY KEY (station_id, line)
);

-- Add FK from metro_stations to national_rail_stations now that both tables exist.
-- SET NULL: removing the NR station just clears the interchange pointer.
ALTER TABLE metro_stations
    ADD CONSTRAINT fk_metro_nr_station
    FOREIGN KEY (interchange_nr_station_id)
    REFERENCES national_rail_stations(station_id) ON DELETE SET NULL;

-- ── Schedules ─────────────────────────────────────────────────

CREATE TABLE metro_schedules (
    -- Natural key "MS_SCH01" etc. from mock data.
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(5)   NOT NULL,
    direction               VARCHAR(20)  NOT NULL,
    -- RESTRICT: a station in active use as a schedule endpoint cannot be deleted.
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    base_fare_usd           NUMERIC(6,2) NOT NULL CHECK (base_fare_usd >= 0),
    per_stop_rate_usd       NUMERIC(6,2) NOT NULL CHECK (per_stop_rate_usd >= 0),
    frequency_min           INT          NOT NULL CHECK (frequency_min > 0)
);

-- Stop sequence + cumulative travel time for each metro schedule.
-- Replaces the stops_in_order / travel_time_from_origin JSONB columns with a
-- properly normalised junction table — one row per (schedule, stop).
-- PK is (schedule_id, stop_order) so stop ordering is the natural key;
-- UNIQUE(schedule_id, station_id) additionally forbids a station appearing
-- twice in the same schedule (valid here since no metro line is circular).
CREATE TABLE metro_schedule_stops (
    schedule_id     VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    station_id      VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id)   ON DELETE RESTRICT,
    stop_order      INT         NOT NULL,                 -- 1 = origin, 2 = next stop, ...
    travel_time_min INT         NOT NULL CHECK (travel_time_min >= 0),  -- cumulative minutes from origin
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id)
);

CREATE TABLE metro_schedule_days (
    -- CASCADE: operating days belong to a schedule.
    schedule_id VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    day_of_week VARCHAR(3)  NOT NULL CHECK (day_of_week IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')),
    PRIMARY KEY (schedule_id, day_of_week)
);

CREATE TABLE national_rail_schedules (
    -- Natural key "NR_SCH01" etc. from mock data.
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(10)  NOT NULL,
    service_type            VARCHAR(10)  NOT NULL CHECK (service_type IN ('normal', 'express')),
    direction               VARCHAR(20)  NOT NULL,
    -- RESTRICT: cannot delete a station used as a schedule endpoint.
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    -- passed_through_stations stays JSONB: it is informational only (stations the
    -- train passes without stopping) and no query ever filters by it individually.
    passed_through_stations JSONB,
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    std_base_fare_usd       NUMERIC(6,2) NOT NULL CHECK (std_base_fare_usd >= 0),
    std_per_stop_rate_usd   NUMERIC(6,2) NOT NULL CHECK (std_per_stop_rate_usd >= 0),
    first_base_fare_usd     NUMERIC(6,2) NOT NULL CHECK (first_base_fare_usd >= 0),
    first_per_stop_rate_usd NUMERIC(6,2) NOT NULL CHECK (first_per_stop_rate_usd >= 0),
    frequency_min           INT          NOT NULL CHECK (frequency_min > 0)
);

-- Stop sequence + cumulative travel time for each national rail schedule.
-- Replaces the stops_in_order / travel_time_from_origin JSONB columns.
CREATE TABLE national_rail_schedule_stops (
    schedule_id     VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    station_id      VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id)   ON DELETE RESTRICT,
    stop_order      INT         NOT NULL,                 -- 1 = origin, 2 = next stop, ...
    travel_time_min INT         NOT NULL CHECK (travel_time_min >= 0),  -- cumulative minutes from origin
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id)
);

CREATE TABLE national_rail_schedule_days (
    -- CASCADE: operating days belong to a schedule.
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    day_of_week VARCHAR(3)  NOT NULL CHECK (day_of_week IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')),
    PRIMARY KEY (schedule_id, day_of_week)
);

-- ── Seat Layouts ──────────────────────────────────────────────

CREATE TABLE seat_layouts (
    -- Natural key "L01" etc. from mock data.
    layout_id   VARCHAR(10) PRIMARY KEY,
    -- CASCADE: a layout exists only for its schedule.
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE
);

CREATE TABLE coaches (
    -- SERIAL surrogate key: a coach has no natural ID of its own (its label
    -- e.g. "A" is only unique within a layout, see UNIQUE below).
    coach_id   SERIAL      PRIMARY KEY,
    -- CASCADE: coaches belong to a layout.
    layout_id  VARCHAR(10) NOT NULL REFERENCES seat_layouts(layout_id) ON DELETE CASCADE,
    coach      VARCHAR(5)  NOT NULL,
    fare_class VARCHAR(10) NOT NULL CHECK (fare_class IN ('standard', 'first')),
    UNIQUE (layout_id, coach)
);

CREATE TABLE seats (
    -- SERIAL surrogate key: seat_id (e.g. "A05") is only unique within a coach,
    -- so a synthetic PK is used and (coach_id, seat_id) enforced UNIQUE below.
    seat_pk    SERIAL      PRIMARY KEY,
    -- CASCADE: seats belong to a coach.
    coach_id   INT         NOT NULL REFERENCES coaches(coach_id) ON DELETE CASCADE,
    seat_id    VARCHAR(10) NOT NULL,
    row_num    INT         NOT NULL,
    col_letter VARCHAR(2)  NOT NULL,
    UNIQUE (coach_id, seat_id)
);

-- ── Bookings & Trips ──────────────────────────────────────────

CREATE TABLE bookings (
    -- Natural key "BK-XXXXXX" generated at booking time (see queries.py _gen_booking_id).
    booking_id             VARCHAR(10)  PRIMARY KEY,
    -- RESTRICT on all references: a booking is a financial/audit record, so the
    -- user, schedule, and stations it points to must not be deletable while it exists.
    user_id                VARCHAR(10)  NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    schedule_id            VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id      VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    travel_date            DATE         NOT NULL,
    departure_time         TIME         NOT NULL,
    ticket_type            VARCHAR(10)  NOT NULL CHECK (ticket_type IN ('single', 'return', 'season')),
    fare_class             VARCHAR(10)  NOT NULL CHECK (fare_class IN ('standard', 'first')),
    coach                  VARCHAR(5)   NOT NULL,
    seat_id                VARCHAR(10)  NOT NULL,
    stops_travelled        INT          NOT NULL CHECK (stops_travelled > 0),
    amount_usd             NUMERIC(8,2) NOT NULL CHECK (amount_usd >= 0),
    status                 VARCHAR(20)  NOT NULL CHECK (status IN ('confirmed', 'completed', 'cancelled')),
    booked_at              TIMESTAMPTZ  NOT NULL,
    travelled_at           TIMESTAMPTZ
);

CREATE TABLE metro_trips (
    -- Natural key "MT001" etc. from mock data.
    trip_id                VARCHAR(10)  PRIMARY KEY,
    -- RESTRICT: trips are financial/audit records (same reasoning as bookings).
    user_id                VARCHAR(10)  NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    schedule_id            VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id      VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    travel_date            DATE         NOT NULL,
    ticket_type            VARCHAR(10)  NOT NULL CHECK (ticket_type IN ('single', 'day_pass')),
    -- Self-reference to the day-pass parent trip. SET NULL: if the parent trip is
    -- removed, this trip survives but loses its day-pass linkage.
    day_pass_ref           VARCHAR(10)  REFERENCES metro_trips(trip_id) ON DELETE SET NULL,
    stops_travelled        INT          CHECK (stops_travelled > 0),
    amount_usd             NUMERIC(8,2) NOT NULL CHECK (amount_usd >= 0),
    status                 VARCHAR(20)  NOT NULL CHECK (status IN ('confirmed', 'completed', 'cancelled')),
    purchased_at           TIMESTAMPTZ,
    travelled_at           TIMESTAMPTZ
);

-- ── Payments & Feedback ───────────────────────────────────────

CREATE TABLE payments (
    -- Natural key "PM-XXXXXX" generated at payment time.
    payment_id    VARCHAR(10)  PRIMARY KEY,
    -- Exclusive arc (see CHECK below): exactly one of booking_id / metro_trip_id is set.
    -- CASCADE: a payment has no meaning once its booking/trip is gone.
    booking_id    VARCHAR(10)  REFERENCES bookings(booking_id) ON DELETE CASCADE,
    metro_trip_id VARCHAR(10)  REFERENCES metro_trips(trip_id) ON DELETE CASCADE,
    amount_usd    NUMERIC(8,2) NOT NULL CHECK (amount_usd >= 0),
    method        VARCHAR(20)  NOT NULL CHECK (method IN ('credit_card', 'debit_card', 'ewallet')),
    status        VARCHAR(20)  NOT NULL CHECK (status IN ('paid', 'pending', 'refunded', 'failed')),
    paid_at       TIMESTAMPTZ  NOT NULL,
    CONSTRAINT chk_payment_exclusive_arc CHECK (
        (booking_id IS NOT NULL AND metro_trip_id IS NULL) OR
        (booking_id IS NULL AND metro_trip_id IS NOT NULL)
    )
);

CREATE TABLE feedback (
    -- Natural key "FB001" etc. from mock data.
    feedback_id   VARCHAR(10) PRIMARY KEY,
    -- Exclusive arc (see CHECK below). CASCADE: feedback dies with its booking/trip.
    booking_id    VARCHAR(10) REFERENCES bookings(booking_id) ON DELETE CASCADE,
    metro_trip_id VARCHAR(10) REFERENCES metro_trips(trip_id) ON DELETE CASCADE,
    -- CASCADE: feedback is owned by the user; deleting the user removes it.
    user_id       VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    rating        SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    submitted_at  TIMESTAMPTZ NOT NULL,
    CONSTRAINT chk_feedback_exclusive_arc CHECK (
        (booking_id IS NOT NULL AND metro_trip_id IS NULL) OR
        (booking_id IS NULL AND metro_trip_id IS NOT NULL)
    )
);

-- ── Indexes ───────────────────────────────────────────────────

CREATE INDEX idx_bookings_user        ON bookings(user_id);
CREATE INDEX idx_bookings_schedule    ON bookings(schedule_id);
CREATE INDEX idx_bookings_travel_date ON bookings(travel_date);
CREATE INDEX idx_metro_trips_user     ON metro_trips(user_id);
CREATE INDEX idx_metro_trips_date     ON metro_trips(travel_date);
CREATE INDEX idx_payments_booking     ON payments(booking_id);
CREATE INDEX idx_payments_metro_trip  ON payments(metro_trip_id);
CREATE INDEX idx_feedback_user        ON feedback(user_id);

-- Prevent double booking: same train, same date, same seat cannot be booked twice
CREATE UNIQUE INDEX idx_prevent_double_booking
    ON bookings (schedule_id, travel_date, coach, seat_id)
    WHERE status IN ('confirmed', 'completed');

-- Prevent duplicate feedback per booking
CREATE UNIQUE INDEX idx_feedback_unique_booking
    ON feedback (booking_id) WHERE booking_id IS NOT NULL;

CREATE UNIQUE INDEX idx_feedback_unique_metro_trip
    ON feedback (metro_trip_id) WHERE metro_trip_id IS NOT NULL;

-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,
    content     TEXT         NOT NULL,
    -- 768-dim  → Ollama nomic-embed-text (default)
    -- 3072-dim → Gemini gemini-embedding-001
    -- If you switch LLM_PROVIDER to gemini, change to vector(3072) and reset the database.
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_policy_embedding ON policy_documents USING hnsw (embedding vector_cosine_ops);
