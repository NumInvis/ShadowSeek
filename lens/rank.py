"""候选合并与排序。

原则：
1. 同图证据永远优先于文字线索；
2. 相似图引擎给出的线索强度封顶为 1，不能当作同图证明；
3. 没有同图证据时，只在证据明显领先的情况下给出单一账号，否则展示两个并列候选。
"""

from __future__ import annotations

from collections import defaultdict

from .urls import status_id, is_x_status, platform_of
from .models import LEVEL_LINK, LEVEL_SIGNED, LEVEL_MENTION, Candidate, Selection


def candidate_key(candidate: Candidate) -> str:
    sid = status_id(candidate.url)
    if sid:
        return f"x:status:{sid}"
    return candidate.url


def _rank_key(candidate: Candidate) -> tuple:
    return (
        int(candidate.verified),
        int(candidate.relation),
        int(is_x_status(candidate.url)),
        candidate.level,
        len(set(candidate.hosts)),
        int(candidate.engine == "google_lens"),
    )


def merge(first: Candidate, second: Candidate) -> Candidate:
    """合并同一条来源，保留更强证据和可用元数据。"""
    winner, other = (
        (second, first) if _rank_key(second) > _rank_key(first) else (first, second)
    )
    # 同一帖子 ID 可能出现在不同作者路径下；作者不一致时只保留强者。
    if winner.account and other.account and winner.account.casefold() != other.account.casefold():
        return winner
    account = winner.account or other.account
    url = winner.url
    if not platform_of(url)[1] and platform_of(other.url)[1]:
        url = other.url
    level = max(winner.level, other.level)
    hosts = tuple(
        sorted(
            {
                host
                for item in (winner, other)
                if item.level == level
                for host in item.hosts
            }
        )
    )
    return winner.with_(
        account=account,
        name=winner.name or other.name,
        url=url,
        level=level,
        hosts=hosts,
        followers=winner.followers if winner.followers is not None else other.followers,
        verified=winner.verified or other.verified,
        relation=winner.relation or other.relation,
    )


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    unique: dict[str, Candidate] = {}
    for candidate in candidates:
        key = candidate_key(candidate)
        if key in unique:
            unique[key] = merge(unique[key], candidate)
        else:
            unique[key] = candidate
    return list(unique.values())


def _reason(candidate: Candidate) -> str:
    if candidate.relation:
        return "引用关系与被引用帖配图均比对通过"
    if candidate.verified:
        if candidate.kind == "quote":
            return "被引用帖配图比对通过"
        if candidate.level >= LEVEL_SIGNED:
            return "同图匹配，且来源明确署名与该账号一致"
        if is_x_status(candidate.url):
            return "同图匹配，优先展示具体帖子"
        return "同图匹配，引擎直接给出该来源"
    return {
        LEVEL_SIGNED: "来源明确署名该账号，但尚未确认同图",
        LEVEL_LINK: "来源直接给出帖子链接，但尚未确认同图",
        LEVEL_MENTION: "来源文字提及该账号，尚未确认同图",
    }.get(candidate.level, "仅有弱线索，尚未确认同图")


def select(candidates: list[Candidate], exact_pages: int = 0) -> Selection:
    """从候选里挑出最多两条可展示的 X 来源。"""
    x_candidates = [item for item in candidates if item.platform == "X"]
    verified = [item for item in x_candidates if item.verified]
    if verified:
        best = max(verified, key=_rank_key)
        return Selection(
            primary=best.with_(chosen_reason=best.chosen_reason or _reason(best)),
            found_pages_without_x=False,
        )

    groups: dict[str, list[Candidate]] = defaultdict(list)
    for item in x_candidates:
        account = platform_of(item.url)[1]
        if not account:
            account = item.account
        if item.level > 0 and (account.startswith("@") or account.startswith("#status:")):
            groups[account.casefold()].append(item)

    if not groups:
        return Selection(found_pages_without_x=bool(exact_pages))

    # 关注者数只用于在多个“同名候选”里挑一个，不作为同图证明。
    counted = [item for items in groups.values() for item in items if item.followers is not None]
    if counted:
        best = max(
            counted,
            key=lambda item: (
                item.followers,
                item.level,
                len(set(item.hosts)),
                is_x_status(item.url),
            ),
        )
        return Selection(
            primary=best.with_(
                chosen_reason=f"未确认同图；在已取得关注者数的候选中最多（{best.followers:,}）"
            ),
            found_pages_without_x=False,
        )

    scored: list[tuple[tuple[int, int, bool], Candidate]] = []
    for items in groups.values():
        level = max(item.level for item in items)
        hosts = {host for item in items if item.level == level for host in item.hosts}
        winner = max(
            items,
            key=lambda item: (
                item.level,
                len(set(item.hosts)),
                is_x_status(item.url),
                bool(item.name),
            ),
        )
        scored.append(((level, len(hosts), is_x_status(winner.url)), winner))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    def ordered(items: list[Candidate]) -> list[Candidate]:
        return sorted(
            items,
            key=lambda item: (item.level, len(set(item.hosts)), is_x_status(item.url)),
            reverse=True,
        )

    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        tied = [candidate for score, candidate in scored if score == scored[0][0]]
        top = ordered(tied)[:4]
        return Selection(
            primary=top[0].with_(chosen_reason="多个账号证据相当，暂不判定归属"),
            alternates=top[1:2],
            ambiguous=True,
            found_pages_without_x=False,
        )

    best = scored[0][1]
    return Selection(
        primary=best.with_(chosen_reason=best.chosen_reason or _reason(best)),
        found_pages_without_x=False,
    )
