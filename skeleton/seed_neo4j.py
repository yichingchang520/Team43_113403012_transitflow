"""
TransitFlow — Neo4j Seeder
==========================
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies

Graph schema:
  Nodes : :Station:MetroStation        {station_id, name}
          :Station:NationalRailStation  {station_id, name}
  Edges : METRO_LINK     {line, travel_time_min}  MetroStation    → MetroStation
          RAIL_LINK      {line, travel_time_min}  NRStation       → NRStation
          INTERCHANGE_TO {walking_time_min}        Metro ↔ NR (both directions)
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

        # ── 建立唯一性約束 ────────────────────────────────────────────────────
        # 必須在 MERGE 之前建立，確保 station_id 在全圖唯一。
        # IF NOT EXISTS 讓腳本可以安全重複執行而不報錯。
        session.run(
            "CREATE CONSTRAINT station_id_unique IF NOT EXISTS "
            "FOR (n:Station) REQUIRE n.station_id IS UNIQUE"
        )
        print("  Ensured station_id uniqueness constraint")

        # ── 清除舊的 Station 資料 ─────────────────────────────────────────────
        # 只刪除 :Station 節點，避免誤刪 Neo4j 中其他應用程式的資料。
        # 原本的 MATCH (n) DETACH DELETE n 會刪掉整個資料庫所有節點。
        session.run("MATCH (n:Station) DETACH DELETE n")
        print("  Cleared existing Station nodes")

        # ── 建立捷運站節點 ────────────────────────────────────────────────────
        # 使用 UNWIND 批次建立，一次 round-trip 取代逐站呼叫（N 次 → 1 次）。
        # MERGE 搭配前面建立的 constraint 確保不產生重複節點。
        session.run(
            """
            UNWIND $stations AS s
            MERGE (n:Station:MetroStation {station_id: s.id})
            SET n.name = s.name
            """,
            stations=[{"id": s["station_id"], "name": s["name"]} for s in metro_stations]
        )
        print(f"  Created {len(metro_stations)} MetroStation nodes")

        # ── 建立國鐵站節點 ────────────────────────────────────────────────────
        session.run(
            """
            UNWIND $stations AS s
            MERGE (n:Station:NationalRailStation {station_id: s.id})
            SET n.name = s.name
            """,
            stations=[{"id": s["station_id"], "name": s["name"]} for s in rail_stations]
        )
        print(f"  Created {len(rail_stations)} NationalRailStation nodes")

        # ── 建立捷運路線連結 (METRO_LINK) ─────────────────────────────────────
        # adjacent_stations 原始資料已包含雙向記錄，直接展開即可。
        # 先在 Python 端整理成清單，再用 UNWIND 一次送入 Neo4j，
        # 避免每條邊各自一次 session.run() 的 round-trip 成本。
        metro_links = [
            {
                "from_id": s["station_id"],
                "to_id":   adj["station_id"],
                "line":    adj["line"],
                "time":    adj["travel_time_min"],
            }
            for s in metro_stations
            for adj in s.get("adjacent_stations", [])
        ]
        session.run(
            """
            UNWIND $links AS l
            MATCH (a:MetroStation {station_id: l.from_id})
            MATCH (b:MetroStation {station_id: l.to_id})
            MERGE (a)-[r:METRO_LINK {line: l.line}]->(b)
            SET r.travel_time_min = l.time
            """,
            links=metro_links
        )
        print(f"  Created {len(metro_links)} METRO_LINK relationships")

        # ── 建立國鐵路線連結 (RAIL_LINK) ──────────────────────────────────────
        rail_links = [
            {
                "from_id": s["station_id"],
                "to_id":   adj["station_id"],
                "line":    adj["line"],
                "time":    adj["travel_time_min"],
            }
            for s in rail_stations
            for adj in s.get("adjacent_stations", [])
        ]
        session.run(
            """
            UNWIND $links AS l
            MATCH (a:NationalRailStation {station_id: l.from_id})
            MATCH (b:NationalRailStation {station_id: l.to_id})
            MERGE (a)-[r:RAIL_LINK {line: l.line}]->(b)
            SET r.travel_time_min = l.time
            """,
            links=rail_links
        )
        print(f"  Created {len(rail_links)} RAIL_LINK relationships")

        # ── 建立換乘連結 (INTERCHANGE_TO) ─────────────────────────────────────
        # APOC Dijkstra 需要有向邊，因此捷運 ↔ 國鐵 各建一條方向相反的邊。
        # walking_time_min 設為 5 分鐘（原始資料未提供各換乘站的步行時間）。
        interchange_pairs = [
            {
                "metro_id": s["station_id"],
                "nr_id":    s["interchange_national_rail_station_id"],
            }
            for s in metro_stations
            if s.get("is_interchange_national_rail")
            and s.get("interchange_national_rail_station_id")
        ]
        # Both walking_time_min and travel_time_min are set on INTERCHANGE_TO edges.
        # walking_time_min is the semantically correct name for on-foot transfers,
        # but APOC Dijkstra uses travel_time_min as the weight property across all
        # edge types. Setting both ensures the total journey time is calculated
        # correctly when crossing the network boundary.
        session.run(
            """
            UNWIND $pairs AS p
            MATCH (m:MetroStation        {station_id: p.metro_id})
            MATCH (r:NationalRailStation {station_id: p.nr_id})
            MERGE (m)-[i:INTERCHANGE_TO]->(r)
            SET i.walking_time_min = 5, i.travel_time_min = 5
            MERGE (r)-[j:INTERCHANGE_TO]->(m)
            SET j.walking_time_min = 5, j.travel_time_min = 5
            """,
            pairs=interchange_pairs
        )
        print(f"  Created {len(interchange_pairs) * 2} INTERCHANGE_TO relationships")

    driver.close()
    print("\nNeo4j graph seeded successfully.")
    print("   Open http://localhost:7475 to explore the graph.")


if __name__ == "__main__":
    print("Connecting to Neo4j...")
    seed()
