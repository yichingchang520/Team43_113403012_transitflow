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
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`:
  ```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
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
CREATE TABLE users (
    user_id         VARCHAR(10)  PRIMARY KEY,
    full_name       VARCHAR(100) NOT NULL,
    email           VARCHAR(100) NOT NULL UNIQUE,
    phone           VARCHAR(20),
    date_of_birth   DATE,
    registered_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE user_credentials ( 
user_id 		VARCHAR(10) PRIMARY KEY REFERENCES users(user_id), 
password_hash 	VARCHAR(255) NOT NULL, 
secret_question 	VARCHAR(255), 
secret_answer 	VARCHAR(255) 
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
    base_fare_usd       NUMERIC(6,2) NOT NULL,
    per_stop_rate_usd   NUMERIC(6,2) NOT NULL,
    frequency_min       INT          NOT NULL
);

CREATE TABLE metro_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id),
    day_of_week VARCHAR(3)  NOT NULL,  
    PRIMARY KEY (schedule_id, day_of_week)
);

CREATE TABLE national_rail_schedules (
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(10)  NOT NULL,
    service_type            VARCHAR(10)  NOT NULL,  
    direction               VARCHAR(20)  NOT NULL,
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    stops_in_order          JSONB        NOT NULL,  
    passed_through_stations JSONB,                  
    travel_time_from_origin JSONB        NOT NULL,  
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    std_base_fare_usd       NUMERIC(6,2) NOT NULL,
    std_per_stop_rate_usd   NUMERIC(6,2) NOT NULL,
    first_base_fare_usd     NUMERIC(6,2) NOT NULL,
    first_per_stop_rate_usd NUMERIC(6,2) NOT NULL,
    frequency_min           INT          NOT NULL
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
    fare_class  VARCHAR(10)  NOT NULL,   
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
    ticket_type             VARCHAR(10)  NOT NULL,  
    fare_class              VARCHAR(10)  NOT NULL,  
    coach                   VARCHAR(5)   NOT NULL,
    seat_id                 VARCHAR(10)  NOT NULL,
    stops_travelled         INT          NOT NULL,
    amount_usd              NUMERIC(8,2) NOT NULL,
    status                  VARCHAR(20)  NOT NULL,  
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
    ticket_type             VARCHAR(10)  NOT NULL,  
    day_pass_ref            VARCHAR(10),            
    stops_travelled         INT,                    
    amount_usd              NUMERIC(8,2) NOT NULL,
    status                  VARCHAR(20)  NOT NULL,  
    purchased_at            TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);

CREATE TABLE payments (
    payment_id  VARCHAR(10)  PRIMARY KEY,
    booking_id  VARCHAR(10)  NOT NULL,   
    amount_usd  NUMERIC(8,2) NOT NULL,
    method      VARCHAR(20)  NOT NULL,   
    status      VARCHAR(20)  NOT NULL,   
    paid_at     TIMESTAMPTZ  NOT NULL
);

CREATE TABLE feedback (
    feedback_id     VARCHAR(10)  PRIMARY KEY,
    booking_id      VARCHAR(10)  NOT NULL,   
    user_id         VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    rating          SMALLINT     NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment         TEXT,
    submitted_at    TIMESTAMPTZ  NOT NULL
);

CREATE INDEX idx_bookings_user        ON bookings(user_id);
CREATE INDEX idx_bookings_schedule    ON bookings(schedule_id);
CREATE INDEX idx_bookings_travel_date ON bookings(travel_date);
CREATE INDEX idx_metro_trips_user     ON metro_trips(user_id);
CREATE INDEX idx_metro_trips_date     ON metro_trips(travel_date);
CREATE INDEX idx_payments_booking     ON payments(booking_id);
CREATE INDEX idx_feedback_user        ON feedback(user_id);

```

## Agreed Graph Schema

<!-- ============================================================
  FILL THIS IN after your team agrees on Neo4j node labels and
  relationship types.
  ============================================================ -->

Node labels:

```
MATCH (n) DETACH DELETE n;

CREATE CONSTRAINT station_id_unique IF NOT EXISTS
FOR (s:Station) REQUIRE s.station_id IS UNIQUE;

CREATE (:Station:MetroStation {station_id: "MS01", name: "Central Square"});
CREATE (:Station:MetroStation {station_id: "MS02", name: "Riverside"});
CREATE (:Station:MetroStation {station_id: "MS03", name: "Northgate"});
CREATE (:Station:MetroStation {station_id: "MS04", name: "Elm Park"});
CREATE (:Station:MetroStation {station_id: "MS05", name: "Westfield"});
CREATE (:Station:MetroStation {station_id: "MS06", name: "Harbour View"});
CREATE (:Station:MetroStation {station_id: "MS07", name: "Old Town"});
CREATE (:Station:MetroStation {station_id: "MS08", name: "University"});
CREATE (:Station:MetroStation {station_id: "MS09", name: "Queensbridge"});
CREATE (:Station:MetroStation {station_id: "MS10", name: "Parkside"});
CREATE (:Station:MetroStation {station_id: "MS11", name: "Greenhill"});
CREATE (:Station:MetroStation {station_id: "MS12", name: "Lakeshore"});
CREATE (:Station:MetroStation {station_id: "MS13", name: "Clifton"});
CREATE (:Station:MetroStation {station_id: "MS14", name: "Eastwick"});
CREATE (:Station:MetroStation {station_id: "MS15", name: "Ferndale"});
CREATE (:Station:MetroStation {station_id: "MS16", name: "Hilltop"});
CREATE (:Station:MetroStation {station_id: "MS17", name: "Broadmoor"});
CREATE (:Station:MetroStation {station_id: "MS18", name: "Sunnyvale"});
CREATE (:Station:MetroStation {station_id: "MS19", name: "Redwood"});
CREATE (:Station:MetroStation {station_id: "MS20", name: "Thornton"});

CREATE (:Station:NationalRailStation {station_id: "NR01", name: "Central Station"});
CREATE (:Station:NationalRailStation {station_id: "NR02", name: "Maplewood"});
CREATE (:Station:NationalRailStation {station_id: "NR03", name: "Old Town Junction"});
CREATE (:Station:NationalRailStation {station_id: "NR04", name: "Ashford"});
CREATE (:Station:NationalRailStation {station_id: "NR05", name: "Stonehaven"});
CREATE (:Station:NationalRailStation {station_id: "NR06", name: "Bridgeport"});
CREATE (:Station:NationalRailStation {station_id: "NR07", name: "Ferndale Halt"});
CREATE (:Station:NationalRailStation {station_id: "NR08", name: "Coalport"});
CREATE (:Station:NationalRailStation {station_id: "NR09", name: "Dunmore"});
CREATE (:Station:NationalRailStation {station_id: "NR10", name: "Langford End"});

MATCH (a:Station {station_id: "MS01"}), (b:Station {station_id: "MS05"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 3}]->(b);
MATCH (a:Station {station_id: "MS01"}), (b:Station {station_id: "MS02"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 3}]->(b);
MATCH (a:Station {station_id: "MS01"}), (b:Station {station_id: "MS06"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 3}]->(b);
MATCH (a:Station {station_id: "MS01"}), (b:Station {station_id: "MS07"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 2}]->(b);

MATCH (a:Station {station_id: "MS02"}), (b:Station {station_id: "MS01"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 3}]->(b);
MATCH (a:Station {station_id: "MS02"}), (b:Station {station_id: "MS03"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 2}]->(b);

MATCH (a:Station {station_id: "MS03"}), (b:Station {station_id: "MS02"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 2}]->(b);
MATCH (a:Station {station_id: "MS03"}), (b:Station {station_id: "MS04"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 4}]->(b);

MATCH (a:Station {station_id: "MS04"}), (b:Station {station_id: "MS03"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 4}]->(b);
MATCH (a:Station {station_id: "MS04"}), (b:Station {station_id: "MS17"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 3}]->(b);
MATCH (a:Station {station_id: "MS04"}), (b:Station {station_id: "MS12"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 3}]->(b);

MATCH (a:Station {station_id: "MS05"}), (b:Station {station_id: "MS20"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 2}]->(b);
MATCH (a:Station {station_id: "MS05"}), (b:Station {station_id: "MS01"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 3}]->(b);

MATCH (a:Station {station_id: "MS06"}), (b:Station {station_id: "MS01"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 3}]->(b);

MATCH (a:Station {station_id: "MS07"}), (b:Station {station_id: "MS01"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 2}]->(b);
MATCH (a:Station {station_id: "MS07"}), (b:Station {station_id: "MS18"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 2}]->(b);

MATCH (a:Station {station_id: "MS08"}), (b:Station {station_id: "MS18"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 4}]->(b);
MATCH (a:Station {station_id: "MS08"}), (b:Station {station_id: "MS09"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 3}]->(b);
MATCH (a:Station {station_id: "MS08"}), (b:Station {station_id: "MS17"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 4}]->(b);
MATCH (a:Station {station_id: "MS08"}), (b:Station {station_id: "MS12"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 4}]->(b);

MATCH (a:Station {station_id: "MS09"}), (b:Station {station_id: "MS08"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 3}]->(b);

MATCH (a:Station {station_id: "MS10"}), (b:Station {station_id: "MS11"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 2}]->(b);
MATCH (a:Station {station_id: "MS10"}), (b:Station {station_id: "MS12"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 4}]->(b);

MATCH (a:Station {station_id: "MS11"}), (b:Station {station_id: "MS10"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 2}]->(b);
MATCH (a:Station {station_id: "MS11"}), (b:Station {station_id: "MS19"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 3}]->(b);

MATCH (a:Station {station_id: "MS12"}), (b:Station {station_id: "MS04"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 3}]->(b);
MATCH (a:Station {station_id: "MS12"}), (b:Station {station_id: "MS10"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 4}]->(b);
MATCH (a:Station {station_id: "MS12"}), (b:Station {station_id: "MS08"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 4}]->(b);
MATCH (a:Station {station_id: "MS12"}), (b:Station {station_id: "MS14"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 4}]->(b);

MATCH (a:Station {station_id: "MS13"}), (b:Station {station_id: "MS19"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 2}]->(b);

MATCH (a:Station {station_id: "MS14"}), (b:Station {station_id: "MS12"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 4}]->(b);
MATCH (a:Station {station_id: "MS14"}), (b:Station {station_id: "MS15"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 2}]->(b);

MATCH (a:Station {station_id: "MS15"}), (b:Station {station_id: "MS14"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 2}]->(b);
MATCH (a:Station {station_id: "MS15"}), (b:Station {station_id: "MS16"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 3}]->(b);

MATCH (a:Station {station_id: "MS16"}), (b:Station {station_id: "MS15"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 3}]->(b);

MATCH (a:Station {station_id: "MS17"}), (b:Station {station_id: "MS04"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 3}]->(b);
MATCH (a:Station {station_id: "MS17"}), (b:Station {station_id: "MS08"})
CREATE (a)-[:CONNECTS_TO {line: "M4", travel_time_min: 4}]->(b);

MATCH (a:Station {station_id: "MS18"}), (b:Station {station_id: "MS07"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 2}]->(b);
MATCH (a:Station {station_id: "MS18"}), (b:Station {station_id: "MS08"})
CREATE (a)-[:CONNECTS_TO {line: "M2", travel_time_min: 4}]->(b);

MATCH (a:Station {station_id: "MS19"}), (b:Station {station_id: "MS13"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 2}]->(b);
MATCH (a:Station {station_id: "MS19"}), (b:Station {station_id: "MS11"})
CREATE (a)-[:CONNECTS_TO {line: "M3", travel_time_min: 3}]->(b);

MATCH (a:Station {station_id: "MS20"}), (b:Station {station_id: "MS05"})
CREATE (a)-[:CONNECTS_TO {line: "M1", travel_time_min: 2}]->(b);
```

Relationship types:

```
MATCH (a:Station {station_id: "NR01"}), (b:Station {station_id: "NR02"})
CREATE (a)-[:CONNECTS_TO {line: "NR1", travel_time_min: 12}]->(b);
MATCH (a:Station {station_id: "NR01"}), (b:Station {station_id: "NR06"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 14}]->(b);

MATCH (a:Station {station_id: "NR02"}), (b:Station {station_id: "NR01"})
CREATE (a)-[:CONNECTS_TO {line: "NR1", travel_time_min: 12}]->(b);
MATCH (a:Station {station_id: "NR02"}), (b:Station {station_id: "NR03"})
CREATE (a)-[:CONNECTS_TO {line: "NR1", travel_time_min: 18}]->(b);

MATCH (a:Station {station_id: "NR03"}), (b:Station {station_id: "NR02"})
CREATE (a)-[:CONNECTS_TO {line: "NR1", travel_time_min: 18}]->(b);
MATCH (a:Station {station_id: "NR03"}), (b:Station {station_id: "NR04"})
CREATE (a)-[:CONNECTS_TO {line: "NR1", travel_time_min: 15}]->(b);

MATCH (a:Station {station_id: "NR04"}), (b:Station {station_id: "NR03"})
CREATE (a)-[:CONNECTS_TO {line: "NR1", travel_time_min: 15}]->(b);
MATCH (a:Station {station_id: "NR04"}), (b:Station {station_id: "NR05"})
CREATE (a)-[:CONNECTS_TO {line: "NR1", travel_time_min: 20}]->(b);

MATCH (a:Station {station_id: "NR05"}), (b:Station {station_id: "NR04"})
CREATE (a)-[:CONNECTS_TO {line: "NR1", travel_time_min: 20}]->(b);

MATCH (a:Station {station_id: "NR06"}), (b:Station {station_id: "NR01"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 14}]->(b);
MATCH (a:Station {station_id: "NR06"}), (b:Station {station_id: "NR07"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 16}]->(b);

MATCH (a:Station {station_id: "NR07"}), (b:Station {station_id: "NR06"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 16}]->(b);
MATCH (a:Station {station_id: "NR07"}), (b:Station {station_id: "NR08"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 22}]->(b);

MATCH (a:Station {station_id: "NR08"}), (b:Station {station_id: "NR07"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 22}]->(b);
MATCH (a:Station {station_id: "NR08"}), (b:Station {station_id: "NR09"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 21}]->(b);

MATCH (a:Station {station_id: "NR09"}), (b:Station {station_id: "NR08"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 21}]->(b);
MATCH (a:Station {station_id: "NR09"}), (b:Station {station_id: "NR10"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 19}]->(b);

MATCH (a:Station {station_id: "NR10"}), (b:Station {station_id: "NR09"})
CREATE (a)-[:CONNECTS_TO {line: "NR2", travel_time_min: 19}]->(b);

MATCH (a:Station {station_id: "MS01"}), (b:Station {station_id: "NR01"})
CREATE (a)-[:INTERCHANGE_WITH {walking_time_min: 5}]->(b);
MATCH (a:Station {station_id: "NR01"}), (b:Station {station_id: "MS01"})
CREATE (a)-[:INTERCHANGE_WITH {walking_time_min: 5}]->(b);

MATCH (a:Station {station_id: "MS07"}), (b:Station {station_id: "NR03"})
CREATE (a)-[:INTERCHANGE_WITH {walking_time_min: 5}]->(b);
MATCH (a:Station {station_id: "NR03"}), (b:Station {station_id: "MS07"})
CREATE (a)-[:INTERCHANGE_WITH {walking_time_min: 5}]->(b);

MATCH (a:Station {station_id: "MS15"}), (b:Station {station_id: "NR07"})
CREATE (a)-[:INTERCHANGE_WITH {walking_time_min: 5}]->(b);
MATCH (a:Station {station_id: "NR07"}), (b:Station {station_id: "MS15"})
CREATE (a)-[:INTERCHANGE_WITH {walking_time_min: 5}]->(b);
```

Key properties:

```
- TODO
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

<!-- Add entries as you make decisions. Format: "Decision: X. Why: Y." -->

- [ ] Schema design: TODO — add your table/column decisions here
- [ ] Graph schema: TODO — add your node label and relationship type decisions here
- [ ] (example) Metro schedule stop ordering: using `jsonb_array_elements` approach — easier to debug than containment operators

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
