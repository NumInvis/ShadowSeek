"""ShadowSeek 找鸡：发送或引用图片，寻找最可能的 X 原帖与账号。"""

from gsuid_core.sv import Plugins

Plugins(
    name="ShadowSeek",
    priority=5,
    area="SV",
    force_prefix=[],
    allow_empty_prefix=True,
)

from .lens import command  # noqa: E402,F401
