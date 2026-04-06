from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

from .config import AppConfig


class DictionaryEngine:
    """Dictionary translation backend with install/list/select capabilities."""

    def __init__(self) -> None:
        self._translate_mod = None
        self._package_mod = None
        self._init_error = ""
        self._import_attempted = False

    def _ensure_runtime(self) -> bool:
        if self._translate_mod is not None and self._package_mod is not None:
            return True
        if self._import_attempted:
            return False

        self._import_attempted = True
        try:
            import argostranslate.package as package_mod  # type: ignore
            import argostranslate.translate as translate_mod  # type: ignore

            self._translate_mod = translate_mod
            self._package_mod = package_mod
            self._init_error = ""
            return True
        except Exception as exc:
            self._translate_mod = None
            self._package_mod = None
            self._init_error = f"{type(exc).__name__}: {exc}"
            return False

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
        if not self._ensure_runtime():
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

    def import_attempted(self) -> bool:
        return bool(self._import_attempted)

    def runtime_ready(self, probe: bool = False) -> bool:
        if probe:
            self._ensure_runtime()
        return self._translate_mod is not None and self._package_mod is not None

    def init_error(self) -> str:
        return self._init_error.strip()

    def is_available(self) -> bool:
        return len(self.list_directions()) > 0

    def install_model_file(self, model_path: str) -> str:
        if not self._ensure_runtime() or self._package_mod is None:
            raise RuntimeError("词典模型功能暂不可用，请使用发布版程序。")

        path = Path(model_path)
        if not path.exists() or not path.is_file():
            raise RuntimeError("模型文件不存在")

        self._package_mod.install_from_path(str(path))
        count = len(self.list_directions())
        return f"模型导入成功，当前可用词典方向: {count}"

    @staticmethod
    def _basic_split_sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[。！？!?;；\.])\s+", text)
        cleaned = [p.strip() for p in parts if p and p.strip()]
        return cleaned if cleaned else [text]

    def _attach_dictionary_sentencizer(self, translation_obj: Any) -> None:
        class _DictionarySentencizer:
            def split_sentences(self, text: str):
                return DictionaryEngine._basic_split_sentences(text)

        package_translation = translation_obj
        underlying = getattr(translation_obj, "underlying", None)
        if underlying is not None:
            package_translation = underlying

        if hasattr(package_translation, "sentencizer"):
            package_translation.sentencizer = _DictionarySentencizer()

    def _select_translation(self, text: str, preferred_direction: str):
        selected = None

        if preferred_direction and preferred_direction != "auto":
            parts = preferred_direction.split("->")
            if len(parts) == 2:
                selected = self._find_translation(parts[0].strip(), parts[1].strip())
                if selected is None:
                    raise RuntimeError(f"所选词典方向不可用: {preferred_direction}")

        if selected is None:
            source_code = "zh" if self._contains_chinese(text) else "en"
            target_code = "en" if source_code == "zh" else "zh"
            selected = self._find_translation(source_code, target_code)

        if selected is None:
            raise RuntimeError(f"未找到可用词典方向: {source_code}->{target_code}")

        self._attach_dictionary_sentencizer(selected)
        return selected

    def translate(self, text: str, preferred_direction: str = "auto") -> str:
        if not self._ensure_runtime() or self._translate_mod is None:
            raise RuntimeError("词典翻译运行环境不可用")

        translation = self._select_translation(text, preferred_direction)
        if translation is None:
            raise RuntimeError("未找到可用词典模型")

        chunks = self._split_text(text)
        translated_chunks: list[str] = []
        for chunk in chunks:
            if not chunk.strip():
                translated_chunks.append(chunk)
            else:
                translated_chunks.append(translation.translate(chunk))
        return "".join(translated_chunks).strip()


class DictionaryTranslator:
    def __init__(self, cfg_getter, status_cache_path: Path | None = None) -> None:
        self.cfg_getter = cfg_getter
        self.engine = DictionaryEngine()
        self._status_cache_path = Path(status_cache_path) if status_cache_path else None
        self._status_cache: dict[str, Any] | None = None
        self._load_status_cache_from_disk()

    def _preferred_direction(self) -> str:
        cfg: AppConfig = self.cfg_getter()
        value = str(getattr(cfg.dictionary, "preferred_direction", "auto") or "auto").strip()
        return value or "auto"

    def _runtime_hint_from_status(self, runtime_ready: bool, probe: bool) -> str:
        if runtime_ready:
            return "词典翻译已准备就绪"
        if not probe and not self.engine.import_attempted():
            return "词典翻译将在需要时自动检测。"
        return "词典翻译暂不可用，请安装依赖或使用发布版。"

    def _diagnostics_from_status(self, runtime_ready: bool, items: list[dict[str, str]], probe: bool) -> str:
        preferred = self._preferred_direction()
        if not probe and not self.engine.import_attempted():
            return "词典翻译将在首次使用时自动检测。"

        if not runtime_ready:
            return "词典翻译暂不可用，请安装依赖或使用发布版。"

        if not items:
            return "已启用词典翻译，但还没有可用模型，请先导入模型。"

        if preferred != "auto":
            if any(item["direction"] == preferred for item in items):
                return f"词典翻译已启用，当前固定方向：{preferred}"
            return f"已配置 {preferred}，但该方向模型不可用，当前将自动匹配。"

        return "词典翻译已启用（自动匹配方向）"

    def _invalidate_status_cache(self) -> None:
        self._status_cache = None
        if self._status_cache_path is not None:
            try:
                self._status_cache_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _load_status_cache_from_disk(self) -> None:
        if self._status_cache_path is None:
            return
        try:
            if not self._status_cache_path.exists():
                return
            raw = json.loads(self._status_cache_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            models = raw.get("models", [])
            if not isinstance(models, list):
                models = []
            self._status_cache = {
                "ts": float(raw.get("ts", 0.0) or 0.0),
                "runtime_ready": bool(raw.get("runtime_ready", False)),
                "runtime_hint": str(raw.get("runtime_hint", "")),
                "diagnostics": str(raw.get("diagnostics", "")),
                "models": list(models),
            }
        except Exception:
            self._status_cache = None

    def _persist_status_cache(self) -> None:
        if self._status_cache_path is None or not self._status_cache:
            return
        try:
            self._status_cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "ts": float(self._status_cache.get("ts", 0.0) or 0.0),
                "runtime_ready": bool(self._status_cache.get("runtime_ready", False)),
                "runtime_hint": str(self._status_cache.get("runtime_hint", "")),
                "diagnostics": str(self._status_cache.get("diagnostics", "")),
                "models": list(self._status_cache.get("models", [])),
            }
            self._status_cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def status(self, probe: bool = False, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        cached = self._status_cache
        if not force_refresh and cached is not None:
            cached_ready = bool(cached.get("runtime_ready", False))
            # Permanent cache + event invalidation:
            # - probe=False: always trust cached status for startup speed.
            # - probe=True: reuse positive cache, refresh when cached unavailable.
            if (not probe) or cached_ready:
                return {
                    "runtime_ready": cached_ready,
                    "runtime_hint": str(cached.get("runtime_hint", "")),
                    "diagnostics": str(cached.get("diagnostics", "")),
                    "models": list(cached.get("models", [])),
                }

        runtime_ready = bool(self.engine.runtime_ready(probe=probe))
        models = self.engine.list_directions() if runtime_ready else []
        hint = self._runtime_hint_from_status(runtime_ready, probe=probe)
        diagnostics = self._diagnostics_from_status(runtime_ready, models, probe=probe)

        status_payload = {
            "ts": now,
            "runtime_ready": runtime_ready,
            "runtime_hint": hint,
            "diagnostics": diagnostics,
            "models": list(models),
        }

        persistable = probe or self.engine.import_attempted() or runtime_ready or bool(models)
        if persistable:
            self._status_cache = status_payload
            self._persist_status_cache()
        elif self._status_cache is None:
            self._status_cache = status_payload

        return {
            "runtime_ready": runtime_ready,
            "runtime_hint": hint,
            "diagnostics": diagnostics,
            "models": list(models),
        }

    def refresh_status(self, probe: bool = True) -> dict[str, Any]:
        return self.status(probe=probe, force_refresh=True)

    def invalidate_status(self) -> None:
        self._invalidate_status_cache()

    def list_models(self, probe: bool = False) -> list[dict[str, str]]:
        return self.status(probe=probe)["models"]

    def import_model_file(self, model_path: str) -> str:
        result = self.engine.install_model_file(model_path)
        self._invalidate_status_cache()
        self.refresh_status(probe=True)
        return result

    def runtime_ready(self, probe: bool = False) -> bool:
        return bool(self.status(probe=probe)["runtime_ready"])

    def runtime_hint(self, probe: bool = False) -> str:
        return str(self.status(probe=probe)["runtime_hint"])

    def diagnostics(self, probe: bool = False) -> str:
        return str(self.status(probe=probe)["diagnostics"])

    def translate(self, text: str) -> str:
        normalized = text.strip()
        if not normalized:
            return ""

        if not self.runtime_ready(probe=True):
            return "[词典翻译暂不可用，请先安装依赖或使用发布版。]"

        items = self.engine.list_directions()
        if not items:
            return "[未检测到词典模型，请先在设置中导入模型。]"

        try:
            return self.engine.translate(normalized, self._preferred_direction())
        except Exception as exc:
            return f"[词典翻译失败: {exc}]"


class PartialStreamError(RuntimeError):
    """Streaming backend failed after already yielding partial content."""


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

    @staticmethod
    def _extract_text_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, list):
            text_chunks: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "")).strip().lower()
                if item_type in {"text", "output_text"}:
                    text_chunks.append(str(item.get("text", "")))
            return "".join(text_chunks)
        return str(content)

    def _extract_openai_content(self, data: dict[str, Any]) -> str:
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("AI 响应格式异常：缺少 choices")

        message = choices[0].get("message", {})
        result = self._extract_text_content(message.get("content", "")).strip()
        if result:
            return result

        reasoning = str(message.get("reasoning_content", "")).strip()
        fallback = self._extract_answer_from_reasoning(reasoning)
        if fallback:
            return fallback

        raise RuntimeError("AI 响应为空")

    def _stream_json_lines(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_sec: int,
        parse_line: Callable[[str], str],
        on_delta: Callable[[str], None],
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
        req = request.Request(
            url=url,
            method="POST",
            headers=headers,
            data=json.dumps(payload).encode("utf-8"),
        )
        chunks: list[str] = []
        try:
            with request.urlopen(req, timeout=timeout_sec) as resp:
                while True:
                    if should_cancel and should_cancel():
                        raise RuntimeError("请求已取消")
                    raw_line = resp.readline()
                    if not raw_line:
                        break
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    chunk = parse_line(line)
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    on_delta(chunk)
        except TimeoutError as exc:
            raise RuntimeError(f"请求超时: {exc}") from exc
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code}: {body[:240] or exc.reason}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"网络错误: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"请求失败: {exc}") from exc

        result = "".join(chunks).strip()
        if result:
            return result
        raise RuntimeError("AI 流式响应为空")

    def _parse_openai_stream_line(self, line: str) -> str:
        if line.startswith(":") or not line.startswith("data:"):
            return ""
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            return ""
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI 流式响应解析失败: {exc}") from exc

        error_info = data.get("error")
        if error_info:
            if isinstance(error_info, dict):
                message = str(error_info.get("message", "")).strip() or json.dumps(error_info, ensure_ascii=False)
            else:
                message = str(error_info).strip()
            raise RuntimeError(message or "OpenAI 流式响应异常")

        choices = data.get("choices", [])
        if not choices:
            return ""
        delta = choices[0].get("delta", {})
        if not isinstance(delta, dict):
            return ""

        content = self._extract_text_content(delta.get("content"))
        if content:
            return content

        return self._extract_text_content(delta.get("text"))

    def _parse_ollama_stream_line(self, line: str) -> str:
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ollama 流式响应解析失败: {exc}") from exc

        error_text = str(data.get("error", "")).strip()
        if error_text:
            raise RuntimeError(error_text)

        message = data.get("message", {})
        if not isinstance(message, dict):
            return ""
        return str(message.get("content", "") or "")

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

    def _chat_openai_compatible_stream(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        timeout_sec: int,
        on_delta: Callable[[str], None],
        should_cancel: Callable[[], bool] | None = None,
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
            "stream": True,
        }
        if self._is_local_base_url(base_url):
            payload["think"] = False

        headers = self._build_headers(api_key)
        return self._stream_json_lines(url, payload, headers, timeout_sec, self._parse_openai_stream_line, on_delta, should_cancel)

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

    def _chat_ollama_native_stream(
        self,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        timeout_sec: int,
        on_delta: Callable[[str], None],
        should_cancel: Callable[[], bool] | None = None,
        num_predict: int = 600,
    ) -> str:
        native_base = base_url[:-3] if base_url.endswith("/v1") else base_url
        url = self._join_url(native_base, "api/chat")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        headers = {"Content-Type": "application/json"}
        return self._stream_json_lines(url, payload, headers, timeout_sec, self._parse_ollama_stream_line, on_delta, should_cancel)

    def _run_backend(
        self,
        candidates: list[int],
        error_message: str,
        use_stream: bool,
        stream_call: Callable[[int, Callable[[str], None]], str],
        sync_call: Callable[[int], str],
        on_delta: Callable[[str], None] | None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
        def emit_full_result(result: str) -> str:
            if on_delta and result:
                on_delta(result)
            return result

        last_error: Exception | None = None
        for timeout in candidates:
            emitted = False

            def relay(chunk: str) -> None:
                nonlocal emitted
                if should_cancel and should_cancel():
                    raise RuntimeError("请求已取消")
                emitted = True
                if on_delta:
                    on_delta(chunk)

            try:
                if use_stream:
                    return stream_call(timeout, relay)
                return sync_call(timeout)
            except Exception as exc:
                last_error = exc
                if use_stream and not emitted:
                    try:
                        return emit_full_result(sync_call(timeout))
                    except Exception as fallback_exc:
                        last_error = fallback_exc
                if use_stream and emitted:
                    raise PartialStreamError(str(last_error) if last_error else error_message) from last_error
                if not self._is_timeout_text(str(last_error)):
                    break

        raise RuntimeError(str(last_error) if last_error else error_message)

    def _chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        purpose: str = "translate",
        on_delta: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
        cfg: AppConfig = self.cfg_getter()
        base_url = cfg.openai.base_url.strip().rstrip("/")
        api_key = cfg.openai.api_key.strip()
        model = cfg.openai.model.strip()
        timeout_sec = max(5, int(cfg.openai.timeout_sec))

        if not base_url or not model:
            raise ValueError("请先在设置中填写 AI 服务地址和模型。")

        if not api_key and not self._is_local_base_url(base_url):
            raise ValueError("当前服务需要 API Key，请先填写。")

        if purpose == "test":
            candidates = [min(max(timeout_sec, 8), 25)]
        else:
            candidates = self._generation_timeouts(timeout_sec)

        is_local = self._is_local_base_url(base_url)
        use_stream = on_delta is not None and purpose != "test"

        def try_ollama() -> str:
            return self._run_backend(
                candidates=candidates,
                error_message="Ollama 调用失败",
                use_stream=use_stream,
                stream_call=lambda timeout, relay: self._chat_ollama_native_stream(
                    base_url, model, messages, temperature, timeout, relay, should_cancel
                ),
                sync_call=lambda timeout: self._chat_ollama_native(base_url, model, messages, temperature, timeout),
                on_delta=on_delta,
                should_cancel=should_cancel,
            )

        def try_openai() -> str:
            return self._run_backend(
                candidates=candidates,
                error_message="OpenAI兼容接口调用失败",
                use_stream=use_stream,
                stream_call=lambda timeout, relay: self._chat_openai_compatible_stream(
                    base_url, api_key, model, messages, temperature, timeout, relay, should_cancel
                ),
                sync_call=lambda timeout: self._chat_openai_compatible(
                    base_url, api_key, model, messages, temperature, timeout
                ),
                on_delta=on_delta,
                should_cancel=should_cancel,
            )

        if is_local:
            try:
                return try_ollama()
            except PartialStreamError as ollama_exc:
                raise RuntimeError(str(ollama_exc)) from ollama_exc
            except Exception as ollama_exc:
                if self._is_timeout_text(str(ollama_exc)):
                    raise RuntimeError(
                        f"AI 响应超时（{candidates[-1]} 秒），请稍后重试或提高超时时间。"
                    ) from ollama_exc
                try:
                    return try_openai()
                except PartialStreamError as openai_exc:
                    raise RuntimeError(str(openai_exc)) from openai_exc
                except Exception as openai_exc:
                    raise RuntimeError("AI 服务暂时不可用，请检查设置后重试。") from openai_exc

        openai_err: Exception | None = None
        try:
            return try_openai()
        except PartialStreamError as exc:
            raise RuntimeError(str(exc)) from exc
        except Exception as exc:
            openai_err = exc

        try:
            return try_ollama()
        except PartialStreamError as exc:
            raise RuntimeError(str(exc)) from exc
        except Exception as ollama_exc:
            if openai_err:
                raise RuntimeError("AI 服务暂时不可用，请检查设置后重试。") from ollama_exc
            raise RuntimeError("AI 服务暂时不可用，请稍后重试。") from ollama_exc

    @staticmethod
    def _translation_system_prompt() -> str:
        return (
            "你是专业翻译助手。默认把输入翻译为简体中文。"
            "若输入本身为中文，则翻译为自然英文。"
            "仅输出翻译结果，不要解释。"
        )

    @staticmethod
    def _extract_json_payload(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        if not text:
            raise RuntimeError("AI 响应为空")
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise RuntimeError("候选解析失败：未找到 JSON 对象")
        try:
            data = json.loads(match.group(0))
        except Exception as exc:
            raise RuntimeError(f"候选解析失败：{exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("候选解析失败：JSON 顶层不是对象")
        return data

    def _parse_candidate_list(self, raw: str, expected_count: int) -> list[str]:
        payload = self._extract_json_payload(raw)
        items = payload.get("candidates", [])
        if not isinstance(items, list):
            raise RuntimeError("候选解析失败：candidates 字段不是数组")

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
            if len(cleaned) >= expected_count:
                break

        # 允许返回部分候选，由上层重试聚合，避免一次不足导致整次失败。
        if not cleaned:
            raise RuntimeError("候选数量不足，请重试")
        return cleaned

    def translate(self, text: str) -> str:
        messages = [
            {
                "role": "system",
                "content": self._translation_system_prompt(),
            },
            {"role": "user", "content": text.strip()},
        ]
        return self._chat(messages, temperature=0.15, purpose="translate")

    def translate_stream(self, text: str, on_delta: Callable[[str], None], should_cancel: Callable[[], bool] | None = None) -> str:
        messages = [
            {
                "role": "system",
                "content": self._translation_system_prompt(),
            },
            {"role": "user", "content": text.strip()},
        ]
        return self._chat(messages, temperature=0.15, purpose="translate", on_delta=on_delta, should_cancel=should_cancel)

    def translate_candidates(self, text: str, count: int = 4, reference_result: str = "") -> list[str]:
        normalized = str(text or "").strip()
        reference = str(reference_result or "").strip()
        target_count = max(2, min(4, int(count or 4)))
        target_lang_hint = "ZH" if re.search(r"[\u4e00-\u9fff]", reference) else ("EN" if re.search(r"[A-Za-z]", reference) else "AUTO")
        reference_clause = (
            f'候选译文必须与参考译文保持同一目标语言、同一语体方向。参考译文："{reference}"。'
            if reference
            else "候选译文必须保持与常见翻译方向一致，不要切换目标语言。"
        )
        messages = [
            {
                "role": "system",
                "content": (
                    f"{self._translation_system_prompt()}"
                    "你还需要为同一输入给出多个候选译文版本，语义保持一致，但语气或用词略有差异。"
                    f"{reference_clause}"
                    "候选之间不得重复，也不得与参考译文重复。"
                    "严格禁止改变翻译方向：若参考译文为英文，则所有候选必须是英文；若参考译文为中文，则所有候选必须是中文。"
                    "禁止输出原文语言，禁止中英混写。"
                    f"目标语言标记：{target_lang_hint}。你必须严格遵守该标记。"
                    "输出必须是 JSON 对象，格式为 "
                    '{"candidates":["候选1","候选2","候选3","候选4"]}。'
                    f"只返回 {target_count} 条候选，不要添加解释。"
                ),
            },
            {"role": "user", "content": normalized},
        ]
        raw = self._chat(messages, temperature=0.35, purpose="translate")
        return self._parse_candidate_list(raw, target_count)

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

    def polish_stream(self, text: str, on_delta: Callable[[str], None], should_cancel: Callable[[], bool] | None = None) -> str:
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
        return self._chat(messages, temperature=0.35, purpose="polish", on_delta=on_delta, should_cancel=should_cancel)

    def test_connection(self) -> tuple[bool, str]:
        cfg: AppConfig = self.cfg_getter()
        base_url = cfg.openai.base_url.strip().rstrip("/")
        api_key = cfg.openai.api_key.strip()
        model = cfg.openai.model.strip()
        timeout_sec = max(5, int(cfg.openai.timeout_sec))

        if not base_url or not model:
            return False, "请先填写 AI 服务地址和模型。"

        if not api_key and not self._is_local_base_url(base_url):
            return False, "请填写 API Key。"

        headers = self._build_headers(api_key)

        v1_root = base_url if base_url.endswith("/v1") else self._join_url(base_url, "v1")
        try:
            models_data = self._get_json(self._join_url(v1_root, "models"), headers, timeout_sec)
            model_ids = [str(item.get("id", "")) for item in models_data.get("data", []) if isinstance(item, dict)]
            if model_ids and model not in model_ids:
                sample = ", ".join(model_ids[:6])
                return False, f"连接成功，但当前模型不可用。可用模型示例：{sample}"
        except Exception:
            pass

        try:
            probe = self._chat(
                [
                    {"role": "system", "content": "你是连接测试助手。如果你接收到用户消息，只回复“收到”。"},
                    {"role": "user", "content": "如果你接收到我的信息，请说收到"},
                ],
                temperature=0.0,
                purpose="test",
            )
            normalized_probe = str(probe or "").strip()
            if "收到" in normalized_probe:
                return True, "连接成功，可以正常使用 AI 翻译。"
            if normalized_probe:
                # Some models may not follow the exact "收到" instruction, but any non-empty
                # assistant reply still proves the endpoint/model is reachable.
                return True, "连接成功，可以正常使用 AI 翻译。"
            return False, "连接已建立，但模型返回内容异常，请检查模型配置。"
        except Exception as exc:
            return False, f"连接失败：{exc}"


class TranslationService:
    def __init__(self, cfg_getter, data_dir: Path | None = None) -> None:
        cache_path = (Path(data_dir) / "dictionary_status_cache.json") if data_dir is not None else None
        self.dictionary = DictionaryTranslator(cfg_getter, status_cache_path=cache_path)
        self.ai = OpenAICompatibleTranslator(cfg_getter)

    def translate(self, text: str, mode: str) -> str:
        if mode == "ai":
            return self.ai.translate(text)
        return self.dictionary.translate(text)

    def translate_stream(
        self,
        text: str,
        mode: str,
        on_delta: Callable[[str], None],
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
        if mode == "ai":
            return self.ai.translate_stream(text, on_delta, should_cancel=should_cancel)
        result = self.dictionary.translate(text)
        if result:
            on_delta(result)
        return result

    def polish(self, text: str, mode: str) -> str:
        if mode != "ai":
            raise ValueError("润色仅支持 AI 模式")
        return self.ai.polish(text)

    def polish_stream(
        self,
        text: str,
        mode: str,
        on_delta: Callable[[str], None],
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
        if mode != "ai":
            raise ValueError("润色仅支持 AI 模式")
        return self.ai.polish_stream(text, on_delta, should_cancel=should_cancel)

    def translate_candidates(self, text: str, mode: str, count: int = 4, reference_result: str = "") -> list[str]:
        if mode != "ai":
            raise ValueError("多候选仅支持 AI 模式")
        return self.ai.translate_candidates(text, count=count, reference_result=reference_result)

    def test_ai_connection(self) -> tuple[bool, str]:
        return self.ai.test_connection()

    def dictionary_diagnostics(self, probe: bool = False) -> str:
        return self.dictionary.diagnostics(probe=probe)

    def list_dictionary_models(self, probe: bool = False) -> list[dict[str, str]]:
        return self.dictionary.list_models(probe=probe)

    def import_dictionary_model(self, model_path: str) -> str:
        return self.dictionary.import_model_file(model_path)

    def dictionary_runtime_ready(self, probe: bool = False) -> bool:
        return self.dictionary.runtime_ready(probe=probe)

    def dictionary_runtime_hint(self, probe: bool = False) -> str:
        return self.dictionary.runtime_hint(probe=probe)

    def dictionary_status(self, probe: bool = False, force_refresh: bool = False) -> dict[str, Any]:
        return self.dictionary.status(probe=probe, force_refresh=force_refresh)












