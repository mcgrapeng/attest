"""attest skill 自测：固定等待判据的增强（AST 感知 / 轮询放行 / 显式豁免）。

放在 skill 自己的 scripts/ 下，与其它自测脚本并列 ⇒ 改判据后先跑这个。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
READINESS = SKILL_ROOT / "scripts" / "readiness.py"


def _load():
    spec = importlib.util.spec_from_file_location("readiness_under_test", READINESS)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["readiness_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_fixed_wait_detects_positive_waits() -> None:
    mod = _load()
    cases = [
        ("import time\ntime.sleep(0.5)\n", 1),
        ("import asyncio\nawait asyncio.sleep(0.01)\n", 1),
        ("import asyncio\nawait asyncio.sleep(5)\n", 1),
    ]
    for src, want in cases:
        assert len(mod.fixed_waits_in_python(src)) == want, src


def test_fixed_wait_ignores_zero_waits() -> None:
    """sleep(0) 只是让出一次事件循环，没有时间依赖 ⇒ 不是固定等待。"""
    mod = _load()
    for src in (
        "import asyncio\nawait asyncio.sleep(0)\n",
        "import time\ntime.sleep(0)\n",
        "import asyncio\nawait asyncio.sleep(0.0)\n",
    ):
        assert mod.fixed_waits_in_python(src) == [], src


def test_fixed_wait_ignores_polling_backoff() -> None:
    """轮询循环体里的 sleep 是退避间隔（外层已按条件退出）⇒ 放行。"""
    mod = _load()
    src = "import asyncio\nwhile True:\n    await asyncio.sleep(1.0)\n"
    assert mod.fixed_waits_in_python(src) == [], src


def test_fixed_wait_ignores_docstrings_and_comments() -> None:
    """文档字符串/注释里的 sleep(0.5) 不是真等待 ⇒ 不算。"""
    mod = _load()
    src = '"""模拟 sleep(0.5) 的说明"""\nimport time\n# time.sleep(0.5)\n'
    assert mod.fixed_waits_in_python(src) == [], src


def test_fixed_wait_exemption_marker_needs_reason() -> None:
    """显式豁免必须带理由（缺理由 ⇒ 报错，不静默放行）。"""
    mod = _load()
    assert mod._NO_FIXED_WAIT_RE.search(
        '# <!-- attest:fixed-wait reason="桩里的确定性耗时" -->\n'
    )
    assert not mod._NO_FIXED_WAIT_RE.search(
        "# <!-- attest:fixed-wait -->\n"
    )


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"BAD {name}: {exc}")
    print("全部通过" if not failures else f"{failures} 条失败")
    raise SystemExit(1 if failures else 0)
