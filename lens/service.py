"""觅影流程编排：多引擎识图 → 来源页读线索 → X 元数据核验 → 排序。

流程刻意把“同图证明”和“文字线索”分开：只有下载到候选配图并完成同图比对，
才会把候选升级为已核实；其余线索再强也只用于展示或并列。
"""

from __future__ import annotations

import re
import asyncio
import logging
from urllib.parse import urlsplit

import httpx

from .llm import extract_source_line
from .rank import dedupe, select
from .urls import status_id, platform_of, normalize_url
from .cache import get_cache, report_payload, report_from_payload
from .clues import fetch_page, page_candidates
from .xmeta import (
    PostMeta,
    QuotePost,
    get_post,
    get_profile,
    resolve_quote,
    check_media_match,
)
from .config import Settings
from .models import (
    LEVEL_LINK,
    LEVEL_SIGNED,
    Report,
    Candidate,
    Selection,
)
from .engines import run_engine
from .serpapi import SerpApiError, x_site_search
from .imageops import dhash

logger = logging.getLogger(__name__)

_PAGE_CONCURRENCY = 3
_X_CONCURRENCY = 3
_SOURCE_PREFIX = re.compile(
    r"^\s*(?:来源|出处|作者|投稿|搬运|转自|source|src|via|credit|author|by)\s*[:：]\s*",
    re.I,
)


async def seek(image: bytes, settings: Settings) -> tuple[Report, Selection]:
    """执行一次完整觅影，返回报告与展示选择。"""
    digest = dhash(image)
    cache = get_cache()
    cached = await cache.get(digest, settings.cache_ttl_hours)
    if cached:
        report = report_from_payload(cached)
        if report is not None:
            return report, select(report.candidates, report.exact_pages)

    report = Report()
    candidates = dedupe(await _run_engines(image, settings, report))

    pages = _source_pages(candidates, settings.max_pages)
    if pages:
        page_items, page_text, source_lines = await _read_pages(pages, settings, report)
        candidates = dedupe(candidates + page_items)
        await _keyword_probe(page_text, source_lines, settings, report, candidates)

    report.candidates = dedupe(candidates)
    await _verify_x(report.candidates, image, settings, report)
    report.candidates = dedupe(report.candidates)
    report.exact_pages, report.visual_pages = _count_pages(report.candidates)

    selection = select(report.candidates, report.exact_pages)
    await cache.put(digest, report_payload(report), settings.cache_ttl_hours)
    return report, selection


async def _run_engines(
    image: bytes, settings: Settings, report: Report
) -> list[Candidate]:
    tasks = [
        run_engine(
            engine,
            image,
            proxy=settings.proxy or None,
            timeout=settings.search_timeout,
            serpapi_key=settings.serpapi_key,
            serpapi_visual=settings.serpapi_visual,
            saucenao_key=settings.saucenao_key,
        )
        for engine in settings.engines
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    candidates: list[Candidate] = []
    for engine, result in zip(settings.engines, results):
        if isinstance(result, BaseException):
            report.errors[engine] = "检索异常"
            continue
        items, error = result
        report.engine_counts[engine] = len(items)
        if error:
            report.errors[engine] = error
        candidates.extend(items)
    return candidates


def _source_pages(candidates: list[Candidate], limit: int) -> list[str]:
    exact: list[str] = []
    visual: list[str] = []
    for item in candidates:
        if item.platform == "X" or not item.url:
            continue
        bucket = exact if item.kind == "exact" else visual if item.kind == "visual" else None
        if bucket is not None:
            bucket.append(item.url)
    ordered = list(dict.fromkeys(exact)) + list(dict.fromkeys(visual))
    return ordered[:limit]


async def _read_pages(
    pages: list[str], settings: Settings, report: Report
) -> tuple[list[Candidate], str, list[str]]:
    semaphore = asyncio.Semaphore(_PAGE_CONCURRENCY)
    timeout = min(settings.search_timeout, 12)

    async def one(url: str) -> tuple[list[Candidate], str, list[str]]:
        async with semaphore:
            clues, status = await fetch_page(url, settings.proxy, timeout)
        host = (urlsplit(url).hostname or url).lower()
        report.page_status[host] = status
        if status != "read":
            return [], "", []
        return page_candidates(url, clues), clues.text, clues.source_lines

    results = await asyncio.gather(*(one(url) for url in pages))
    candidates: list[Candidate] = []
    texts: list[str] = []
    lines: list[str] = []
    for items, text, source_lines in results:
        candidates.extend(items)
        if text:
            texts.append(text)
        lines.extend(source_lines)
    return candidates, "\n\n".join(texts)[:4000], lines


async def _keyword_probe(
    page_text: str,
    source_lines: list[str],
    settings: Settings,
    report: Report,
    candidates: list[Candidate],
) -> None:
    """用来源页里的一句原文去 X 限定搜索，补回引擎没直接给出的帖子。"""
    if not settings.x_keyword_search or not page_text or not settings.serpapi_key:
        return
    keyword = await _source_keyword(page_text, source_lines, settings)
    if not keyword:
        return
    report.llm["keyword"] = keyword
    try:
        items = await x_site_search(
            keyword,
            settings.serpapi_key,
            settings.search_timeout,
            settings.proxy or None,
        )
    except SerpApiError as exc:
        report.errors["keyword"] = str(exc)
        return
    except Exception as exc:
        logger.warning("[ShadowSeek] 关键词反查失败：%s", type(exc).__name__)
        report.errors["keyword"] = "关键词反查失败"
        return
    for item in items:
        url = normalize_url(item.get("link", ""))
        if not url:
            continue
        platform, account = platform_of(url)
        if platform != "X":
            continue
        candidates.append(
            Candidate(
                platform="X",
                account=account,
                url=url,
                title=str(item.get("title", ""))[:3000],
                source="serpapi",
                engine="keyword",
                kind="link",
                level=LEVEL_LINK,
                hosts=("serpapi",),
            )
        )


async def _source_keyword(
    page_text: str, source_lines: list[str], settings: Settings
) -> str:
    if settings.llm_enabled and settings.llm_api_key:
        result = await extract_source_line(
            page_text,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout,
            proxy=settings.proxy,
        )
        if result.get("source_line"):
            return _clean_keyword(result["source_line"])
    return _clean_keyword(source_lines[0]) if source_lines else ""


def _clean_keyword(line: str) -> str:
    clean = _SOURCE_PREFIX.sub("", " ".join(line.split())).strip()
    return clean[:100] if len(clean) >= 4 else ""


async def _verify_x(
    candidates: list[Candidate],
    image: bytes,
    settings: Settings,
    report: Report,
) -> None:
    """读取 X 帖子/账号元数据，并只在配图同图时升级为已核实。"""
    statuses: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        sid = status_id(item.url)
        if sid and sid not in seen:
            seen.add(sid)
            statuses.append((sid, item.account))

    async with httpx.AsyncClient(
        proxy=settings.proxy or None,
        timeout=settings.x_timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        if statuses:
            posts = await _lookup_posts(
                client, statuses[: settings.max_posts], image, settings, report
            )
            if posts:
                _apply_posts(candidates, posts)

        handles = _unknown_handles(candidates)
        if len(handles) > 1:
            profiles = await _lookup_profiles(
                client, handles[: settings.max_posts], settings
            )
            if profiles:
                _apply_profiles(candidates, profiles)


async def _lookup_posts(
    client: httpx.AsyncClient,
    statuses: list[tuple[str, str]],
    image: bytes,
    settings: Settings,
    report: Report,
) -> dict[str, tuple[PostMeta, bool, QuotePost | None]]:
    semaphore = asyncio.Semaphore(_X_CONCURRENCY)

    async def one(sid: str, hint: str) -> tuple[str, PostMeta, bool, QuotePost | None] | None:
        async with semaphore:
            meta = await get_post(client, sid, hint)
            if meta is None:
                return None
            matched = bool(meta.media) and await check_media_match(
                client, image, meta.media, settings.nitter_base
            )
            quote = None
            if not matched:
                quote = await resolve_quote(client, image, meta, settings.nitter_base)
        return sid, meta, matched, quote

    results = await asyncio.gather(
        *(one(sid, hint) for sid, hint in statuses), return_exceptions=True
    )
    updates: dict[str, tuple[PostMeta, bool, QuotePost | None]] = {}
    for result in results:
        if result is None or isinstance(result, BaseException):
            continue
        sid, meta, matched, quote = result
        report.looked_up_posts += 1
        updates[sid] = (meta, matched, quote)
    return updates


def _apply_posts(
    candidates: list[Candidate],
    updates: dict[str, tuple[PostMeta, bool, QuotePost | None]],
) -> None:
    for index, item in enumerate(candidates):
        sid = status_id(item.url)
        if sid not in updates:
            continue
        meta, matched, quote = updates[sid]
        account = item.account or (f"@{meta.handle}" if meta.handle else "")
        name = item.name or meta.name
        if quote is not None:
            candidates[index] = item.with_(
                account=f"@{quote.handle}" if quote.handle else account,
                name=name or quote.name,
                url=quote.url,
                level=LEVEL_SIGNED,
                verified=True,
                relation=True,
                kind="quote",
                followers=meta.followers,
            )
        elif matched:
            candidates[index] = item.with_(
                account=account,
                name=name,
                level=LEVEL_SIGNED,
                verified=True,
                kind="exact",
                followers=meta.followers,
            )
        else:
            candidates[index] = item.with_(
                account=account,
                name=name,
                followers=meta.followers,
            )


def _unknown_handles(candidates: list[Candidate]) -> list[str]:
    handles: list[str] = []
    for item in candidates:
        if item.platform != "X" or item.followers is not None:
            continue
        handle = item.handle
        if handle and handle not in handles:
            handles.append(handle)
    return handles


async def _lookup_profiles(
    client: httpx.AsyncClient,
    handles: list[str],
    settings: Settings,
) -> dict[str, tuple[str, int | None]]:
    semaphore = asyncio.Semaphore(_X_CONCURRENCY)

    async def one(handle: str) -> tuple[str, str, int | None] | None:
        async with semaphore:
            meta = await get_profile(client, handle, settings.nitter_base)
        if meta is None:
            return None
        return handle, meta.name, meta.followers

    results = await asyncio.gather(
        *(one(handle) for handle in handles), return_exceptions=True
    )
    updates: dict[str, tuple[str, int | None]] = {}
    for result in results:
        if result is None or isinstance(result, BaseException):
            continue
        handle, name, followers = result
        updates[handle] = (name, followers)
    return updates


def _apply_profiles(
    candidates: list[Candidate],
    updates: dict[str, tuple[str, int | None]],
) -> None:
    for index, item in enumerate(candidates):
        if item.platform != "X":
            continue
        found = updates.get(item.handle)
        if not found:
            continue
        name, followers = found
        candidates[index] = item.with_(
            name=item.name or name,
            followers=item.followers if item.followers is not None else followers,
        )


def _count_pages(candidates: list[Candidate]) -> tuple[int, int]:
    exact: set[str] = set()
    visual: set[str] = set()
    for item in candidates:
        if item.platform == "X" or not item.url:
            continue
        host = (urlsplit(item.url).hostname or "").lower()
        if not host:
            continue
        if item.kind == "exact":
            exact.add(host)
        elif item.kind == "visual":
            visual.add(host)
    return len(exact), len(visual)
