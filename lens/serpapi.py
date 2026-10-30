"""通过 SerpApi 调用 Google Lens（完全匹配 / 相似图）与限定 X 的网页搜索。"""

from __future__ import annotations

import re
import asyncio
import logging
from typing import Any
from contextlib import asynccontextmanager

import httpx

from .imageops import prepare_upload

_SERP_KEY = re.compile(
    r"(https://serpapi\.com/(?:search\.json|image)\?[^\s\"']*?api_key=)[^&\s\"']+",
    re.I,
)


class SerpApiError(RuntimeError):
    """可直接展示给用户的检索失败原因。"""


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = _SERP_KEY.sub(r"\1[REDACTED]", message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


@asynccontextmanager
async def _client(proxy: str | None, timeout: float):
    http_logger = logging.getLogger("httpx")
    log_filter = _RedactFilter()
    http_logger.addFilter(log_filter)
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=timeout, trust_env=False) as client:
            yield client
    finally:
        http_logger.removeFilter(log_filter)


def _json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in {401, 403}:
        raise SerpApiError("SerpApi Key 无效或没有访问权限")
    if response.status_code == 429:
        raise SerpApiError("SerpApi 额度不足或请求过于频繁")
    if response.status_code >= 500:
        raise SerpApiError("SerpApi 服务暂时不可用")
    if response.status_code >= 300:
        raise SerpApiError("SerpApi 拒绝了这次请求")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SerpApiError("SerpApi 返回了无效的 JSON") from exc
    if not isinstance(payload, dict):
        raise SerpApiError("SerpApi 返回了异常结构")
    return payload


def _items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    metadata = payload.get("search_metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise SerpApiError("SerpApi 返回了异常结构")
    status = (metadata or {}).get("status")
    if status is not None and status != "Success":
        raise SerpApiError("SerpApi 没有完成检索")
    if payload.get("error") and status != "Success":
        raise SerpApiError("SerpApi 没有完成检索")
    data = payload.get(key, [])
    if not isinstance(data, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("link"), str)
        for item in data
    ):
        raise SerpApiError("SerpApi 返回了异常结构")
    return [item for item in data if item.get("link", "").strip()]


async def lens_search(
    image: bytes,
    api_key: str,
    timeout: float,
    proxy: str | None,
    visual: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """上传一次图片，分别请求完全匹配和（可选）相似图。"""
    key = api_key.strip()
    if not key:
        raise SerpApiError("未配置 SerpApi Key")
    if not image:
        raise SerpApiError("待检索图片为空")
    upload = image if len(image) <= 500_000 else await asyncio.to_thread(prepare_upload, image)
    filename, content_type = "image.jpg", "image/jpeg"
    if upload.startswith(b"\x89PNG\r\n\x1a\n"):
        filename, content_type = "image.png", "image/png"
    elif upload.startswith(b"RIFF") and upload[8:12] == b"WEBP":
        filename, content_type = "image.webp", "image/webp"
    try:
        async with _client(proxy, timeout) as client:
            uploaded = _json(
                await client.post(
                    "https://serpapi.com/image",
                    data={"api_key": key},
                    files={"image": (filename, upload, content_type)},
                )
            )
            if uploaded.get("error"):
                raise SerpApiError("SerpApi 图片上传失败")
            image_id = uploaded.get("image_id")
            if not isinstance(image_id, str) or not image_id.strip():
                raise SerpApiError("SerpApi 未返回图片 ID")
            params = {
                "engine": "google_lens",
                "image_id": image_id.strip(),
                "api_key": key,
                "hl": "en",
                "safe": "off",
                "type": "exact_matches",
            }
            exact_payload = _json(
                await client.get("https://serpapi.com/search.json", params=params)
            )
            result = {"exact": _items(exact_payload, "exact_matches")}
            if not visual:
                return result
            # 相似图只用于找线索；失败不能吞掉已经拿到的完全匹配。
            try:
                visual_payload = _json(
                    await client.get(
                        "https://serpapi.com/search.json",
                        params={**params, "type": "visual_matches"},
                    )
                )
                result["visual"] = _items(visual_payload, "visual_matches")
            except SerpApiError:
                result["visual"] = []
            return result
    except httpx.TimeoutException as exc:
        raise SerpApiError("SerpApi 请求超时") from exc
    except httpx.HTTPError as exc:
        raise SerpApiError("SerpApi 网络请求失败") from exc


async def x_site_search(
    query: str,
    api_key: str,
    timeout: float,
    proxy: str | None,
) -> list[dict[str, Any]]:
    """用一段原文线索在 Google 上限定 X/Twitter 域名搜索。"""
    key = api_key.strip()
    if not key:
        raise SerpApiError("X 线索检索需要 SerpApi Key")
    clue = " ".join(str(query).replace('"', " ").split())[:100]
    if not clue:
        return []
    try:
        async with _client(proxy, timeout) as client:
            payload = _json(
                await client.get(
                    "https://serpapi.com/search.json",
                    params={
                        "engine": "google",
                        "q": f'(site:x.com OR site:twitter.com) "{clue}"',
                        "api_key": key,
                        "num": 5,
                        "safe": "off",
                    },
                )
            )
            return _items(payload, "organic_results")
    except httpx.TimeoutException as exc:
        raise SerpApiError("X 线索检索超时") from exc
    except httpx.HTTPError as exc:
        raise SerpApiError("X 线索检索网络失败") from exc
