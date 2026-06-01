"""
TransitFlow — Neo4j Seeder
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies
  - metro_schedules.json        — to extract base fares per stop
  - national_rail_schedules.json— to extract standard and first class fares per stop
"""

import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)

def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)

def seed():
    # 載入站點資料
    metro_stations = _load("metro_stations.json")
    rail_stations  = _load("national_rail_stations.json")
    
    # 載入班表資料 (為了擷取票價)
    metro_schedules = _load("metro_schedules.json")
    rail_schedules = _load("national_rail_schedules.json")

    # 1. 整理地鐵路線的 per_stop_rate_usd (每站票價)
    metro_fares = {}
    for sch in metro_schedules:
        if sch["line"] not in metro_fares:
            metro_fares[sch["line"]] = sch["per_stop_rate_usd"]

    # 2. 整理國家鐵路不同艙等的 per_stop_rate_usd (每站票價)
    rail_fares = {}
    for sch in rail_schedules:
        if sch["line"] not in rail_fares:
            rail_fares[sch["line"]] = {
                "standard": sch["fare_classes"]["standard"]["per_stop_rate_usd"],
                "first": sch["fare_classes"]["first"]["per_stop_rate_usd"]
            }

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:

        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # 3. 建立地鐵站點 (雙重標籤: Station + MetroStation)
        for s in metro_stations:
            session.run(
                "MERGE (n:Station:MetroStation {station_id: $id}) "
                "SET n.name = $name, n.lines = $lines",
                id=s["station_id"],
                name=s["name"],
                lines=s.get("lines", [])
            )
        print(f"  Created {len(metro_stations)} MetroStation nodes")

        # 4. 建立國家鐵路站點 (雙重標籤: Station + NationalRailStation)
        for s in rail_stations:
            session.run(
                "MERGE (n:Station:NationalRailStation {station_id: $id}) "
                "SET n.name = $name, n.lines = $lines",
                id=s["station_id"],
                name=s["name"],
                lines=s.get("lines", [])
            )
        print(f"  Created {len(rail_stations)} NationalRailStation nodes")

        # 5. 建立地鐵連線關係 (METRO_LINK 包含 fare 屬性)
        for s in metro_stations:
            for adj in s.get("adjacent_stations", []):
                fare = metro_fares.get(adj["line"], 0.0)
                session.run(
                    "MATCH (a:MetroStation {station_id: $from_id}) "
                    "MATCH (b:MetroStation {station_id: $to_id}) "
                    "MERGE (a)-[r:METRO_LINK {line: $line}]->(b) "
                    "SET r.travel_time_min = $time, r.fare = $fare",
                    from_id=s["station_id"],
                    to_id=adj["station_id"],
                    line=adj["line"],
                    time=adj["travel_time_min"],
                    fare=fare
                )
        print("  Created METRO_LINK relationships with fares")

        # 6. 建立國家鐵路連線關係 (RAIL_LINK 包含 standard_fare 與 first_class_fare 屬性)
        for s in rail_stations:
            for adj in s.get("adjacent_stations", []):
                fares = rail_fares.get(adj["line"], {"standard": 0.0, "first": 0.0})
                session.run(
                    "MATCH (a:NationalRailStation {station_id: $from_id}) "
                    "MATCH (b:NationalRailStation {station_id: $to_id}) "
                    "MERGE (a)-[r:RAIL_LINK {line: $line}]->(b) "
                    "SET r.travel_time_min = $time, "
                    "    r.standard_fare = $std_fare, "
                    "    r.first_class_fare = $first_fare",
                    from_id=s["station_id"],
                    to_id=adj["station_id"],
                    line=adj["line"],
                    time=adj["travel_time_min"],
                    std_fare=fares["standard"],
                    first_fare=fares["first"]
                )
        print("  Created RAIL_LINK relationships with fare classes")

        # 7. 建立跨系統轉乘連線 (INTERCHANGE_TO 包含 walking_time_min = 5)
        for s in metro_stations:
            if s.get("is_interchange_national_rail"):
                nr_id = s.get("interchange_national_rail_station_id")
                if nr_id:
                    session.run(
                        "MATCH (m:MetroStation {station_id: $m_id}) "
                        "MATCH (r:NationalRailStation {station_id: $r_id}) "
                        "MERGE (m)-[r1:INTERCHANGE_TO]->(r) "
                        "SET r1.walking_time_min = 5 "
                        "MERGE (r)-[r2:INTERCHANGE_TO]->(m) "
                        "SET r2.walking_time_min = 5",
                        m_id=s["station_id"],
                        r_id=nr_id
                    )
        print("  Created INTERCHANGE_TO relationships with 5 min walking time")

    driver.close()
    print("\nNeo4j graph seeded successfully.")
    print("   Open http://localhost:7475 to explore the graph.")

if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()