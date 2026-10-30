"""结果文案。

刻意采用“判定 / 账号 / 原帖 / 依据 / 可信度”的结构，与原插件的五行模板区分。
"""

from __future__ import annotations

from .urls import platform_of
from .models import (
    LEVEL_LINK,
    LEVEL_SIGNED,
    LEVEL_MENTION,
    Report,
    Candidate,
    Selection,
)

HELP_TEXT = """【觅影】以图搜源
发送图片并附带「觅影」，或引用一张图片发送「觅影」，即可寻找最可能的 X 原帖或账号。
· 同图比对优先，相似图只作弱线索
· 多个账号证据相当时并列展示，不强行判定
· 只读取公开信息，不需要登录
别名：图源定位 / 找原图
配置：网页控制台 → ShadowSeek"""


def _judgement(candidate: Candidate) -> str:
    if candidate.relation:
        return "引用关系确认"
    if candidate.verified:
        return "已确认同图"
    if candidate.level >= LEVEL_SIGNED:
        return "来源署名"
    if candidate.level >= LEVEL_LINK:
        return "链接指向"
    if candidate.level >= LEVEL_MENTION:
        return "文字提及"
    return "弱线索"


def _confidence(candidate: Candidate) -> str:
    if candidate.verified:
        return "高"
    if candidate.level >= LEVEL_LINK:
        return "中"
    return "低"


def _account(candidate: Candidate) -> str:
    if candidate.account and not candidate.account.startswith("#status:"):
        return candidate.account
    _, account = platform_of(candidate.url)
    return "" if account.startswith("#status:") else account


def _line(candidate: Candidate, prefix: str = "账号") -> str:
    account = _account(candidate) or "未知"
    if candidate.name and candidate.name != account:
        return f"{prefix}：{account} · {candidate.name}"
    return f"{prefix}：{account}"


def format_result(selection: Selection, report: Report) -> str:
    primary = selection.primary
    if primary is None:
        lines = ["【觅影】未找到可定位的来源"]
        if selection.found_pages_without_x:
            lines.append("已经找到同图页面，但没有从中读到可核对的 X 账号或帖子。")
        else:
            lines.append("识图引擎没有返回可用的 X 来源。")
        lines.extend(_diagnostics(report))
        return "\n".join(lines)

    lines = [
        f"【觅影】判定：{_judgement(primary)}（可信度：{_confidence(primary)}）",
        _line(primary),
    ]
    if primary.url:
        lines.append(f"原帖：{primary.url}")
    reason = primary.chosen_reason or "未提供判定依据"
    lines.append(f"依据：{reason}")

    if selection.ambiguous:
        lines.append("说明：多个账号证据相当，暂不判定归属。")
    for alternate in selection.alternates:
        lines.append(f"并列候选：{_account(alternate) or '未知'}")
        if alternate.url:
            lines.append(f"　　　　　{alternate.url}")

    if report.cache_hit:
        lines.append("（本次结果来自缓存）")
    if report.errors:
        errors = [f"{name}：{reason}" for name, reason in list(report.errors.items())[:4]]
        lines.append("异常：" + "；".join(errors))
    return "\n".join(lines)


def _diagnostics(report: Report) -> list[str]:
    parts: list[str] = []
    for engine, count in report.engine_counts.items():
        parts.append(f"{engine} {count}")
    if parts:
        parts_line = "检索： " + " · ".join(parts)
    else:
        parts_line = ""
    errors = [
        f"{name}：{reason}"
        for name, reason in list(report.errors.items())[:4]
    ]
    lines: list[str] = []
    if parts_line:
        lines.append(parts_line)
    if errors:
        lines.append("异常：" + "；".join(errors))
    return lines


def format_error(message: str) -> str:
    return f"【觅影】{message}"
