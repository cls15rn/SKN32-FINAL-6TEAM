# -*- coding: utf-8 -*-
"""묶음별 어댑터 — 이름 → 만드는 함수. 앱 import 가 필요한 어댑터는 안에서 늦게 import 한다."""
from __future__ import annotations

from typing import Callable

from ..core import Bundle
from .paths import Sources


def _lazy(module: str, func: str = "build") -> Callable[[Sources | None], Bundle]:
    def make(src: Sources | None = None) -> Bundle:
        import importlib
        return getattr(importlib.import_module(f"{__package__}.{module}"), func)(src)
    return make


REGISTRY: dict[str, Callable[[Sources | None], Bundle]] = {
    "activity": _lazy("activity"),
    "g96": _lazy("classification"),
    "rag": _lazy("rag"),
    "e2_v2": _lazy("e2"),
    "pointing_h": _lazy("pointing", "build_h"),
    "pointing_mx_dispute": _lazy("pointing", "build_mx_dispute"),
    "pointing_mx_check": _lazy("pointing", "build_mx_check"),
}


def build(name: str, src: Sources | None = None) -> Bundle:
    if name not in REGISTRY:
        raise KeyError(f"알 수 없는 묶음 「{name}」 — 있는 것: {', '.join(REGISTRY)}")
    return REGISTRY[name](src)
