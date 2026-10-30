"""读取 X 公开元数据：作者、关注者、配图与引用帖。

优先 fxTwitter 公共接口，失败后回退 X syndication / oembed；不依赖任何第三方插件。
"""

from __future__ import annotations

import re
from typing import Any
from dataclasses import field, dataclass
from urllib.parse import urlsplit

import httpx

from .urls import status_id
from .models import LEVEL_MENTION

_FX_STATUS = "https://api.fxtwitter.com/2/status/{id}"
_FX_STATUS_LEGACY = "https://api.fxtwitter.com/{handle}/status/{id}"
_FX_PROFILE = "https://api.fxtwitter.com/{handle}"
_SYNDICATION = "https://cdn.syndication.twimg.com/tweet-result?id={id}&token=shadow"
_OEMBED = "https://publish.twitter.com/oembed"
_HANDLE_RE = re.compile(r"[A-Za-z0-9_]{1,15}")


@dataclass
class QuotePost:
    handle: str = ""
    name: str = ""
    post_id: str = ""
    media: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        if not self.post_id:
            return ""
        handle = self.handle or "i"
        return f"https://x.com/{handle}/status/{self.post_id}"


@dataclass
class PostMeta:
    handle: str = ""
    name: str = ""
    followers: int | None = None
    media: list[str] = field(default_factory=list)
    quote: QuotePost | None = None
    source: str = ""


@dataclass
class ProfileMeta:
    handle: str = ""
    name: str = ""
    followers: int | None = None


def _as_int(value: Any) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9][0-9,\s]*", value.strip()):
        return int(re.sub(r"[,\s]", "", value))
    return None


def _media_urls(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.startswith(("http://", "https://")) else []
    if isinstance(value, list):
        urls: list[str] = []
        for item in value:
            urls.extend(_media_urls(item))
        return urls
    if isinstance(value, dict):
        for key in ("all", "photos", "videos", "mediaDetails"):
            if key in value:
                return _media_urls(value[key])
        for key in ("url", "media_url_https", "media_url"):
            if isinstance(value.get(key), str):
                return [value[key]]
    return []


def _parse_quote(value: Any) -> QuotePost | None:
    if not isinstance(value, dict):
        return None
    author = value.get("author") if isinstance(value.get("author"), dict) else {}
    handle = str(
        author.get("screen_name")
        or value.get("screen_name")
        or value.get("username")
        or ""
    ).lstrip("@")
    post_id = str(value.get("id") or value.get("tweet_id") or "")
    if not post_id:
        post_id = status_id(str(value.get("url") or ""))
    if not post_id and not handle:
        return None
    if not _HANDLE_RE.fullmatch(handle):
        handle = ""
    name = str(author.get("name") or value.get("name") or "")[:100]
    return QuotePost(
        handle=handle,
        name=name,
        post_id=post_id,
        media=_media_urls(value.get("media") or value.get("mediaDetails")),
    )


def _normalize_tweet(payload: Any, source: str) -> PostMeta | None:
    if not isinstance(payload, dict):
        return None
    tweet = payload.get("tweet") if isinstance(payload.get("tweet"), dict) else payload
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    handle = str(
        author.get("screen_name")
        or tweet.get("screen_name")
        or author.get("username")
        or ""
    ).lstrip("@")
    if not _HANDLE_RE.fullmatch(handle):
        handle = ""
    name = str(author.get("name") or tweet.get("user_name") or "")[:100]
    followers = _as_int(author.get("followers") or author.get("followers_count"))
    media = _media_urls(tweet.get("media") or tweet.get("mediaDetails") or tweet.get("photos"))
    quote = _parse_quote(tweet.get("quote") or tweet.get("quoted_tweet") or tweet.get("quoted_status"))
    return PostMeta(
        handle=handle,
        name=name,
        followers=followers,
        media=media,
        quote=quote,
        source=source,
    )


async def get_post(
    client: httpx.AsyncClient,
    post_id: str,
    author_hint: str = "",
) -> PostMeta | None:
    """按 fxTwitter → syndication → oembed 的顺序取帖子元数据。"""
    if not post_id.isdigit():
        return None
    candidates: list[tuple[str, dict[str, Any]]] = []
    handle = author_hint.lstrip("@")

    async def get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            response = await client.get(url, params=params, follow_redirects=False)
            if response.status_code != 200:
                return None
            data = response.json()
            return data if isinstance(data, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    data = await get_json(_FX_STATUS.format(id=post_id))
    if data:
        candidates.append(("fxtwitter", data))
    if handle and handle != "i":
        data = await get_json(_FX_STATUS_LEGACY.format(id=post_id, handle=handle))
        if data:
            candidates.append(("fxtwitter", data))
    data = await get_json(_SYNDICATION.format(id=post_id))
    if data:
        candidates.append(("syndication", data))
    data = await get_json(
        _OEMBED,
        {"url": f"https://twitter.com/i/status/{post_id}", "omit_script": "true"},
    )
    if data:
        candidates.append(("oembed", data))

    for source, payload in candidates:
        if source == "oembed":
            author_url = str(payload.get("author_url") or "")
            parsed_handle = urlsplit(author_url).path.strip("/").split("/")[0]
            if not _HANDLE_RE.fullmatch(parsed_handle):
                continue
            return PostMeta(
                handle=parsed_handle,
                name=str(payload.get("author_name") or "")[:100],
                source="oembed",
            )
        meta = _normalize_tweet(payload, source)
        if meta and (meta.handle or meta.media or meta.name):
            return meta
    return None


async def get_profile(
    client: httpx.AsyncClient,
    handle: str,
    nitter_base: str = "",
) -> ProfileMeta | None:
    """读取账号显示名和关注者数；X 接口失败时可选走 Nitter。"""
    clean = handle.lstrip("@")
    if not _HANDLE_RE.fullmatch(clean):
        return None
    try:
        response = await client.get(
            _FX_PROFILE.format(handle=clean), follow_redirects=False
        )
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict):
                node = payload.get("user") or payload.get("profile") or payload.get("tweet") or payload
                if isinstance(node, dict):
                    found = str(node.get("screen_name") or node.get("username") or "").lstrip("@")
                    if found.casefold() == clean.casefold():
                        return ProfileMeta(
                            handle=found,
                            name=str(node.get("name") or node.get("screen_name") or "")[:100],
                            followers=_as_int(node.get("followers") or node.get("followers_count")),
                        )
    except (httpx.HTTPError, ValueError):
        pass

    if not nitter_base:
        return None
    try:
        response = await client.get(nitter_base.rstrip("/") + "/" + clean, follow_redirects=False)
        if response.status_code != 200:
            return None
        page = response.text
        match = re.search(
            r'class="profile-statlist[^"]*".*?followers.*?profile-stat-num[^>]*>([^<]+)<',
            page,
            re.I | re.S,
        )
        name_match = re.search(r'<h1[^>]*class="profile-card-fullname"[^>]*>([^<]+)<', page, re.I)
        return ProfileMeta(
            handle=clean,
            name=(name_match[1].strip() if name_match else "")[:100],
            followers=_as_int(match[1]) if match else None,
        )
    except httpx.HTTPError:
        return None


def media_allowed(url: str, nitter_base: str = "") -> bool:
    """只允许 Twitter CDN 或已配置的 Nitter 图片路径。"""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return False
        if parsed.hostname == "pbs.twimg.com" and parsed.port in {None, 80, 443}:
            return True
        base = urlsplit(nitter_base)
        return bool(
            base.hostname
            and (parsed.scheme, parsed.hostname, parsed.port)
            == (base.scheme, base.hostname, base.port)
            and parsed.path.startswith(base.path.rstrip("/") + "/pic/")
        )
    except ValueError:
        return False


async def fetch_media(
    client: httpx.AsyncClient,
    url: str,
    nitter_base: str = "",
    max_bytes: int = 10 * 1024 * 1024,
) -> bytes | None:
    if not media_allowed(url, nitter_base):
        return None
    try:
        async with client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code != 200:
                return None
            length = response.headers.get("content-length", "")
            if length.isdigit() and int(length) > max_bytes:
                return None
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(data) + len(chunk) > max_bytes:
                    return None
                data.extend(chunk)
            return bytes(data)
    except httpx.HTTPError:
        return None


async def check_media_match(
    client: httpx.AsyncClient,
    original: bytes,
    urls: list[str],
    nitter_base: str = "",
) -> bool:
    from .imageops import same_image

    for url in urls[:3]:
        content = await fetch_media(client, url, nitter_base)
        if content and same_image(original, content):
            return True
    return False


async def resolve_quote(
    client: httpx.AsyncClient,
    original: bytes,
    meta: PostMeta,
    nitter_base: str = "",
) -> QuotePost | None:
    """只有引用帖自己的配图也匹配，才把它升级为已核实来源。"""
    quote = meta.quote
    if quote is None:
        return None
    if quote.media and await check_media_match(client, original, quote.media, nitter_base):
        return quote
    if quote.post_id:
        detail = await get_post(client, quote.post_id, quote.handle)
        if detail and detail.media and await check_media_match(
            client, original, detail.media, nitter_base
        ):
            quote.handle = quote.handle or detail.handle
            quote.name = quote.name or detail.name
            return quote
    return None


def clue_from_meta(meta: PostMeta) -> int:
    """帖子元数据本身只能说明账号存在，不能证明图片归属。"""
    return LEVEL_MENTION
