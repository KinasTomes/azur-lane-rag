import sqlite3
import json
import re
from pathlib import Path

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_NAME = PROJECT_ROOT / "data" / "azur_lane.db"
DATA_DIR = PROJECT_ROOT / "AzurLaneData" / "data"

APP_TABLES = [
    "voice_lines",
    "skins",
    "ship_skills",
    "ship_stats",
    "ship_slots",
    "slot_allowed_types",
    "ghost_equipments",
    "fleet_tech",
    "ship_events",
    "augments",
    "ships",
    "skills",
    "rarities",
    "nations",
    "hulls",
    "equip_types",
]

EXPECTED_SCHEMAS = {
    "rarities": [("id", "INTEGER", 1), ("name", "TEXT", 0)],
    "nations": [("id", "INTEGER", 1), ("name", "TEXT", 0)],
    "hulls": [("id", "INTEGER", 1), ("name", "TEXT", 0)],
    "equip_types": [("id", "INTEGER", 1), ("name", "TEXT", 0)],
    "ships": [
        ("id", "INTEGER", 1), ("gid", "INTEGER", 0), ("name", "TEXT", 0),
        ("global_name", "TEXT", 0), ("rarity_id", "INTEGER", 0),
        ("nation_id", "INTEGER", 0), ("hull_id", "INTEGER", 0),
        ("ship_class", "TEXT", 0), ("sub_class", "TEXT", 0),
        ("release_date", "INTEGER", 0), ("icon", "TEXT", 0),
        ("flags", "INTEGER", 0), ("timer", "TEXT", 0),
        ("pool_light", "BOOLEAN", 0), ("pool_heavy", "BOOLEAN", 0),
        ("pool_special", "BOOLEAN", 0), ("limited_event", "TEXT", 0),
        ("tags", "TEXT", 0),
    ],
    "ship_events": [("ship_id", "INTEGER", 1), ("event_name", "TEXT", 2)],
    "ship_stats": [
        ("ship_id", "INTEGER", 1), ("limit_break", "INTEGER", 2),
        ("hp", "INTEGER", 0), ("fp", "INTEGER", 0), ("trp", "INTEGER", 0),
        ("avi", "INTEGER", 0), ("aa", "INTEGER", 0), ("rld", "INTEGER", 0),
        ("hit", "INTEGER", 0), ("eva", "INTEGER", 0), ("spd", "INTEGER", 0),
        ("luck", "INTEGER", 0), ("armor", "INTEGER", 0),
        ("oil_start", "INTEGER", 0), ("oil_end", "INTEGER", 0),
    ],
    "ship_slots": [
        ("ship_id", "INTEGER", 1), ("limit_break", "INTEGER", 2),
        ("slot_index", "INTEGER", 3), ("efficiency", "REAL", 0),
        ("base", "INTEGER", 0), ("preload", "INTEGER", 0),
        ("parallel", "INTEGER", 0), ("default_equip_id", "INTEGER", 0),
    ],
    "slot_allowed_types": [
        ("ship_id", "INTEGER", 1), ("slot_index", "INTEGER", 2),
        ("equip_type_id", "INTEGER", 3),
    ],
    "ghost_equipments": [
        ("ship_id", "INTEGER", 0), ("limit_break", "INTEGER", 0),
        ("equip_id", "INTEGER", 0), ("efficiency", "REAL", 0),
    ],
    "fleet_tech": [
        ("ship_id", "INTEGER", 1), ("collect_pts", "INTEGER", 0),
        ("lb_pts", "INTEGER", 0), ("lvl120_pts", "INTEGER", 0),
        ("bonus_stat", "TEXT", 0), ("bonus_value", "INTEGER", 0),
    ],
    "skills": [("id", "INTEGER", 1), ("name", "TEXT", 0), ("description", "TEXT", 0)],
    "ship_skills": [
        ("ship_id", "INTEGER", 1), ("skill_id", "INTEGER", 2),
        ("limit_break", "INTEGER", 3),
    ],
    "augments": [
        ("id", "INTEGER", 1), ("name", "TEXT", 0), ("ship_id", "INTEGER", 0),
        ("skill_upgrade_id", "INTEGER", 0), ("rarity", "INTEGER", 0),
    ],
    "voice_lines": [
        ("id", "INTEGER", 1), ("ship_id", "INTEGER", 0), ("skin_id", "INTEGER", 0),
        ("type", "TEXT", 0), ("content", "TEXT", 0),
    ],
    "skins": [("id", "INTEGER", 1), ("ship_id", "INTEGER", 0), ("name", "TEXT", 0)],
}

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

# Mappings from docs/common.md
RARITIES = {1: "Common (T1)", 2: "Common (T2)", 3: "Rare", 4: "Elite", 5: "Super Rare", 6: "Ultra Rare"}
NATIONS = {
    0: "Univ", 1: "Eagle", 2: "Royal", 3: "Sakura", 4: "Iron Blood",
    5: "Dragon", 6: "Sardengna", 7: "Northern", 8: "Iris", 9: "Vichiya",
    10: "French", 11: "Dutch", 94: "Council", 95: "X", 96: "Tempesta",
    97: "META", 98: "Burin", 99: "Siren", 101: "Neptunia", 102: "Bili",
    103: "Uta", 104: "Kizuna", 105: "Holo", 106: "Venus", 107: "Idol",
    108: "SSSS", 109: "Ryza", 110: "Senran", 111: "LoveRu", 112: "B★RS",
    113: "Yumia", 114: "Danmachi", 115: "DAL"
}
HULLS = {
    0: "Unknown", 1: "DD", 2: "CL", 3: "CA", 4: "BC", 5: "BB",
    6: "CVL", 7: "CV", 8: "SS", 9: "CAV", 10: "BBV", 11: "CT",
    12: "AR", 13: "BM", 14: "TRP", 15: "Cargo", 16: "Bomb",
    17: "SSV", 18: "CB", 19: "AE", 20: "DDGv", 21: "DDGm",
    22: "IXs", 23: "IXv", 24: "IXm", 25: "Special"
}
EQUIP_TYPES = {
    0: "Unknown", 1: "DD Gun", 2: "CL Gun", 3: "CA Gun", 4: "BB Gun",
    5: "Torpedo", 6: "AA Gun (Normal)", 7: "Fighter", 8: "Torpedo Bomber",
    9: "Dive Bomber", 10: "Auxiliary", 11: "CB Gun", 12: "Seaplane",
    13: "Sub Torpedo", 14: "Depth Charge", 15: "ASW Bomber", 17: "ASW Heli",
    18: "Cargo", 20: "Missile", 21: "Fuze AA Gun", 99: "Raid Bomber"
}

def create_tables(cursor, drop_existing=False):
    if drop_existing:
        for table in APP_TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")

    # Reference tables
    cursor.execute("CREATE TABLE IF NOT EXISTS rarities (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS nations (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS hulls (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS equip_types (id INTEGER PRIMARY KEY, name TEXT)")

    # Main Ship Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ships (
            id INTEGER PRIMARY KEY,
            gid INTEGER,
            name TEXT,
            global_name TEXT,
            rarity_id INTEGER,
            nation_id INTEGER,
            hull_id INTEGER,
            ship_class TEXT,
            sub_class TEXT,
            release_date INTEGER,
            icon TEXT,
            flags INTEGER,
            timer TEXT,
            pool_light BOOLEAN,
            pool_heavy BOOLEAN,
            pool_special BOOLEAN,
            limited_event TEXT,
            tags TEXT,
            FOREIGN KEY(rarity_id) REFERENCES rarities(id),
            FOREIGN KEY(nation_id) REFERENCES nations(id),
            FOREIGN KEY(hull_id) REFERENCES hulls(id)
        )
    """)

    # Ship Events
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ship_events (
            ship_id INTEGER,
            event_name TEXT,
            PRIMARY KEY(ship_id, event_name),
            FOREIGN KEY(ship_id) REFERENCES ships(id)
        )
    """)

    # Ship Stats per Limit Break
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ship_stats (
            ship_id INTEGER,
            limit_break INTEGER,
            hp INTEGER, fp INTEGER, trp INTEGER, avi INTEGER, aa INTEGER,
            rld INTEGER, hit INTEGER, eva INTEGER, spd INTEGER, luck INTEGER,
            armor INTEGER, oil_start INTEGER, oil_end INTEGER,
            PRIMARY KEY(ship_id, limit_break),
            FOREIGN KEY(ship_id) REFERENCES ships(id)
        )
    """)

    # Ship Slots per Limit Break
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ship_slots (
            ship_id INTEGER,
            limit_break INTEGER,
            slot_index INTEGER,
            efficiency REAL,
            base INTEGER,
            preload INTEGER,
            parallel INTEGER,
            default_equip_id INTEGER,
            PRIMARY KEY(ship_id, limit_break, slot_index),
            FOREIGN KEY(ship_id) REFERENCES ships(id)
        )
    """)

    # Slot Allowed Equipment Types
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS slot_allowed_types (
            ship_id INTEGER,
            slot_index INTEGER,
            equip_type_id INTEGER,
            PRIMARY KEY(ship_id, slot_index, equip_type_id),
            FOREIGN KEY(ship_id) REFERENCES ships(id),
            FOREIGN KEY(equip_type_id) REFERENCES equip_types(id)
        )
    """)

    # Ghost Equipment
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ghost_equipments (
            ship_id INTEGER,
            limit_break INTEGER,
            equip_id INTEGER,
            efficiency REAL,
            FOREIGN KEY(ship_id) REFERENCES ships(id)
        )
    """)

    # Fleet Tech
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fleet_tech (
            ship_id INTEGER PRIMARY KEY,
            collect_pts INTEGER,
            lb_pts INTEGER,
            lvl120_pts INTEGER,
            bonus_stat TEXT,
            bonus_value INTEGER,
            FOREIGN KEY(ship_id) REFERENCES ships(id)
        )
    """)

    # Skills
    cursor.execute("CREATE TABLE IF NOT EXISTS skills (id INTEGER PRIMARY KEY, name TEXT, description TEXT)")
    
    # Ship-Skill Link
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ship_skills (
            ship_id INTEGER,
            skill_id INTEGER,
            limit_break INTEGER,
            PRIMARY KEY(ship_id, skill_id, limit_break),
            FOREIGN KEY(ship_id) REFERENCES ships(id),
            FOREIGN KEY(skill_id) REFERENCES skills(id)
        )
    """)

    # Augments
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS augments (
            id INTEGER PRIMARY KEY,
            name TEXT,
            ship_id INTEGER,
            skill_upgrade_id INTEGER,
            rarity INTEGER,
            FOREIGN KEY(ship_id) REFERENCES ships(id)
        )
    """)

    # Voice Lines (for RAG)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS voice_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ship_id INTEGER,
            skin_id INTEGER,
            type TEXT,
            content TEXT,
            FOREIGN KEY(ship_id) REFERENCES ships(id)
        )
    """)

    # Skins
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS skins (
            id INTEGER PRIMARY KEY,
            ship_id INTEGER,
            name TEXT,
            FOREIGN KEY(ship_id) REFERENCES ships(id)
        )
    """)

def schema_matches(cursor):
    for table, expected in EXPECTED_SCHEMAS.items():
        cursor.execute(f"PRAGMA table_info({table})")
        actual = [(row[1], row[2].upper(), row[5]) for row in cursor.fetchall()]
        if actual != expected:
            return False
    return True

def delete_ship_related_rows(cursor, ship_id):
    for table in (
        "ship_events",
        "ship_stats",
        "ship_slots",
        "slot_allowed_types",
        "ghost_equipments",
        "fleet_tech",
        "ship_skills",
        "skins",
        "voice_lines",
    ):
        cursor.execute(f"DELETE FROM {table} WHERE ship_id = ?", (ship_id,))

def prune_missing_rows(cursor, table, id_column, active_ids):
    if not active_ids:
        cursor.execute(f"DELETE FROM {table}")
        return
    placeholders = ",".join("?" for _ in active_ids)
    cursor.execute(
        f"DELETE FROM {table} WHERE {id_column} NOT IN ({placeholders})",
        tuple(active_ids),
    )

def migrate():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    db_has_schema = all(
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        for table in EXPECTED_SCHEMAS
    )
    full_rebuild = not db_has_schema or not schema_matches(cursor)
    if full_rebuild:
        print("Schema missing or changed. Rebuilding database from scratch...")
    else:
        print("Schema unchanged. Updating database in place...")

    create_tables(cursor, drop_existing=full_rebuild)

    # Populate reference tables
    cursor.executemany("INSERT OR REPLACE INTO rarities VALUES (?, ?)", list(RARITIES.items()))
    cursor.executemany("INSERT OR REPLACE INTO nations VALUES (?, ?)", list(NATIONS.items()))
    cursor.executemany("INSERT OR REPLACE INTO hulls VALUES (?, ?)", list(HULLS.items()))
    cursor.executemany("INSERT OR REPLACE INTO equip_types VALUES (?, ?)", list(EQUIP_TYPES.items()))

    # Load JSON files
    def load_json(name):
        path = DATA_DIR / name
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    ships_data = load_json("ships.json")
    skills_data = load_json("skills.json")
    skins_data = load_json("skins.json")
    augments_data = load_json("augments.json")
    words_data = load_json("ships_words.json")
    drops_data = load_json("ship_drops.json")

    source_ship_ids = {int(ship_id) for ship_id in ships_data.keys()}
    source_skill_ids = {int(skill_id) for skill_id in skills_data.keys()}
    source_skin_ids = {int(skin_id) for skin_id in skins_data.keys()}
    source_augment_ids = {int(augment_id) for augment_id in augments_data.keys()}

    if not full_rebuild:
        cursor.execute("SELECT id FROM ships")
        existing_ship_ids = {row[0] for row in cursor.fetchall()}
        for deleted_ship_id in existing_ship_ids - source_ship_ids:
            delete_ship_related_rows(cursor, deleted_ship_id)
            cursor.execute("DELETE FROM ships WHERE id = ?", (deleted_ship_id,))

        prune_missing_rows(cursor, "skills", "id", source_skill_ids)
        prune_missing_rows(cursor, "skins", "id", source_skin_ids)
        prune_missing_rows(cursor, "augments", "id", source_augment_ids)

    # Migrate Skills
    print("Migrating Skills...")
    for s_id, s_info in skills_data.items():
        description = clean_skill_description(s_info.get("description"), s_id, skills_data)
        cursor.execute("INSERT OR REPLACE INTO skills VALUES (?, ?, ?)", 
                       (int(s_id), s_info.get("name"), description))

    # Migrate Augments
    print("Migrating Augments...")
    for a_id, a_info in augments_data.items():
        cursor.execute("INSERT OR REPLACE INTO augments (id, name, ship_id, skill_upgrade_id, rarity) VALUES (?, ?, ?, ?, ?)",
                       (int(a_id), a_info.get("name"), a_info.get("ship_id"), 
                        a_info.get("skill_upgrades", [{}])[0].get("with") if a_info.get("skill_upgrades") else None,
                        a_info.get("rarity")))

    # Migrate Ships
    print("Migrating Ships...")
    for ship_id_str, ship in ships_data.items():
        ship_id = int(ship_id_str)
        drop_info = drops_data.get(ship_id_str, {})

        if not full_rebuild:
            delete_ship_related_rows(cursor, ship_id)
        
        # Main ship info
        cursor.execute("""
            INSERT OR REPLACE INTO ships (
                id, gid, name, global_name, rarity_id, nation_id, hull_id, 
                ship_class, sub_class, release_date, icon, flags,
                timer, pool_light, pool_heavy, pool_special, limited_event, tags
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ship_id, ship.get("gid"), ship.get("name"), ship.get("global_name"), 
              ship.get("rarity"), ship.get("nation"), ship.get("hull"), 
              ship.get("class"), ship.get("sub_class"), ship.get("date"), ship.get("icon"), ship.get("flags"),
              drop_info.get("timer"), drop_info.get("light"), drop_info.get("heavy"), 
              drop_info.get("special"), drop_info.get("limited"),
              json.dumps(ship.get("tags", []))))

        # Ship Events
        for event_name in drop_info.get("events", []):
            cursor.execute("INSERT OR REPLACE INTO ship_events (ship_id, event_name) VALUES (?, ?)", (ship_id, event_name))

        # Stats per LB
        stats_list = ship.get("stats", [])
        lb_data = ship.get("lb_data", [])
        for lb, stats in enumerate(stats_list):
            oil_start = 0
            oil_end = 0
            if lb < len(lb_data):
                oil = lb_data[lb].get("oil", {})
                oil_start = oil.get("start", 0)
                oil_end = oil.get("end", 0)

            cursor.execute("""
                INSERT OR REPLACE INTO ship_stats (ship_id, limit_break, hp, fp, trp, avi, aa, rld, hit, eva, spd, luck, armor, oil_start, oil_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ship_id, lb, stats.get("hp"), stats.get("fp"), stats.get("trp"), stats.get("avi"), stats.get("aa"),
                  stats.get("rld"), stats.get("hit"), stats.get("eva"), stats.get("spd"), stats.get("luck"),
                  stats.get("armor"), oil_start, oil_end))

        # Slots per LB
        slots_lb_list = ship.get("slots", [])
        for lb, slots in enumerate(slots_lb_list):
            for idx, slot in enumerate(slots):
                cursor.execute("""
                    INSERT OR REPLACE INTO ship_slots (ship_id, limit_break, slot_index, efficiency, base, preload, parallel, default_equip_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (ship_id, lb, idx + 1, slot.get("efficiency"), slot.get("base"), 
                      slot.get("preload"), slot.get("parallel"), slot.get("default_id")))
                
                # Allowed types (only need once per slot, assuming constant across LB unless override)
                if lb == len(slots_lb_list) - 1: # Use max LB for types
                    for t_id in slot.get("types", []):
                        cursor.execute("INSERT OR REPLACE INTO slot_allowed_types VALUES (?, ?, ?)", (ship_id, idx + 1, t_id))

        # Ghost Equipment
        ghost_lb_list = ship.get("ghost_equipment", [])
        for lb, ghost_list in enumerate(ghost_lb_list):
            for g in ghost_list:
                cursor.execute("INSERT OR REPLACE INTO ghost_equipments VALUES (?, ?, ?, ?)",
                               (ship_id, lb, g.get("id"), g.get("efficiency")))

        # Fleet Tech
        ft = ship.get("fleet_tech")
        if ft:
            bonus_stat = None
            bonus_val = 0
            # Use 'collect' info for bonus
            collect = ft.get("collect", {})
            bonus_stat = collect.get("stat")
            bonus_val = collect.get("value", 0)
            
            # Ensure bonus_val is an integer (sometimes it might be missing or different type)
            if not isinstance(bonus_val, (int, float)):
                bonus_val = 0

            cursor.execute("""
                INSERT OR REPLACE INTO fleet_tech VALUES (?, ?, ?, ?, ?, ?)
            """, (ship_id, collect.get("pts"), ft.get("limit_break", {}).get("pts"),
                  ft.get("level", {}).get("pts"), bonus_stat, bonus_val))

        # Ship Skills Link
        skill_lb_list = ship.get("skills", [])
        for lb, skill_ids in enumerate(skill_lb_list):
            for s_id in skill_ids:
                cursor.execute("INSERT OR REPLACE INTO ship_skills VALUES (?, ?, ?)", (ship_id, s_id, lb))

    # Migrate Skins
    print("Migrating Skins...")
    for s_id, s_info in skins_data.items():
        cursor.execute("INSERT OR REPLACE INTO skins VALUES (?, ?, ?)", 
                       (int(s_id), s_info.get("ship_id"), s_info.get("name")))

    # Migrate Voice Lines
    print("Migrating Voice Lines...")
    for s_id_str, s_words in words_data.items():
        ship_id = int(s_id_str)
        for skin in s_words.get("skins", []):
            skin_id = skin.get("id")
            for line in skin.get("lines", []):
                cursor.execute("INSERT INTO voice_lines (ship_id, skin_id, type, content) VALUES (?, ?, ?, ?)",
                               (ship_id, skin_id, line.get("type"), line.get("line")))

    conn.commit()
    conn.close()
    print(f"Successfully created {DB_NAME}")

if __name__ == "__main__":
    migrate()
