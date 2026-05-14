import json
import sqlite3
from pathlib import Path

from src.graph_rag.delta import compute_file_hash, ensure_graph_meta_table


BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "azur_lane_graph.db"
OUTPUT_DIR = BASE_DIR / "output"


def init_graph_db(db_path=DB_PATH, force_rebuild=True):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if force_rebuild and db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    create_graph_schema(conn)
    return conn


def create_graph_schema(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            label TEXT,
            name TEXT,
            properties TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS edges (
            source_id TEXT,
            target_id TEXT,
            type TEXT,
            metadata TEXT,
            PRIMARY KEY (source_id, target_id, type)
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_label ON nodes(label)")
    ensure_graph_meta_table(conn)
    conn.commit()


def load_all_ship_files(conn, output_dir=OUTPUT_DIR):
    ship_files = sorted(
        path for path in output_dir.glob("*.json")
        if path.stem.isdigit()
    ) if output_dir.exists() else []

    for ship_file in ship_files:
        upsert_ship_file(conn, ship_file, compute_file_hash(ship_file))
    conn.commit()
    return len(ship_files)


def upsert_ship_file(conn, ship_file, file_hash=None):
    data = json.loads(Path(ship_file).read_text(encoding="utf-8"))
    if data.get("node_type") != "Ship":
        return False

    ship_node_id = f"ship_{data.get('id')}"
    cursor = conn.cursor()
    cursor.execute("DELETE FROM edges WHERE source_id = ?", (ship_node_id,))
    upsert_ship_graph(cursor, ship_node_id, data)

    if file_hash is not None:
        cursor.execute(
            "INSERT OR REPLACE INTO graph_meta VALUES (?, ?, datetime('now'))",
            (ship_node_id, file_hash),
        )

    conn.commit()
    return True


def upsert_ship_graph(cursor, ship_node_id, data):
    cursor.execute(
        """
        INSERT OR REPLACE INTO nodes (id, label, name, properties)
        VALUES (?, ?, ?, ?)
        """,
        (ship_node_id, "Ship", data.get("name"), json.dumps(data)),
    )

    attr = data.get("attributes", {})
    _upsert_lookup_edge(
        cursor,
        ship_node_id,
        attr.get("faction"),
        "fact",
        "Faction",
        "BELONGS_TO_FACTION",
    )
    _upsert_lookup_edge(
        cursor,
        ship_node_id,
        attr.get("hull"),
        "hull",
        "Hull",
        "IS_HULL",
    )
    _upsert_lookup_edge(
        cursor,
        ship_node_id,
        attr.get("class"),
        "class",
        "Class",
        "IN_CLASS",
    )

    for skill in data.get("skills", []):
        skill_id = f"skill_{skill.get('id')}"
        cursor.execute(
            """
            INSERT OR IGNORE INTO nodes (id, label, name, properties)
            VALUES (?, ?, ?, ?)
            """,
            (skill_id, "Skill", skill.get("name"), json.dumps(skill)),
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO edges (source_id, target_id, type, metadata)
            VALUES (?, ?, ?, ?)
            """,
            (
                ship_node_id,
                skill_id,
                "HAS_SKILL",
                json.dumps(skill.get("edges", {})),
            ),
        )


def delete_ship(conn, ship_node_id):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM edges WHERE source_id = ?", (ship_node_id,))
    cursor.execute("DELETE FROM nodes WHERE id = ?", (ship_node_id,))
    cursor.execute("DELETE FROM graph_meta WHERE ship_id = ?", (ship_node_id,))
    conn.commit()


def apply_delta(graph_db_path, delta):
    if not graph_db_path.exists():
        conn = init_graph_db(graph_db_path, force_rebuild=True)
        try:
            for ship_id, info in delta["current"].items():
                upsert_ship_file(conn, info["path"], info["hash"])
        finally:
            conn.close()
        return

    conn = sqlite3.connect(graph_db_path)
    try:
        create_graph_schema(conn)
        for ship_id in delta["new"] + delta["updated"]:
            info = delta["current"][ship_id]
            upsert_ship_file(conn, info["path"], info["hash"])

        for ship_id in delta["deleted"]:
            delete_ship(conn, f"ship_{ship_id}")
    finally:
        conn.close()


def graph_counts(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM nodes")
    node_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM edges")
    edge_count = cursor.fetchone()[0]
    return node_count, edge_count


def _upsert_lookup_edge(cursor, ship_node_id, name, prefix, label, edge_type):
    if not name:
        return

    lookup_id = f"{prefix}_{str(name).lower().replace(' ', '_')}"
    cursor.execute(
        """
        INSERT OR IGNORE INTO nodes (id, label, name, properties)
        VALUES (?, ?, ?, ?)
        """,
        (lookup_id, label, name, json.dumps({"name": name})),
    )
    cursor.execute(
        """
        INSERT OR REPLACE INTO edges (source_id, target_id, type, metadata)
        VALUES (?, ?, ?, ?)
        """,
        (ship_node_id, lookup_id, edge_type, "{}"),
    )
