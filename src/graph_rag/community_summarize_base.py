import sqlite3
import json
import os
import time
import logging
import hashlib
from dotenv import load_dotenv
from src.utils.ai_gateway import AIGateway

# Load environment variables from .env file
load_dotenv()

# ANSI color codes
CLR_RESET = "\033[0m"
CLR_RED = "\033[31m"
CLR_GREEN = "\033[32m"
CLR_YELLOW = "\033[33m"
CLR_CYAN = "\033[36m"
CLR_BOLD = "\033[1m"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# Custom formatter to add colors to level names
class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.INFO: CLR_GREEN,
        logging.WARNING: CLR_YELLOW,
        logging.ERROR: CLR_RED,
        logging.DEBUG: CLR_CYAN
    }

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, CLR_RESET)
        record.levelname = f"{color}{record.levelname}{CLR_RESET}"
        return super().format(record)

# Update existing handler
for handler in logging.root.handlers:
    handler.setFormatter(ColorFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))

ai_gateway = AIGateway()


def get_strategic_summary(conn, community_id):
    """
    Lấy dữ liệu (Hulls, Factions, Skills) từ các Ship trong một community_id, 
    như cấu trúc trong file test.py.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT name, properties FROM nodes WHERE community_id = ? AND label = 'Ship'", (community_id,))
    
    ships = cursor.fetchall()
    if not ships:
        return None
        
    prompt_ready_text = f"=== COMMUNITY STRATEGIC ANALYSIS FOR COMMUNITY #{community_id} ===\n"
    
    for name, props in ships:
        # Load string properties into JSON
        try:
            data = json.loads(props)
        except json.JSONDecodeError:
            continue
            
        attr = data.get('attributes', {})
        hull = attr.get('hull', 'Unknown')
        faction = attr.get('faction', 'Unknown')
        
        prompt_ready_text += f"\n- {name} ({hull}, {faction}):\n"
        
        for skill in data.get('skills', []):
            stypes = ", ".join(skill.get('type', ['UNKNOWN']))
            cond = skill.get('edges', {}).get('AFFECTS', {}).get('condition', {})
            cond_str = f" | Trigger: {list(cond.keys())[0]}" if cond else ""
            
            prompt_ready_text += f"  + {skill.get('name')} [{stypes}]{cond_str}\n"
            
    return prompt_ready_text


def compute_community_content_hash(conn, community_id):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, properties
        FROM nodes
        WHERE community_id = ? AND label = 'Ship'
        ORDER BY id
        """,
        (community_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return None

    content = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def summarize_community(conn, community_id, force=False):
    content_hash = compute_community_content_hash(conn, community_id)
    if content_hash is None:
        logger.warning(f"Skip Community {community_id}: no ship data.")
        return False

    if not force:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content_hash FROM communities WHERE id = ? AND level = 0",
            (community_id,),
        )
        row = cursor.fetchone()
        if row and row[0] == content_hash:
            logger.info(f"Community {community_id}: SKIP (unchanged)")
            return False

    compressed_data = get_strategic_summary(conn, community_id)
    if not compressed_data:
        logger.warning(f"Skip Community {community_id}: no prompt data.")
        return False

    update_community_summary(conn, community_id, compressed_data, content_hash)
    return True


def update_community_summary(conn, community_id, compressed_data, content_hash=None):
    """
    Sử dụng LLM phân tích bản tóm tắt và cập nhật CSDL.
    """
    prompt = f"""You are a Naval Strategic Analyst specializing in the Azur Lane combat system. Your task is to analyze a specific "Knowledge Graph Community" consisting of ships and their technical attributes to generate a structured strategic report.

### Input Data:
{compressed_data}

### Instructions:
1. **Analyze Synergy:** Identify how the ships within this community interact based on their Factions, Hull Types, and Skill Types (e.g., how a [SLOW] skill from one ship supports a [BARRAGE] skill from another).
2. **Identify Patterns:** Determine the tactical "soul" of the group (e.g., Torpedo Vanguard, Anti-Air Umbrella, or Carrier-based Burst).
3. **Output Format:** You must respond with a **single, valid JSON object** only. Do not include any conversational text, markdown headers (outside the JSON), or explanations.

### JSON Schema:
{{
  "title": "A concise, thematic name for the community (e.g., 'Eagle Union Anti-Air Division')",
  "summary": "A 200-300 word technical analysis of the group’s combat logic, internal synergies, and meta-relevance. Explain WHY these ships are grouped together.",
  "findings": [
    "Insight 1: Core Combat Mechanics",
    "Insight 2: Strategic Strengths and Weaknesses",
    "Insight 3: Recommended Fleet Synergies",
    "Insight 4: Timing or Trigger dependencies"
  ]
}}
"""

    logger.info(f"Calling LLM for Community ID: {community_id}...")
    result = ai_gateway.chat_object(
        os.getenv("ANTHROPIC_BASE_MODEL") or os.getenv("LLM_MODEL") or "deepseek-ai/deepseek-v3.1",
        [
            {"role": "system", "content": "You are a helpful AI that returns ONLY valid JSON objects built from the provided schema."},
            {"role": "user", "content": prompt},
        ],
        max_retries=3,
        max_tokens=8192 * 2,
        temperature=0.1,
    )

    title = result.get('title', f"Community {community_id}")
    summary = result.get('summary', "")
    findings = json.dumps(result.get('findings', []), ensure_ascii=False)
    full_content = compressed_data

    cursor = conn.cursor()
    cursor.execute(
        '''
        UPDATE communities
        SET title = ?, summary = ?, findings = ?, full_content = ?, content_hash = ?
        WHERE id = ? AND level = 0
        ''',
        (title, summary, findings, full_content, content_hash, community_id),
    )
    conn.commit()

    logger.info("Successfully updated Community ID: %s", community_id)
    time.sleep(2)

def main():
    db_path = 'data/azur_lane_graph.db'
    if not os.path.exists(db_path):
        logger.error(f"Không tìm thấy Database tại {db_path}!")
        return
        
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("NVIDIA_API_KEY") or os.getenv("LLM_API_KEY")):
        logger.error("Không tìm thấy API key phù hợp trong file .env. Vui lòng thiết lập biến này để tiếp tục.")
        return
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Truy vấn danh sách community có trong CSDL
    cursor.execute("SELECT id FROM communities")
    community_ids = [row[0] for row in cursor.fetchall()]
    
    logger.info(f"Total communities found: {len(community_ids)}")
    
    for c_id in community_ids:
        if c_id == 0:
            continue  # Skip community_id = 0 

        summarize_community(conn, c_id, force=True)
            
    conn.close()
    logger.info("All communities processed.")

if __name__ == "__main__":
    main()
