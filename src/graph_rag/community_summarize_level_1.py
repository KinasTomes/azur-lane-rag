import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


# Load environment variables from .env file
load_dotenv()

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


api_key = os.getenv("ANTHROPIC_API_KEY")
base_url = os.getenv("ANTHROPIC_BASE_URL")
model_name = os.getenv("ANTHROPIC_BASE_MODEL")

client = OpenAI(api_key=api_key, base_url=base_url)

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "data" / "azur_lane_graph.db"


def extract_balanced_json_fragment(text, start_index):
    opening = text[start_index]
    if opening not in "[{":
        return None

    stack = []
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
                return text[start_index : index + 1]

    return None


def parse_llm_json_object(response_text):
    candidates = []

    fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", response_text, flags=re.DOTALL | re.IGNORECASE)
    for block in fenced_blocks:
        block = block.strip()
        if block:
            candidates.append(block)

    stripped = response_text.strip()
    if stripped:
        candidates.append(stripped)

    for match in re.finditer(r"[\[{]", response_text):
        fragment = extract_balanced_json_fragment(response_text, match.start())
        if fragment:
            candidates.append(fragment)

    last_error = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as error:
            last_error = error

    if last_error:
        raise ValueError(f"Invalid JSON from LLM: {last_error}")
    raise ValueError("No valid JSON object found in LLM response")


def get_level0_community_map(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, title, summary, findings, full_content
        FROM communities
        WHERE level = 0
        """
    )

    level0_map = {}
    for cid, title, summary, findings, full_content in cursor.fetchall():
        level0_map[cid] = {
            "title": title or f"Community {cid}",
            "summary": summary or "",
            "findings": findings or "[]",
            "full_content": full_content or "",
        }

    return level0_map


def get_level1_children(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT parent_community_id, child_community_id
        FROM community_hierarchy
        WHERE parent_level = 1 AND child_level = 0
        ORDER BY parent_community_id, child_community_id
        """
    )

    parent_to_children = {}
    for parent_id, child_id in cursor.fetchall():
        parent_to_children.setdefault(parent_id, []).append(child_id)

    return parent_to_children


def build_level1_input(level1_db_id, child_ids, level0_map):
    lines = [f"=== LEVEL 1 COMMUNITY SYNTHESIS #{level1_db_id} ==="]

    for cid in child_ids:
        item = level0_map.get(cid, {})
        title = item.get("title") or f"Community {cid}"
        summary = item.get("summary") or ""
        findings_raw = item.get("findings") or "[]"

        try:
            findings_list = json.loads(findings_raw) if isinstance(findings_raw, str) else findings_raw
        except json.JSONDecodeError:
            findings_list = []

        lines.append(f"\n- Level0 Community {cid}: {title}")
        if summary:
            lines.append(f"  Summary: {summary}")
        if findings_list:
            lines.append("  Findings:")
            for finding in findings_list:
                lines.append(f"  + {finding}")

    return "\n".join(lines)


def summarize_level1(conn, level1_db_id, compressed_data):
    prompt = f"""You are a Naval Strategic Analyst specializing in Azur Lane graph synthesis.

### Input Data:
{compressed_data}

### Instructions:
1. Merge the provided Level 0 communities into one higher-level strategic archetype.
2. Explain shared mechanics, overlap zones, and composition pivots.
3. Return a single valid JSON object only.

### JSON Schema:
{{
  "title": "A concise Level 1 umbrella name",
  "summary": "A 180-280 word synthesis of how these Level 0 communities connect strategically",
  "findings": [
    "Insight 1: Core shared mechanics",
    "Insight 2: Strategic role overlap",
    "Insight 3: Fleet-building pivots",
    "Insight 4: Constraints and counters"
  ]
}}
"""

    logger.info(f"Summarizing Level 1 Community ID: {level1_db_id}...")
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": "You are a helpful AI that returns ONLY valid JSON objects built from the provided schema.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=8192 * 2,
        temperature=0.1,
    )

    content = response.choices[0].message.content or ""
    parsed = parse_llm_json_object(content)

    title = parsed.get("title", f"Level 1 Community {level1_db_id}")
    summary = parsed.get("summary", "")
    findings = json.dumps(parsed.get("findings", []), ensure_ascii=False)
    full_content = compressed_data

    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE communities
        SET title = ?, summary = ?, findings = ?, full_content = ?
        WHERE id = ? AND level = 1
        """,
        (title, summary, findings, full_content, level1_db_id),
    )
    conn.commit()

    logger.info(f"Updated Level 1 Community ID: {level1_db_id}")
    time.sleep(2)


def main():
    if not DB_PATH.exists():
        logger.error(f"Database not found: {DB_PATH}")
        return

    if not api_key:
        logger.error("Missing ANTHROPIC_API_KEY in .env")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        level0_map = get_level0_community_map(conn)
        if not level0_map:
            logger.error("No level 0 communities found. Run level 0 summaries first.")
            return

        parent_to_children = get_level1_children(conn)
        if not parent_to_children:
            logger.error("No level 1 hierarchy found. Run build_level1_communities.py first.")
            return

        logger.info(f"Level 1 communities to summarize: {len(parent_to_children)}")

        for level1_id, child_ids in parent_to_children.items():
            compressed_data = build_level1_input(level1_id, child_ids, level0_map)
            summarize_level1(conn, level1_id, compressed_data)

        logger.info("Level 1 summarization completed.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
