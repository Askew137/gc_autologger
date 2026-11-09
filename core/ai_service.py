import json
import requests
from typing import Tuple, Optional
from core.config import AiConfig


class AiService:
    """
    Handles AI answer generation for EarthCache and Virtual Cache questions.
    Supports OpenAI, MetaCentrum (CESNET), custom OpenAI-compatible endpoints (vLLM, Ollama),
    and Google Gemini.
    """

    @staticmethod
    def generate_answers(
        config: AiConfig,
        listing: str,
        note: str,
        gc_code: str = "",
        cache_name: str = ""
    ) -> Tuple[bool, str]:
        """
        Generate concise answers from cache listing and player note using the configured AI provider.
        Returns: (success: bool, text_or_error: str)
        """
        if not config.api_key:
            return False, "AI API key is missing. Please configure it in Settings."

        clean_listing = listing[:15000].strip() if listing else ""
        clean_note = note.strip() if note else "(žádná poznámka hráče)"

        user_content = (
            f"Geocache: {gc_code} - {cache_name}\n\n"
            f"Listing keše:\n{clean_listing}\n\n"
            f"Poznámka hráče z terénu:\n{clean_note}\n\n"
            f"Úkol: Vypracuj velmi stručné, věcné odpovědi na otázky z listingu. "
            f"Formátuj pouze jako číslovaný seznam: 1) ... 2) ... 3) ... Bez zdvořilostních frází."
        )

        return AiService._call_provider(config, user_content, timeout=120)

    @staticmethod
    def test_connection(config: AiConfig) -> Tuple[bool, str]:
        """
        Send a lightweight ping to verify that the API key and endpoint are working.
        Returns: (success: bool, message: str)
        """
        if not config.api_key:
            return False, "API key cannot be empty."

        test_prompt = "Odpověz jedním slovem: OK"
        ok, res = AiService._call_provider(config, test_prompt, timeout=30)
        if ok:
            clean_res = res.replace("\n", " ").strip()
            return True, f"Connection successful! Response: '{clean_res[:60]}'"
        return False, res

    @staticmethod
    def _call_provider(config: AiConfig, user_content: str, timeout: int = 120) -> Tuple[bool, str]:
        provider = (config.provider or "openai").lower().strip()

        try:
            if provider == "gemini":
                model_name = config.model.strip() or "gemini-2.5-flash"
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={config.api_key.strip()}"
                
                budget = getattr(config, "thinking_budget", 1024)
                gen_config = {"temperature": 0.2}
                if budget is not None and budget >= 0:
                    gen_config["thinkingConfig"] = {"thinkingBudget": int(budget)}

                payload = {
                    "systemInstruction": {
                        "parts": [{"text": config.system_prompt}]
                    },
                    "contents": [{
                        "parts": [{"text": user_content}]
                    }],
                    "generationConfig": gen_config
                }
                resp = requests.post(url, json=payload, timeout=timeout)
                if resp.status_code != 200:
                    # If model doesn't support thinkingConfig, gracefully retry without it
                    if "thinking" in resp.text.lower() and "thinkingConfig" in gen_config:
                        del gen_config["thinkingConfig"]
                        resp = requests.post(url, json=payload, timeout=timeout)

                if resp.status_code != 200:
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("error", {}).get("message", resp.text[:200])
                    except Exception:
                        err_msg = resp.text[:200]
                    return False, f"Gemini API error (HTTP {resp.status_code}): {err_msg}"

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return False, "Gemini returned no response candidates."
                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    return False, "Gemini returned empty parts."
                text = str(parts[0].get("text", "") or "").strip()
                return True, text

            else:
                # OpenAI / MetaCentrum / Custom OpenAI-compatible endpoint
                base = config.base_url.strip() if config.base_url else ""
                if base:
                    if base.endswith("/chat/completions"):
                        endpoint = base
                    else:
                        endpoint = f"{base.rstrip('/')}/chat/completions"
                else:
                    endpoint = "https://api.openai.com/v1/chat/completions"

                model_name = config.model.strip() or "gpt-4o-mini"
                headers = {
                    "Authorization": f"Bearer {config.api_key.strip()}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": config.system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1500
                }
                budget = getattr(config, "thinking_budget", 1024)
                if any(model_name.lower().startswith(p) for p in ("o1", "o3", "o4")):
                    if budget <= 0:
                        payload["reasoning_effort"] = "low"
                    elif budget > 2048:
                        payload["reasoning_effort"] = "high"
                    else:
                        payload["reasoning_effort"] = "medium"

                resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
                if resp.status_code != 200:
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("error", {}).get("message", resp.text[:200])
                    except Exception:
                        err_msg = resp.text[:200]
                    return False, f"AI API error (HTTP {resp.status_code}): {err_msg}"

                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    return False, "AI returned no choices."
                msg_obj = choices[0].get("message", {})
                raw_text = msg_obj.get("content") or msg_obj.get("reasoning_content") or ""
                text = str(raw_text).strip()
                return True, text

        except requests.exceptions.Timeout:
            return False, f"AI request timed out after {timeout} seconds."
        except requests.exceptions.ConnectionError as ce:
            return False, f"Connection error reaching AI service: {ce}"
        except Exception as e:
            return False, f"Error calling AI API: {e}"
