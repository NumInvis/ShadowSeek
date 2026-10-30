"""结果文案。

结果统一使用“判定 / 账号 / 原帖 / 依据 / 可信度”的结构，措辞保持直白。
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

HELP_TEXT = """【找鸡】发图帮你找出这张图在 X 上的原始出处
发图配「找鸡」，或引用图片再发「找鸡」，即可检索最可能的 X 原帖或账号。
· 只认同图：配图对上了才算找到，长得像的只作弱线索
· 多个候选证据一样强时并列给出，不硬选一个
· 只读取公开页面，不登录、不需要账号
别名：觅影 / 图源定位 / 找原图
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
        lines = ["【找鸡】没有找到可确认的来源"]
        if selection.found_pages_without_x:
            lines.append("找到了同图页面，但页面里没有可核对的 X 账号或帖子。")
        else:
            lines.append("识图引擎没有返回可用的 X 来源。")
        lines.extend(_diagnostics(report))
        return "\n".join(lines)

    lines = [
        f"【找鸡】判定：{_judgement(primary)}（可信度：{_confidence(primary)}）",
        _line(primary),
    ]
    if primary.url:
        lines.append(f"原帖：{primary.url}")
    reason = primary.chosen_reason or "未提供判定依据"
    lines.append(f"依据：{reason}")

    if selection.ambiguous:
        lines.append("说明：多个候选证据相当，暂不判定归属。")
    for alternate in selection.alternates:
        lines.append(f"并列候选：{_account(alternate) or '未知'}")
        if alternate.url:
            lines.append(f"　　　　　{alternate.url}")

    if report.cache_hit:
        lines.append("（结果来自缓存）")
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
    return f"【找鸡】{message}"
