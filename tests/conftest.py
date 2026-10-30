"""让测试可以直接导入 lens 包，而不触发 GsCore 主体初始化。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

LENS_DIR = Path(__file__).resolve().parents[1] / "lens"

if "shadow_lens" not in sys.modules:
    package = types.ModuleType("shadow_lens")
    package.__path__ = [str(LENS_DIR)]  # type: ignore[attr-defined]
    sys.modules["shadow_lens"] = package
