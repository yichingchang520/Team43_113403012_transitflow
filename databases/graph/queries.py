"""
TransitFlow — Neo4j Graph Database Layer
=========================================
"""

from __future__ import annotations
from typing import Optional
from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def example_count_nodes() -> int:
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
    """
    rel_types = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"
    if network == "metro":
        rel_types = "METRO_LINK"
    elif network == "rail":
        rel_types = "RAIL_LINK"

    cypher = f"""
    MATCH (start:Station {{station_id: $orig}}), (end:Station {{station_id: $dest}})
    CALL apoc.algo.dijkstra(start, end, '{rel_types}', 'travel_time_min') YIELD path, weight
    RETURN [n IN nodes(path) | {{station_id: n.station_id, name: n.name}}] AS stations,
           [r IN relationships(path) | type(r)] AS legs,
           weight AS total_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            record = session.run(cypher, orig=origin_id, dest=destination_id).single()
            if not record:
                return {"found": False}
            
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["total_time_min"],
                "path": record["stations"],
                "legs": record["legs"]
            }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str, 
    destination_id: str, 
    network: str = "auto", 
    fare_class: str = "standard"
) -> dict:
    """
    Find the cheapest route by reading the fare properties set on the edges.
    """
    # 根據網路類型決定要搜尋哪些鐵軌 (Relationships)
    rel_types = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"
    if network == "metro":
        rel_types = "METRO_LINK"
    elif network == "rail":
        rel_types = "RAIL_LINK"

    # 在 Cypher 中使用動態路徑與 CASE WHEN 來計算票價
    cypher = f"""
    MATCH (start:Station {{station_id: $orig}}), (end:Station {{station_id: $dest}})
    MATCH path = shortestPath((start)-[:{rel_types}*]-(end))
    RETURN [n IN nodes(path) | {{station_id: n.station_id, name: n.name}}] AS stations,
           reduce(total_cost = 0, r IN relationships(path) | 
               total_cost + CASE 
                   WHEN type(r) = 'RAIL_LINK' AND $fare_class = 'first' THEN coalesce(r.first_class_fare, 0)
                   WHEN type(r) = 'RAIL_LINK' AND $fare_class = 'standard' THEN coalesce(r.standard_fare, 0)
                   WHEN type(r) = 'METRO_LINK' THEN coalesce(r.fare, 0)
                   ELSE 0 
               END
           ) AS total_cost
    """
    
    with _driver() as driver:
        with driver.session() as session:
            # 記得把 fare_class 作為參數傳進去 session.run
            record = session.run(cypher, orig=origin_id, dest=destination_id, fare_class=fare_class).single()
            if not record:
                return {"found": False}
            
            return {
                "found": True,
                "total_cost": record["total_cost"],
                "stations": record["stations"]
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
    """
    cypher = """
    MATCH (start:Station {station_id: $orig}), (end:Station {station_id: $dest})
    MATCH path = (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..15]-(end)
    WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid)
    RETURN [n IN nodes(path) | {station_id: n.station_id, name: n.name}] AS route_stations,
           reduce(time = 0, r IN relationships(path) | time + coalesce(r.travel_time_min, r.walking_time_min, 0)) AS total_time_min
    ORDER BY total_time_min ASC
    LIMIT $max
    """
    routes = []
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, orig=origin_id, dest=destination_id, avoid=avoid_station_id, max=max_routes)
            for record in result:
                routes.append({
                    "total_time_min": record["total_time_min"],
                    "stations": record["route_stations"]
                })
    return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find a path between a metro station and a national rail station crossing 
    the network boundary via interchange relationships.
    """
    cypher = """
    MATCH (start:Station {station_id: $orig}), (end:Station {station_id: $dest})
    MATCH path = shortestPath((start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*]-(end))
    WHERE any(r IN relationships(path) WHERE type(r) = 'INTERCHANGE_TO')
    RETURN [n IN nodes(path) | {station_id: n.station_id, name: n.name}] AS stations,
           reduce(time = 0, r IN relationships(path) | time + coalesce(r.travel_time_min, r.walking_time_min, 0)) AS total_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            record = session.run(cypher, orig=origin_id, dest=destination_id).single()
            if not record:
                return {"found": False}
            
            return {
                "found": True,
                "total_time_min": record["total_time_min"],
                "stations": record["stations"]
            }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Find all stations within N hops of a delayed or disrupted station.
    """
    if hops <= 0:
        return []

    cypher = f"""
    MATCH (start:Station {{station_id: $delayed}})-[*1..{hops}]-(affected:Station)
    RETURN DISTINCT affected.station_id AS station_id, 
           affected.name AS name,
           length(shortestPath((start)-[*]-(affected))) AS hops_away,
           affected.lines AS lines_affected
    ORDER BY hops_away ASC
    """
    affected_stations = []
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, delayed=delayed_station_id)
            for record in result:
                affected_stations.append({
                    "station_id": record["station_id"],
                    "name": record["name"],
                    "hops_away": record["hops_away"],
                    "lines_affected": record.get("lines_affected", [])
                })
    return affected_stations


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections from a given station.
    """
    cypher = """
    MATCH (start:Station {station_id: $orig})-[r]-(connected:Station)
    RETURN connected.station_id AS dest_id, connected.name AS dest_name,
           type(r) AS link_type, r.travel_time_min AS time_min, r.line AS line
    """
    connections = []
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, orig=station_id)
            for record in result:
                connections.append({
                    "destination_id": record["dest_id"],
                    "destination_name": record["dest_name"],
                    "link_type": record["link_type"],
                    "travel_time_min": record["time_min"],
                    "line": record["line"]
                })
    return connections