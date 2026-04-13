import json
import logging
import sqlite3
from pathlib import Path

import igraph as ig
import leidenalg as la

# ANSI color codes
CLR_RESET = "\033[0m"
CLR_RED = "\033[31m"
CLR_GREEN = "\033[32m"
CLR_YELLOW = "\033[33m"
CLR_CYAN = "\033[36m"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.INFO: CLR_GREEN,
        logging.WARNING: CLR_YELLOW,
        logging.ERROR: CLR_RED,
        logging.DEBUG: CLR_CYAN,
    }

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, CLR_RESET)
        record.levelname = f"{color}{record.levelname}{CLR_RESET}"
        return super().format(record)


for handler in logging.root.handlers:
    handler.setFormatter(
        ColorFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )


BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "azur_lane_graph.db"


def ensure_level1_tables(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS community_hierarchy (
            parent_community_id INTEGER,
            child_community_id INTEGER,
            parent_level INTEGER,
            child_level INTEGER,
            PRIMARY KEY (parent_community_id, child_community_id)
        )
        """
    )
    conn.commit()


def get_level0_communities(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, title, summary, findings, full_content
        FROM communities
        WHERE level = 0
        ORDER BY id
        """
    )
    rows = cursor.fetchall()

    communities = {}
    for cid, title, summary, findings, full_content in rows:
        communities[cid] = {
            "id": cid,
            "title": title or f"Community {cid}",
            "summary": summary or "",
            "findings": findings or "[]",
            "full_content": full_content or "",
        }
    return communities


def get_meta_edges(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            CASE WHEN n1.community_id < n2.community_id THEN n1.community_id ELSE n2.community_id END AS c_a,
            CASE WHEN n1.community_id < n2.community_id THEN n2.community_id ELSE n1.community_id END AS c_b,
            COUNT(*) AS weight
        FROM edges e
        JOIN nodes n1 ON n1.id = e.source_id
        JOIN nodes n2 ON n2.id = e.target_id
        WHERE n1.community_id IS NOT NULL
          AND n2.community_id IS NOT NULL
          AND n1.community_id != n2.community_id
        GROUP BY c_a, c_b
        ORDER BY weight DESC
        """
    )
    return cursor.fetchall()


def run_level1_leiden(level0_ids, meta_edges):
    id_to_idx = {cid: idx for idx, cid in enumerate(level0_ids)}
    idx_to_id = {idx: cid for cid, idx in id_to_idx.items()}

    weighted_edges = []
    weights = []

    for c_a, c_b, w in meta_edges:
        if c_a in id_to_idx and c_b in id_to_idx:
            weighted_edges.append((id_to_idx[c_a], id_to_idx[c_b]))
            weights.append(float(w))

    g = ig.Graph(n=len(level0_ids), edges=weighted_edges, directed=False)
    if weights:
        g.es["weight"] = weights

    partition = la.find_partition(
        g,
        la.ModularityVertexPartition,
        weights=g.es["weight"] if g.ecount() > 0 else None,
    )

    level1_groups = []
    for l1_idx, members in enumerate(partition):
        child_ids = [idx_to_id[m] for m in members]
        level1_groups.append((l1_idx, sorted(child_ids)))

    return level1_groups


def upsert_level1_structure(conn, level1_groups):
    cursor = conn.cursor()

    # Xoa du lieu level 1 cu de co ket qua idempotent khi chay lai.
    cursor.execute("DELETE FROM community_hierarchy WHERE parent_level = 1 AND child_level = 0")
    cursor.execute("DELETE FROM communities WHERE level = 1")

    cursor.execute("SELECT COALESCE(MAX(id), 0) FROM communities")
    max_id = cursor.fetchone()[0]
    next_id = max_id + 1

    level1_id_map = {}
    for local_l1_id, child_ids in level1_groups:
        db_l1_id = next_id
        next_id += 1
        level1_id_map[local_l1_id] = db_l1_id

        cursor.execute(
            """
            INSERT INTO communities (id, level, title, summary, findings, full_content)
            VALUES (?, 1, ?, '', '[]', '')
            """,
            (db_l1_id, f"Level 1 Community {local_l1_id}"),
        )

        for child_id in child_ids:
            cursor.execute(
                """
                INSERT OR REPLACE INTO community_hierarchy
                (parent_community_id, child_community_id, parent_level, child_level)
                VALUES (?, ?, 1, 0)
                """,
                (db_l1_id, child_id),
            )

    conn.commit()
    return level1_id_map


def main():
    if not DB_PATH.exists():
        logger.error(f"Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_level1_tables(conn)

        level0_map = get_level0_communities(conn)
        if not level0_map:
            logger.error("No level 0 communities found. Run community detection + level 0 summaries first.")
            return

        level0_ids = sorted(level0_map.keys())
        meta_edges = get_meta_edges(conn)

        logger.info(f"Level 0 communities: {len(level0_ids)}")
        logger.info(f"Meta-graph inter-community edges: {len(meta_edges)}")

        level1_groups = run_level1_leiden(level0_ids, meta_edges)
        logger.info(f"Detected level 1 communities: {len(level1_groups)}")

        upsert_level1_structure(conn, level1_groups)
        logger.info("Level 1 clustering completed and saved to DB.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
