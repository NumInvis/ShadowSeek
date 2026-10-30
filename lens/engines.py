"""识图引擎适配层。

默认组合：SerpApi Google Lens（或直连 Lens）、百度识图、Copyseeker、Yandex。
Yandex 与 Google 相似图只提供“待核实线索”，不构成同图证明。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit

from .urls import public_url, platform_of, normalize_url
from .models import LEVEL_LINK, LEVEL_MENTION, Candidate
from .serpapi import SerpApiError, lens_search

logger = logging.getLogger(__name__)

SUPPORTED_ENGINES = ("google_lens", "baidu", "copyseeker", "yandex", "ascii2d", "saucenao")
CLUE_ENGINES = {"yandex"}
_SEMAPHORE = asyncio.Semaphore(4)


class EngineError(RuntimeError):
    """引擎失败，异常文本可直接回给用户。"""


async def _guard(response: Any) -> None:
    """在解析前拦住验证码 / 安全验证页面。"""
    status = getattr(response, "status_code", 200)
    if status >= 400:
        raise EngineError(f"搜索服务返回 HTTP {status}")
    url = str(getattr(response, "url", ""))
    if "/sorry/" in url or "/captcha" in url:
        raise EngineError("搜索服务要求人机验证")
    await response.aread()
    content_type = str(response.headers.get("content-type", "")).lower()
    if "html" not in content_type:
        return
    page = response.text.lower()
    host = (urlsplit(url).hostname or "").lower()
    if host in {"google.com", "www.google.com", "lens.google.com", "www.google.com.hk"}:
        if "retry/enablejs" in page and page.count("<a ") <= 5:
            raise EngineError("Google 要求 JavaScript 验证，服务器没有拿到结果")
        if any(
            marker in page
            for marker in (
                'id="captcha-form"',
                "our systems have detected unusual traffic",
                'id="recaptcha"',
            )
        ):
            raise EngineError("Google 要求人机验证，服务器没有拿到结果")
    if any(
        marker in page
        for marker in (
            "<title>just a moment",
            "<title>百度安全验证",
            "<title>安全验证",
            "<title>captcha",
        )
    ):
        raise EngineError("搜索服务要求验证码或安全验证")


def _candidate(item: Any, url: str, engine: str, kind: str) -> Candidate | None:
    safe = normalize_url(url)
    if not safe:
        return None
    platform, account = platform_of(safe)
    author_url = public_url(getattr(item, "author_url", ""))
    if not account and author_url:
        author_platform, author_account = platform_of(author_url)
        if author_platform == platform:
            account = author_account
    return Candidate(
        platform=platform,
        account=account,
        name=str(getattr(item, "author", "") or "")[:100],
        url=safe,
        title=str(getattr(item, "title", "") or "")[:3000],
        source=str(getattr(item, "source", "") or "")[:100],
        engine=engine,
        kind=kind,
        # 引擎自称“完全匹配”也只算强线索；只有下载配图并同图比对后才升级。
        verified=False,
        level=LEVEL_LINK if kind == "exact" else LEVEL_MENTION,
    )


def _parse_response(response: Any, engine: str) -> list[Candidate]:
    groups: list[tuple[list[Any], str]] = [(getattr(response, "raw", []), "visual")]
    if engine == "baidu":
        groups.insert(0, (getattr(response, "exact_matches", []), "exact"))
    if engine == "copyseeker":
        groups = [(getattr(response, "raw", []), "exact")]
    if engine == "google_lens":
        groups = [(getattr(response, "raw", []), "exact")]
    if engine == "saucenao":
        groups = [(getattr(response, "raw", []), "similarity")]

    results: list[Candidate] = []
    for items, kind in groups:
        for item in items:
            links: list[str] = []
            primary = getattr(item, "url", "") or ""
            if primary:
                links.append(primary)
            elif engine == "ascii2d":
                links.extend(
                    getattr(link, "href", "") for link in getattr(item, "url_list", [])
                )
            if engine == "saucenao":
                try:
                    similarity = float(getattr(item, "similarity", 0) or 0)
                except (TypeError, ValueError):
                    similarity = 0
                if similarity < 60:
                    continue
                links.extend(getattr(item, "ext_urls", []) or [])
            for link in links:
                result = _candidate(item, link, engine, kind)
                if result is not None:
                    results.append(result)
    return results


async def _pic_search(
    engine: str,
    image: bytes,
    proxy: str | None,
    timeout: float,
    saucenao_key: str,
) -> list[Candidate]:
    from PicImageSearch import BaiDu, Yandex, Ascii2D, Network, SauceNAO, Copyseeker, GoogleLens

    async with Network(proxies=proxy, timeout=timeout) as client:
        client.event_hooks["response"].append(_guard)
        if engine == "google_lens":
            response = await GoogleLens(client=client, search_type="exact_matches").search(file=image)
        elif engine == "baidu":
            response = await BaiDu(client=client).search(file=image)
        elif engine == "copyseeker":
            response = await Copyseeker(client=client).search(file=image)
        elif engine == "yandex":
            response = await Yandex(base_url="https://yandex.ru", client=client).search(file=image)
        elif engine == "ascii2d":
            response = await Ascii2D(client=client).search(file=image)
        else:
            response = await SauceNAO(
                client=client, api_key=saucenao_key, numres=10, minsim=60
            ).search(file=image)
            status = getattr(response, "status", None)
            if status is not None and status < 0:
                raise EngineError("SauceNAO 拒绝请求，请检查 API Key 或额度")
    return _parse_response(response, engine)


async def run_engine(
    engine: str,
    image: bytes,
    *,
    proxy: str | None,
    timeout: float,
    serpapi_key: str,
    serpapi_visual: bool,
    saucenao_key: str,
) -> tuple[list[Candidate], str]:
    """返回 (候选列表, 安全错误文本)。单引擎失败不影响其他引擎。"""
    if engine not in SUPPORTED_ENGINES:
        return [], "不支持的搜索引擎"
    if engine == "saucenao" and not saucenao_key:
        return [], "未配置 SauceNAO Key"

    async with _SEMAPHORE:
        try:
            if engine == "google_lens" and serpapi_key:
                payload = await lens_search(
                    image, serpapi_key, timeout, proxy, visual=serpapi_visual
                )
                results: list[Candidate] = []
                for key, kind in (("exact", "exact"), ("visual", "visual")):
                    for item in payload.get(key, []):
                        result = _candidate(
                            type("Meta", (), {"title": item.get("title", ""), "source": item.get("source", "")}),
                            item.get("link", ""),
                            engine,
                            kind,
                        )
                        if result is not None:
                            results.append(result)
                return results, ""
            results = await asyncio.wait_for(
                _pic_search(engine, image, proxy, timeout, saucenao_key), timeout=timeout
            )
            return results, ""
        except asyncio.TimeoutError:
            return [], "检索超时"
        except SerpApiError as exc:
            return [], str(exc)
        except EngineError as exc:
            return [], str(exc)
        except Exception as exc:  # 异常文本可能包含代理凭据，只记录类型
            logger.warning("[ShadowSeek] %s 引擎失败：%s", engine, type(exc).__name__)
            return [], "检索请求或结果解析失败"
