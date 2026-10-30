"""命令注册与消息处理。"""

from __future__ import annotations

import re
import time
import asyncio
from pathlib import Path

import httpx
from PIL import Image

from gsuid_core.sv import SV, get_plugin_available_prefix
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.help.utils import register_help

from .config import Settings, read_settings
from .service import seek
from .imageops import ImageError, load_source
from .formatting import HELP_TEXT, format_error, format_result

ASSETS = Path(__file__).resolve().parent.parent / "assets"

sv_shadowseek = SV("觅影", priority=5, area="ALL")
sv_help = SV("觅影帮助", priority=2)

_CQ_IMAGE = re.compile(r"\[CQ:image,([^\]]*)\]")
_IMAGE_URL = re.compile(r"\.(?:jpg|jpeg|png|webp|gif)(?:[?#].*)?$", re.I)

_cooldowns: dict[str, float] = {}
_cooldown_lock: asyncio.Lock | None = None
_slots_lock: asyncio.Lock | None = None
_slots: asyncio.Semaphore | None = None
_slots_limit = 0


def _cooldown_guard() -> asyncio.Lock:
    global _cooldown_lock
    if _cooldown_lock is None:
        _cooldown_lock = asyncio.Lock()
    return _cooldown_lock


def _slot_builder() -> asyncio.Lock:
    global _slots_lock
    if _slots_lock is None:
        _slots_lock = asyncio.Lock()
    return _slots_lock


async def _acquire_slot(limit: int) -> asyncio.Semaphore:
    global _slots, _slots_limit
    async with _slot_builder():
        if _slots is None or _slots_limit != limit:
            _slots = asyncio.Semaphore(limit)
            _slots_limit = limit
        semaphore = _slots
    await semaphore.acquire()
    return semaphore


async def _cooldown_wait(key: str, seconds: int) -> int:
    if seconds <= 0:
        return 0
    global _cooldowns
    now = time.monotonic()
    async with _cooldown_guard():
        last = _cooldowns.get(key, 0.0)
        if now - last < seconds:
            return int(seconds - (now - last)) + 1
        _cooldowns[key] = now
        if len(_cooldowns) > 2000:
            cutoff = now - seconds
            _cooldowns = {item: stamp for item, stamp in _cooldowns.items() if stamp >= cutoff}
    return 0


def _cq_images(text: str) -> list[str]:
    sources: list[str] = []
    for match in _CQ_IMAGE.finditer(text or ""):
        fields = {
            key: value
            for part in match[1].split(",")
            if "=" in part
            for key, value in (part.split("=", 1),)
        }
        for key in ("url", "file"):
            value = fields.get(key, "").strip()
            if value:
                sources.append(value)
    return sources


def _plain_image_urls(text: str) -> list[str]:
    urls: list[str] = []
    for url in re.findall(r"https?://[^\s\"'<>]+", text or "", re.I):
        if _IMAGE_URL.search(url) or "pbs.twimg.com/media" in url:
            urls.append(url)
    return urls


def _safe_prefix() -> str:
    try:
        return get_plugin_available_prefix("ShadowSeek")
    except ValueError:
        return ""


async def _reply_sources(ev: Event, settings: Settings) -> list[str]:
    sources = _cq_images(ev.reply or "")
    sources.extend(_plain_image_urls(ev.reply or ""))
    if sources or not settings.onebot_api_url or not ev.reply_id:
        return sources
    return await _onebot_reply_sources(ev.reply_id, settings)


async def _onebot_reply_sources(reply_id: str, settings: Settings) -> list[str]:
    message_id: int | str = int(reply_id) if reply_id.isdigit() else reply_id
    headers = (
        {"Authorization": f"Bearer {settings.onebot_access_token}"}
        if settings.onebot_access_token
        else {}
    )
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.post(
                f"{settings.onebot_api_url}/get_msg",
                json={"message_id": message_id},
                headers=headers,
            )
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    if not isinstance(payload, dict) or payload.get("retcode") not in {0, None}:
        return []
    message = (payload.get("data") or {}).get("message")
    sources: list[str] = []
    if isinstance(message, str):
        sources.extend(_cq_images(message))
        sources.extend(_plain_image_urls(message))
    elif isinstance(message, list):
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "image":
                continue
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
            for key in ("url", "file"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    sources.append(value.strip())
    return sources


async def _collect_sources(ev: Event, settings: Settings) -> list[str]:
    """同一条消息里的图片优先，其次才是引用消息。"""
    sources = [str(item) for item in ev.image_list if item]
    if not sources:
        sources = await _reply_sources(ev, settings)
    return list(dict.fromkeys(sources))


@sv_shadowseek.on_command(("找鸡", "觅影", "图源定位", "找原图"), block=True)
async def shadowseek_command(bot: Bot, ev: Event) -> None:
    settings = read_settings()
    if not settings.allowed_in(ev.group_id or None):
        return

    sources = await _collect_sources(ev, settings)
    if not sources:
        await bot.send(
            format_error(
                "图呢？发张图再配一句「觅影」，或者引用图片再发「觅影」。\n"
                f"发送「{_safe_prefix()}觅影帮助」看看怎么用。"
            )
        )
        return

    key = f"{ev.group_id or 'direct'}:{ev.user_id}"
    wait = await _cooldown_wait(key, settings.cooldown_seconds)
    if wait:
        await bot.send(format_error(f"别急，让子弹飞 {wait} 秒。"))
        return

    semaphore = await _acquire_slot(settings.max_concurrency)
    try:
        if settings.send_progress:
            await bot.send("【觅影】正在全网翻箱倒柜，顺着图找本尊，稍等…")
        raw = await load_source(
            sources[0], settings.proxy or None, timeout=min(settings.search_timeout, 12)
        )
        report, selection = await seek(raw, settings)
        await bot.send(format_result(selection, report))
        if (
            settings.send_result_image
            and selection.primary is not None
            and selection.primary.verified
            and (ASSETS / "success.png").is_file()
        ):
            await bot.send(MessageSegment.image(ASSETS / "success.png"))
    except ImageError as exc:
        await bot.send(format_error(str(exc)))
    except Exception as exc:
        logger.exception(f"[ShadowSeek] 觅影失败: {exc}")
        await bot.send(format_error("半路翻车了，稍后再来一趟。"))
    finally:
        semaphore.release()


@sv_help.on_fullmatch(("觅影帮助", "图源帮助", "ShadowSeek帮助"), block=True)
async def shadowseek_help(bot: Bot, ev: Event) -> None:
    prefix = _safe_prefix()
    text = HELP_TEXT if not prefix else HELP_TEXT.replace("「觅影」", f"「{prefix}觅影」")
    await bot.send(text)
    if (ASSETS / "help.png").is_file():
        await bot.send(MessageSegment.image(ASSETS / "help.png"))


if (ASSETS / "ICON.png").is_file():
    register_help(
        "ShadowSeek",
        f"{_safe_prefix()}觅影帮助",
        Image.open(ASSETS / "ICON.png"),
    )
else:
    register_help("ShadowSeek", f"{_safe_prefix()}觅影帮助")
