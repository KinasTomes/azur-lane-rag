import sqlite3
import json
from pathlib import Path
import igraph as ig
import leidenalg as la

# Cấu hình đường dẫn
BASE_DIR = Path(__file__).parent.parent.parent
DB_PATH = BASE_DIR / "data" / "azur_lane_graph.db"

def setup_db_for_communities(conn):
    cursor = conn.cursor()
    
    # 1. Thêm cột community_id vào bảng nodes nếu chưa có
    try:
        cursor.execute("ALTER TABLE nodes ADD COLUMN community_id INTEGER")
        print("Added 'community_id' column to 'nodes' table.")
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

    print(f"Building graph with {len(nodes_list)} nodes and {len(edges_list)} edges...")
    
    # Tạo đồ thị bằng igraph (Leidenalg hoạt động tốt nhất với igraph)
    g = ig.Graph(len(nodes_list), edges_list)
    g.to_undirected() # Community detection thường chạy trên đồ thị vô hướng

    print("Running Leiden algorithm...")
    # Chạy thuật toán Leiden (ModularityVertexPartition là mặc định phổ biến)
    partition = la.find_partition(g, la.ModularityVertexPartition)
    
    # 3. Cập nhật community_id vào database
    print(f"Found {len(partition)} communities. Updating database...")
    
    active_community_ids = []
    for comm_id, node_indices in enumerate(partition):
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
    print("Community detection and database updates completed.")


def get_community_assignments(conn):
    """Return {ship_node_id: community_id} for ship nodes after community detection."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, community_id FROM nodes WHERE label = 'Ship' AND community_id IS NOT NULL"
    )
    return {node_id: community_id for node_id, community_id in cursor.fetchall()}

if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"Error: Database {DB_PATH} not found. Please run init_sqlite_graph.py first.")
    else:
        connection = sqlite3.connect(DB_PATH)
        setup_db_for_communities(connection)
        run_community_detection(connection)
        connection.close()
