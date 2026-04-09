import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.events import Key
from textual.screen import Screen
from textual.suggester import SuggestFromList
from textual.widgets import DataTable, Footer, Header, Input, Markdown, Static


# Mappings from docs/common.md
RARITY_MAP = {
    1: "Common (T1)",
    2: "Common (T2)",
    3: "Rare",
    4: "Elite",
    5: "Super Rare",
    6: "Ultra Rare",
}

NATION_MAP = {
    0: "Univ", 1: "Eagle", 2: "Royal", 3: "Sakura", 4: "Iron Blood",
    5: "Dragon", 6: "Sardengna", 7: "Northern", 8: "Iris", 9: "Vichiya",
    10: "French", 11: "Dutch", 94: "Council", 95: "X", 96: "Tempesta",
    97: "META", 98: "Burin", 99: "Siren", 101: "Neptunia", 102: "Bili",
    103: "Uta", 104: "Kizuna", 105: "Holo", 106: "Venus", 107: "Idol",
    108: "SSSS", 109: "Ryza", 110: "Senran", 111: "LoveRu", 112: "B★RS",
    113: "Yumia", 114: "Danmachi", 115: "DAL"
}

HULL_MAP = {
    0: "Unknown", 1: "DD", 2: "CL", 3: "CA", 4: "BC", 5: "BB",
    6: "CVL", 7: "CV", 8: "SS", 9: "CAV", 10: "BBV", 11: "CT",
    12: "AR", 13: "BM", 14: "TRP", 15: "Cargo", 16: "Bomb",
    17: "SSV", 18: "CB", 19: "AE", 20: "DDGv", 21: "DDGm",
    22: "IXs", 23: "IXv", 24: "IXm", 25: "Special"
}

EQUIP_TYPE_MAP = {
    0: "Unknown", 1: "DD Gun", 2: "CL Gun", 3: "CA Gun", 4: "BB Gun",
    5: "Torpedo", 6: "AA Gun (Normal)", 7: "Fighter", 8: "Torpedo Bomber",
    9: "Dive Bomber", 10: "Auxiliary", 11: "CB Gun", 12: "Seaplane",
    13: "Sub Torpedo", 14: "Depth Charge", 15: "ASW Bomber", 17: "ASW Heli",
    18: "Cargo", 20: "Missile", 21: "Fuze AA Gun", 99: "Raid Bomber"
}

UNLOCK_TYPE_MAP = {
    0: "Guild Shop", 1: "Medal Shop", 2: "Core Data Shop", 3: "Merit Shop",
    4: "Requisition Gacha", 5: "Prototype Shop", 6: "Permanent UR Pity",
    7: "Weekly Missions", 8: "Monthly Login", 9: "Returnee Reward",
    10: "Collection Reward", 11: "Cruise Pass", 12: "META Shop",
    13: "META Showdown", 14: "Dossier Analysis", 15: "Shipyard", 16: "Quest"
}

SPECIFIC_BUFF_MAP = {
    "gnr": "Half shots for AoA",
    "torp": "Reduced torpedo spread",
    "aux": "+30% aux stats",
}

SHIP_PROPERTY_DOCS: list[tuple[str, str, str]] = [
    ("global_name", "string", "The prefixed English name of this ship displayed by clients."),
    ("id", "number", "The unique ID of this ship."),
    ("gid", "number", "The group ID of this ship."),
    ("flags", "number", "5-bit bitmask representing categories this ship belongs to."),
    ("name", "string", "Names this ship has."),
    ("rarity", "Rarity", "The rarity of this ship."),
    ("tags", "string[] | string[][]", "Tags assigned to this ship."),
    ("gift_dislike?", "number[] | number[][]", "Gift IDs this ship dislikes."),
    ("nation", "Nation", "The nation of this ship."),
    ("hull", "Hull", "The hull type of this ship."),
    ("specific_buff", "SpecificBuff", "Special buff granted at max limit break."),
    ("slots", "SlotData[][]", "Equipment slot data at each limit break."),
    ("stats", "ShipStatsData[][]", "Stats at each limit break."),
    ("ghost_equipment", "GhostEquipmentData[][]", "Ghost equipment at each limit break."),
    ("retro?", "RetroData", "Retrofit data if this ship has retrofit."),
    ("research?", "PRData", "Research data for PR/DR ships."),
    ("skins", "number[]", "IDs of all skins this ship has."),
    ("skin_share_ids", "number[]", "Ship IDs this ship can share skins with."),
    ("lb_data", "LimitBreakData[]", "Per-limit-break data not fitting other fields."),
    ("date", "number", "Approximate release date as UNIX timestamp in milliseconds."),
    ("strengthen_exp?", "object", "Stat EXP given when used for enhancing."),
    ("fleet_tech?", "FleetTech", "Fleet technology data if this ship grants bonuses."),
    ("servers", "AlServer[]", "Servers where this ship was/is obtainable."),
    ("icon", "string", "Icon key for artwork paths."),
    ("aliases?", "string[]", "Known community aliases."),
    ("class", "string", "Historical/logical class."),
    ("sub_class?", "string", "Historical/logical sub-class."),
    ("upgrade_text", "UpgradeText[]", "Limit break/development unlock text."),
    ("oath_skin?", "boolean", "Whether this ship has an oath skin."),
    ("unique_aug?", "number", "ID of this ship's unique augment."),
    ("skills", "number[][]", "Skill IDs at each limit break."),
]

FLAG_BITS: list[tuple[int, str, str]] = [
    (0, "Bulin", "Special category for the 3 bulin ships."),
    (1, "Retrofit", "The ship has a retrofit."),
    (2, "Research", "The ship is a research ship."),
    (3, "Fate Simulation", "Research ship with fate simulation."),
    (4, "META", "The ship is a META ship."),
]


class ShipDetailScreen(Screen[None]):
    BINDINGS = [Binding("escape", "back", "Back", show=True)]

    def __init__(self, ship: dict[str, Any], drop: dict[str, Any] | None, app: "ShipBrowserApp") -> None:
        super().__init__()
        self.ship = ship
        self.drop = drop or {}
        self.parent_app = app

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Markdown(self._render_markdown(), id="ship_detail")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def _render_markdown(self) -> str:
        name = str(self.ship.get("name", "Unknown Ship"))
        ship_id = self.ship.get("id", "N/A")
        lines: list[str] = [
            f"# {name}",
            "",
            f"ID: **{ship_id}**",
            "",
            "Press `Esc` to go back to the list.",
            "",
            "## Ship Data",
            "",
        ]

        for key, type_name, description in SHIP_PROPERTY_DOCS:
            value = self._resolve_ship_value(key)
            lines.append(f"### `{key}`")
            lines.append(f"- Type: `{type_name}`")
            lines.append(f"- Description: {description}")
            
            if key == "date" and isinstance(value, (int, float)):
                lines.append(f"- Value: `{value}` ({self._to_utc(value)})")
            elif key == "rarity" and isinstance(value, int):
                lines.append(f"- Value: `{RARITY_MAP.get(value, value)}`")
            elif key == "nation" and isinstance(value, int):
                lines.append(f"- Value: `{NATION_MAP.get(value, value)}`")
            elif key == "hull" and isinstance(value, int):
                lines.append(f"- Value: `{HULL_MAP.get(value, value)}`")
            elif key == "specific_buff":
                lines.append(f"- Value: `{SPECIFIC_BUFF_MAP.get(value, value) if value else 'None'}`")
            elif key == "skills" and isinstance(value, list):
                lines.append("- Value:")
                for lb_idx, lb_skills in enumerate(value):
                    lines.append(f"  - **Limit Break {lb_idx}:**")
                    for skill_id in lb_skills:
                        skill_info = self.parent_app.skills.get(str(skill_id), {})
                        s_name = skill_info.get("name", "Unknown")
                        s_desc = skill_info.get("description", "No description").replace("\n", " ")
                        lines.append(f"    - `{skill_id}`: **{s_name}** - *{s_desc}*")
            elif key == "skins" and isinstance(value, list):
                lines.append("- Value:")
                for skin_id in value:
                    skin_info = self.parent_app.skins.get(str(skin_id), {})
                    sk_name = skin_info.get("name", "Unknown")
                    lines.append(f"  - `{skin_id}`: **{sk_name}**")
            elif key == "unique_aug?" and value:
                aug_info = self.parent_app.augments.get(str(value), {})
                aug_name = aug_info.get("name", "Unknown")
                lines.append(f"- Value: `{value}` (**{aug_name}**)")
            elif key == "slots" and isinstance(value, list):
                lines.append("- Value:")
                for lb_idx, lb_slots in enumerate(value):
                    lines.append(f"  - **Limit Break {lb_idx}:**")
                    for slot_idx, slot in enumerate(lb_slots):
                        types = [EQUIP_TYPE_MAP.get(t, t) for t in slot.get("types", [])]
                        eff = slot.get("efficiency", 0) * 100
                        base = slot.get("base", 0)
                        lines.append(f"    - Slot {slot_idx+1}: {types} | Eff: {eff}% | Mounts: {base}")
            else:
                lines.append(f"- Value: {self._format_value(value)}")
            lines.append("")

        lines.extend([
            "## Ship Flags",
            "",
            "Bit interpretation for this ship's `flags` value:",
            "",
        ])

        flags = int(self.ship.get("flags") or 0)
        for bit, label, description in FLAG_BITS:
            enabled = (flags & (1 << bit)) != 0
            lines.append(
                f"- Bit {bit} (`1 << {bit}`) {label}: `{'true' if enabled else 'false'}` - {description}"
            )

        lines.extend([
            "",
            "## Drop Data",
            "",
        ])

        if not self.drop:
            lines.append("No drop entry found for this ship in `data/ship_drops.json`.")
            lines.append("")
        else:
            for key, type_name, description in [
                ("id", "number", "The ship ID this drop data belongs to."),
                ("timer", "string | null", "Construction timer or null."),
                ("light", "boolean", "Light pool."),
                ("heavy", "boolean", "Heavy pool."),
                ("special", "boolean", "Special pool."),
                ("limited", "string | null", "Limited pool event."),
                ("other", "UnlockType[]", "Other sources."),
                ("maps", "MapDrop[][]", "Map drop data."),
                ("events", "string[]", "Events."),
            ]:
                val = self.drop.get(key)
                lines.append(f"### `{key}`")
                lines.append(f"- Description: {description}")
                if key == "other" and isinstance(val, list):
                    labels = [UNLOCK_TYPE_MAP.get(t, t) for t in val]
                    lines.append(f"- Value: `{labels}`")
                else:
                    lines.append(f"- Value: {self._format_value(val)}")
                lines.append("")

        # Add voice lines
        ship_words = self.parent_app.words.get(str(ship_id))
        if ship_words:
            lines.append("## Voice Lines")
            lines.append("")
            for skin in ship_words.get("skins", []):
                skin_id = skin.get("id")
                skin_name = "Default" if skin_id == -1 else self.parent_app.skins.get(str(skin_id), {}).get("name", f"Skin {skin_id}")
                lines.append(f"### Skin: {skin_name}")
                for line in skin.get("lines", [])[:10]: # Limit to 10 lines for brevity
                    l_type = line.get("type")
                    l_text = line.get("line")
                    lines.append(f"- **{l_type}**: {l_text}")
                lines.append("")

        return "\n".join(lines)

    def _resolve_ship_value(self, key: str) -> Any:
        clean_key = key.rstrip("?")
        if clean_key == "strengthen_exp":
            return self.ship.get("strengthen_exp")
        
        if key.startswith("strengthen_exp?."):
            strengthen = self.ship.get("strengthen_exp") or {}
            prop = key.split("?.", maxsplit=1)[1].replace("?", "")
            return strengthen.get(prop)

        return self.ship.get(clean_key)

    def _format_value(self, value: Any) -> str:
        if value is None:
            return "`N/A`"
        if isinstance(value, (dict, list)):
            return f"```json\n{json.dumps(value, indent=2, ensure_ascii=True)}\n```"
        return f"`{value}`"

    def _to_utc(self, ms: int | float) -> str:
        try:
            dt = datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except (OSError, OverflowError, ValueError):
            return "invalid timestamp"


class ShipBrowserApp(App[None]):
    TITLE = "Azur Lane Ship Browser"
    SUB_TITLE = "Arrow keys to navigate, Enter to inspect, / to search, Esc to go back"

    CSS = """
    Screen {
        background: #0f1115;
        color: #e7ebf2;
    }

    Header {
        background: #111a24;
        color: #f4c66a;
    }

    Footer {
        background: #131d2a;
        color: #93d5ff;
    }

    #main {
        layout: vertical;
        padding: 1 2;
        height: 1fr;
    }

    #search_help {
        color: #8aa2bf;
        margin-bottom: 1;
    }

    #search {
        margin-bottom: 1;
        background: #161f2d;
        color: #f4f7fb;
        border: round #3a8fb7;
    }

    #ship_table {
        background: #10161f;
        border: round #2b4860;
        height: 1fr;
    }

    #ship_table > .datatable--header {
        color: #f4c66a;
        background: #1b2836;
        text-style: bold;
    }

    #ship_table > .datatable--cursor {
        color: #0b1017;
        background: #6ad3c4;
        text-style: bold;
    }

    #ship_detail {
        background: #0f1115;
        color: #e7ebf2;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search", show=True),
        Binding("enter", "open_selected", "Open", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.ships_by_id: dict[int, dict[str, Any]] = {}
        self.ship_drops_by_id: dict[int, dict[str, Any]] = {}
        self.sorted_ship_ids: list[int] = []
        self.filtered_ship_ids: list[int] = []
        self.ship_names: list[str] = []
        
        # New data
        self.skills: dict[str, Any] = {}
        self.skins: dict[str, Any] = {}
        self.augments: dict[str, Any] = {}
        self.words: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main"):
            yield Static("Press / to jump to search. Enter opens highlighted ship. Esc exits detail view.", id="search_help")
            yield Input(placeholder="Search ships by name...", id="search")
            yield DataTable(id="ship_table")
        yield Footer()

    def on_mount(self) -> None:
        self._load_data()

        search = self.query_one("#search", Input)
        search.suggester = SuggestFromList(self.ship_names, case_sensitive=False)

        table = self.query_one("#ship_table", DataTable)
        table.zebra_stripes = True
        table.cursor_type = "row"
        table.add_columns("ID", "Ship Name")

        self.filtered_ship_ids = self.sorted_ship_ids.copy()
        self._refresh_table()
        table.focus()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_open_selected(self) -> None:
        self._open_selected_ship()

    def on_data_table_row_selected(self, _: DataTable.RowSelected) -> None:
        self._open_selected_ship()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        selected_ship_id = self._get_current_ship_id()

        if not query:
            self.filtered_ship_ids = self.sorted_ship_ids.copy()
        else:
            self.filtered_ship_ids = [
                ship_id
                for ship_id in self.sorted_ship_ids
                if query in str(self.ships_by_id[ship_id].get("name", "")).lower()
            ]

        self._refresh_table(preferred_ship_id=selected_ship_id)

    def on_key(self, event: Key) -> None:
        focused = self.focused
        if not isinstance(focused, Input) or focused.id != "search":
            return

        if event.key == "up":
            self._move_selection(-1)
            event.stop()
        elif event.key == "down":
            self._move_selection(1)
            event.stop()
        elif event.key == "enter":
            self._open_selected_ship()
            event.stop()

    def _load_data(self) -> None:
        base_dir = Path(__file__).resolve().parents[1]
        data_dir = base_dir / "AzurLaneData" / "data"
        
        def load_json(name):
            path = data_dir / name
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            return {}

        raw_ships = load_json("ships.json")
        raw_ship_drops = load_json("ship_drops.json")
        self.skills = load_json("skills.json")
        self.skins = load_json("skins.json")
        self.augments = load_json("augments.json")
        self.words = load_json("ships_words.json")

        self.ships_by_id = {int(k): v for k, v in raw_ships.items()}
        self.ship_drops_by_id = {int(k): v for k, v in raw_ship_drops.items()}

        self.sorted_ship_ids = sorted(
            self.ships_by_id.keys(),
            key=lambda ship_id: str(self.ships_by_id[ship_id].get("name", "")).lower(),
        )
        self.ship_names = [str(self.ships_by_id[ship_id].get("name", "")) for ship_id in self.sorted_ship_ids]

    def _refresh_table(self, preferred_ship_id: int | None = None) -> None:
        table = self.query_one("#ship_table", DataTable)
        table.clear()

        for ship_id in self.filtered_ship_ids:
            ship = self.ships_by_id[ship_id]
            table.add_row(str(ship.get("id", ship_id)), str(ship.get("name", "Unknown")))

        if not self.filtered_ship_ids:
            return

        target_index = 0
        if preferred_ship_id is not None and preferred_ship_id in self.filtered_ship_ids:
            target_index = self.filtered_ship_ids.index(preferred_ship_id)

        table.move_cursor(row=target_index)

    def _open_selected_ship(self) -> None:
        ship_id = self._get_current_ship_id()
        if ship_id is None:
            return

        ship = self.ships_by_id[ship_id]
        drop = self.ship_drops_by_id.get(ship_id)
        self.push_screen(ShipDetailScreen(ship=ship, drop=drop, app=self))

    def _move_selection(self, delta: int) -> None:
        if not self.filtered_ship_ids:
            return

        table = self.query_one("#ship_table", DataTable)
        current_row = table.cursor_row if table.cursor_row is not None else 0
        new_row = max(0, min(len(self.filtered_ship_ids) - 1, current_row + delta))
        table.move_cursor(row=new_row)

    def _get_current_ship_id(self) -> int | None:
        table = self.query_one("#ship_table", DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self.filtered_ship_ids):
            return None
        return self.filtered_ship_ids[row]


if __name__ == "__main__":
    ShipBrowserApp().run()
