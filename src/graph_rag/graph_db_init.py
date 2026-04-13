import sqlite3
import json
from pathlib import Path

# Cấu hình đường dẫn
BASE_DIR = Path(__file__).parent.parent.parent
DB_PATH = BASE_DIR / "data" / "azur_lane_graph.db"
OUTPUT_DIR = BASE_DIR / "output"

def init_db(db_path=DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Xóa file db cũ nếu có để khởi tạo lại từ đầu với schema mới
    if db_path.exists():
        db_path.unlink()
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Bảng Nodes chung cho tất cả thực thể
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY, 
            label TEXT, -- 'Ship', 'Skill', 'Faction', 'Hull', 'Class'
            name TEXT,
            properties TEXT -- Lưu JSON toàn bộ data còn lại
        )
    ''')

    # Bảng Edges chung cho tất cả quan hệ
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS edges (
            source_id TEXT,
            target_id TEXT,
            type TEXT, -- 'HAS_SKILL', 'BELONGS_TO_FACTION', 'IS_HULL', 'IN_CLASS'
            metadata TEXT,
            PRIMARY KEY (source_id, target_id, type)
        )
    ''')
    
    # Tạo index để query graph nhanh hơn
    cursor.execute('CREATE INDEX idx_edges_source ON edges(source_id)')
    cursor.execute('CREATE INDEX idx_edges_target ON edges(target_id)')
    cursor.execute('CREATE INDEX idx_edges_type ON edges(type)')
    cursor.execute('CREATE INDEX idx_nodes_label ON nodes(label)')
    
    conn.commit()
    return conn

def load_data(conn, output_dir=OUTPUT_DIR):
    cursor = conn.cursor()
    
    if not output_dir.exists():
        print(f"Error: Directory {output_dir} not found.")
        return

    json_files = list(output_dir.glob("*.json"))
    print(f"Found {len(json_files)} JSON files in {output_dir}. Starting import...")

    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if data.get("node_type") != "Ship":
                continue

            # Giả sử ship_id = "ship_616" để tránh trùng ID với skill
            s_id = f"ship_{data.get('id')}"
            attr = data.get("attributes", {})
            
            # 1. Insert Ship Node
            cursor.execute("INSERT OR REPLACE INTO nodes VALUES (?, ?, ?, ?)", 
                          (s_id, 'Ship', data.get("name"), json.dumps(data)))

            # 2. Xử lý Faction Node & Edge (BELONGS_TO_FACTION)
            f_name = attr.get("faction")
            if f_name:
                f_id = f"fact_{f_name.lower().replace(' ', '_')}"
                cursor.execute("INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?)", 
                              (f_id, 'Faction', f_name, json.dumps({"name": f_name})))
                cursor.execute("INSERT OR REPLACE INTO edges VALUES (?, ?, ?, ?)", 
                              (s_id, f_id, 'BELONGS_TO_FACTION', "{}"))

            # 3. Xử lý Hull Node & Edge (IS_HULL)
            h_name = attr.get("hull")
            if h_name:
                h_id = f"hull_{h_name.lower()}"
                cursor.execute("INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?)", 
                              (h_id, 'Hull', h_name, json.dumps({"name": h_name})))
                cursor.execute("INSERT OR REPLACE INTO edges VALUES (?, ?, ?, ?)", 
                              (s_id, h_id, 'IS_HULL', "{}"))
                
            # 4. Xử lý Class Node & Edge (IN_CLASS)
            c_name = attr.get("class")
            if c_name:
                c_id = f"class_{c_name.lower().replace(' ', '_')}"
                cursor.execute("INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?)", 
                              (c_id, 'Class', c_name, json.dumps({"name": c_name})))
                cursor.execute("INSERT OR REPLACE INTO edges VALUES (?, ?, ?, ?)", 
                              (s_id, c_id, 'IN_CLASS', "{}"))

            # 5. Xử lý Skills (HAS_SKILL)
            for skill in data.get("skills", []):
                sk_id = f"skill_{skill.get('id')}"
                cursor.execute("INSERT OR IGNORE INTO nodes VALUES (?, ?, ?, ?)", 
                              (sk_id, 'Skill', skill.get("name"), json.dumps(skill)))
                cursor.execute("INSERT OR REPLACE INTO edges VALUES (?, ?, ?, ?)", 
                              (s_id, sk_id, 'HAS_SKILL', json.dumps(skill.get("edges", {}))))

        except Exception as e:
            print(f"Error processing {json_file}: {e}")

    conn.commit()
    print(f"Import completed. Database saved to: {DB_PATH}")
    
    # In thống kê
    cursor.execute("SELECT label, count(*) FROM nodes GROUP BY label")
    print("\nNode Summary:")
    for row in cursor.fetchall():
        print(f"- {row[0]}: {row[1]}")
        
    cursor.execute("SELECT type, count(*) FROM edges GROUP BY type")
    print("\nEdge Summary:")
    for row in cursor.fetchall():
        print(f"- {row[0]}: {row[1]}")

if __name__ == "__main__":
    connection = init_db()
    load_data(connection)
    connection.close()
