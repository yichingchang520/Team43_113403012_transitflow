"""
TransitFlow — Neo4j Seeder
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies

Design your graph schema (node labels, relationship types, properties)
based on the data in these files, then implement the seed() function below.
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
    metro_stations = _load("metro_stations.json")
    rail_stations  = _load("national_rail_stations.json")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:

        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # 1. 建立捷運站節點
        for s in metro_stations:
            session.run(
                "MERGE (n:Station:MetroStation {station_id: $id}) "
                "SET n.name = $name",
                id=s["station_id"], name=s["name"]
            )
        print(f"  Created {len(metro_stations)} MetroStation nodes")

        # 2. 建立國鐵站節點
        for s in rail_stations:
            session.run(
                "MERGE (n:Station:NationalRailStation {station_id: $id}) "
                "SET n.name = $name",
                id=s["station_id"], name=s["name"]
            )
        print(f"  Created {len(rail_stations)} NationalRailStation nodes")

        # 3. 建立捷運路線連結 (METRO_LINK) — 雙向
        metro_link_count = 0
        for s in metro_stations:
            for adj in s.get("adjacent_stations", []):
                session.run(
                    "MATCH (a:MetroStation {station_id: $from_id}) "
                    "MATCH (b:MetroStation {station_id: $to_id}) "
                    "MERGE (a)-[r:METRO_LINK {line: $line}]->(b) "
                    "SET r.travel_time_min = $time",
                    from_id=s["station_id"],
                    to_id=adj["station_id"],
                    line=adj["line"],
                    time=adj["travel_time_min"]
                )
                metro_link_count += 1
        print(f"  Created {metro_link_count} METRO_LINK relationships")

        # 4. 建立國鐵路線連結 (RAIL_LINK) — 雙向
        rail_link_count = 0
        for s in rail_stations:
            for adj in s.get("adjacent_stations", []):
                session.run(
                    "MATCH (a:NationalRailStation {station_id: $from_id}) "
                    "MATCH (b:NationalRailStation {station_id: $to_id}) "
                    "MERGE (a)-[r:RAIL_LINK {line: $line}]->(b) "
                    "SET r.travel_time_min = $time",
                    from_id=s["station_id"],
                    to_id=adj["station_id"],
                    line=adj["line"],
                    time=adj["travel_time_min"]
                )
                rail_link_count += 1
        print(f"  Created {rail_link_count} RAIL_LINK relationships")

        # 5. 建立轉乘連結 (INTERCHANGE_TO) — 雙向
        interchange_count = 0
        for s in metro_stations:
            if s.get("is_interchange_national_rail") and s.get("interchange_national_rail_station_id"):
                nr_id = s["interchange_national_rail_station_id"]
                session.run(
                    "MATCH (a:MetroStation {station_id: $metro_id}) "
                    "MATCH (b:NationalRailStation {station_id: $nr_id}) "
                    "MERGE (a)-[r:INTERCHANGE_TO]->(b) "
                    "SET r.walking_time_min = 5",
                    metro_id=s["station_id"], nr_id=nr_id
                )
                session.run(
                    "MATCH (a:NationalRailStation {station_id: $nr_id}) "
                    "MATCH (b:MetroStation {station_id: $metro_id}) "
                    "MERGE (a)-[r:INTERCHANGE_TO]->(b) "
                    "SET r.walking_time_min = 5",
                    nr_id=nr_id, metro_id=s["station_id"]
                )
                interchange_count += 2
        print(f"  Created {interchange_count} INTERCHANGE_TO relationships")

    driver.close()
    print("\nNeo4j graph seeded successfully.")
    print("   Open http://localhost:7475 to explore the graph.")

if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()
