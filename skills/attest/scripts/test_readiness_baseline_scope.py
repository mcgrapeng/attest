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


def _baseline_repo(script_body: str) -> Path:
    """一个只有 svc/ 的仓库，svc 的基线脚本就是给定的 shell 脚本。"""
    root = mkdtemp()
    write(root / "svc" / "package.json",
          '{"name":"svc","scripts":{"eval:baseline:check":"sh scripts/baseline.sh"}}')
    write(root / "svc" / "scripts" / "baseline.sh", script_body)
    return root


def test_missing_artifact_is_not_a_regression():
    print("\ntest_missing_artifact_is_not_a_regression")
    # 基线脚本的「缺产物」提示常带一句「（缺产物不是数据回归）」⇒ 那句里的"回归"
    # 是否定语境，不能当成数据回归（否则凭空造出一个阻塞项）。
    root = _baseline_repo(
        '#!/bin/sh\n'
        'echo "没有找到记忆召回评测产物 e2e/eval/memory/artifacts/x.json：'
        '先跑 pnpm eval:memory 生成产物，再跑本脚本对拍基线（缺产物不是数据回归）。"\n'
        'exit 2\n')
    chk = readiness.check_data_accuracy(_module(root), root)
    expect(chk.status != "fail",
           f"缺产物不是数据回归 ⇒ 不该报红（实际 {chk.status}：{chk.detail}）")
    expect(chk.status == "skipped",
           f"缺产物 ⇒ 判「未实测」而不是「回归」（实际 {chk.status}）")


def test_real_regression_still_fails():
    print("\ntest_real_regression_still_fails")
    root = _baseline_repo(
        '#!/bin/sh\n'
        'echo "recall_rate 0.42 低于基线 0.50"\n'
        'exit 1\n')
    chk = readiness.check_data_accuracy(_module(root), root)
    expect(chk.status == "fail",
           f"真回归仍要报红（实际 {chk.status}：{chk.detail}）")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_other_package_baseline_is_not_taken()
    test_baseline_in_other_package_mentioning_the_table_is_taken()
    test_data_accuracy_reports_module_has_no_baseline()
    test_missing_artifact_is_not_a_regression()
    test_real_regression_still_fails()

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
