"""可选的模型辅助：只从来源页正文里抽取原文标识。

模型不参与“是否同图”的判定；抽取结果必须能在原文中找到，否则丢弃。
"""

from __future__ import annotations

import re
import json
import logging

import httpx

logger = logging.getLogger(__name__)

_HANDLE_RE = re.compile(r"[A-Za-z0-9_]{1,15}")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _content_of(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict)
        )
    return ""


def _json_object(raw: str) -> dict[str, object]:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def extract_source_line(
    text: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float,
    proxy: str = "",
) -> dict[str, str]:
    """返回 ``{"source_line": ..., "account": ...}``；失败或不可信时返回空字典。"""
    body = text.strip()
    if not body or not api_key:
        return {}
    snippet = body[:4000]
    instruction = (
        "从网页正文中找出这张图片的原始出处标识。只输出 JSON，字段为 "
        '{"source_line": "原文中的一处作者/账号/帖子文字（逐字摘录，勿改写）", '
        '"account": "X 账号（不带@，没有就留空）"}。'
        "找不到就输出 {}。不要推测，不要补充原文没有的内容。"
    )
    try:
        async with httpx.AsyncClient(
            proxy=proxy or None, timeout=timeout, trust_env=False
        ) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": instruction},
                        {"role": "user", "content": snippet},
                    ],
                },
            )
            if response.status_code != 200:
                return {}
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    except Exception as exc:  # 配置异常不应打断觅影
        logger.warning("[ShadowSeek] 模型抽取失败：%s", type(exc).__name__)
        return {}

    data = _json_object(_content_of(payload))
    line = data.get("source_line")
    account = data.get("account")
    result: dict[str, str] = {}

    if isinstance(line, str):
        clean = " ".join(line.split())
        if 4 <= len(clean) <= 120 and _normalize(clean) in _normalize(body):
            result["source_line"] = clean

    if isinstance(account, str):
        handle = account.strip().lstrip("@")
        if _HANDLE_RE.fullmatch(handle) and handle.casefold() in body.casefold():
            result["account"] = handle

    return result
