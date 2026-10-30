"""URL 归一化与平台识别。

只接受公网 HTTP(S) 链接，拒绝内网、凭据和可疑字符，避免把检索结果带进 SSRF。
"""

from __future__ import annotations

import re
import ipaddress
from urllib.parse import unquote, parse_qs, urlsplit, urlunsplit

_X_HOSTS = {
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
}
_WEIBO_HOSTS = {
    "weibo.com",
    "www.weibo.com",
    "m.weibo.com",
    "weibo.cn",
    "www.weibo.cn",
    "m.weibo.cn",
}
_BILI_HOSTS = {
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
    "space.bilibili.com",
    "t.bilibili.com",
}
_X_RESERVED = {
    "home",
    "explore",
    "search",
    "i",
    "intent",
    "hashtag",
    "share",
    "settings",
    "messages",
    "notifications",
    "login",
    "logout",
    "signup",
    "tos",
    "privacy",
    "about",
    "help",
    "compose",
    "download",
    "account",
    "communities",
    "jobs",
}
_HANDLE_RE = re.compile(r"[A-Za-z0-9_]{1,15}")


def public_url(value: object) -> str:
    """返回可安全请求的公开 URL，否则返回空串。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    raw = value.strip()
    if any(ch in raw for ch in "\r\n\\"):
        return ""
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            return ""
        if parsed.username or parsed.password:
            return ""
        if host.endswith((".localhost", ".local", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                return ""
        parsed.port  # 非法端口会抛 ValueError
        return urlunsplit(
            (parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
        )
    except (TypeError, ValueError):
        return ""


def canonical_x(value: object) -> str:
    """把 X/Twitter 链接收敛为 ``https://x.com/...``。

    帖子 ID 在不同域名、不同作者路径下都代表同一条帖子，因此统一到
    ``/status/<id>``；额外路径（photo/1）和查询参数全部丢弃。
    """
    url = public_url(value)
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.hostname not in _X_HOSTS:
        return ""
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    head = parts[0].lower()
    if head == "intent" and len(parts) > 1 and parts[1] == "user":
        uid = parse_qs(parsed.query).get("user_id", [""])[0]
        return f"https://x.com/i/user/{uid}" if uid.isdigit() else "https://x.com/i"
    if not _HANDLE_RE.fullmatch(parts[0]) or head in _X_RESERVED:
        # 状态 ID 仍可定位，但无法确定作者时不生成作者路径。
        status = re.search(r"/(?:status|statuses)/(\d+)(?:/|$)", parsed.path)
        return f"https://x.com/i/status/{status[1]}" if status else ""
    if len(parts) == 1:
        return f"https://x.com/{parts[0]}"
    if len(parts) in {3, 5} and parts[1] == "status" and parts[2].isdigit():
        if len(parts) == 5 and not (parts[3] in {"photo", "video"} and parts[4].isdigit()):
            return ""
        return f"https://x.com/{parts[0]}/status/{parts[2]}"
    return ""


def normalize_url(value: object) -> str:
    """识别结果里的链接统一格式；X 走 canonical_x，其余仅去尾斜杠。"""
    url = public_url(value)
    if not url:
        return ""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host in _X_HOSTS:
        return canonical_x(url) or urlunsplit(("https", "x.com", parsed.path or "/", "", ""))
    path = parsed.path.rstrip("/") or "/"
    if host in _WEIBO_HOSTS or host in _BILI_HOSTS:
        return urlunsplit(("https", parsed.netloc, path, "", ""))
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def platform_of(url: str) -> tuple[str, str]:
    """返回 ``(平台, 账号)``；账号可能是 @handle、UID 或空串。"""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "other", ""
    host = (parsed.hostname or "").lower()
    parts = [unquote(part) for part in parsed.path.split("/") if part]

    if host in _X_HOSTS:
        if parts and parts[0].lower() == "intent" and len(parts) > 1 and parts[1] == "user":
            uid = parse_qs(parsed.query).get("user_id", [""])[0]
            return "X", f"UID {uid}" if uid.isdigit() else ""
        if parts and parts[0].lower() not in _X_RESERVED and _HANDLE_RE.fullmatch(parts[0]):
            return "X", f"@{parts[0]}"
        if parts and parts[0] == "i" and len(parts) >= 3 and parts[1] == "status":
            status = parts[2]
            return ("X", f"#status:{status}") if status.isdigit() else ("X", "")
        return "X", ""

    if host in _WEIBO_HOSTS:
        if len(parts) >= 2 and parts[0] in {"u", "profile"} and parts[1].isdigit():
            return "微博", f"UID {parts[1]}"
        if len(parts) >= 2 and parts[0] == "n":
            return "微博", f"@{parts[1]}"
        if parts and parts[0].isdigit() and host != "m.weibo.cn":
            return "微博", f"UID {parts[0]}"
        if (
            host in {"weibo.com", "www.weibo.com"}
            and len(parts) == 1
            and parts[0] not in {"login", "signup", "newlogin", "tv", "hot", "search"}
        ):
            return "微博", f"@{parts[0]}"
        return "微博", ""

    if host in _BILI_HOSTS:
        if host == "space.bilibili.com" and parts and parts[0].isdigit():
            return "哔哩哔哩", f"UID {parts[0]}"
        return "哔哩哔哩", ""

    return "other", ""


def status_id(url: str) -> str:
    """取出 X 帖子 ID；不是帖子链接时返回空串。"""
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.hostname not in _X_HOSTS:
        return ""
    match = re.search(r"/(?:status|statuses)/(\d+)(?:/|$)", parsed.path)
    return match[1] if match else ""


def is_x_status(url: str) -> bool:
    return bool(status_id(url))


def profile_handle(url: str) -> str:
    """取出 X 主页 handle（不带 @），不是主页时返回空串。"""
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.hostname not in _X_HOSTS:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 1 and _HANDLE_RE.fullmatch(parts[0]) and parts[0].lower() not in _X_RESERVED:
        return parts[0]
    return ""
