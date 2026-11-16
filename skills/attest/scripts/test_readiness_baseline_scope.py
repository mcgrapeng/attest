#!/usr/bin/env python3
"""基线对拍车道的**模块收口**回归测试。

多包仓库里，别的包的基线脚本会被当成这个模块的基线脚本跑起来 ——
指标口径根本不是一回事：记忆域的模块跑上了 RAG 域的基线对拍，
报出来的差异是别的域的。

收口口径（跟对账车道一致）：脚本要么在模块自己的 workspace 里，
要么脚本正文/命令提到这个模块（表名或模块路径）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
FAILURES: list[str] = []
TMPDIRS: list[str] = []

sys.path.insert(0, str(HERE))
import readiness  # noqa: E402


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def mkdtemp() -> Path:
    p = Path(tempfile.mkdtemp())
    TMPDIRS.append(str(p))
    return p


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _repo(other_body: str, svc_body: str = "") -> Path:
    """一个多包仓库：svc/（被测模块的包）与 other/（另一个域的包）。"""
    root = mkdtemp()
    write(root / "svc" / "package.json",
          '{"name":"svc","scripts":{"eval:baseline:check":'
          '"tsx scripts/eval-metrics-baseline.ts"}}')
    write(root / "svc" / "scripts" / "eval-metrics-baseline.ts", svc_body)
    write(root / "other" / "package.json",
          '{"name":"other","scripts":{"eval:baseline:check":'
          '"tsx scripts/eval-metrics-baseline.ts"}}')
    write(root / "other" / "scripts" / "eval-metrics-baseline.ts", other_body)
    return root


def _module(root: Path) -> readiness.Module:
    return readiness.Module(
        root=root, path="svc/src/mem", base=root / "svc" / "src" / "mem",
        tables=["agent_memory"], workspace=root / "svc", lang="ts")


def test_other_package_baseline_is_not_taken():
    print("\ntest_other_package_baseline_is_not_taken")
    root = _repo(other_body="// RAG 域的基线：读 rag/artifacts/e2e-eval\n")
    cands = readiness.find_baseline_scripts(_module(root), root)
    names = [(ws.name, name) for ws, name, _ in cands]
    expect(("other", "eval:baseline:check") not in names,
           f"别的包的基线脚本不被收进来（实际 {names}）")
    expect(("svc", "eval:baseline:check") in names,
           f"模块自己包里的基线脚本被收进来（实际 {names}）")


def test_baseline_in_other_package_mentioning_the_table_is_taken():
    print("\ntest_baseline_in_other_package_mentioning_the_table_is_taken")
    root = _repo(other_body="// 对拍 agent_memory 的召回指标\n")
    cands = readiness.find_baseline_scripts(_module(root), root)
    names = [(ws.name, name) for ws, name, _ in cands]
    expect(("other", "eval:baseline:check") in names,
           f"别的包的脚本提到本模块的表名 ⇒ 收进来（实际 {names}）")


def test_data_accuracy_reports_module_has_no_baseline():
    print("\ntest_data_accuracy_reports_module_has_no_baseline")
    root = _repo(other_body="// RAG 域的基线\n")
    # 模块自己的包**没有**基线脚本，只有别的域有
    write(root / "svc" / "package.json", '{"name":"svc","scripts":{}}')
    chk = readiness.check_data_accuracy(_module(root), root)
    expect(chk.status == "skipped",
           f"别的域有基线脚本时，本模块判「未实测」而不是去跑它（实际 {chk.status}）")
    expect("本模块" in (chk.detail or ""),
           f"文案说清是「本模块没有」（实际 {(chk.detail or '')[:60]}）")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_other_package_baseline_is_not_taken()
    test_baseline_in_other_package_mentioning_the_table_is_taken()
    test_data_accuracy_reports_module_has_no_baseline()

    print()
    for d in TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)
    if FAILURES:
        print(f"{len(FAILURES)} 项失败:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
