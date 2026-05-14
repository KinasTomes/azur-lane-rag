import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class AIGateway:
    """
    Smart router for multiple AI providers.
    """

    NVIDIA_MODELS = {
        "qwen3.5_397b": "qwen/qwen3.5-397b-a17b",
        "qwen3_5_397b": "qwen/qwen3.5-397b-a17b",
        "ds_r1_qwen_32b": "deepseek-ai/deepseek-r1-distill-qwen-32b",
        "qwen3_80b_thinking": "qwen/qwen3-next-80b-a3b-thinking",
        "nemotron_super": "nvidia/nemotron-3-super-120b-a12b",
        "qwen2.5_7b": "qwen/qwen2.5-7b-instruct",
        "deepseek_v3.2": "deepseek-ai/deepseek-v4-pro",
        "deepseek_v4_pro": "deepseek-ai/deepseek-v4-pro",
        "deepseek-v4-pro": "deepseek-ai/deepseek-v4-pro",
        "minimax_m2.7": "minimaxai/minimax-m2.7",
    }

    XIAOMI_MODELS = {
        "mimo_v2_5_pro": os.getenv("XIAOMI_MODEL", "MiMo-V2.5-Pro"),
        "mimo_v2_5": os.getenv("XIAOMI_FALLBACK_MODEL", "MiMo-V2.5"),
    }

    CF_MODELS = {
        "qwq_32b": "@cf/baai/qwq-32b",
        "qwen3_30b_fp8": "@cf/qwen/qwen3-30b-a3b-fp8",
        "glm_4.7_flash": "@cf/zai-org/glm-4.7-flash",
        "glm_4.7_flash_cf": "@cf/zai-org/glm-4.7-flash",
    }

    def __init__(self):
        self.nvidia_client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=os.getenv("NVIDIA_API_KEY"),
            max_retries=3,
        )
        self.xiaomi_client = OpenAI(
            base_url=os.getenv("XIAOMI_BASE_URL"),
            api_key=os.getenv("XIAOMI_API_KEY"),
            max_retries=3,
        )
        legacy_api_key = os.getenv("LLM_API_KEY") or os.getenv("NVIDIA_API_KEY")
        legacy_base_url = os.getenv("LLM_BASE_URL") or "https://integrate.api.nvidia.com/v1"
        self.legacy_client = None
        if legacy_api_key:
            self.legacy_client = OpenAI(
                base_url=legacy_base_url,
                api_key=legacy_api_key,
                max_retries=3,
            )
        self.cf_gateway_url = os.getenv("CF_GATEWAY_URL", "http://127.0.0.1:8787")

    def embeddings(self, texts: List[str], model: str = "bge-m3") -> List[List[float]]:
        try:
            payload = {
                "model": model,
                "text": texts if len(texts) > 1 else texts[0],
            }
            response = requests.post(self.cf_gateway_url, json=payload)
            response.raise_for_status()
            data = response.json()
            if "data" in data:
                return data["data"] if isinstance(data["data"][0], list) else [data["data"]]
            return data.get("result", {}).get("data", [])
        except Exception as e:
            logger.error(f"Cloudflare Worker embeddings failed: {e}")
            raise

    def chat(self, model_id: str, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        logger.info(f"Routing request for model: {model_id}")

        if model_id in self.NVIDIA_MODELS:
            return self._call_openai_compatible(
                client=self.nvidia_client,
                model=self.NVIDIA_MODELS[model_id],
                messages=messages,
                **kwargs,
            )

        if model_id in self.XIAOMI_MODELS:
            return self._call_openai_compatible(
                client=self.xiaomi_client,
                model=self.XIAOMI_MODELS[model_id],
                messages=messages,
                **kwargs,
            )

        if model_id in self.CF_MODELS or model_id.startswith("@cf/"):
            return self._call_cloudflare(model_id, messages, **kwargs)

        if self.legacy_client is not None:
            return self._call_openai_compatible(
                client=self.legacy_client,
                model=model_id,
                messages=messages,
                **kwargs,
            )

        raise ValueError(f"Unknown model_id: {model_id}. Check mappings in AIGateway.")

    def chat_json(
        self,
        model_id: str,
        messages: List[Dict[str, str]],
        expect: str = "object",
        max_retries: int = 3,
        **kwargs,
    ) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.chat(model_id, messages, **kwargs)
                content = response.get("content") or ""
                parsed = self._parse_json_response(content, expect=expect)
                return parsed
            except Exception as error:
                last_error = error
                if attempt < max_retries:
                    logger.warning(
                        "Retrying parsed JSON call (%s/%s) for model %s due to: %s",
                        attempt,
                        max_retries,
                        model_id,
                        error,
                    )
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise

        raise RuntimeError(f"Failed after {max_retries} JSON parsing attempts.") from last_error

    def chat_object(
        self,
        model_id: str,
        messages: List[Dict[str, str]],
        max_retries: int = 3,
        **kwargs,
    ) -> Dict[str, Any]:
        result = self.chat_json(model_id, messages, expect="object", max_retries=max_retries, **kwargs)
        if not isinstance(result, dict):
            raise ValueError(f"Expected JSON object from model {model_id}, got {type(result).__name__}")
        return result

    def chat_array(
        self,
        model_id: str,
        messages: List[Dict[str, str]],
        max_retries: int = 3,
        **kwargs,
    ) -> List[Any]:
        result = self.chat_json(model_id, messages, expect="array", max_retries=max_retries, **kwargs)
        if not isinstance(result, list):
            raise ValueError(f"Expected JSON array from model {model_id}, got {type(result).__name__}")
        return result

    def _call_openai_compatible(self, client: OpenAI, model: str, messages: List[Dict], **kwargs) -> Dict:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 4096),
                stream=False,
            )
            return {
                "content": response.choices[0].message.content,
                "model": model,
                "usage": response.usage.dict() if hasattr(response, "usage") else {},
            }
        except Exception as e:
            logger.error(f"OpenAI-Compatible Call Failed ({model}): {e}")
            raise

    def _call_cloudflare(self, model_id: str, messages: List[Dict], **kwargs) -> Dict:
        payload = {
            "model": model_id,
            "messages": messages,
            "query": kwargs.get("query"),
        }
        try:
            response = requests.post(self.cf_gateway_url, json=payload)
            response.raise_for_status()
            res_json = response.json()

            if "intent" in res_json:
                return {
                    "raw": res_json,
                    "content": json.dumps(res_json),
                    "model": model_id,
                    "provider": "cloudflare",
                }

            return {
                "content": res_json.get("raw") or res_json.get("content") or json.dumps(res_json),
                "raw": res_json,
                "model": model_id,
                "provider": "cloudflare",
            }
        except Exception as e:
            logger.error(f"Cloudflare Gateway Call Failed: {e}")
            raise

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
                    return text[start_index : index + 1]

        return None

    def _iter_json_candidates(self, response_text: str):
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

    def _unwrap_json_payload(self, parsed_data: Any, expect: str) -> Any:
        if expect == "array":
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
                    break
            return current

        return parsed_data

    def _parse_json_response(self, response_text: str, expect: str = "object") -> Any:
        if expect not in {"object", "array"}:
            raise ValueError(f"Unsupported JSON expectation: {expect}")

        last_error: Optional[Exception] = None
        for candidate in self._iter_json_candidates(response_text):
            try:
                parsed_data = json.loads(candidate)
                parsed_data = self._unwrap_json_payload(parsed_data, expect)
                if expect == "object" and not isinstance(parsed_data, dict):
                    raise ValueError(f"Expected JSON object, got {type(parsed_data).__name__}")
                if expect == "array" and not isinstance(parsed_data, list):
                    raise ValueError(f"Expected JSON array, got {type(parsed_data).__name__}")
                return parsed_data
            except (json.JSONDecodeError, ValueError) as error:
                last_error = error

        if last_error:
            raise ValueError(f"Invalid JSON from model: {last_error}")
        raise ValueError("No valid JSON object found in model response")


if __name__ == "__main__":
    gateway = AIGateway()
    test_messages = [{"role": "user", "content": "Hello, who are you?"}]
    print(gateway.chat("glm_4.7_flash", test_messages))
