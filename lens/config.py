"""插件配置模板与运行时设置。

所有可调项都暴露在网页控制台；命令触发时即时读取，改配置无需重载插件。
"""

from __future__ import annotations

import os
from typing import Any
from dataclasses import dataclass

from gsuid_core.data_store import get_res_path
from gsuid_core.utils.plugins_config.models import (
    GsDivider,
    GsIntConfig,
    GsStrConfig,
    GsBoolConfig,
    GsListStrConfig,
)
from gsuid_core.utils.plugins_config.gs_config import StringConfig

from .engines import SUPPORTED_ENGINES

CONFIG_DEFAULT: dict[str, Any] = {
    "基础设置": GsDivider("基础设置", "命令范围、白名单与冷却"),
    "group_whitelist": GsListStrConfig("群白名单", "留空表示所有群都能使用", []),
    "private_whitelist": GsListStrConfig("私聊白名单", "留空表示所有私聊都能使用", []),
    "cooldown_seconds": GsIntConfig("冷却秒数", "同一用户两次觅影的最短间隔", 15, max_value=600),
    "max_concurrency": GsIntConfig("最大并发", "同时进行的觅影任务上限", 4, max_value=32),
    "send_progress": GsBoolConfig("发送进度提示", "开始检索时先回一条提示", True),
    "send_result_image": GsBoolConfig("发送结论配图", "命中同图时附带一张结论图", True),
    "识图引擎": GsDivider("识图引擎", "按需启用；未配置 Key 的付费引擎会自动跳过"),
    "engines": GsListStrConfig(
        "启用的引擎",
        "Google Lens 建议配置 SerpApi Key；Yandex 结果只作为待核实线索",
        ["google_lens", "baidu", "copyseeker", "yandex"],
        options=list(SUPPORTED_ENGINES),
    ),
    "search_timeout": GsIntConfig("识图超时", "单个引擎的检索超时（秒）", 20, max_value=120),
    "proxy": GsStrConfig("检索代理", "可留空；形如 http://127.0.0.1:7890", ""),
    "serpapi_api_key": GsStrConfig("SerpApi Key", "用于 Google Lens 与 X 关键词检索", "", secret=True),
    "serpapi_visual": GsBoolConfig("启用相似图", "相似图只作弱线索，不会判定为同图", True),
    "saucenao_api_key": GsStrConfig("SauceNAO Key", "启用 saucenao 引擎时必填", "", secret=True),
    "X 来源核验": GsDivider("X 来源核验", "读取帖子作者、关注者与配图"),
    "x_lookup_timeout": GsIntConfig("X 读取超时", "读取单条 X 帖子的超时（秒）", 10, max_value=60),
    "max_posts": GsIntConfig("最多核验帖子", "一次觅影最多读取多少条 X 帖子", 6, max_value=20),
    "max_pages": GsIntConfig("最多读取来源页", "一次觅影最多打开多少个来源网页", 6, max_value=20),
    "x_keyword_search": GsBoolConfig("启用关键词反查", "从来源页文字里抽一句，去 X 上反查原帖", True),
    "nitter_base": GsStrConfig("Nitter 地址", "可选，仅用于读取账号资料与图片", ""),
    "模型辅助": GsDivider("模型辅助", "可选：用兼容 OpenAI 的模型抽取来源文字"),
    "llm_enabled": GsBoolConfig("启用模型辅助", "只抽取来源页里的原文标识，不参与判定", False),
    "llm_base_url": GsStrConfig("模型地址", "兼容 OpenAI 的 /v1 地址", "https://api.openai.com/v1"),
    "llm_api_key": GsStrConfig("模型 Key", "留空则不调用", "", secret=True),
    "llm_model": GsStrConfig("模型名称", "例如 gpt-4o-mini", "gpt-4o-mini"),
    "llm_timeout": GsIntConfig("模型超时", "单次抽取超时（秒）", 15, max_value=60),
    "缓存与引用": GsDivider("缓存与引用", "图源结果缓存、引用消息读取"),
    "cache_ttl_hours": GsIntConfig("缓存时长", "同一张图的结果复用时长（小时），0 表示关闭", 6, max_value=168),
    "onebot_api_url": GsStrConfig(
        "OneBot HTTP 地址",
        "可选；用于读取被引用消息里的图片，形如 http://127.0.0.1:3000",
        "",
    ),
    "onebot_access_token": GsStrConfig("OneBot Token", "可选；访问 OneBot HTTP 接口的令牌", "", secret=True),
}

CONFIG_PATH = get_res_path() / "ShadowSeek" / "config.json"
os.makedirs(CONFIG_PATH.parent, exist_ok=True)

shadow_config = StringConfig("ShadowSeek", CONFIG_PATH, CONFIG_DEFAULT)


@dataclass(frozen=True)
class Settings:
    """一次命令执行所需的全部设置快照。"""

    engines: tuple[str, ...]
    proxy: str
    serpapi_key: str
    serpapi_visual: bool
    x_keyword_search: bool
    saucenao_key: str
    search_timeout: float
    x_timeout: float
    max_pages: int
    max_posts: int
    cooldown_seconds: int
    max_concurrency: int
    cache_ttl_hours: float
    send_result_image: bool
    send_progress: bool
    nitter_base: str
    llm_enabled: bool
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout: float
    onebot_api_url: str
    onebot_access_token: str
    group_whitelist: tuple[str, ...]
    private_whitelist: tuple[str, ...]

    def allowed_in(self, group_id: str | None) -> bool:
        if group_id:
            return not self.group_whitelist or str(group_id) in self.group_whitelist
        return not self.private_whitelist


def _get(key: str, default: Any) -> Any:
    value = getattr(shadow_config.get_config(key), "data", default)
    return default if value is None else value


def _text(key: str, default: str = "") -> str:
    return str(_get(key, default)).strip()


def _int(key: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(_get(key, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _str_list(key: str) -> tuple[str, ...]:
    raw = _get(key, [])
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    return tuple(str(item).strip() for item in raw if str(item).strip())


def read_settings() -> Settings:
    engines = tuple(item for item in _str_list("engines") if item in SUPPORTED_ENGINES)
    return Settings(
        engines=engines or ("baidu", "copyseeker"),
        proxy=_text("proxy"),
        serpapi_key=_text("serpapi_api_key"),
        serpapi_visual=bool(_get("serpapi_visual", True)),
        x_keyword_search=bool(_get("x_keyword_search", True)),
        saucenao_key=_text("saucenao_api_key"),
        search_timeout=float(_int("search_timeout", 20)),
        x_timeout=float(_int("x_lookup_timeout", 10)),
        max_pages=_int("max_pages", 6),
        max_posts=_int("max_posts", 6),
        cooldown_seconds=_int("cooldown_seconds", 15, minimum=0),
        max_concurrency=_int("max_concurrency", 4),
        cache_ttl_hours=float(_int("cache_ttl_hours", 6, minimum=0)),
        send_result_image=bool(_get("send_result_image", True)),
        send_progress=bool(_get("send_progress", True)),
        nitter_base=_text("nitter_base").rstrip("/"),
        llm_enabled=bool(_get("llm_enabled", False)),
        llm_base_url=_text("llm_base_url", "https://api.openai.com/v1").rstrip("/"),
        llm_api_key=_text("llm_api_key"),
        llm_model=_text("llm_model", "gpt-4o-mini"),
        llm_timeout=float(_int("llm_timeout", 15)),
        onebot_api_url=_text("onebot_api_url").rstrip("/"),
        onebot_access_token=_text("onebot_access_token"),
        group_whitelist=_str_list("group_whitelist"),
        private_whitelist=_str_list("private_whitelist"),
    )
