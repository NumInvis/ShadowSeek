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

HELP_TEXT = """【觅影】专业鉴图，专治「这图到底谁发的」
发图配一句「觅影」，或引用图片再发「觅影」，我就去全网翻线索。
· 只认原图不认脸熟，长得像的一律先当嫌疑人
· 几位嫌疑人证据一样硬，就一起摆上桌，不硬拗答案
· 全程只翻公开信息，不登录、不查水表
别名：找鸡 / 图源定位 / 找原图
配置：网页控制台 → ShadowSeek"""


def _judgement(candidate: Candidate) -> str:
    if candidate.relation:
        return "引用链锁定"
    if candidate.verified:
        return "同图实锤"
    if candidate.level >= LEVEL_SIGNED:
        return "署名对得上"
    if candidate.level >= LEVEL_LINK:
        return "摸到主页了"
    if candidate.level >= LEVEL_MENTION:
        return "有人提过名"
    return "只有皮毛"


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
        lines = ["【觅影】翻遍犄角旮旯，没逮到本尊。"]
        if selection.found_pages_without_x:
            lines.append("图是找着了，可页面里没留下 X 账号或帖子，线索断在这儿。")
        else:
            lines.append("识图引擎集体哑火，一张能用的 X 来源都没吐出来。")
        lines.extend(_diagnostics(report))
        return "\n".join(lines)

    label = "本尊" if (primary.verified or primary.relation) else "嫌疑人"
    lines = [
        f"【觅影】判定：{_judgement(primary)}（可信度：{_confidence(primary)}）",
        _line(primary, label),
    ]
    if primary.url:
        lines.append(f"首发现场：{primary.url}")
    reason = primary.chosen_reason or "暂时没找到硬线索"
    lines.append(f"线索：{reason}")

    if selection.ambiguous:
        lines.append("说明：几位嫌疑人证据一样硬，先不急着定罪。")
    for alternate in selection.alternates:
        lines.append(f"同期嫌疑人：{_account(alternate) or '未知'}")
        if alternate.url:
            lines.append(f"　　　　　　　{alternate.url}")

    if report.cache_hit:
        lines.append("（熟人局，直接从缓存里请出来的）")
    if report.errors:
        errors = [f"{name}：{reason}" for name, reason in list(report.errors.items())[:4]]
        lines.append("小状况：" + "；".join(errors))
    return "\n".join(lines)


def _diagnostics(report: Report) -> list[str]:
    parts: list[str] = []
    for engine, count in report.engine_counts.items():
        parts.append(f"{engine} {count}")
    if parts:
        parts_line = "引擎战绩： " + " · ".join(parts)
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
        lines.append("小状况：" + "；".join(errors))
    return lines


def format_error(message: str) -> str:
    return f"【觅影】{message}"
