"""
TransitFlow — Neo4j Graph Database Layer
=========================================
This module handles all queries to Neo4j.

GRAPH ROLE:
  - Model the dual transit network (city metro M1–M4 + national rail NR1–NR2)
  - Find fastest routes (Dijkstra by travel_time_min via APOC)
  - Find cheapest routes (Dijkstra by fare via APOC)
  - Find alternative routes avoiding a given station
  - Find cross-network interchange paths (metro → rail or rail → metro)
  - Show delay ripple: which stations are affected within N hops

STUDENT TASK
------------
Design your graph schema (node labels, relationship types, properties)
based on the data in train-mock-data/, seed it with skeleton/seed_neo4j.py,
then implement the query_ functions below.

Functions prefixed with `query_` are called by the agent (skeleton/agent.py).
"""

from __future__ import annotations

from typing import Optional

from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a session, run Cypher, return data.

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Find the fastest path between two stations, minimising total travel time.
    Uses apoc.algo.dijkstra (APOC required; enabled in docker-compose.yml).

    Args:
        origin_id:       e.g. "MS01" or "NR01"
        destination_id:  e.g. "MS09" or "NR05"
        network:         "metro", "rail", or "auto" (inferred from IDs)

    Returns:
        dict with keys: found, origin_id, destination_id,
                        total_time_min, path (list of station dicts), legs
    """
    if network == "auto":
        if origin_id.startswith("MS") and destination_id.startswith("MS"):
            network = "metro"
        elif origin_id.startswith("NR") and destination_id.startswith("NR"):
            network = "rail"
        else:
            network = "cross"

    if network == "metro":
        rel_type = "METRO_LINK"
    elif network == "rail":
        rel_type = "RAIL_LINK"
    else:
        rel_type = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"

    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH (start:Station {{station_id: $origin}}),
                      (end:Station {{station_id: $destination}})
                CALL apoc.algo.dijkstra(start, end, '{rel_type}', 'travel_time_min')
                YIELD path, weight
                RETURN path, weight AS total_time_min
                """,
                origin=origin_id, destination=destination_id
            )
            record = result.single()
            if not record:
                return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

            path_nodes = list(record["path"].nodes)
            path = [{"station_id": n["station_id"], "name": n["name"]} for n in path_nodes]

            legs = []
            rels = list(record["path"].relationships)
            for i, rel in enumerate(rels):
                legs.append({
                    "from": path[i]["station_id"],
                    "to": path[i+1]["station_id"],
                    "type": rel.type,
                    "travel_time_min": rel.get("travel_time_min") or rel.get("walking_time_min", 0),
                    "line": rel.get("line", ""),
                })

            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["total_time_min"],
                "path": path,
                "legs": legs,
            }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path between two stations, minimising total estimated fare.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        network:         "metro", "rail", or "auto"
        fare_class:      "standard" or "first" (national rail only)

    Returns:
        dict with found, total_fare_usd (approximate), stations, legs
    """
    if network == "auto":
        if origin_id.startswith("MS") and destination_id.startswith("MS"):
            network = "metro"
        elif origin_id.startswith("NR") and destination_id.startswith("NR"):
            network = "rail"
        else:
            network = "cross"

    if network == "metro":
        rel_type = "METRO_LINK"
        cost_prop = "travel_time_min"
    elif network == "rail":
        rel_type = "RAIL_LINK"
        cost_prop = "travel_time_min"
    else:
        rel_type = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"
        cost_prop = "travel_time_min"

    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH (start:Station {{station_id: $origin}}),
                      (end:Station {{station_id: $destination}})
                CALL apoc.algo.dijkstra(start, end, '{rel_type}', '{cost_prop}')
                YIELD path, weight
                RETURN path, weight AS total_time_min
                """,
                origin=origin_id, destination=destination_id
            )
            record = result.single()
            if not record:
                return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

            path_nodes = list(record["path"].nodes)
            stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path_nodes]
            stops = len(stations) - 1

            # 用 stops 數估算票價
            total_fare = round(1.50 + 0.30 * stops, 2)

            legs = []
            rels = list(record["path"].relationships)
            for i, rel in enumerate(rels):
                legs.append({
                    "from": stations[i]["station_id"],
                    "to": stations[i+1]["station_id"],
                    "type": rel.type,
                    "line": rel.get("line", ""),
                })

            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_fare_usd": total_fare,
                "fare_class": fare_class,
                "stations": stations,
                "legs": legs,
            }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """
    Find paths between two stations that avoid a specific intermediate station.
    Useful for routing around a delayed or closed station.

    Args:
        origin_id:         e.g. "NR01"
        destination_id:    e.g. "NR05"
        avoid_station_id:  e.g. "NR03"
        network:           "metro", "rail", or "auto"
        max_routes:        max number of alternatives to return

    Returns:
        List of routes, each route is a list of leg dicts
    """
    if network == "auto":
        if origin_id.startswith("MS") and destination_id.startswith("MS"):
            network = "metro"
        elif origin_id.startswith("NR") and destination_id.startswith("NR"):
            network = "rail"
        else:
            network = "cross"

    if network == "metro":
        rel_type = "METRO_LINK"
    elif network == "rail":
        rel_type = "RAIL_LINK"
    else:
        rel_type = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"

    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH path = (start:Station {{station_id: $origin}})
                             -[:{rel_type}*]-
                             (end:Station {{station_id: $destination}})
                WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid)
                  AND start <> end
                RETURN path
                ORDER BY length(path)
                LIMIT $max_routes
                """,
                origin=origin_id,
                destination=destination_id,
                avoid=avoid_station_id,
                max_routes=max_routes
            )

            routes = []
            for record in result:
                path_nodes = list(record["path"].nodes)
                rels = list(record["path"].relationships)
                legs = []
                for i, rel in enumerate(rels):
                    legs.append({
                        "from": path_nodes[i]["station_id"],
                        "from_name": path_nodes[i]["name"],
                        "to": path_nodes[i+1]["station_id"],
                        "to_name": path_nodes[i+1]["name"],
                        "type": rel.type,
                        "line": rel.get("line", ""),
                        "travel_time_min": rel.get("travel_time_min") or rel.get("walking_time_min", 0),
                    })
                routes.append(legs)
            return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find a path between a metro station and a national rail station (or vice versa)
    crossing the network boundary via interchange relationships.

    Args:
        origin_id:       e.g. "MS03" (metro) or "NR05" (national rail)
        destination_id:  e.g. "NR05" (national rail) or "MS09" (metro)

    Returns:
        dict with found, stations list, interchange points, total_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (start:Station {station_id: $origin}),
                      (end:Station {station_id: $destination})
                CALL apoc.algo.dijkstra(
                    start, end,
                    'METRO_LINK|RAIL_LINK|INTERCHANGE_TO',
                    'travel_time_min'
                )
                YIELD path, weight
                RETURN path, weight AS total_time_min
                """,
                origin=origin_id, destination=destination_id
            )
            record = result.single()
            if not record:
                return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

            path_nodes = list(record["path"].nodes)
            rels = list(record["path"].relationships)

            stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path_nodes]

            interchange_points = [
                {"station_id": path_nodes[i]["station_id"], "name": path_nodes[i]["name"]}
                for i, rel in enumerate(rels)
                if rel.type == "INTERCHANGE_TO"
            ]

            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["total_time_min"],
                "stations": stations,
                "interchange_points": interchange_points,
            }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Find all stations within N hops of a delayed or disrupted station.
    Works on both metro and national rail networks.

    Args:
        delayed_station_id: e.g. "NR03" or "MS01"
        hops:               how many connections out to search (default 2)

    Returns:
        List of dicts: {station_id, name, hops_away, lines_affected}
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (start:Station {station_id: $station_id})
                MATCH (start)-[r:METRO_LINK|RAIL_LINK*1..$hops]-(nearby:Station)
                WHERE nearby.station_id <> $station_id
                WITH nearby,
                     min(length([(start)-[:METRO_LINK|RAIL_LINK*]-(nearby) | 1])) AS hops_away,
                     collect(DISTINCT r[0].line) AS lines_affected
                RETURN nearby.station_id AS station_id,
                       nearby.name       AS name,
                       hops_away,
                       lines_affected
                ORDER BY hops_away
                """,
                station_id=delayed_station_id, hops=hops
            )
            return [dict(record) for record in result]


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections from a given station.

    Args:
        station_id: e.g. "MS01" or "NR01"

    Returns:
        List of dicts: {station_id, name, relationship_type, line, travel_time_min}
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (s:Station {station_id: $station_id})-[r]->(n:Station)
                RETURN n.station_id      AS station_id,
                       n.name            AS name,
                       type(r)           AS relationship_type,
                       r.line            AS line,
                       r.travel_time_min AS travel_time_min
                ORDER BY type(r), r.line
                """,
                station_id=station_id
            )
            return [dict(record) for record in result]