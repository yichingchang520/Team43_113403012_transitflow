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

KEY DESIGN NOTES:
  - _get_driver() returns a module-level singleton to avoid per-query TCP overhead.
    Creating a new driver on every query adds ~20–50 ms; the singleton reuses the
    connection for the lifetime of the process.
  - All Cypher parameters use $param syntax — never string-format values into Cypher.
  - Route queries fail gracefully: return {"found": False, ...} instead of raising,
    so the agent can report "no route found" without crashing.
  - APOC is required for Dijkstra (enabled in docker-compose.yml via NEO4J_PLUGINS).
"""

from __future__ import annotations

import logging
from typing import Optional

from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

logger = logging.getLogger(__name__)

# ── Driver singleton ──────────────────────────────────────────────────────────
# A GraphDatabase.driver() opens a TCP connection + authenticates on creation.
# Re-creating it on every query call wastes that overhead on each request.
# The module-level singleton is created once and reused across all queries.
_neo4j_driver = None


def _get_driver():
    """Return the module-level Neo4j driver, creating it on first call.

    Using a singleton avoids the TCP connection + auth overhead (~20–50 ms)
    that would otherwise be paid on every query.
    """
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _neo4j_driver


# ── Helpers ───────────────────────────────────────────────────────────────────

def _station_exists(session, station_id: str) -> bool:
    """Return True if a :Station node with the given station_id exists in Neo4j.

    Used to provide a clear error before running expensive Dijkstra queries,
    rather than silently returning an empty result when a station ID is wrong.
    """
    result = session.run(
        "MATCH (n:Station {station_id: $id}) RETURN n LIMIT 1",
        id=station_id
    )
    return result.single() is not None


# ── Example ───────────────────────────────────────────────────────────────────

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _get_driver().session() as session:
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
    # Infer network from station ID prefix when network="auto"
    if network == "auto":
        if origin_id.startswith("MS") and destination_id.startswith("MS"):
            network = "metro"
        elif origin_id.startswith("NR") and destination_id.startswith("NR"):
            network = "rail"
        else:
            network = "cross"

    # Each network uses its own relationship type so Dijkstra stays within the
    # correct sub-graph; cross-network queries traverse all three types.
    if network == "metro":
        rel_type = "METRO_LINK"
    elif network == "rail":
        rel_type = "RAIL_LINK"
    else:
        rel_type = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"

    with _get_driver().session() as session:
        # Validate stations before running the expensive Dijkstra call
        for sid in (origin_id, destination_id):
            if not _station_exists(session, sid):
                logger.warning("query_shortest_route: station not found: %s", sid)
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "error": f"Station '{sid}' not found in graph",
                }

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

        # Build leg-by-leg breakdown for the UI / LLM summary.
        # INTERCHANGE_TO uses walking_time_min instead of travel_time_min.
        legs = []
        rels = list(record["path"].relationships)
        for i, rel in enumerate(rels):
            legs.append({
                "from": path[i]["station_id"],
                "to":   path[i + 1]["station_id"],
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

    Note: fare is approximated from stop count (base $1.50 + $0.30 per stop)
    because actual fares live in PostgreSQL, not in the graph. A future improvement
    would query PostgreSQL for the exact fare after path-finding.

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
    elif network == "rail":
        rel_type = "RAIL_LINK"
    else:
        rel_type = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"

    with _get_driver().session() as session:
        for sid in (origin_id, destination_id):
            if not _station_exists(session, sid):
                logger.warning("query_cheapest_route: station not found: %s", sid)
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "error": f"Station '{sid}' not found in graph",
                }

        # Use travel_time_min as a proxy for cost: fewer stops ≈ lower fare.
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
        stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path_nodes]
        stops = len(stations) - 1

        # Approximate fare: base $1.50 + $0.30 per stop
        total_fare = round(1.50 + 0.30 * stops, 2)

        legs = []
        rels = list(record["path"].relationships)
        for i, rel in enumerate(rels):
            legs.append({
                "from": stations[i]["station_id"],
                "to":   stations[i + 1]["station_id"],
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

    with _get_driver().session() as session:
        # *1..12 limits search depth to 12 hops.
        # Without an upper bound, Neo4j explores all possible paths including
        # very long cycles, causing the query to hang on any graph with loops.
        # 12 is generous for a 20-station metro network (diameter < 10).
        #
        # -> (directed) is used instead of - (undirected) to prevent the same
        # logical path from being returned multiple times via different edge
        # direction combinations (a common duplicate issue on bidirectional graphs).
        result = session.run(
            f"""
            MATCH path = (start:Station {{station_id: $origin}})
                         -[:{rel_type}*1..12]->
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

        # Deduplicate routes by their station-ID sequence as a safety net.
        # Even with directed edges, the same logical path can occasionally
        # appear more than once if multiple edge types connect the same nodes.
        seen: set[tuple] = set()
        routes = []
        for record in result:
            path_nodes = list(record["path"].nodes)
            rels = list(record["path"].relationships)

            # Use the ordered tuple of station IDs as a unique key for this path
            path_key = tuple(n["station_id"] for n in path_nodes)
            if path_key in seen:
                continue
            seen.add(path_key)

            # Skip routes that contain loops (any station visited more than once).
            # Neo4j's variable-length match allows revisiting nodes, which produces
            # nonsensical routes like A→B→A→C→D that detour and double back.
            station_ids = [n["station_id"] for n in path_nodes]
            if len(station_ids) != len(set(station_ids)):
                continue  # skip — this path loops back through a visited station

            legs = []
            for i, rel in enumerate(rels):
                legs.append({
                    "from":            path_nodes[i]["station_id"],
                    "from_name":       path_nodes[i]["name"],
                    "to":              path_nodes[i + 1]["station_id"],
                    "to_name":         path_nodes[i + 1]["name"],
                    "type":            rel.type,
                    "line":            rel.get("line", ""),
                    "travel_time_min": rel.get("travel_time_min") or rel.get("walking_time_min", 0),
                })
            routes.append(legs)
        return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find a path between a metro station and a national rail station (or vice versa)
    crossing the network boundary via INTERCHANGE_TO relationships.

    Uses all three relationship types so Dijkstra can cross the network boundary.
    Interchange points (where the traveller switches network) are highlighted
    in the returned result.

    Args:
        origin_id:       e.g. "MS03" (metro) or "NR05" (national rail)
        destination_id:  e.g. "NR05" (national rail) or "MS09" (metro)

    Returns:
        dict with found, stations list, interchange_points, total_time_min
    """
    with _get_driver().session() as session:
        for sid in (origin_id, destination_id):
            if not _station_exists(session, sid):
                logger.warning("query_interchange_path: station not found: %s", sid)
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "error": f"Station '{sid}' not found in graph",
                }

        result = session.run(
            """
            MATCH (start:Station {station_id: $origin}),
                  (end:Station   {station_id: $destination})
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

        raw_time = record["total_time_min"]
        total_time = None if (raw_time is None or raw_time != raw_time) else raw_time

        return {
            "found": True,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "total_time_min": total_time,
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
    # Note: Cypher variable-length path bounds (*1..N) do not support parameters.
    # int() cast is a safety guard to prevent injection since hops is caller-supplied.
    with _get_driver().session() as session:
        result = session.run(
            f"""
            MATCH (start:Station {{station_id: $station_id}})
            MATCH p = (start)-[:METRO_LINK|RAIL_LINK*1..{int(hops)}]-(nearby:Station)
            WHERE nearby.station_id <> $station_id
            WITH nearby, min(length(p)) AS hops_away
            RETURN nearby.station_id AS station_id,
                   nearby.name       AS name,
                   hops_away
            ORDER BY hops_away
            """,
            station_id=delayed_station_id,
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
    with _get_driver().session() as session:
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
