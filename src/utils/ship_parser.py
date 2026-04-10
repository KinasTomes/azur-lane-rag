import json
import os
import re
import time
import logging
from typing import List, Dict, Any, Iterator, Optional
from openai import OpenAI
from dotenv import load_dotenv

# Load .env file if exists
load_dotenv()

logger = logging.getLogger(__name__)

class ShipDataParser:
    SHIP_SCHEMA_EXAMPLE = {
        "id": 12,
        "skills": [
            {
                "id": 30562,
                "name": "All Out Assault II",
                "type": ["BARRAGE"],
                "edges": {
                    "HAS_SKILL": 12,
                    "AFFECTS": {
                        "scope": "self",
                        "condition": {}
                    }
                }
            }
        ]
    }

    SYSTEM_PROMPT = f"""
You are a specialized Azur Lane skill analyzer.

Your job:
- Analyze the provided ship skills and extract their types, scopes, and conditions.
- Output a JSON array of objects, each representing a ship with its analyzed skills.
- Do not output markdown, code fences, or any text other than the JSON.

Output schema example:
{json.dumps([SHIP_SCHEMA_EXAMPLE], indent=2, ensure_ascii=False)}

Skill Type Allowed List (Select one or more):
- [BARRAGE, SELF_BUFF, ALLY_BUFF, FLEET_BUFF, HEAL, SHIELD, DEBUFF, SUPPORT, TANK, ASW, AVIATION, TORPEDO, ANTI_AIR, CROSS_FLEET, SURVIVABILITY, ACCURACY, RELOAD, AMMO_CHANGE, TRANSFORM, SLOW, STOP, ARMOR_BREAK, SHIELD_BYPASS, BURN, FLOOD, AVOID_DEATH, EVADE_RATE_BUFF, CRITICAL, LUCK_MANIPULATION, SPECIAL]

Rules:
1. Return a JSON array of ships. Each ship must have "id" and "skills" fields.
2. Infer skills[].type as an array (e.g., ["BARRAGE", "BUFF", "HEAL"]).
3. Infer skills[].edges.AFFECTS.scope as "self", "ally", "fleet", or "unknown".
4. Infer skills[].edges.AFFECTS.condition as an object of specific triggers (use {{}} if none).
5. Only reason about the skills. Do not invent other ship attributes.
""".strip()

    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        # Ưu tiên tham số truyền vào, sau đó đến biến môi trường, cuối cùng là giá trị mặc định
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("NVIDIA_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL") or "https://integrate.api.nvidia.com/v1"
        self.model = model or os.getenv("LLM_MODEL") or "deepseek-ai/deepseek-v3.1"
        
        if not self.api_key:
            logger.warning("LLM_API_KEY or NVIDIA_API_KEY is not set.")

        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key
        )

    def _send_to_llm(self, messages: List[Dict[str, str]], max_retries: int = 3) -> str:
        """Gửi yêu cầu đến LLM API với cơ chế retry."""
        for attempt in range(max_retries):
            try:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.1,
                    top_p=0.95,
                    max_tokens=8192,
                    stream=False
                )
                return completion.choices[0].message.content
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Retrying API call ({attempt + 1}/{max_retries}) due to: {e}")
                    time.sleep(2 ** attempt)
                    continue
                raise e
        raise Exception("Failed after retries.")

    def _extract_balanced_json_fragment(self, text: str, start_index: int) -> Optional[str]:
        opening = text[start_index]
        if opening not in "[{":
            return None

        stack: List[str] = []
        in_string = False
        escape_next = False

        for index in range(start_index, len(text)):
            char = text[index]

            if in_string:
                if escape_next:
                    escape_next = False
                elif char == "\\":
                    escape_next = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue

            if char == "{":
                stack.append("}")
            elif char == "[":
                stack.append("]")
            elif char in "}]":
                if not stack or char != stack[-1]:
                    return None
                stack.pop()
                if not stack:
                    return text[start_index:index + 1]

        return None

    def _iter_json_candidates(self, response_text: str) -> Iterator[str]:
        fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", response_text, flags=re.DOTALL | re.IGNORECASE)
        for block in fenced_blocks:
            block = block.strip()
            if block:
                yield block

        stripped_text = response_text.strip()
        if stripped_text:
            yield stripped_text

        for match in re.finditer(r"[\[{]", response_text):
            fragment = self._extract_balanced_json_fragment(response_text, match.start())
            if fragment:
                yield fragment

    def _unwrap_response_payload(self, parsed_data: Any) -> List[Dict[str, Any]]:
        current = parsed_data

        while isinstance(current, dict):
            for key in ("ships", "data", "results", "items"):
                value = current.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    current = value
                    break
            else:
                return [current]

        if isinstance(current, list):
            return current

        return []

    def _parse_response(self, response_text: str) -> List[Dict[str, Any]]:
        """Xử lý phản hồi văn bản thành JSON."""
        last_error: Optional[Exception] = None

        for candidate in self._iter_json_candidates(response_text):
            try:
                parsed_data = json.loads(candidate)
                return self._unwrap_response_payload(parsed_data)
            except json.JSONDecodeError as error:
                last_error = error

        if last_error:
            raise ValueError(f"Invalid JSON: {str(last_error)}")
        return []

    def parse_ship_summaries(self, ship_batch: List[Dict[str, Any]], max_retries: int = 3) -> List[Dict[str, Any]]:
        # Chuẩn bị nội dung gửi LLM
        reasoning_inputs = [s.get("reasoning_input", "") for s in ship_batch]
        summaries_str = "\n\n".join(reasoning_inputs)

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": f"Analyze these skills:\n{summaries_str}"}
        ]

        ship_ids = [s.get("id") for s in ship_batch]
        logger.info(f"Sending batch for skill analysis (IDs: {ship_ids})...")        
        start_time = time.time()

        try:
            response = self._send_to_llm(messages, max_retries)
            logger.info(f"Done! ({time.time() - start_time:.2f}s)")
            
            # Phân tích kết quả từ LLM (chỉ chứa skills)
            llm_results = self._parse_response(response)
            llm_results_map = {item["id"]: item["skills"] for item in llm_results if "id" in item}
            
            # Gộp Hard Data với Skill Reasoning
            final_ships = []
            for ship_item in ship_batch:
                sid = ship_item["id"]
                hard_data = ship_item["hard_data"]
                
                # Lấy skills đã qua xử lý hoặc mặc định là mảng rỗng
                reasoned_skills = llm_results_map.get(sid, [])
                
                # Merge
                combined = hard_data.copy()
                combined["skills"] = reasoned_skills
                final_ships.append(combined)

            # Inject tags từ DB và trả về
            return self._inject_tags_from_db(final_ships)
        except Exception as e:
            logger.error(f"Failed to process batch {ship_ids}: {e}")
            raise

    def _inject_tags_from_db(self, ships: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Truy vấn tags từ SQLite và inject vào attributes của mỗi tàu."""
        from pathlib import Path
        import sqlite3

        repo_root = Path(__file__).resolve().parents[2]
        db_path = repo_root / "src" / "azur_lane.db"

        if not db_path.exists():
            logger.warning(f"DB not found at {db_path}, skipping tag injection.")
            return ships

        injected_ids = []
        missing_ids = []
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            for ship in ships:
                ship_id = ship.get("id")
                if not ship_id: continue

                cursor.execute("SELECT tags FROM ships WHERE id = ?", (ship_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    tags = json.loads(row[0])
                    if "attributes" not in ship:
                        ship["attributes"] = {}
                    ship["attributes"]["tags"] = tags
                    injected_ids.append(ship_id)
                else:
                    missing_ids.append(ship_id)

            conn.close()
            if injected_ids:
                logger.info(f"Injected tags for: {injected_ids}")
            if missing_ids:
                logger.info(f"No tags found in DB for: {missing_ids}")

        except Exception as e:
            logger.error(f"Failed to inject tags: {e}")

        return ships
