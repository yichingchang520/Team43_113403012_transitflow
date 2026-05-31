"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)

STUDENT TASK
------------
Design your schema in databases/relational/schema.sql, seed it with
skeleton/seed_postgres.py, then implement the query functions below.

Functions prefixed with `query_`  are read-only lookups called by the agent.
Functions prefixed with `execute_` are write operations (booking/cancellation).

The vector functions (query_policy_vector_search, store_policy_document)
are already implemented — do not modify them.
"""

from __future__ import annotations

import json
import re
import secrets
import string
from datetime import date, datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from psycopg2 import pool as _pg_pool
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

_ph = PasswordHasher()

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD

# ── Email validation ──────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# ── Password policy ───────────────────────────────────────────────────────────
_PW_MIN_LEN = 8
_PW_MAX_LEN = 128   # guard against DoS via intentionally slow argon2 hashing

# ── ID alphabet for booking / payment IDs ────────────────────────────────────
_ID_CHARS = string.ascii_uppercase + string.digits

# ── Connection pool ───────────────────────────────────────────────────────────
# One pool per process; lazily initialised on first use.
# min=2 keeps warm connections alive; max=10 caps load on the DB server.
_pool: _pg_pool.ThreadedConnectionPool | None = None
_POOL_MIN = 2
_POOL_MAX = 10


def _get_pool() -> _pg_pool.ThreadedConnectionPool:
    """Lazily initialise and return the thread-safe connection pool."""
    global _pool
    if _pool is None:
        _pool = _pg_pool.ThreadedConnectionPool(_POOL_MIN, _POOL_MAX, PG_DSN)
    return _pool


class _PooledConn:
    """
    Context manager: borrows a connection from the pool on enter,
    resets it to a clean state, and returns it to the pool on exit.

    Usage::
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(...)
    """

    def __init__(self, autocommit: bool = True) -> None:
        self._autocommit = autocommit
        self._conn: psycopg2.extensions.connection | None = None

    def __enter__(self) -> psycopg2.extensions.connection:
        self._conn = _get_pool().getconn()
        self._conn.autocommit = self._autocommit
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn is not None:
            try:
                # Roll back any uncommitted work, then reset to a clean state
                # so the next borrower gets a pristine connection.
                if not self._conn.autocommit:
                    self._conn.rollback()
                self._conn.autocommit = True
                _get_pool().putconn(self._conn)
            except Exception:
                # If we cannot reset, discard the connection entirely so the
                # pool doesn't hand out a broken connection to the next caller.
                _get_pool().putconn(self._conn, close=True)
        return False  # never suppress exceptions


def _connect() -> _PooledConn:
    """Return a pooled read-only (autocommit=True) connection context manager."""
    return _PooledConn(autocommit=True)


def _gen_booking_id() -> str:
    """Cryptographically random booking ID, e.g. 'BK-A3F9K2'."""
    return "BK-" + "".join(secrets.choice(_ID_CHARS) for _ in range(6))


def _gen_payment_id() -> str:
    """Cryptographically random payment ID, e.g. 'PM-X7Q2P1'."""
    return "PM-" + "".join(secrets.choice(_ID_CHARS) for _ in range(6))


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a cursor, run SQL, return rows.
# Use _connect() for read-only queries; for write operations use a manual
# connection with conn.commit() / conn.rollback() (see execute_booking below).

def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination stations
    in the correct order, along with seat occupancy for the requested travel date.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        travel_date:     e.g. "2025-06-01" — used to count bookings; omit for general info
    """
    # When travel_date is given we need seat availability as well.
    # Use a single CTE query instead of 2 extra round-trips per schedule
    # (the old approach was an N+1 pattern that scaled badly).
    # stops_in_order is rebuilt from the national_rail_schedule_stops junction
    # table via array_agg so the returned shape stays identical to the old JSONB
    # design (the agent still sees an ordered list of station IDs).
    # Origin/destination positions now come from JOINs on stop_order instead of
    # jsonb_array_elements_text LATERAL scans.
    if travel_date:
        sql = """
            WITH seat_totals AS (
                SELECT sl.schedule_id,
                       COUNT(se.seat_pk) AS total_seats
                FROM   seat_layouts sl
                JOIN   coaches co ON co.layout_id = sl.layout_id
                JOIN   seats   se ON se.coach_id  = co.coach_id
                GROUP  BY sl.schedule_id
            ),
            booking_counts AS (
                SELECT schedule_id,
                       COUNT(*) AS booked_seats
                FROM   bookings
                WHERE  travel_date = %(travel_date)s
                  AND  status NOT IN ('cancelled')
                GROUP  BY schedule_id
            )
            SELECT
                s.schedule_id, s.line, s.service_type, s.direction,
                s.origin_station_id, s.destination_station_id,
                s.first_train_time::text  AS first_train_time,
                s.last_train_time::text   AS last_train_time,
                s.std_base_fare_usd, s.std_per_stop_rate_usd,
                s.first_base_fare_usd, s.first_per_stop_rate_usd,
                s.frequency_min,
                o_ns.name AS origin_name,
                d_ns.name AS destination_name,
                (d_st.stop_order - o_st.stop_order)::int           AS stops_travelled,
                (SELECT array_agg(ss.station_id ORDER BY ss.stop_order)
                   FROM national_rail_schedule_stops ss
                   WHERE ss.schedule_id = s.schedule_id)           AS stops_in_order,
                COALESCE(st.total_seats,  0)                       AS seats_total,
                COALESCE(bc.booked_seats, 0)                       AS seats_booked,
                COALESCE(st.total_seats, 0)
                    - COALESCE(bc.booked_seats, 0)                 AS seats_available
            FROM national_rail_schedules s
            JOIN national_rail_stations o_ns ON o_ns.station_id = %(origin_id)s
            JOIN national_rail_stations d_ns ON d_ns.station_id = %(destination_id)s
            JOIN national_rail_schedule_stops o_st
                ON o_st.schedule_id = s.schedule_id AND o_st.station_id = %(origin_id)s
            JOIN national_rail_schedule_stops d_st
                ON d_st.schedule_id = s.schedule_id AND d_st.station_id = %(destination_id)s
            LEFT JOIN seat_totals    st ON st.schedule_id = s.schedule_id
            LEFT JOIN booking_counts bc ON bc.schedule_id = s.schedule_id
            WHERE o_st.stop_order < d_st.stop_order
              AND s.schedule_id IN (
                  SELECT schedule_id FROM national_rail_schedule_days
                  WHERE  day_of_week = lower(to_char(%(travel_date)s::date, 'Dy'))
              )
        """
        params: dict = {
            "origin_id":    origin_id,
            "destination_id": destination_id,
            "travel_date":  travel_date,
        }
    else:
        sql = """
            SELECT
                s.schedule_id, s.line, s.service_type, s.direction,
                s.origin_station_id, s.destination_station_id,
                s.first_train_time::text  AS first_train_time,
                s.last_train_time::text   AS last_train_time,
                s.std_base_fare_usd, s.std_per_stop_rate_usd,
                s.first_base_fare_usd, s.first_per_stop_rate_usd,
                s.frequency_min,
                o_ns.name AS origin_name,
                d_ns.name AS destination_name,
                (d_st.stop_order - o_st.stop_order)::int AS stops_travelled,
                (SELECT array_agg(ss.station_id ORDER BY ss.stop_order)
                   FROM national_rail_schedule_stops ss
                   WHERE ss.schedule_id = s.schedule_id)  AS stops_in_order
            FROM national_rail_schedules s
            JOIN national_rail_stations o_ns ON o_ns.station_id = %(origin_id)s
            JOIN national_rail_stations d_ns ON d_ns.station_id = %(destination_id)s
            JOIN national_rail_schedule_stops o_st
                ON o_st.schedule_id = s.schedule_id AND o_st.station_id = %(origin_id)s
            JOIN national_rail_schedule_stops d_st
                ON d_st.schedule_id = s.schedule_id AND d_st.station_id = %(destination_id)s
            WHERE o_st.stop_order < d_st.stop_order
        """
        params = {"origin_id": origin_id, "destination_id": destination_id}

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.

    Args:
        schedule_id:     e.g. "NR_SCH01"
        fare_class:      "standard" or "first"
        stops_travelled: number of stops between origin and destination (inclusive)

    Returns:
        dict with fare_class, base_fare_usd, per_stop_rate_usd, total_fare_usd
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT std_base_fare_usd,   std_per_stop_rate_usd,
                       first_base_fare_usd, first_per_stop_rate_usd
                FROM national_rail_schedules
                WHERE schedule_id = %s
                """,
                (schedule_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            if fare_class == "first":
                base = float(row["first_base_fare_usd"])
                rate = float(row["first_per_stop_rate_usd"])
            else:
                base = float(row["std_base_fare_usd"])
                rate = float(row["std_per_stop_rate_usd"])

            total = round(base + rate * stops_travelled, 2)
            return {
                "fare_class":        fare_class,
                "base_fare_usd":     base,
                "per_stop_rate_usd": rate,
                "stops_travelled":   stops_travelled,
                "total_fare_usd":    total,
            }


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve both origin and destination in the correct order.

    Args:
        origin_id:       e.g. "MS01"
        destination_id:  e.g. "MS09"
    """
    # stops_in_order rebuilt from metro_schedule_stops junction table via array_agg;
    # origin/destination positions come from JOINs on stop_order.
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.direction,
            s.origin_station_id,
            s.destination_station_id,
            s.first_train_time::text AS first_train_time,
            s.last_train_time::text  AS last_train_time,
            s.base_fare_usd,
            s.per_stop_rate_usd,
            s.frequency_min,
            o_ms.name AS origin_name,
            d_ms.name AS destination_name,
            (d_st.stop_order - o_st.stop_order)::int AS stops_travelled,
            (SELECT array_agg(ss.station_id ORDER BY ss.stop_order)
               FROM metro_schedule_stops ss
               WHERE ss.schedule_id = s.schedule_id) AS stops_in_order
        FROM metro_schedules s
        JOIN metro_stations o_ms ON o_ms.station_id = %(origin_id)s
        JOIN metro_stations d_ms ON d_ms.station_id = %(destination_id)s
        JOIN metro_schedule_stops o_st
            ON o_st.schedule_id = s.schedule_id AND o_st.station_id = %(origin_id)s
        JOIN metro_schedule_stops d_st
            ON d_st.schedule_id = s.schedule_id AND d_st.station_id = %(destination_id)s
        WHERE o_st.stop_order < d_st.stop_order
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, {"origin_id": origin_id, "destination_id": destination_id})
            return [dict(r) for r in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the metro fare for a single-ticket journey.

    Args:
        schedule_id:     e.g. "MS_SCH01"
        stops_travelled: number of stops between origin and destination

    Returns:
        dict with base_fare_usd, per_stop_rate_usd, total_fare_usd
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT base_fare_usd, per_stop_rate_usd FROM metro_schedules WHERE schedule_id = %s",
                (schedule_id,),
            )
            row = cur.fetchone()
            if not row:
                return None

            base  = float(row["base_fare_usd"])
            rate  = float(row["per_stop_rate_usd"])
            total = round(base + rate * stops_travelled, 2)
            return {
                "base_fare_usd":     base,
                "per_stop_rate_usd": rate,
                "stops_travelled":   stops_travelled,
                "total_fare_usd":    total,
            }


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """
    Return available seats for a national rail journey on a given date.

    Args:
        schedule_id:  e.g. "NR_SCH01"
        travel_date:  e.g. "2025-06-01"
        fare_class:   "standard" or "first"

    Returns:
        List of dicts: {seat_id, coach, row, column}
    """
    sql = """
        SELECT
            se.seat_id,
            co.coach,
            se.row_num    AS row,
            se.col_letter AS column
        FROM seat_layouts sl
        JOIN coaches co ON co.layout_id = sl.layout_id
                       AND co.fare_class = %(fare_class)s
        JOIN seats   se ON se.coach_id   = co.coach_id
        WHERE sl.schedule_id = %(schedule_id)s
          AND NOT EXISTS (
              SELECT 1
              FROM bookings b
              WHERE b.schedule_id = %(schedule_id)s
                AND b.travel_date  = %(travel_date)s
                AND b.coach        = co.coach
                AND b.seat_id      = se.seat_id
                AND b.status       NOT IN ('cancelled')
          )
        ORDER BY co.coach, se.row_num, se.col_letter
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, {
                "schedule_id": schedule_id,
                "travel_date": travel_date,
                "fare_class":  fare_class,
            })
            return [dict(r) for r in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """
    Select `count` seats that are as close together as possible (same row preferred,
    then adjacent rows). Returns a list of seat_ids.

    Args:
        available_seats: output of query_available_seats()
        count:           number of seats needed
    """
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict
    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """Return a user's profile by email."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT user_id, full_name, first_name, surname,
                       email, phone, date_of_birth, registered_at, is_active
                FROM users
                WHERE email = %s
                """,
                (user_email,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """
    Return a user's combined booking history (national rail + metro).

    Returns:
        dict with keys 'national_rail' (list) and 'metro' (list)
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT user_id FROM users WHERE email = %s", (user_email,))
            user = cur.fetchone()
            if not user:
                return {"national_rail": [], "metro": []}
            user_id = user["user_id"]

            # National rail bookings
            cur.execute(
                """
                SELECT b.*,
                       o.name AS origin_name,
                       d.name AS destination_name,
                       s.line, s.service_type
                FROM bookings b
                JOIN national_rail_stations  o ON o.station_id  = b.origin_station_id
                JOIN national_rail_stations  d ON d.station_id  = b.destination_station_id
                JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                WHERE b.user_id = %s
                ORDER BY b.booked_at DESC
                """,
                (user_id,),
            )
            nr_bookings = [dict(r) for r in cur.fetchall()]

            # Metro trips
            cur.execute(
                """
                SELECT t.*,
                       o.name AS origin_name,
                       d.name AS destination_name,
                       s.line
                FROM metro_trips     t
                JOIN metro_stations  o ON o.station_id  = t.origin_station_id
                JOIN metro_stations  d ON d.station_id  = t.destination_station_id
                JOIN metro_schedules s ON s.schedule_id = t.schedule_id
                WHERE t.user_id = %s
                ORDER BY t.purchased_at DESC NULLS LAST
                """,
                (user_id,),
            )
            metro_trips = [dict(r) for r in cur.fetchall()]

            return {"national_rail": nr_bookings, "metro": metro_trips}


def query_payment_info(reference_id: str) -> Optional[dict]:
    """
    Return payment record for a booking or metro trip.

    Args:
        reference_id: a booking_id (e.g. "BK-A3F9K2") or a metro trip_id (e.g. "MT001")
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM payments WHERE booking_id = %s OR metro_trip_id = %s",
                (reference_id, reference_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
    payment_method: str = "credit_card",
) -> tuple[bool, dict | str]:
    """
    Create a national rail booking for a logged-in user.

    Args:
        user_id:                e.g. "RU01" — must match the logged-in user
        schedule_id:            e.g. "NR_SCH01"
        origin_station_id:      e.g. "NR01"
        destination_station_id: e.g. "NR05"
        travel_date:            e.g. "2025-06-01"
        fare_class:             "standard" or "first"
        seat_id:                e.g. "B05" (or "any" to auto-assign)
        ticket_type:            "single" (default) or "return"
        payment_method:         "credit_card" (default), "debit_card", or "ewallet"

    Returns:
        (True, booking_dict)   on success
        (False, error_message) on failure
    """
    # Validate payment method before opening connection
    valid_methods = ("credit_card", "debit_card", "ewallet")
    if payment_method not in valid_methods:
        return False, f"Invalid payment method. Must be one of: {', '.join(valid_methods)}"

    # Validate travel date is not in the past
    try:
        travel_date_obj = date.fromisoformat(travel_date)
    except ValueError:
        return False, "Invalid travel_date format. Use YYYY-MM-DD"
    if travel_date_obj < date.today():
        return False, "Travel date cannot be in the past"

    conn = _get_pool().getconn()
    conn.autocommit = False
    conn.set_session(isolation_level="SERIALIZABLE")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. Validate user
            cur.execute(
                "SELECT user_id FROM users WHERE user_id = %s AND is_active = TRUE",
                (user_id,),
            )
            if not cur.fetchone():
                conn.rollback()
                return False, "User not found or inactive"

            # 1b. Check the schedule operates on the travel_date's day of week
            cur.execute(
                """
                SELECT 1 FROM national_rail_schedule_days
                WHERE schedule_id = %s
                  AND day_of_week = lower(to_char(%s::date, 'Dy'))
                """,
                (schedule_id, travel_date),
            )
            if not cur.fetchone():
                conn.rollback()
                return False, "This schedule does not operate on that day of the week"

            # 2. Get schedule — verify origin and destination are both stops on this
            #    schedule in the correct order, using the junction table join.
            cur.execute(
                """
                SELECT s.*,
                       o_st.stop_order AS origin_pos,
                       d_st.stop_order AS dest_pos
                FROM national_rail_schedules s
                JOIN national_rail_schedule_stops o_st
                    ON o_st.schedule_id = s.schedule_id AND o_st.station_id = %s
                JOIN national_rail_schedule_stops d_st
                    ON d_st.schedule_id = s.schedule_id AND d_st.station_id = %s
                WHERE s.schedule_id = %s
                  AND o_st.stop_order < d_st.stop_order
                """,
                (origin_station_id, destination_station_id, schedule_id),
            )
            schedule = cur.fetchone()
            if not schedule:
                conn.rollback()
                return False, "Schedule not found, or origin/destination not valid for this route"

            stops_travelled = schedule["dest_pos"] - schedule["origin_pos"]

            # 3. Calculate fare
            if fare_class == "first":
                base = float(schedule["first_base_fare_usd"])
                rate = float(schedule["first_per_stop_rate_usd"])
            else:
                base = float(schedule["std_base_fare_usd"])
                rate = float(schedule["std_per_stop_rate_usd"])
            amount = round(base + rate * stops_travelled, 2)

            # 4. Seat selection
            if seat_id.lower() == "any":
                cur.execute(
                    """
                    SELECT se.seat_id, co.coach
                    FROM seat_layouts sl
                    JOIN coaches co ON co.layout_id = sl.layout_id
                                   AND co.fare_class = %s
                    JOIN seats   se ON se.coach_id   = co.coach_id
                    WHERE sl.schedule_id = %s
                      AND NOT EXISTS (
                          SELECT 1 FROM bookings b
                          WHERE b.schedule_id = %s
                            AND b.travel_date  = %s
                            AND b.coach        = co.coach
                            AND b.seat_id      = se.seat_id
                            AND b.status NOT IN ('cancelled')
                      )
                    ORDER BY se.row_num, se.col_letter
                    LIMIT 1
                    """,
                    (fare_class, schedule_id, schedule_id, travel_date),
                )
                seat_row = cur.fetchone()
                if not seat_row:
                    conn.rollback()
                    return False, "No available seats for this schedule and date"
                chosen_seat  = seat_row["seat_id"]
                chosen_coach = seat_row["coach"]
            else:
                # Verify the seat exists in the right class
                cur.execute(
                    """
                    SELECT se.seat_id, co.coach
                    FROM seat_layouts sl
                    JOIN coaches co ON co.layout_id = sl.layout_id
                                   AND co.fare_class = %s
                    JOIN seats   se ON se.coach_id   = co.coach_id
                    WHERE sl.schedule_id = %s AND se.seat_id = %s
                    """,
                    (fare_class, schedule_id, seat_id),
                )
                seat_row = cur.fetchone()
                if not seat_row:
                    conn.rollback()
                    return False, f"Seat '{seat_id}' not found in {fare_class} class for this schedule"

                # Check it is not already booked
                cur.execute(
                    """
                    SELECT 1 FROM bookings
                    WHERE schedule_id = %s
                      AND travel_date  = %s
                      AND coach        = %s
                      AND seat_id      = %s
                      AND status NOT IN ('cancelled')
                    """,
                    (schedule_id, travel_date, seat_row["coach"], seat_id),
                )
                if cur.fetchone():
                    conn.rollback()
                    return False, f"Seat '{seat_id}' is already booked for {travel_date}"

                chosen_seat  = seat_row["seat_id"]
                chosen_coach = seat_row["coach"]

            # 5. Generate IDs and insert
            booking_id     = _gen_booking_id()
            payment_id     = _gen_payment_id()
            booked_at      = datetime.now(timezone.utc)
            departure_time = schedule["first_train_time"]

            cur.execute(
                """
                INSERT INTO bookings (
                    booking_id, user_id, schedule_id,
                    origin_station_id, destination_station_id,
                    travel_date, departure_time,
                    ticket_type, fare_class,
                    coach, seat_id, stops_travelled,
                    amount_usd, status, booked_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    booking_id, user_id, schedule_id,
                    origin_station_id, destination_station_id,
                    travel_date, departure_time,
                    ticket_type, fare_class,
                    chosen_coach, chosen_seat, stops_travelled,
                    amount, "confirmed", booked_at,
                ),
            )

            cur.execute(
                """
                INSERT INTO payments (payment_id, booking_id, metro_trip_id, amount_usd, method, status, paid_at)
                VALUES (%s, %s, NULL, %s, %s, %s, %s)
                """,
                (payment_id, booking_id, amount, payment_method, "paid", booked_at),
            )

            conn.commit()
            return True, {
                "booking_id":              booking_id,
                "payment_id":              payment_id,
                "user_id":                 user_id,
                "schedule_id":             schedule_id,
                "origin_station_id":       origin_station_id,
                "destination_station_id":  destination_station_id,
                "travel_date":             travel_date,
                "departure_time":          str(departure_time),
                "fare_class":              fare_class,
                "ticket_type":             ticket_type,
                "coach":                   chosen_coach,
                "seat_id":                 chosen_seat,
                "stops_travelled":         stops_travelled,
                "amount_usd":              amount,
                "status":                  "confirmed",
                "booked_at":               booked_at.isoformat(),
            }
    except psycopg2.errors.SerializationFailure:
        conn.rollback()
        return False, "Booking failed due to concurrent conflict. Please try again."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        try:
            conn.autocommit = True
            _get_pool().putconn(conn)
        except Exception:
            _get_pool().putconn(conn, close=True)


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.

    Calculates the refund amount according to the booking's service type:
      - Normal service: RF001 windows (100% / 75% / 50% / 0%)
      - Express service: RF002 windows (100% / 50% / 0%)

    Args:
        booking_id: e.g. "BK001"
        user_id:    must match the booking's user_id

    Returns:
        (True, result_dict)  with refund_amount_usd and policy note
        (False, error_msg)
    """
    conn = _get_pool().getconn()
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT b.*, s.service_type
                FROM bookings b
                JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                WHERE b.booking_id = %s AND b.user_id = %s
                """,
                (booking_id, user_id),
            )
            booking = cur.fetchone()
            if not booking:
                conn.rollback()
                return False, "Booking not found or does not belong to this user"

            if booking["status"] == "cancelled":
                conn.rollback()
                return False, "Booking is already cancelled"
            if booking["status"] == "completed":
                conn.rollback()
                return False, "Cannot cancel a completed journey"

            # Hours until departure (treat stored times as UTC)
            travel_dt = datetime.combine(
                booking["travel_date"], booking["departure_time"]
            ).replace(tzinfo=timezone.utc)
            hours_until = (travel_dt - datetime.now(timezone.utc)).total_seconds() / 3600

            service_type = (booking["service_type"] or "normal").lower()

            if service_type == "express":
                # RF002: 100% > 72 h | 50% 3–72 h | 0% < 3 h
                if hours_until > 72:
                    refund_pct   = 1.0
                    policy_note  = "RF002: Full refund (>72 h before departure)"
                elif hours_until > 3:
                    refund_pct   = 0.5
                    policy_note  = "RF002: 50% refund (3–72 h before departure)"
                else:
                    refund_pct   = 0.0
                    policy_note  = "RF002: No refund (<3 h before departure)"
            else:
                # RF001: 100% > 72 h | 75% 24–72 h | 50% 3–24 h | 0% < 3 h
                if hours_until > 72:
                    refund_pct   = 1.0
                    policy_note  = "RF001: Full refund (>72 h before departure)"
                elif hours_until > 24:
                    refund_pct   = 0.75
                    policy_note  = "RF001: 75% refund (24–72 h before departure)"
                elif hours_until > 3:
                    refund_pct   = 0.5
                    policy_note  = "RF001: 50% refund (3–24 h before departure)"
                else:
                    refund_pct   = 0.0
                    policy_note  = "RF001: No refund (<3 h before departure)"

            refund_amount = round(float(booking["amount_usd"]) * refund_pct, 2)

            cur.execute(
                "UPDATE bookings SET status = 'cancelled' WHERE booking_id = %s",
                (booking_id,),
            )

            # Update payment status: refunded if money is returned, else keep as paid
            payment_status = "refunded" if refund_amount > 0 else "paid"
            cur.execute(
                "UPDATE payments SET status = %s WHERE booking_id = %s",
                (payment_status, booking_id),
            )

            conn.commit()
            return True, {
                "booking_id":          booking_id,
                "original_amount_usd": float(booking["amount_usd"]),
                "refund_amount_usd":   refund_amount,
                "refund_percentage":   int(refund_pct * 100),
                "policy_note":         policy_note,
                "status":              "cancelled",
            }
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        try:
            conn.autocommit = True
            _get_pool().putconn(conn)
        except Exception:
            _get_pool().putconn(conn, close=True)


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """
    Register a new user.
    Returns (True, user_id) on success or (False, error_message) on failure.

    Passwords and secret answers are hashed with argon2id before storage.
    Emails are stored normalised (stripped and lower-cased).
    """
    # ── Input validation (fail fast before touching the DB) ──────────────────
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        return False, "Invalid email address"

    if len(password) < _PW_MIN_LEN:
        return False, f"Password must be at least {_PW_MIN_LEN} characters"
    if len(password) > _PW_MAX_LEN:
        return False, f"Password must be at most {_PW_MAX_LEN} characters"

    conn = _get_pool().getconn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # Advisory lock: serialise concurrent registrations so two threads
            # cannot both read the same MAX(user_id) and generate a duplicate.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('register_user'))")

            # Reject duplicate email
            cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                conn.rollback()
                return False, "Email is already registered"

            # Atomic user ID generation via sequence — no race condition.
            cur.execute(
                "SELECT 'RU' || LPAD(nextval('user_id_seq')::text, 2, '0')"
            )
            new_user_id = cur.fetchone()[0]

            dob = date(year_of_birth, 1, 1)

            cur.execute(
                """
                INSERT INTO users
                    (user_id, first_name, surname, email, date_of_birth, registered_at, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                """,
                (new_user_id, first_name.strip(), surname.strip(),
                 email, dob, datetime.now(timezone.utc)),
            )
            cur.execute(
                """
                INSERT INTO user_credentials
                    (user_id, password_hash, secret_question, secret_answer)
                VALUES (%s, %s, %s, %s)
                """,
                (new_user_id, _ph.hash(password), secret_question,
                 _ph.hash(secret_answer.strip().lower()) if secret_answer else None),
            )

            conn.commit()
            return True, new_user_id
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        try:
            conn.autocommit = True
            _get_pool().putconn(conn)
        except Exception:
            _get_pool().putconn(conn, close=True)


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify credentials. Returns a user dict on success or None on failure.
    Dict keys: user_id, email, full_name, first_name, surname, phone, date_of_birth, is_active.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT u.user_id, u.email, u.full_name, u.first_name, u.surname,
                       u.phone, u.date_of_birth, u.is_active,
                       uc.password_hash
                FROM users u
                JOIN user_credentials uc ON uc.user_id = u.user_id
                WHERE u.email = %s
                  AND u.is_active = TRUE
                """,
                (email.strip().lower(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            try:
                _ph.verify(row["password_hash"], password)
            except (VerifyMismatchError, InvalidHashError):
                return None
            result = dict(row)
            result.pop("password_hash")
            return result


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None if not found."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT uc.secret_question
                FROM user_credentials uc
                JOIN users u ON u.user_id = uc.user_id
                WHERE u.email = %s
                """,
                (email.strip().lower(),),
            )
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """Return True if the provided answer matches the stored secret answer (case-insensitive)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT uc.secret_answer
                FROM user_credentials uc
                JOIN users u ON u.user_id = uc.user_id
                WHERE u.email = %s
                """,
                (email.strip().lower(),),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                return False
            try:
                _ph.verify(row[0], answer.strip().lower())
                return True
            except (VerifyMismatchError, InvalidHashError):
                return False


def update_password(email: str, new_password: str) -> bool:
    """
    Update the password for a user.

    Returns True if the password was updated, False otherwise
    (email not found or password fails length policy).
    """
    if not (_PW_MIN_LEN <= len(new_password) <= _PW_MAX_LEN):
        return False

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_credentials
                SET    password_hash = %s,
                       updated_at    = NOW()
                WHERE  user_id = (SELECT user_id FROM users WHERE email = %s)
                """,
                (_ph.hash(new_password), email.strip().lower()),
            )
            return cur.rowcount > 0


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.

    Args:
        embedding: Query vector from llm.embed(user_question)
        top_k:     Number of results to return

    Returns:
        List of dicts with title, category, content, and similarity score
    """
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))
            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """
    Insert a policy document with its embedding into the database.
    Used by skeleton/seed_vectors.py — students don't need to call this directly.

    Returns:
        The new document's id
    """
    sql = """
        INSERT INTO policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]
