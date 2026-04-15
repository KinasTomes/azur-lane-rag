import os
import json
import logging
import requests
from openai import OpenAI
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class AIGateway:
    """
    Smart Router for multiple AI Providers (NVIDIA NIM, Cloudflare, Third-Party).
    Handles model mapping, provider-specific protocols, and fallbacks.
    """

    # Model Mappings based on temp.md + User Updates
    NVIDIA_MODELS = {
        "qwen3.5_397b": "qwen/qwen3.5-397b-a17b",
        "ds_r1_qwen_32b": "deepseek-ai/deepseek-r1-distill-qwen-32b",
        "qwen3_80b_thinking": "qwen/qwen3-next-80b-a3b-thinking",
        "nemotron_super": "nvidia/nemotron-3-super-120b-a12b",
        "qwen2.5_7b": "qwen/qwen2.5-7b-instruct",
        "deepseek_v3.2": "deepseek-ai/deepseek-v3.2",
        "minimax_m2.7": "minimaxai/minimax-m2.7",
        "kimi_k2_thinking": "moonshotai/kimi-k2-thinking",
        "glm_4.7": "z-ai/glm-4.7",
        "kimi_k2.5": "moonshotai/kimi-k2.5"
    }

    THIRD_PARTY_MODELS = {
        "qwen3_235b": "qwen-3-235b-a22b-instruct-2507"
    }

    CF_MODELS = {
        "qwq_32b": "@cf/baai/qwq-32b",
        "qwen3_30b_fp8": "@cf/qwen/qwen3-30b-a3b-fp8",
        "glm_4.7_flash": "@cf/zai-org/glm-4.7-flash",
        "glm_4.7_flash_cf": "@cf/zai-org/glm-4.7-flash"
    }

    def __init__(self):
        # Initialize OpenAI clients with NO auto-retries to handle concurrency better
        self.nvidia_client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=os.getenv("NVIDIA_API_KEY"),
            max_retries=0 
        )
        self.tp_client = OpenAI(
            base_url=os.getenv("THIRD_PARTY_BASE_URL"),
            api_key=os.getenv("THIRD_PARTY_API_KEY"),
            max_retries=0
        )
        self.cf_gateway_url = os.getenv("CF_GATEWAY_URL", "http://127.0.0.1:8787")

    def embeddings(self, texts: List[str], model: str = "baai/bge-m3") -> List[List[float]]:
        """
        Fetch embeddings from NVIDIA API.
        """
        try:
            response = self.nvidia_client.embeddings.create(
                input=texts,
                model=model,
                encoding_format="float",
                extra_body={"truncate": "NONE"}
            )
            return [data.embedding for data in response.data]
        except Exception as e:
            logger.error(f"NVIDIA Embedding Call Failed: {e}")
            raise

    def chat(self, model_id: str, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """
        Unified chat interface with automatic fallback logic.
        """
        logger.info(f"Routing request for model: {model_id}")

        # 1. SPECIAL CASE: Kimi K2.5
        if model_id == "kimi_k2.5":
            return self._call_kimi(messages, **kwargs)


        # 2. NVIDIA Provider
        if model_id in self.NVIDIA_MODELS:
            return self._call_openai_compatible(
                client=self.nvidia_client,
                model=self.NVIDIA_MODELS[model_id],
                messages=messages,
                **kwargs
            )

        # 3. Third Party Provider
        if model_id in self.THIRD_PARTY_MODELS:
            return self._call_openai_compatible(
                client=self.tp_client,
                model=self.THIRD_PARTY_MODELS[model_id],
                messages=messages,
                **kwargs
            )

        # 4. Cloudflare Provider (Calling our Worker)
        if model_id in self.CF_MODELS or model_id.startswith("@cf/"):
            return self._call_cloudflare(model_id, messages, **kwargs)

        raise ValueError(f"Unknown model_id: {model_id}. Check mappings in AIGateway.")

    def _call_openai_compatible(self, client: OpenAI, model: str, messages: List[Dict], **kwargs) -> Dict:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 4096),
                stream=False
            )
            return {
                "content": response.choices[0].message.content,
                "model": model,
                "usage": response.usage.dict() if hasattr(response, 'usage') else {}
            }
        except Exception as e:
            logger.error(f"OpenAI-Compatible Call Failed ({model}): {e}")
            raise

    def _call_kimi(self, messages: List[Dict], **kwargs) -> Dict:
        """Specific implementation for Kimi K2.5 using requests."""
        invoke_url = os.getenv("NVIDIA_BASE_URL") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {os.getenv('NVIDIA_API_KEY')}",
            "Accept": "application/json"
        }
        payload = {
            "model": self.NVIDIA_MODELS["kimi_k2.5"],
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 16384),
            "temperature": kwargs.get("temperature", 1.0),
            "top_p": 1.0,
            "stream": False,
            "chat_template_kwargs": {"thinking": True},
        }
        try:
            response = requests.post(invoke_url, headers=headers, json=payload)
            response.raise_for_status()
            res_json = response.json()
            return {
                "content": res_json["choices"][0]["message"]["content"],
                "model": "kimi-k2.5",
                "raw": res_json
            }
        except Exception as e:
            logger.error(f"Kimi K2.5 Call Failed: {e}")
            raise

    def _call_cloudflare(self, model_id: str, messages: List[Dict], **kwargs) -> Dict:
        """Calls the Cloudflare Worker Gateway."""
        payload = {
            "model": model_id, # Đổi thành 'model' để khớp với Worker
            "messages": messages,
            "query": kwargs.get("query")
        }
        try:
            response = requests.post(self.cf_gateway_url, json=payload)
            response.raise_for_status()
            res_json = response.json()
            
            # Nếu kết quả từ Worker đã là JSON của Plan (có intent)
            if "intent" in res_json:
                return {
                    "raw": res_json, # Giữ nguyên object gốc
                    "content": json.dumps(res_json),
                    "model": model_id,
                    "provider": "cloudflare"
                }
            
            return {
                "content": res_json.get("raw") or res_json.get("content") or json.dumps(res_json),
                "raw": res_json,
                "model": model_id,
                "provider": "cloudflare"
            }
        except Exception as e:
            logger.error(f"Cloudflare Gateway Call Failed: {e}")
            raise

if __name__ == "__main__":
    # Quick Test
    gateway = AIGateway()
    test_messages = [{"role": "user", "content": "Hello, who are you?"}]
    
    # Test NVIDIA (DeepSeek)
    # print(gateway.chat("deepseek_v3.2", test_messages))
    
    # Test Third Party
    print(gateway.chat("glm_4.7_flash", test_messages))
    
    # Test Kimi
    # print(gateway.chat("kimi_k2.5", test_messages))
