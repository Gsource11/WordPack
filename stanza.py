from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class _Sentence:
    text: str


class _Doc:
    def __init__(self, sentences: List[_Sentence]) -> None:
        self.sentences = sentences


class Pipeline:
    """Lightweight compatibility shim for argostranslate.sbd.

    This avoids bundling the heavyweight stanza/torch runtime in packaged builds.
    It provides a minimal API-compatible fallback sentence splitter.
    """

    def __init__(
        self,
        *,
        lang: str | None = None,
        dir: str | None = None,
        processors: str | None = None,
        use_gpu: bool | None = None,
        logging_level: str | None = None,
    ) -> None:
        del lang, dir, processors, use_gpu, logging_level

    def __call__(self, text: str) -> _Doc:
        value = str(text or "").strip()
        if not value:
            return _Doc([])
        # Split sentences on common Chinese/English punctuation.
        pieces = [p.strip() for p in re.split(r"(?<=[。！？!?；;\.])\s+", value) if p and p.strip()]
        if not pieces:
            pieces = [value]
        return _Doc([_Sentence(text=p) for p in pieces])
