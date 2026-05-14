import hashlib
import json
import sqlite3
from pathlib import Path


def compute_file_hash(file_path):
    return hashlib.sha256(Path(file_path).read_bytes()).hexdigest()


def scan_ship_outputs(output_dir):
    """Return {ship_id: {"path": Path, "hash": str, "name": str}} for output/*.json."""
    ships = {}
    if not output_dir.exists():
        return ships

    for json_file in sorted(output_dir.glob("*.json")):
        if not json_file.stem.isdigit():
            continue

        ship_id = json_file.stem
        name = "?"
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            name = data.get("name", "?")
        except (OSError, json.JSONDecodeError):
            pass

        ships[ship_id] = {
            "path": json_file,
            "hash": compute_file_hash(json_file),
            "name": name,
        }
    return ships


def load_graph_hashes(graph_db_path):
    """Return {ship_id_without_prefix: file_content_hash} from graph_meta."""
    if not graph_db_path.exists():
        return {}

    conn = sqlite3.connect(graph_db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'graph_meta'"
        )
        if not cursor.fetchone():
            return {}
        cursor.execute("SELECT ship_id, file_content_hash FROM graph_meta")
        return {
            ship_id.replace("ship_", "", 1): file_hash
            for ship_id, file_hash in cursor.fetchall()
        }
    finally:
        conn.close()


def ensure_graph_meta_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_meta (
            ship_id TEXT PRIMARY KEY,
            file_content_hash TEXT,
            loaded_at TEXT
        )
        """
    )
    conn.commit()


def detect_delta(output_dir, graph_db_path):
    """Compare output JSON hashes with graph_meta."""
    current = scan_ship_outputs(output_dir)
    stored_hashes = load_graph_hashes(graph_db_path)

    new = []
    updated = []
    deleted = []

    for ship_id, info in current.items():
        if ship_id not in stored_hashes:
            new.append(ship_id)
        elif stored_hashes[ship_id] != info["hash"]:
            updated.append(ship_id)

    for ship_id in stored_hashes:
        if ship_id not in current:
            deleted.append(ship_id)

    return {
        "new": new,
        "updated": updated,
        "deleted": deleted,
        "unchanged": len(current) - len(new) - len(updated),
        "current": current,
        "stored_count": len(stored_hashes),
    }
