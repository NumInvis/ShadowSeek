"""轻量数据结构：候选来源、检索报告与最终选择。"""

from __future__ import annotations

from typing import Any, Optional
from dataclasses import field, replace, dataclass

# 证据等级：数字越大越强，但“相似图来源”永远封顶为 1。
LEVEL_MENTION = 1
LEVEL_LINK = 2
LEVEL_SIGNED = 3


@dataclass(frozen=True)
class Candidate:
    """一条可能指向图片出处的公开来源。"""

    platform: str = "other"          # X / 微博 / 哔哩哔哩 / other
    account: str = ""                # @handle 或 UID xxx
    name: str = ""
    url: str = ""
    title: str = ""
    source: str = ""                 # 来源站点名
    engine: str = ""                 # 哪个识图引擎给出
    kind: str = "clue"               # exact / verified / quote / clue
    level: int = 0                   # 线索强度
    hosts: tuple[str, ...] = ()      # 支持该线索的来源站点
    verified: bool = False           # 是否已通过同图比对
    relation: bool = False           # 是否通过引用关系 + 被引用帖同图
    followers: Optional[int] = None  # 仅用于多候选时的排序，不作为证据
    chosen_reason: str = ""

    @property
    def handle(self) -> str:
        return self.account.lstrip("@").casefold()

    def with_(
        self,
        *,
        account: Optional[str] = None,
        name: Optional[str] = None,
        url: Optional[str] = None,
        level: Optional[int] = None,
        hosts: Optional[tuple[str, ...]] = None,
        verified: Optional[bool] = None,
        relation: Optional[bool] = None,
        followers: Optional[int] = None,
        chosen_reason: Optional[str] = None,
        kind: Optional[str] = None,
        title: Optional[str] = None,
    ) -> "Candidate":
        return replace(
            self,
            account=self.account if account is None else account,
            name=self.name if name is None else name,
            url=self.url if url is None else url,
            level=self.level if level is None else level,
            hosts=self.hosts if hosts is None else hosts,
            verified=self.verified if verified is None else verified,
            relation=self.relation if relation is None else relation,
            followers=self.followers if followers is None else followers,
            chosen_reason=self.chosen_reason if chosen_reason is None else chosen_reason,
            kind=self.kind if kind is None else kind,
            title=self.title if title is None else title,
        )


@dataclass
class Report:
    """一次觅影的完整结果与诊断信息。"""

    candidates: list[Candidate] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    # 各引擎的原始命中数量，便于排查“没搜到”还是“没读到”
    engine_counts: dict[str, int] = field(default_factory=dict)
    # 来源页读取状态：域名 -> read / empty / HTTP 403 / timeout
    page_status: dict[str, str] = field(default_factory=dict)
    exact_pages: int = 0
    visual_pages: int = 0
    looked_up_posts: int = 0
    llm: dict[str, Any] = field(default_factory=dict)
    cache_hit: bool = False

    def copy(self) -> "Report":
        return Report(
            candidates=list(self.candidates),
            errors=dict(self.errors),
            engine_counts=dict(self.engine_counts),
            page_status=dict(self.page_status),
            exact_pages=self.exact_pages,
            visual_pages=self.visual_pages,
            looked_up_posts=self.looked_up_posts,
            llm=dict(self.llm),
            cache_hit=self.cache_hit,
        )


@dataclass
class Selection:
    """排序后的展示结果。"""

    primary: Optional[Candidate] = None
    alternates: list[Candidate] = field(default_factory=list)
    ambiguous: bool = False
    # 是否存在“同图页面但没拿到 X 来源”的情况
    found_pages_without_x: bool = False
