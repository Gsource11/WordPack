from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from .config import AppConfig


class OfflineDictionaryTranslator:
    def __init__(self, dict_path: Path) -> None:
        with dict_path.open("r", encoding="utf-8-sig") as f:
            payload = json.load(f)
        self.en_to_zh: dict[str, str] = payload.get("en_to_zh", {})
        self.zh_to_en: dict[str, str] = payload.get("zh_to_en", {})

    @staticmethod
    def _contains_chinese(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def translate(self, text: str) -> str:
        normalized = text.strip()
        if not normalized:
            return ""

        key = normalized.lower()
        if key in self.en_to_zh:
            return self.en_to_zh[key]
        if normalized in self.zh_to_en:
            return self.zh_to_en[normalized]

        if self._contains_chinese(normalized):
            chars = [token for token in re.split(r"([，。！？；：、\s])", normalized) if token]
            translated = [self.zh_to_en.get(token, token) for token in chars]
            result = "".join(translated).strip()
            if result and result != normalized:
                return result
            return "[离线词典暂无该中文词条]"

        words = re.findall(r"[a-zA-Z']+|[^a-zA-Z']+", key)
        chunks: list[str] = []
        translated_any = False
        for word in words:
            if re.fullmatch(r"[a-zA-Z']+", word):
                translated = self.en_to_zh.get(word)
                if translated:
                    translated_any = True
                    chunks.append(translated)
                else:
                    chunks.append(word)
            else:
                chunks.append(word)
        result = "".join(chunks).strip()
        if translated_any:
            return result
        return "[离线词典暂无结果，可切换 AI 模式]"


class ArgosOfflineTranslator:
    """Argos Translate backend with install/list/select capabilities."""

    def __init__(self) -> None:
        self._translate_mod = None
        self._package_mod = None
        self._init_error = ""
        try:
            import argostranslate.package as argos_package  # type: ignore
            import argostranslate.translate as argos_translate  # type: ignore

            self._translate_mod = argos_translate
            self._package_mod = argos_package
        except Exception as exc:
            self._translate_mod = None
            self._package_mod = None
            self._init_error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _contains_chinese(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    def _split_text(self, text: str, max_chunk: int = 700) -> list[str]:
        if len(text) <= max_chunk:
            return [text]
        parts = re.split(r"([。！？!?；;\n])", text)
        chunks: list[str] = []
        current = ""
        for part in parts:
            if not part:
                continue
            if len(current) + len(part) > max_chunk and current:
                chunks.append(current)
                current = part
            else:
                current += part
        if current:
            chunks.append(current)
        return chunks

    def _get_languages(self) -> list[Any]:
        if self._translate_mod is None:
            return []
        try:
            return list(self._translate_mod.get_installed_languages())
        except Exception:
            return []

    def _find_language(self, code: str):
        languages = self._get_languages()
        exact = next((lang for lang in languages if str(getattr(lang, "code", "")).lower() == code.lower()), None)
        if exact:
            return exact
        return next((lang for lang in languages if str(getattr(lang, "code", "")).lower().startswith(code.lower())), None)

    def _find_translation(self, source_code: str, target_code: str):
        src = self._find_language(source_code)
        tgt = self._find_language(target_code)
        if not src or not tgt:
            return None
        try:
            return src.get_translation(tgt)
        except Exception:
            return None

    def list_directions(self) -> list[dict[str, str]]:
        languages = self._get_languages()
        items: list[dict[str, str]] = []
        seen: set[str] = set()

        for src in languages:
            src_code = str(getattr(src, "code", "")).strip()
            src_name = str(getattr(src, "name", src_code)).strip() or src_code
            if not src_code:
                continue
            for tgt in languages:
                tgt_code = str(getattr(tgt, "code", "")).strip()
                tgt_name = str(getattr(tgt, "name", tgt_code)).strip() or tgt_code
                if not tgt_code or src_code == tgt_code:
                    continue
                try:
                    translation = src.get_translation(tgt)
                except Exception:
                    continue
                if translation is None:
                    continue

                direction = f"{src_code}->{tgt_code}"
                if direction in seen:
                    continue
                seen.add(direction)
                items.append(
                    {
                        "direction": direction,
                        "source_code": src_code,
                        "target_code": tgt_code,
                        "label": f"{src_name}({src_code}) -> {tgt_name}({tgt_code})",
                    }
                )

        items.sort(key=lambda x: x["direction"])
        return items

    def runtime_ready(self) -> bool:
        return self._translate_mod is not None and self._package_mod is not None

    def init_error(self) -> str:
        return self._init_error.strip()

    def is_available(self) -> bool:
        return len(self.list_directions()) > 0

    def install_model_file(self, model_path: str) -> str:
        if self._package_mod is None:
            raise RuntimeError("离线模型功能不可用：当前构建未包含 argostranslate 运行库。请使用发布版 exe。")

        path = Path(model_path)
        if not path.exists() or not path.is_file():
            raise RuntimeError("模型文件不存在")

        self._package_mod.install_from_path(str(path))
        count = len(self.list_directions())
        return f"模型导入成功，当前可用离线方向: {count}"

    @staticmethod
    def _basic_split_sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[。！？!?;；\.])\s+", text)
        cleaned = [p.strip() for p in parts if p and p.strip()]
        return cleaned if cleaned else [text]

    def _attach_offline_sentencizer(self, translation_obj: Any) -> None:
        class _OfflineSentencizer:
            def split_sentences(self, text: str):
                return ArgosOfflineTranslator._basic_split_sentences(text)

        package_translation = translation_obj
        underlying = getattr(translation_obj, "underlying", None)
        if underlying is not None:
            package_translation = underlying

        if hasattr(package_translation, "sentencizer"):
            package_translation.sentencizer = _OfflineSentencizer()

    def _select_translation(self, text: str, preferred_direction: str):
        selected = None

        if preferred_direction and preferred_direction != "auto":
            parts = preferred_direction.split("->")
            if len(parts) == 2:
                selected = self._find_translation(parts[0].strip(), parts[1].strip())
                if selected is None:
                    raise RuntimeError(f"所选离线模型方向不可用: {preferred_direction}")

        if selected is None:
            source_code = "zh" if self._contains_chinese(text) else "en"
            target_code = "en" if source_code == "zh" else "zh"
            selected = self._find_translation(source_code, target_code)

        if selected is None:
            raise RuntimeError(f"未找到可用离线模型方向: {source_code}->{target_code}")

        self._attach_offline_sentencizer(selected)
        return selected

    def translate(self, text: str, preferred_direction: str = "auto") -> str:
        if self._translate_mod is None:
            raise RuntimeError("未安装 argostranslate")

        translation = self._select_translation(text, preferred_direction)
        if translation is None:
            raise RuntimeError("未找到可用离线翻译模型")

        chunks = self._split_text(text)
        translated_chunks: list[str] = []
        for chunk in chunks:
            if not chunk.strip():
                translated_chunks.append(chunk)
            else:
                translated_chunks.append(translation.translate(chunk))
        return "".join(translated_chunks).strip()


class OfflineTranslator:
    def __init__(self, dict_path: Path, cfg_getter) -> None:
        self.cfg_getter = cfg_getter
        self.dictionary = OfflineDictionaryTranslator(dict_path)
        self.argos = ArgosOfflineTranslator()

    def _preferred_direction(self) -> str:
        cfg: AppConfig = self.cfg_getter()
        value = str(getattr(cfg.offline, "preferred_direction", "auto") or "auto").strip()
        return value or "auto"

    def list_models(self) -> list[dict[str, str]]:
        return self.argos.list_directions()

    def import_model_file(self, model_path: str) -> str:
        return self.argos.install_model_file(model_path)

    def runtime_ready(self) -> bool:
        return self.argos.runtime_ready()

    def runtime_hint(self) -> str:
        if self.runtime_ready():
            return "离线模型运行库已就绪"

        exe = sys.executable
        err = self.argos.init_error()
        suffix = f"；底层异常: {err}" if err else ""
        return (
            "当前解释器未加载到 argostranslate。"
            f"当前解释器: {exe}。"
            "请用同一解释器执行安装命令: `\"<python>\" -m pip install -r requirements.txt`。"
            f"{suffix}"
        )

    def diagnostics(self) -> str:
        preferred = self._preferred_direction()
        items = self.argos.list_directions()

        if not self.runtime_ready():
            return "离线模式: Argos 运行库缺失，当前仅词典兜底（发布版会内置）。"

        if not items:
            return "离线模式: 未检测到 Argos 离线模型，请导入 .argosmodel 文件。"

        if preferred != "auto":
            if any(item["direction"] == preferred for item in items):
                return f"离线模式: Argos 已启用，当前固定方向 {preferred}"
            return f"离线模式: 已配置 {preferred}，但该方向模型不可用，当前将自动匹配。"

        return "离线模式: Argos 已启用（自动匹配方向）"

    def translate(self, text: str) -> str:
        normalized = text.strip()
        if not normalized:
            return ""

        if self.runtime_ready():
            items = self.argos.list_directions()
            if items:
                try:
                    return self.argos.translate(normalized, self._preferred_direction())
                except Exception as exc:
                    return f"[离线模型翻译失败: {exc}]"

        if len(normalized) >= 80:
            return "[当前未检测到离线神经翻译模型，长文本离线翻译不可用。请导入 Argos 模型或切换 AI 模式]"

        return self.dictionary.translate(normalized)


class OpenAICompatibleTranslator:
    def __init__(self, cfg_getter) -> None:
        self.cfg_getter = cfg_getter

    @staticmethod
    def _join_url(base_url: str, suffix: str) -> str:
        return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"

    @staticmethod
    def _is_local_base_url(base_url: str) -> bool:
        parsed = parse.urlparse(base_url)
        host = (parsed.hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1"}

    def _build_headers(self, api_key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _is_timeout_text(message: str) -> bool:
        lowered = message.lower()
        return "timed out" in lowered or "timeout" in lowered or "请求超时" in lowered

    @staticmethod
    def _generation_timeouts(base_timeout: int) -> list[int]:
        first = max(60, base_timeout)
        second = max(first + 45, 120)
        return [first, second]

    @staticmethod
    def _extract_answer_from_reasoning(raw_reasoning: str) -> str:
        text = raw_reasoning.strip()
        if not text:
            return ""

        marker_patterns = [
            r"(?:Final Output|Final Answer|Output|答案|最终输出|翻译结果)\s*[:：]\s*(.+)$",
        ]
        for pattern in marker_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                candidate = match.group(1).strip()
                if candidate:
                    return candidate.splitlines()[0].strip()

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""
        return lines[-1]

    def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout_sec: int) -> dict[str, Any]:
        req = request.Request(
            url=url,
            method="POST",
            headers=headers,
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            with request.urlopen(req, timeout=timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                return json.loads(raw)
        except TimeoutError as exc:
            raise RuntimeError(f"请求超时: {exc}") from exc
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code}: {body[:240] or exc.reason}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"网络错误: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"请求失败: {exc}") from exc

    def _get_json(self, url: str, headers: dict[str, str], timeout_sec: int) -> dict[str, Any]:
        req = request.Request(url=url, method="GET", headers=headers)
        try:
            with request.urlopen(req, timeout=timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                return json.loads(raw)
        except TimeoutError as exc:
            raise RuntimeError(f"请求超时: {exc}") from exc
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code}: {body[:240] or exc.reason}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"网络错误: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"请求失败: {exc}") from exc

    def _extract_openai_content(self, data: dict[str, Any]) -> str:
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("AI 响应格式异常：缺少 choices")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            text_chunks: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_chunks.append(str(item.get("text", "")))
            content = "".join(text_chunks)

        result = str(content).strip()
        if result:
            return result

        reasoning = str(message.get("reasoning_content", "")).strip()
        fallback = self._extract_answer_from_reasoning(reasoning)
        if fallback:
            return fallback

        raise RuntimeError("AI 响应为空")

    def _chat_openai_compatible(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        timeout_sec: int,
        max_tokens: int = 600,
    ) -> str:
        v1_root = base_url.rstrip("/")
        if not v1_root.endswith("/v1"):
            v1_root = self._join_url(v1_root, "v1")

        url = self._join_url(v1_root, "chat/completions")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._is_local_base_url(base_url):
            payload["think"] = False

        headers = self._build_headers(api_key)
        data = self._post_json(url, payload, headers, timeout_sec)
        return self._extract_openai_content(data)

    def _chat_ollama_native(
        self,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        timeout_sec: int,
        num_predict: int = 600,
    ) -> str:
        native_base = base_url[:-3] if base_url.endswith("/v1") else base_url
        url = self._join_url(native_base, "api/chat")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        headers = {"Content-Type": "application/json"}
        data = self._post_json(url, payload, headers, timeout_sec)

        message = data.get("message", {})
        content = str(message.get("content", "")).strip()
        if content:
            return content

        reasoning = str(message.get("thinking", "")).strip()
        fallback = self._extract_answer_from_reasoning(reasoning)
        if fallback:
            return fallback

        raise RuntimeError("Ollama 响应为空")

    def _chat(self, messages: list[dict[str, str]], temperature: float = 0.2, purpose: str = "translate") -> str:
        cfg: AppConfig = self.cfg_getter()
        base_url = cfg.openai.base_url.strip().rstrip("/")
        api_key = cfg.openai.api_key.strip()
        model = cfg.openai.model.strip()
        timeout_sec = max(5, int(cfg.openai.timeout_sec))

        if not base_url or not model:
            raise ValueError("AI 配置不完整，请先在设置中填写 Base URL / Model")

        if not api_key and not self._is_local_base_url(base_url):
            raise ValueError("远程 AI 服务通常需要 API Key")

        if purpose == "test":
            candidates = [min(max(timeout_sec, 8), 25)]
        else:
            candidates = self._generation_timeouts(timeout_sec)

        is_local = self._is_local_base_url(base_url)

        def try_ollama() -> str:
            last_error: Exception | None = None
            for timeout in candidates:
                try:
                    return self._chat_ollama_native(base_url, model, messages, temperature, timeout)
                except Exception as exc:
                    last_error = exc
                    if not self._is_timeout_text(str(exc)):
                        break
            raise RuntimeError(str(last_error) if last_error else "Ollama 调用失败")

        def try_openai() -> str:
            last_error: Exception | None = None
            for timeout in candidates:
                try:
                    return self._chat_openai_compatible(base_url, api_key, model, messages, temperature, timeout)
                except Exception as exc:
                    last_error = exc
                    if not self._is_timeout_text(str(exc)):
                        break
            raise RuntimeError(str(last_error) if last_error else "OpenAI兼容接口调用失败")

        if is_local:
            try:
                return try_ollama()
            except Exception as ollama_exc:
                if self._is_timeout_text(str(ollama_exc)):
                    raise RuntimeError(
                        f"Ollama 推理超时（{candidates[-1]}s）。请在设置中提高 Timeout、先执行 `ollama run {model}` 预热，或切换更小模型。"
                    ) from ollama_exc
                try:
                    return try_openai()
                except Exception as openai_exc:
                    raise RuntimeError(f"Ollama原生接口失败: {ollama_exc} | OpenAI兼容接口失败: {openai_exc}") from openai_exc

        openai_err: Exception | None = None
        try:
            return try_openai()
        except Exception as exc:
            openai_err = exc

        try:
            return try_ollama()
        except Exception as ollama_exc:
            if openai_err:
                raise RuntimeError(f"OpenAI兼容接口失败: {openai_err} | Ollama原生接口失败: {ollama_exc}") from ollama_exc
            raise RuntimeError(f"Ollama原生接口失败: {ollama_exc}") from ollama_exc

    def translate(self, text: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是专业翻译助手。默认把输入翻译为简体中文。"
                    "若输入本身为中文，则翻译为自然英文。"
                    "仅输出翻译结果，不要解释。"
                ),
            },
            {"role": "user", "content": text.strip()},
        ]
        return self._chat(messages, temperature=0.15, purpose="translate")

    def polish(self, text: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是文本润色助手。保留原意，优化语法、用词和可读性。"
                    "仅输出润色后的文本，不要解释。"
                ),
            },
            {"role": "user", "content": text.strip()},
        ]
        return self._chat(messages, temperature=0.35, purpose="polish")

    def test_connection(self) -> tuple[bool, str]:
        cfg: AppConfig = self.cfg_getter()
        base_url = cfg.openai.base_url.strip().rstrip("/")
        api_key = cfg.openai.api_key.strip()
        model = cfg.openai.model.strip()
        timeout_sec = max(5, int(cfg.openai.timeout_sec))

        if not base_url or not model:
            return False, "配置不完整：请填写 Base URL 和 Model"

        if not api_key and not self._is_local_base_url(base_url):
            return False, "远程服务未配置 API Key"

        headers = self._build_headers(api_key)

        v1_root = base_url if base_url.endswith("/v1") else self._join_url(base_url, "v1")
        try:
            models_data = self._get_json(self._join_url(v1_root, "models"), headers, timeout_sec)
            model_ids = [str(item.get("id", "")) for item in models_data.get("data", []) if isinstance(item, dict)]
            if model_ids and model not in model_ids:
                sample = ", ".join(model_ids[:6])
                return False, f"连接到服务成功，但模型 `{model}` 不在可用列表中。可用模型示例: {sample}"
        except Exception:
            pass

        try:
            probe = self._chat(
                [
                    {"role": "system", "content": "你是连接测试助手。仅返回 OK。"},
                    {"role": "user", "content": "ping"},
                ],
                temperature=0.0,
                purpose="test",
            )
            return True, f"AI 连接成功，模型响应正常（{probe[:40]}）"
        except Exception as exc:
            return False, f"AI 连接失败: {exc}"


class TranslationService:
    def __init__(self, dict_path: Path, cfg_getter) -> None:
        self.offline = OfflineTranslator(dict_path, cfg_getter)
        self.ai = OpenAICompatibleTranslator(cfg_getter)

    def translate(self, text: str, mode: str) -> str:
        if mode == "ai":
            return self.ai.translate(text)
        return self.offline.translate(text)

    def polish(self, text: str, mode: str) -> str:
        if mode != "ai":
            raise ValueError("润色仅支持 AI 模式")
        return self.ai.polish(text)

    def test_ai_connection(self) -> tuple[bool, str]:
        return self.ai.test_connection()

    def offline_diagnostics(self) -> str:
        return self.offline.diagnostics()

    def list_offline_models(self) -> list[dict[str, str]]:
        return self.offline.list_models()

    def import_offline_model(self, model_path: str) -> str:
        return self.offline.import_model_file(model_path)

    def offline_runtime_ready(self) -> bool:
        return self.offline.runtime_ready()

    def offline_runtime_hint(self) -> str:
        return self.offline.runtime_hint()












