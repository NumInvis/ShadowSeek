"""来源网页读取：正文、外链、账号提及与署名行。

只抓公网 HTML，限制体积和超时；解析结果用于生成弱线索，不作为同图证据。
"""

from __future__ import annotations

import re
from dataclasses import field, dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from .urls import public_url, canonical_x, platform_of
from .models import LEVEL_LINK, LEVEL_MENTION, Candidate

MAX_PAGE_BYTES = 512 * 1024
MAX_PAGE_CHARS = 6000
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "article",
    "section",
    "header",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "tr",
    "td",
    "th",
    "blockquote",
}
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_.])@([A-Za-z0-9_]{1,15})")
_SOURCE_RE = re.compile(
    r"(来源|出处|作者|投稿|搬运|转自|source|src|via|credit|author|by)\s*[:：]?",
    re.I,
)


@dataclass
class PageClues:
    """一个来源网页能提供的文字线索。"""

    text: str = ""
    links: list[str] = field(default_factory=list)
    source_lines: list[str] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            safe = public_url(href)
            if safe:
                self.links.append(safe)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data)


def parse_html(html: str) -> PageClues:
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError):
        pass
    raw = "".join(parser.parts)
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
    text = "\n".join(line for line in lines if line)[:MAX_PAGE_CHARS]
    links = list(dict.fromkeys(parser.links))[:200]
    return PageClues(text=text, links=links, source_lines=_source_lines(text))


def _source_lines(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        clean = line.strip(" -—·|")
        if not 4 <= len(clean) <= 120:
            continue
        if not _SOURCE_RE.search(clean):
            continue
        if clean not in found:
            found.append(clean)
        if len(found) >= 3:
            break
    return found


def mentions(text: str) -> list[str]:
    handles: list[str] = []
    for handle in _MENTION_RE.findall(text):
        folded = handle.casefold()
        if folded not in {item.casefold() for item in handles}:
            handles.append(handle)
    return handles[:20]


def page_candidates(page_url: str, clues: PageClues) -> list[Candidate]:
    """把来源页里的 X 链接和 @提及 转成候选。"""
    host = (urlsplit(page_url).hostname or "").lower()
    results: list[Candidate] = []
    seen: set[str] = set()

    for link in clues.links:
        target = canonical_x(link)
        if not target or target in seen:
            continue
        platform, account = platform_of(target)
        if platform != "X":
            continue
        seen.add(target)
        results.append(
            Candidate(
                platform="X",
                account=account,
                url=target,
                source=host,
                engine="page",
                kind="link",
                level=LEVEL_LINK,
                hosts=(host,) if host else (),
            )
        )

    covered = {
        item.handle for item in results if item.handle
    }
    for handle in mentions(clues.text):
        if handle.casefold() in covered:
            continue
        covered.add(handle.casefold())
        results.append(
            Candidate(
                platform="X",
                account=f"@{handle}",
                url=f"https://x.com/{handle}",
                source=host,
                engine="page",
                kind="clue",
                level=LEVEL_MENTION,
                hosts=(host,) if host else (),
            )
        )
    return results


async def fetch_page(
    url: str,
    proxy: str,
    timeout: float,
) -> tuple[PageClues, str]:
    """返回 (页面线索, 状态)。状态用于诊断，例如 HTTP 403 / timeout。"""
    safe = public_url(url)
    if not safe:
        return PageClues(), "链接被拒绝"
    try:
        async with httpx.AsyncClient(
            proxy=proxy or None,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream("GET", safe) as response:
                status = response.status_code
                if status in {401, 403, 404, 429}:
                    return PageClues(), f"HTTP {status}"
                if status >= 300:
                    return PageClues(), f"HTTP {status}"
                content_type = str(response.headers.get("content-type", "")).lower()
                if "html" not in content_type and "text/plain" not in content_type:
                    return PageClues(), "非 HTML"
                buf = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(buf) + len(chunk) > MAX_PAGE_BYTES:
                        break
                    buf.extend(chunk)
        clues = parse_html(bytes(buf).decode(response.encoding or "utf-8", "ignore"))
        if not clues.text and not clues.links:
            return clues, "空页面"
        return clues, "read"
    except httpx.TimeoutException:
        return PageClues(), "timeout"
    except (httpx.HTTPError, UnicodeError, ValueError):
        return PageClues(), "请求失败"
