"""按图片感知哈希缓存检索结果，减少重复请求。

缓存只保存“候选与诊断”，展示选择在读取后重新计算，避免旧选择逻辑污染新版本。
"""

from __future__ import annotations

import os
import json
import time
import asyncio
from typing import Any
from pathlib import Path
from dataclasses import asdict, fields

from gsuid_core.data_store import get_res_path

from .models import Report, Candidate
from .imageops import hamming

MAX_ENTRIES = 400


class ResultCache:
    def __init__(self, path: Path, max_entries: int = MAX_ENTRIES) -> None:
        self._path = path
        self._max = max_entries
        self._lock = asyncio.Lock()
        self._entries: list[dict[str, Any]] | None = None

    def _load(self) -> None:
        if self._entries is not None:
            return
        entries: list[dict[str, Any]] = []
        try:
            data = json.loads(self._path.read_text("utf-8"))
        except (OSError, ValueError):
            data = []
        if isinstance(data, list):
            for item in data:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("digest"), str)
                    and isinstance(item.get("payload"), dict)
                    and isinstance(item.get("time"), (int, float))
                ):
                    entries.append(item)
        self._entries = entries[-self._max :]

    async def get(self, digest: str, ttl_hours: float) -> dict[str, Any] | None:
        if ttl_hours <= 0:
            return None
        async with self._lock:
            self._load()
            assert self._entries is not None
            cutoff = time.time() - ttl_hours * 3600
            self._entries[:] = [entry for entry in self._entries if entry["time"] >= cutoff]
            for entry in reversed(self._entries):
                if hamming(digest, entry["digest"], limit=4) <= 4:
                    return dict(entry["payload"])
            return None

    async def put(self, digest: str, payload: dict[str, Any], ttl_hours: float) -> None:
        if ttl_hours <= 0:
            return
        async with self._lock:
            self._load()
            assert self._entries is not None
            self._entries[:] = [
                entry
                for entry in self._entries
                if hamming(digest, entry["digest"], limit=4) > 4
            ]
            self._entries.append(
                {"digest": digest, "time": time.time(), "payload": payload}
            )
            self._entries[:] = self._entries[-self._max :]
            await asyncio.to_thread(self._save)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(self._entries or [], ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp, self._path)


_CACHE = ResultCache(get_res_path() / "ShadowSeek" / "cache.json")


def get_cache() -> ResultCache:
    return _CACHE


def report_payload(report: Report) -> dict[str, Any]:
    return {
        "candidates": [asdict(item) for item in report.candidates],
        "engine_counts": dict(report.engine_counts),
        "page_status": dict(report.page_status),
        "exact_pages": report.exact_pages,
        "visual_pages": report.visual_pages,
        "looked_up_posts": report.looked_up_posts,
        "llm": dict(report.llm),
    }


_FIELD_NAMES = {item.name for item in fields(Candidate)}


def report_from_payload(payload: dict[str, Any]) -> Report | None:
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        return None
    candidates: list[Candidate] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        try:
            candidates.append(
                Candidate(**{key: value for key, value in item.items() if key in _FIELD_NAMES})
            )
        except TypeError:
            return None
    return Report(
        candidates=candidates,
        engine_counts=dict(payload.get("engine_counts") or {}),
        page_status=dict(payload.get("page_status") or {}),
        exact_pages=int(payload.get("exact_pages") or 0),
        visual_pages=int(payload.get("visual_pages") or 0),
        looked_up_posts=int(payload.get("looked_up_posts") or 0),
        llm=dict(payload.get("llm") or {}),
        cache_hit=True,
    )
