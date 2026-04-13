import sqlite3
import json
import os
import time
import logging
import re

try:
    from openai import OpenAI
    from dotenv import load_dotenv
except ImportError:
    print("Vui lòng cài đặt thư viện cần thiết: pip install openai python-dotenv")
    exit(1)

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

# Khởi tạo OpenAI client (Sử dụng cấu hình từ .env)
api_key = os.getenv("ANTHROPIC_API_KEY")
base_url = os.getenv("ANTHROPIC_BASE_URL")
model_name = os.getenv("ANTHROPIC_BASE_MODEL")

client = OpenAI(
    api_key=api_key,
    base_url=base_url
)


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
                return text[start_index:index + 1]

    return None


def parse_llm_json_object(response_text):
    candidates = []

    # 1) JSON trong code fence ```json ... ```
    fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", response_text, flags=re.DOTALL | re.IGNORECASE)
    for block in fenced_blocks:
        block = block.strip()
        if block:
            candidates.append(block)

    # 2) Toàn bộ nội dung sau khi strip
    stripped = response_text.strip()
    if stripped:
        candidates.append(stripped)

    # 3) Tìm fragment JSON cân bằng đầu tiên
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

def update_community_summary(conn, community_id, compressed_data):
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
    try:
        response = client.chat.completions.create(
            model=model_name, # Sử dụng model từ biến môi trường
            messages=[
                {"role": "system", "content": "You are a helpful AI that returns ONLY valid JSON objects built from the provided schema."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=8192 * 2,
            temperature=0.1
        )
        
        content = response.choices[0].message.content or ""
        result = parse_llm_json_object(content)
        
        # Gán kết quả trả về
        title = result.get('title', f"Community {community_id}")
        summary = result.get('summary', "")
        # Chuyển mảng findings về dạng chuỗi JSON cho DB
        findings = json.dumps(result.get('findings', []), ensure_ascii=False)
        full_content = compressed_data
        
        # Cập nhật thông tin vào DB
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE communities 
            SET title = ?, summary = ?, findings = ?, full_content = ?
            WHERE id = ?
        ''', (title, summary, findings, full_content, community_id))
        conn.commit()
        
        logger.info(f"Successfully updated Community ID: {community_id}")
        time.sleep(2)  # Sleep 2 giây giữa các request
        
    except Exception as e:
        logger.error(f"Failed to process Community ID {community_id}. Error: {e}")

def main():
    db_path = 'data/azur_lane_graph.db'
    if not os.path.exists(db_path):
        logger.error(f"Không tìm thấy Database tại {db_path}!")
        return
        
    # Kiểm tra Key Anthropic
    if not api_key:
        logger.error("Không tìm thấy ANTHROPIC_API_KEY trong file .env. Vui lòng thiết lập biến này để tiếp tục.")
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

        # 1. Trích xuất text nén từ DB
        compressed_data = get_strategic_summary(conn, c_id)
        
        # 2. Gửi cho LLM và lưu kết quả lại
        if compressed_data:
            update_community_summary(conn, c_id, compressed_data)
        else:
            logger.warning(f"Skip Community {c_id}: Không tìm thấy thông tin Ship hợp lệ.")
            
    conn.close()
    logger.info("All communities processed.")

if __name__ == "__main__":
    main()
