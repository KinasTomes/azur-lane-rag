import sqlite3
import re
import json
from pathlib import Path

# Cấu trúc dự án: src/utils/get_ship_summary.py -> parents[2] là root
REPO_ROOT = Path(__file__).resolve().parents[2]
# DB đã được migrate vào thư mục src/
DB_NAME = REPO_ROOT / "src" / "azur_lane.db"
DATA_DIR = REPO_ROOT / "AzurLaneData" / "data"

def clean_skill_description(description, skill_id, skills_json):
    """Thay thế $1, $2... bằng giá trị Max Level từ dữ liệu thô"""
    if not description: return ""
    
    skill_data = skills_json.get(str(skill_id), {})
    values_list = skill_data.get("values") or skill_data.get("variables") or []
    
    if not values_list:
        return description.replace('\n', ' ').strip()

    def replace_var(match):
        var_index = int(match.group(1)) - 1 
        if var_index < len(values_list):
            val_list = values_list[var_index]
            return str(val_list[-1]) if isinstance(val_list, list) else str(val_list)
        return match.group(0)

    cleaned = re.sub(r'\$(\d+)', replace_var, description)
    return cleaned.replace('\n', ' ').strip()

def get_enhanced_summaries():
    if not DB_NAME.exists():
        raise FileNotFoundError(f"Database not found at {DB_NAME}. Please run migration script first.")

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Mapping Hull ID -> Name để hiển thị thông tin Fleet Tech Target
    cursor.execute("SELECT id, name FROM hulls")
    hull_mapping = {row['id']: row['name'] for row in cursor.fetchall()}

    with open(DATA_DIR / "skills.json", "r", encoding="utf-8") as f:
        skills_json = json.load(f)
    
    with open(DATA_DIR / "ships.json", "r", encoding="utf-8") as f:
        ships_json = json.load(f)

    # Loại bỏ release_date khỏi query chính
    query = """
    SELECT 
        s.id, s.name, s.global_name,
        r.name as rarity, n.name as nation, h.name as hull, s.ship_class,
        ft.bonus_stat, ft.bonus_value
    FROM ships s
    LEFT JOIN rarities r ON s.rarity_id = r.id
    LEFT JOIN nations n ON s.nation_id = n.id
    LEFT JOIN hulls h ON s.hull_id = h.id
    LEFT JOIN fleet_tech ft ON s.id = ft.ship_id
    """
    cursor.execute(query)
    ships = cursor.fetchall()

    ship_summaries = []

    for ship in ships:
        ship_id = ship['id']
        raw_ship_info = ships_json.get(str(ship_id), {})
        
        # Xử lý Fleet Tech targets
        tech_data = raw_ship_info.get("fleet_tech", {})
        applies_to_ids = tech_data.get("collect", {}).get("hulls", [])
        applies_to_names = [hull_mapping.get(hid, str(hid)) for hid in applies_to_ids]
        
        # Lấy Skills (tại mức Limit Break cao nhất)
        cursor.execute("""
            SELECT sk.id, sk.name, sk.description 
            FROM ship_skills ss
            JOIN skills sk ON ss.skill_id = sk.id
            WHERE ss.ship_id = ? AND ss.limit_break = (
                SELECT MAX(limit_break) FROM ship_skills WHERE ship_id = ?
            )
        """, (ship_id, ship_id))
        skills = cursor.fetchall()
        
        skill_texts = []
        for sk in skills:
            desc = clean_skill_description(sk['description'], sk['id'], skills_json)
            skill_texts.append(f"[ID: {sk['id']} | Name: {sk['name']} | Desc: {desc}]")

        # Summary tập trung vào thuộc tính thực thể và kỹ năng (loại bỏ release, timer, pool)
        summary = (
            f"ID: {ship_id} | Name: {ship['name']} | Rarity: {ship['rarity']} | "
            f"Nation: {ship['nation']} | Hull: {ship['hull']} | Class: {ship['ship_class']} | "
            f"Fleet Tech: {ship['bonus_stat']} +{ship['bonus_value']} (Applies to: {', '.join(applies_to_names)}) | "
            f"Skills: {' '.join(skill_texts)}"
        )
        
        # data_pointer trỏ về DB để truy vấn chi tiết (stats, acquisition, v.v.) khi cần
        ship_summaries.append({
            "id": ship_id, 
            "summary": summary
        })

    conn.close()
    return ship_summaries

if __name__ == "__main__":
    summaries = get_enhanced_summaries()
    # In thử mẫu một tàu để kiểm tra cấu trúc mới
    if summaries:
        print(json.dumps(summaries[0], indent=2, ensure_ascii=False))
