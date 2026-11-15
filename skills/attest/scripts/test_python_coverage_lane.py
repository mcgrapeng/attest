#!/usr/bin/env python3
"""回归测试：Python 覆盖率车道在「pytest 没跑起来」时说什么。

实测复现（pallets/itsdangerous）：测试要 `freezegun`（仓库没声明这个依赖）
⇒ pytest 收集失败（退出码 2）⇒ pytest-cov 根本没写 cov.json。

之前工具报的是「覆盖率报告没解析出来（[Errno 2] No such file or directory:
'.../cov.json')」—— 读者只知道"有个文件找不到"，不知道**仓库缺依赖**。
现在报「pytest 没跑起来（ModuleNotFoundError: No module named 'freezegun'）」。

    python3 scripts/test_python_coverage_lane.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import readiness as R  # noqa: E402

FAILURES: list[str] = []
TMPDIRS: list[Path] = []


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    print("\npytest 收集失败（缺依赖）⇒ 说清缺什么，不说 cov.json 找不到")
    root = Path(tempfile.mkdtemp())
    TMPDIRS.append(root)
    (root / "src" / "demo").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "demo" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests" / "test_demo.py").write_text(
        "import defnotinstalled_zzz\n\n\ndef test_x():\n    assert True\n",
        encoding="utf-8")
    m = R.Module(root=root, path="src/demo", base=root / "src" / "demo",
                 sources=[root / "src" / "demo" / "__init__.py"], workspace=root)
    _, _, _, note = R.run_python_coverage(m, root, timeout=120)
    expect("pytest 没跑起来" in note, f"说 pytest 没跑起来（实际：{note[:80]}）")
    expect("cov.json" not in note,
           f"不再拿 cov.json 找不到当结论（实际：{note[:80]}）")
    if "defnotinstalled_zzz" in note:
        print("  ok   点名了缺哪个模块")
    else:
        # 环境里没装 pytest-cov 时 pytest 会先因参数报错 —— 那也是"没跑起来" ✓
        expect("pytest 没跑起来" in note, "（pytest-cov 不可用时也归到这里）")

    print()
    for d in TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
