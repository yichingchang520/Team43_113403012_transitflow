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
    user_id           VARCHAR(10)  PRIMARY KEY REFERENCES users(user_id),
    password_hash     VARCHAR(255) NOT NULL,
    secret_question   VARCHAR(255),
    secret_answer     VARCHAR(255),
    hashing_algorithm VARCHAR(50)  NOT NULL DEFAULT 'argon2id',
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── Stations ──────────────────────────────────────────────────

CREATE TABLE metro_stations (
    station_id                   VARCHAR(10)  PRIMARY KEY,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_metro         BOOLEAN      NOT NULL DEFAULT FALSE,
    is_interchange_national_rail BOOLEAN      NOT NULL DEFAULT FALSE,
    interchange_nr_station_id    VARCHAR(10)
);

CREATE TABLE metro_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    line        VARCHAR(5)  NOT NULL,
    PRIMARY KEY (station_id, line)
);

CREATE TABLE national_rail_stations (
    station_id                   VARCHAR(10)  PRIMARY KEY,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN      NOT NULL DEFAULT FALSE,
    is_interchange_metro         BOOLEAN      NOT NULL DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(10)  REFERENCES metro_stations(station_id)
);

CREATE TABLE national_rail_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    line        VARCHAR(10) NOT NULL,
    PRIMARY KEY (station_id, line)
);

-- Add FK from metro_stations to national_rail_stations now that both tables exist
ALTER TABLE metro_stations
    ADD CONSTRAINT fk_metro_nr_station
    FOREIGN KEY (interchange_nr_station_id)
    REFERENCES national_rail_stations(station_id);

-- ── Schedules ─────────────────────────────────────────────────

CREATE TABLE metro_schedules (
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(5)   NOT NULL,
    direction               VARCHAR(20)  NOT NULL,
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    stops_in_order          JSONB        NOT NULL,
    travel_time_from_origin JSONB        NOT NULL,
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    base_fare_usd           NUMERIC(6,2) NOT NULL CHECK (base_fare_usd >= 0),
    per_stop_rate_usd       NUMERIC(6,2) NOT NULL CHECK (per_stop_rate_usd >= 0),
    frequency_min           INT          NOT NULL CHECK (frequency_min > 0)
);

CREATE TABLE metro_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id),
    day_of_week VARCHAR(3)  NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

CREATE TABLE national_rail_schedules (
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(10)  NOT NULL,
    service_type            VARCHAR(10)  NOT NULL CHECK (service_type IN ('normal', 'express')),
    direction               VARCHAR(20)  NOT NULL,
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    stops_in_order          JSONB        NOT NULL,
    passed_through_stations JSONB,
    travel_time_from_origin JSONB        NOT NULL,
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    std_base_fare_usd       NUMERIC(6,2) NOT NULL CHECK (std_base_fare_usd >= 0),
    std_per_stop_rate_usd   NUMERIC(6,2) NOT NULL CHECK (std_per_stop_rate_usd >= 0),
    first_base_fare_usd     NUMERIC(6,2) NOT NULL CHECK (first_base_fare_usd >= 0),
    first_per_stop_rate_usd NUMERIC(6,2) NOT NULL CHECK (first_per_stop_rate_usd >= 0),
    frequency_min           INT          NOT NULL CHECK (frequency_min > 0)
);

CREATE TABLE national_rail_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    day_of_week VARCHAR(3)  NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

-- ── Seat Layouts ──────────────────────────────────────────────

CREATE TABLE seat_layouts (
    layout_id   VARCHAR(10) PRIMARY KEY,
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id)
);

CREATE TABLE coaches (
    coach_id   SERIAL      PRIMARY KEY,
    layout_id  VARCHAR(10) NOT NULL REFERENCES seat_layouts(layout_id),
    coach      VARCHAR(5)  NOT NULL,
    fare_class VARCHAR(10) NOT NULL CHECK (fare_class IN ('standard', 'first')),
    UNIQUE (layout_id, coach)
);

CREATE TABLE seats (
    seat_pk    SERIAL      PRIMARY KEY,
    coach_id   INT         NOT NULL REFERENCES coaches(coach_id),
    seat_id    VARCHAR(10) NOT NULL,
    row_num    INT         NOT NULL,
    col_letter VARCHAR(2)  NOT NULL,
    UNIQUE (coach_id, seat_id)
);

-- ── Bookings & Trips ──────────────────────────────────────────

CREATE TABLE bookings (
    booking_id             VARCHAR(10)  PRIMARY KEY,
    user_id                VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    schedule_id            VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id),
    origin_station_id      VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
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
    trip_id                VARCHAR(10)  PRIMARY KEY,
    user_id                VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    schedule_id            VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id),
    origin_station_id      VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    travel_date            DATE         NOT NULL,
    ticket_type            VARCHAR(10)  NOT NULL CHECK (ticket_type IN ('single', 'day_pass')),
    day_pass_ref           VARCHAR(10)  REFERENCES metro_trips(trip_id),
    stops_travelled        INT          CHECK (stops_travelled > 0),
    amount_usd             NUMERIC(8,2) NOT NULL CHECK (amount_usd >= 0),
    status                 VARCHAR(20)  NOT NULL CHECK (status IN ('confirmed', 'completed', 'cancelled')),
    purchased_at           TIMESTAMPTZ,
    travelled_at           TIMESTAMPTZ
);

-- ── Payments & Feedback ───────────────────────────────────────

CREATE TABLE payments (
    payment_id    VARCHAR(10)  PRIMARY KEY,
    booking_id    VARCHAR(10)  REFERENCES bookings(booking_id),
    metro_trip_id VARCHAR(10)  REFERENCES metro_trips(trip_id),
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
    feedback_id   VARCHAR(10) PRIMARY KEY,
    booking_id    VARCHAR(10) REFERENCES bookings(booking_id),
    metro_trip_id VARCHAR(10) REFERENCES metro_trips(trip_id),
    user_id       VARCHAR(10) NOT NULL REFERENCES users(user_id),
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
