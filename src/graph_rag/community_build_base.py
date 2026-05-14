import json
import logging
import sqlite3
from pathlib import Path

import igraph as ig
import leidenalg as la

# Cấu hình đường dẫn
BASE_DIR = Path(__file__).parent.parent.parent
DB_PATH = BASE_DIR / "data" / "azur_lane_graph.db"
logger = logging.getLogger(__name__)


def _load_existing_level0_signatures(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT n.community_id, n.id
        FROM nodes n
        JOIN communities c ON c.id = n.community_id
        WHERE n.label = 'Ship'
          AND n.community_id IS NOT NULL
          AND c.level = 0
        ORDER BY n.community_id, n.id
        """
    )

    grouped = {}
    for community_id, ship_id in cursor.fetchall():
        grouped.setdefault(community_id, []).append(ship_id)

    return {
        tuple(ship_ids): community_id
        for community_id, ship_ids in grouped.items()
    }


def _build_partition_signature(node_ids):
    ship_ids = sorted(node_id for node_id in node_ids if node_id.startswith("ship_"))
    if ship_ids:
        return tuple(ship_ids)
    return tuple(sorted(node_ids))

def setup_db_for_communities(conn):
    cursor = conn.cursor()
    
    # 1. Thêm cột community_id vào bảng nodes nếu chưa có
    try:
        cursor.execute("ALTER TABLE nodes ADD COLUMN community_id INTEGER")
        logger.info("Added 'community_id' column to 'nodes' table.")
    except sqlite3.OperationalError:
        # Cột đã tồn tại
        pass

    # 2. Tạo bảng communities theo yêu cầu
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS communities (
            id INTEGER PRIMARY KEY,
            level INTEGER,       -- Cấp độ (0 cho cụm nhỏ, 1 cho cụm lớn)
            title TEXT,         -- Tên cộng đồng (AI tự đặt, VD: "Biệt đội phòng không Eagle Union")
            summary TEXT,       -- Bản tóm tắt chi tiết (đây là cái để dán vào Prompt)
            findings TEXT,      -- Các phát hiện quan trọng (dạng JSON)
            full_content TEXT,  -- Nội dung gốc để sau này Vector hóa
            content_hash TEXT   -- Hash nội dung để skip re-summarize
        )
    ''')
    try:
        cursor.execute("ALTER TABLE communities ADD COLUMN content_hash TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()

def run_community_detection(conn):
    cursor = conn.cursor()
    existing_signatures = _load_existing_level0_signatures(conn)
    next_community_id = (
        max(existing_signatures.values(), default=-1) + 1
    )
    cursor.execute("UPDATE nodes SET community_id = NULL")
    
    # Lấy danh sách tất cả các nốt và id của chúng
    cursor.execute("SELECT id FROM nodes")
    nodes_list = [row[0] for row in cursor.fetchall()]
    node_to_idx = {node_id: i for i, node_id in enumerate(nodes_list)}
    idx_to_node = {i: node_id for i, node_id in enumerate(nodes_list)}

    # Lấy danh sách các cạnh
    cursor.execute("SELECT source_id, target_id FROM edges")
    edges_list = []
    for src, tgt in cursor.fetchall():
        if src in node_to_idx and tgt in node_to_idx:
            edges_list.append((node_to_idx[src], node_to_idx[tgt]))

    logger.info("Building graph with %s nodes and %s edges...", len(nodes_list), len(edges_list))
    
    # Tạo đồ thị bằng igraph (Leidenalg hoạt động tốt nhất với igraph)
    g = ig.Graph(len(nodes_list), edges_list)
    g.to_undirected() # Community detection thường chạy trên đồ thị vô hướng

    logger.info("Running Leiden algorithm...")
    # Chạy thuật toán Leiden (ModularityVertexPartition là mặc định phổ biến)
    partition = la.find_partition(g, la.ModularityVertexPartition)
    
    # 3. Cập nhật community_id vào database
    logger.info("Found %s communities. Updating database...", len(partition))
    
    used_community_ids = set()
    partitions = []
    for node_indices in partition:
        node_ids = [idx_to_node[node_idx] for node_idx in node_indices]
        signature = _build_partition_signature(node_ids)
        partitions.append((signature, node_indices))

    partitions.sort(key=lambda item: item[0])

    active_community_ids = []
    for signature, node_indices in partitions:
        comm_id = existing_signatures.get(signature)
        if comm_id is None:
            while next_community_id in used_community_ids:
                next_community_id += 1
            comm_id = next_community_id
            next_community_id += 1

        used_community_ids.add(comm_id)
        active_community_ids.append(comm_id)
        # Tạo bản ghi trong bảng communities (nếu chưa có)
        cursor.execute('''
            INSERT OR IGNORE INTO communities (id, level, title, summary, findings, full_content, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (comm_id, 0, f"Community {comm_id}", "", "[]", "", None))
        
        # Cập nhật từng nốt thuộc cộng đồng này
        for node_idx in node_indices:
            node_id = idx_to_node[node_idx]
            cursor.execute("UPDATE nodes SET community_id = ? WHERE id = ?", (comm_id, node_id))

    if active_community_ids:
        placeholders = ",".join("?" for _ in active_community_ids)
        cursor.execute(
            f"DELETE FROM communities WHERE level = 0 AND id NOT IN ({placeholders})",
            active_community_ids,
        )

    conn.commit()
    logger.info("Community detection and database updates completed.")


def get_community_assignments(conn):
    """Return {ship_node_id: community_id} for ship nodes after community detection."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, community_id FROM nodes WHERE label = 'Ship' AND community_id IS NOT NULL"
    )
    return {node_id: community_id for node_id, community_id in cursor.fetchall()}

if __name__ == "__main__":
    if not DB_PATH.exists():
        logger.error("Database %s not found. Please run init_sqlite_graph.py first.", DB_PATH)
    else:
        connection = sqlite3.connect(DB_PATH)
        setup_db_for_communities(connection)
        run_community_detection(connection)
        connection.close()
