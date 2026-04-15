import json
import logging
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path when running this file directly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.ai_gateway import AIGateway
from src.core.thinker_executor import ThinkerExecutor
from src.utils.prompts import DISPATCHER_PROMPT, THINKER_PROMPT, SYNTHESIZER_PROMPT

# Config logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class MainOrchestrator:
    def __init__(self):
        self.gateway = AIGateway()
        self.executor = ThinkerExecutor()

    def run_query(self, user_query: str):
        print(f"\n--- [USER QUERY]: {user_query} ---")

        # TẦNG 1: DISPATCH (Lập kế hoạch)
        logger.info("Phase 1: Dispatching Query...")
        plan_response = self.gateway.chat(
            model_id="glm_4.7_flash", 
            messages=[
                {"role": "system", "content": DISPATCHER_PROMPT},
                {"role": "user", "content": user_query}
            ]
        )
        
        # Parse Plan - Cải thiện logic bóc tách JSON (Robust Unwrapping)
        plan_raw = plan_response.get("raw") if "raw" in plan_response else plan_response
        
        # Nếu plan_raw là dict và có chứa trường 'content' (do Gateway gói lại)
        if isinstance(plan_raw, dict) and "content" in plan_raw:
            plan_content = plan_raw["content"]
            try:
                # Nếu content là string, thử parse nó
                if isinstance(plan_content, str):
                    start = plan_content.find("{")
                    end = plan_content.rfind("}") + 1
                    plan = json.loads(plan_content[start:end])
                else:
                    plan = plan_content
            except:
                plan = plan_raw
        else:
            plan = plan_raw

        # Đảm bảo plan luôn là dict và có đủ các trường tối thiểu
        if not isinstance(plan, dict):
            plan = {}
        
        intent = plan.get('intent', 'unknown')
        complexity = plan.get('complexity', 'medium')
        execution_plan = plan.get('execution_plan', {})
        thinker_model = execution_plan.get('thinker_model_required', 'qwq_32b')
        synth_config = plan.get('synthesizer_config', {'model': 'qwen3_30b_fp8'})

        logger.info(f"Plan received: {intent} (Complexity: {complexity})")

        # TẦNG 2: THINK (Suy luận chi tiết & Viết Query)
        logger.info(f"Phase 2: Thinking (using {thinker_model})...")
        
        thinker_response = self.gateway.chat(
            model_id=thinker_model,
            messages=[
                {"role": "system", "content": THINKER_PROMPT},
                {"role": "user", "content": f"Query: {user_query}\nPlan: {json.dumps(plan)}"}
            ]
        )
        
        # Parse Commands từ Thinker
        commands_json = thinker_response.get("content", "{}")
        if isinstance(commands_json, str):
            try:
                start = commands_json.find("{")
                end = commands_json.rfind("}") + 1
                commands = json.loads(commands_json[start:end])
            except:
                commands = {"commands": []}
        else:
            commands = commands_json

        # TẦNG 3: EXECUTE (Thực thi truy vấn local)
        logger.info("Phase 3: Executing Database Queries...")
        all_findings = []
        for cmd in commands.get("commands", []):
            cmd_type = cmd.get("type")
            cmd_str = cmd.get("cmd")
            
            logger.info(f"  Executing {cmd_type}: {cmd_str}")
            if cmd_type == "sql":
                data = self.executor.execute_sql(cmd_str)
                all_findings.append({"source": "sql", "data": data})
            elif cmd_type == "vector":
                data = self.executor.execute_vector(cmd_str)
                all_findings.append({"source": "vector", "data": data})
        
        # TẦNG 4: SYNTHESIZE (Tổng hợp câu trả lời)
        logger.info(f"Phase 4: Synthesizing Answer (using {plan['synthesizer_config']['model']})...")
        synth_model = plan['synthesizer_config']['model']
        
        final_response = self.gateway.chat(
            model_id=synth_model,
            messages=[
                {"role": "system", "content": SYNTHESIZER_PROMPT},
                {"role": "user", "content": f"Question: {user_query}\nFindings: {json.dumps(all_findings)}"}
            ],
            role="synthesize"
        )

        print("\n--- [FINAL RESPONSE] ---")
        print(final_response.get("content"))
        print("------------------------\n")

if __name__ == "__main__":
    orchestrator = MainOrchestrator()
    # Test query
    orchestrator.run_query("So sánh khả năng phòng không của Moskva và San Diego.")
