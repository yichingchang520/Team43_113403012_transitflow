# AI Session Context — TransitFlow

**How to use this file:**
At the start of every AI coding session, paste the full contents of this file as your first message to your AI assistant. This gives the AI the context it needs to produce code that fits your codebase and is consistent with your teammates' work.

**Who maintains this file:**
Whoever makes a schema change or architectural decision updates this file in the same commit. Treat it like a team contract.

---

## Project Overview

TransitFlow is a Python-based AI chat assistant for a fictional transit operator. It queries three databases — PostgreSQL (relational + vector), Neo4j (graph) — and uses an LLM to answer user questions. Our task as students is to design the database schema and implement the query functions in `databases/relational/queries.py` and `databases/graph/queries.py`.

## Tech Stack

- Language: Python 3.11+
- Relational DB: PostgreSQL via `psycopg2` with `RealDictCursor`
- Graph DB: Neo4j via the `neo4j` Python driver
- Vector search: `pgvector` extension (already implemented — do not modify)
- Web UI: Gradio
- LLM: Google Gemini or local Ollama (configured via `.env`)

## Coding Conventions

- **Naming:** `snake_case` for all Python names and SQL identifiers
- **Docstrings:** All functions must have a docstring with `Args:` and `Returns:` sections
- **Return types:** Use type hints. Read-only functions return `list[dict]` or `Optional[dict]`
- **Empty results:** Return `[]` or `None` (as documented), never raise an exception for "not found"
- **SQL:** Use `%s` placeholders for all user inputs — never string-format into SQL
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`. `_connect()` returns a connection borrowed from a `ThreadedConnectionPool`; it is automatically returned to the pool on `__exit__`:
  ```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
  ```
  For write operations, borrow from the pool directly and return it in `finally`:
  ```python
  conn = _get_pool().getconn()
  conn.autocommit = False
  try:
      ...
      conn.commit()
  except Exception:
      conn.rollback()
      raise
  finally:
      conn.autocommit = True
      _get_pool().putconn(conn)
  ```
- **Graph pattern:** Use `_driver()` helper + session:
  ```python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
  ```

## Agreed Relational Schema

<!-- ============================================================
  FILL THIS IN after your team completes the schema design workshop.
  Paste your final CREATE TABLE statements here.
  ============================================================ -->

```sql
-- Sequence for atomic user ID generation (prevents race condition on concurrent registration)
CREATE SEQUENCE user_id_seq;

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

CREATE TABLE user_credentials (
    user_id           VARCHAR(10)  PRIMARY KEY REFERENCES users(user_id),
    password_hash     VARCHAR(255) NOT NULL,
    secret_question   VARCHAR(255),
    secret_answer     VARCHAR(255),
    hashing_algorithm VARCHAR(50)  NOT NULL DEFAULT 'argon2id',
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE metro_stations (
    station_id                      VARCHAR(10)  PRIMARY KEY,
    name                            VARCHAR(100) NOT NULL,
    is_interchange_metro            BOOLEAN      NOT NULL DEFAULT FALSE,
    is_interchange_national_rail    BOOLEAN      NOT NULL DEFAULT FALSE,
    interchange_nr_station_id       VARCHAR(10)  
);

CREATE TABLE metro_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    line        VARCHAR(5)  NOT NULL,
    PRIMARY KEY (station_id, line)
);

CREATE TABLE national_rail_stations (
    station_id                      VARCHAR(10)  PRIMARY KEY,
    name                            VARCHAR(100) NOT NULL,
    is_interchange_national_rail    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_interchange_metro            BOOLEAN      NOT NULL DEFAULT FALSE,
    interchange_metro_station_id    VARCHAR(10)  REFERENCES metro_stations(station_id)
);

CREATE TABLE national_rail_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    line        VARCHAR(10) NOT NULL,
    PRIMARY KEY (station_id, line)
);


ALTER TABLE metro_stations
    ADD CONSTRAINT fk_metro_nr_station
    FOREIGN KEY (interchange_nr_station_id)
    REFERENCES national_rail_stations(station_id);

CREATE TABLE metro_schedules (
    schedule_id         VARCHAR(20)  PRIMARY KEY,
    line                VARCHAR(5)   NOT NULL,
    direction           VARCHAR(20)  NOT NULL,
    origin_station_id   VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    stops_in_order      JSONB        NOT NULL,  
    travel_time_from_origin JSONB    NOT NULL,  
    first_train_time    TIME         NOT NULL,
    last_train_time     TIME         NOT NULL,
    base_fare_usd       NUMERIC(6,2) NOT NULL CHECK (base_fare_usd >= 0),
    per_stop_rate_usd   NUMERIC(6,2) NOT NULL CHECK (per_stop_rate_usd >= 0),
    frequency_min       INT          NOT NULL CHECK (frequency_min > 0)
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

CREATE TABLE seat_layouts (
    layout_id   VARCHAR(10)  PRIMARY KEY,
    schedule_id VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id)
);


CREATE TABLE coaches (
    coach_id    SERIAL       PRIMARY KEY,
    layout_id   VARCHAR(10)  NOT NULL REFERENCES seat_layouts(layout_id),
    coach       VARCHAR(5)   NOT NULL,   
    fare_class  VARCHAR(10)  NOT NULL CHECK (fare_class IN ('standard', 'first')),
    UNIQUE (layout_id, coach)
);


CREATE TABLE seats (
    seat_pk     SERIAL       PRIMARY KEY,
    coach_id    INT          NOT NULL REFERENCES coaches(coach_id),
    seat_id     VARCHAR(10)  NOT NULL,   
    row_num     INT          NOT NULL,
    col_letter  VARCHAR(2)   NOT NULL,
    UNIQUE (coach_id, seat_id)
);

CREATE TABLE bookings (
    booking_id              VARCHAR(10)  PRIMARY KEY,
    user_id                 VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    schedule_id             VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id),
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    travel_date             DATE         NOT NULL,
    departure_time          TIME         NOT NULL,
    ticket_type             VARCHAR(10)  NOT NULL CHECK (ticket_type IN ('single', 'return', 'season')),
    fare_class              VARCHAR(10)  NOT NULL CHECK (fare_class IN ('standard', 'first')),
    coach                   VARCHAR(5)   NOT NULL,
    seat_id                 VARCHAR(10)  NOT NULL,
    stops_travelled         INT          NOT NULL CHECK (stops_travelled > 0),
    amount_usd              NUMERIC(8,2) NOT NULL CHECK (amount_usd >= 0),
    status                  VARCHAR(20)  NOT NULL CHECK (status IN ('confirmed', 'completed', 'cancelled')),
    booked_at               TIMESTAMPTZ  NOT NULL,
    travelled_at            TIMESTAMPTZ
);

CREATE TABLE metro_trips (
    trip_id                 VARCHAR(10)  PRIMARY KEY,
    user_id                 VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    schedule_id             VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id),
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    travel_date             DATE         NOT NULL,
    ticket_type             VARCHAR(10)  NOT NULL CHECK (ticket_type IN ('single', 'day_pass')),
    day_pass_ref            VARCHAR(10)  REFERENCES metro_trips(trip_id),
    stops_travelled         INT          CHECK (stops_travelled > 0),
    amount_usd              NUMERIC(8,2) NOT NULL CHECK (amount_usd >= 0),
    status                  VARCHAR(20)  NOT NULL CHECK (status IN ('confirmed', 'completed', 'cancelled')),
    purchased_at            TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);

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

CREATE INDEX idx_bookings_user        ON bookings(user_id);
CREATE INDEX idx_bookings_schedule    ON bookings(schedule_id);
CREATE INDEX idx_bookings_travel_date ON bookings(travel_date);
CREATE INDEX idx_metro_trips_user     ON metro_trips(user_id);
CREATE INDEX idx_metro_trips_date     ON metro_trips(travel_date);
CREATE INDEX idx_payments_booking     ON payments(booking_id);
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

CREATE INDEX idx_payments_metro_trip ON payments(metro_trip_id);
```

## Auth / Security Notes

- Emails are **normalised** on write and read: `email.strip().lower()`. Always pass normalised emails to auth functions.
- Passwords: min 8 chars, max 128 chars. Hashed with **argon2id** (`_ph.hash()`). Never store plaintext.
- `update_password` returns `bool` — `True` on success, `False` on failure (including invalid password length). This matches `ui.py`'s `if not update_password(...)` caller pattern.
- User ID generation uses `nextval('user_id_seq')` — atomic, no race condition.

## Agreed Graph Schema

### Node Labels

Every station node carries **two labels**: a shared `:Station` label (for cross-network queries) plus a network-specific label.

| Label | ID prefix | Properties |
|---|---|---|
| `:Station:MetroStation` | MS01–MS20 | `station_id`, `name` |
| `:Station:NationalRailStation` | NR01–NR10 | `station_id`, `name` |

Constraint: `station_id` is unique across all `:Station` nodes.

### Relationship Types

Three distinct relationship types are used — one per connection category. Do **not** collapse them into a single generic type; keeping them separate allows Dijkstra and path queries to target only metro, only rail, or both.

| Relationship | Direction | Used between | Properties |
|---|---|---|---|
| `METRO_LINK` | directed (both ways) | MetroStation → MetroStation | `line` (e.g. "M1"), `travel_time_min` |
| `RAIL_LINK` | directed (both ways) | NationalRailStation → NationalRailStation | `line` (e.g. "NR1"), `travel_time_min` |
| `INTERCHANGE_TO` | directed (both ways) | MetroStation ↔ NationalRailStation | `walking_time_min` |

All links are **bidirectional** (two separate directed edges, one each way).

### Network Summary

- **Metro lines**: M1, M2, M3, M4 — 20 stations (MS01–MS20)
- **National Rail lines**: NR1, NR2 — 10 stations (NR01–NR10)
- **Interchange points** (metro ↔ national rail, walking_time_min = 5):
  - MS01 (Central Square) ↔ NR01 (Central Station)
  - MS07 (Old Town) ↔ NR03 (Old Town Junction)
  - MS15 (Ferndale) ↔ NR07 (Ferndale Halt)

### Important Rules for AI Code Generation

- Use `:Station` label (not `:MetroStation` or `:NationalRailStation`) when the query should work across both networks
- Use `$param` syntax for all parameters — never string-format values into Cypher
- `METRO_LINK` and `RAIL_LINK` are directional, but tracks are bidirectional — both directions exist as separate relationships in the graph
- `INTERCHANGE_TO` also exists in both directions
- `station_id` values in Neo4j match exactly the `station_id` values in PostgreSQL (e.g. `"MS01"` in Neo4j = `"MS01"` in the `metro_stations` table)
- `network="auto"` means infer from station ID prefix — `MS` prefix → metro (use `METRO_LINK`), `NR` prefix → rail (use `RAIL_LINK`); if origin and destination are on different networks, use all three relationship types (`METRO_LINK|RAIL_LINK|INTERCHANGE_TO`)

### Example Cypher Pattern

```cypher
// Fastest metro route (Dijkstra by travel_time_min)
MATCH (start:MetroStation {station_id: $origin}),
      (end:MetroStation {station_id: $destination})
CALL apoc.algo.dijkstra(start, end, 'METRO_LINK', 'travel_time_min')
YIELD path, weight
RETURN path, weight AS total_time_min

// Cross-network path (metro → interchange → rail)
MATCH path = shortestPath(
  (a:Station {station_id: $origin})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*]-(b:Station {station_id: $destination})
)
RETURN path
```

## Function Signatures We Are Implementing

These are fixed contracts. AI-generated code must match these signatures exactly.

### Relational (`databases/relational/queries.py`)

```python
# Read-only
def query_national_rail_availability(origin_id: str, destination_id: str, travel_date: Optional[str] = None) -> list[dict]: ...
def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]: ...
def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]: ...
def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]: ...
def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]: ...
def query_user_profile(user_email: str) -> Optional[dict]: ...
def query_user_bookings(user_email: str) -> dict: ...  # returns {"national_rail": [...], "metro": [...]}
def query_payment_info(booking_id: str) -> Optional[dict]: ...

# Write operations
def execute_booking(user_id, schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type="single") -> tuple[bool, dict | str]: ...
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]: ...

# Auth
def register_user(email, first_name, surname, year_of_birth, password, secret_question, secret_answer) -> tuple[bool, str]: ...
def login_user(email: str, password: str) -> Optional[dict]: ...
def get_user_secret_question(email: str) -> Optional[str]: ...
def verify_secret_answer(email: str, answer: str) -> bool: ...
def update_password(email: str, new_password: str) -> bool: ...
```

### Graph (`databases/graph/queries.py`)

```python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[list[dict]]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
```

## Team Decisions Log

### Relational Schema

- **`stops_in_order` and `travel_time_from_origin` stored as JSONB.** Why: the stop list is an ordered array and the travel time map is a key-value dict — both are awkward to normalise into separate rows and are always read as a whole unit, so JSONB is simpler and sufficient.
- **`payments.booking_id` has no foreign key constraint.** Why: the column is a polymorphic reference — it can point to either `bookings.booking_id` (national rail) or `metro_trips.trip_id` (metro). PostgreSQL does not support multi-table FK targets, so the constraint is intentionally omitted.
- **`users` and `user_credentials` are separate tables.** Why: separating auth data (password, secret Q&A) from profile data means query functions that only need name/email don't expose credential columns.
- **`bookings` table covers national rail only; `metro_trips` covers metro.** Why: the two journey types have different fields (seat assignment exists only on national rail; day pass exists only on metro), so merging them into one table would leave many nullable columns.
- **National rail fare split into `std_*` and `first_*` columns.** Why: every national rail schedule has exactly two fare classes with fixed base + per-stop rates; flattening them avoids a separate `fare_classes` join table for a fixed two-row case.

### Graph Schema

- **Three relationship types: `METRO_LINK`, `RAIL_LINK`, `INTERCHANGE_TO`.** Why: keeping them separate lets Dijkstra and path queries target only metro, only rail, or both without filtering on a property. Using a single `CONNECTS_TO` would require `WHERE r.line STARTS WITH 'M'` everywhere.
- **Every node carries two labels (`:Station:MetroStation` or `:Station:NationalRailStation`).** Why: the shared `:Station` label enables cross-network queries in a single pattern; the specific label enables network-scoped queries without relationship-type filtering.
- **All links are bidirectional (two directed edges, one each way).** Why: the source JSON defines adjacency from both sides; storing both directions makes Dijkstra traversal straightforward without needing undirected relationship syntax.
- **`walking_time_min = 5` for all interchange links.** Why: the source data does not specify walking time per interchange; 5 minutes is used as a uniform placeholder.

## Prompts That Worked

<!-- Share prompts that produced good output so teammates can reuse them. -->

### Schema design prompt that worked:
```
TODO — add a prompt here after your schema design workshop
```

### Query implementation prompt that worked:
```
TODO — add after implementing your first function
```
