#!/usr/bin/env python3
"""Python 侧的 DPSP（区分力自证）回归测试。

DPSP 在 JS/TS 上是通的；Python 这条路上有三个洞（规格 §5.1 要求 Python
也要精确支持）：

  1. **变异点的作用域**：`_anchor_at` 按大括号找包围函数 ⇒ Python 恒为
     `<toplevel>` ⇒ 作用域过滤把 Python 变异全丢掉。
  2. **钉住用例的运行时引用**：`parse_imports` 只认 JS/TS ⇒ Python 用例的
     作用域恒为空 ⇒ 结论恒为 `not-applicable`。
  3. **用例名匹配**：pytest 的 node id 是 `tests/test_x.py::test_x` ⇒
     `match_case` 只按 `>` 分段 ⇒ 认不出「钉住的那个用例红了」。

这三条里任何一条不通，Python 仓的「钉有区分力」就是**恒绿橡皮章** ——
比测不出更坏：它看起来在工作。
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
FAILURES: list[str] = []
TMPDIRS: list[Path] = []

sys.path.insert(0, str(HERE))
from discriminate import (  # noqa: E402
    match_case, python_analysis, run_dpsp)
from mutation_check import find_mutations  # noqa: E402

PYPROJECT = """[project]
name = "fixture"
version = "0.0.0"

[tool.pytest.ini_options]
pythonpath = ["."]
"""

#: 有比较、有分支 ⇒ 有变异点
CORE_PY = '''def classify(n):
    if n == 0:
        return "zero"
    return "other"
'''

#: 弱断言：调用了函数，但断言与判定结果无关 ⇒ 改坏判定它也发现不了
WEAK_TEST_PY = '''from pkg.core import classify


def test_classifies_zero():
    assert isinstance(classify(0), str)
'''

#: 强断言：改坏判定就会红
KILL_TEST_PY = '''from pkg.core import classify


def test_classifies_zero():
    assert classify(0) == "zero"
'''

#: 类方法：作用域认的是**方法名**，不是类名
CLASS_PY = '''class Rules:
    def classify(self, n):
        if n == 0:
            return "zero"
        return "other"
'''

CLASS_TEST_PY = '''from pkg.core import Rules


def test_classifies_zero():
    assert Rules().classify(0) == "zero"
'''


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def mkdtemp() -> Path:
    p = Path(tempfile.mkdtemp())
    TMPDIRS.append(p)
    return p


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def py_repo(src: str, test: str) -> Path:
    """一个能用 `python -m pytest` 跑起来的最小仓库。"""
    root = mkdtemp()
    write(root / "pyproject.toml", PYPROJECT)
    write(root / "pkg" / "__init__.py", "")
    write(root / "pkg" / "core.py", src)
    write(root / "tests" / "test_core.py", test)
    return root


def dpsp(root: Path, case: str = "test_classifies_zero") -> dict:
    return run_dpsp(root, ".",
                    [("INV-1", f"tests/test_core.py::{case}", "零归类成 zero")],
                    [sys.executable, "-m", "pytest", "-rA"], ["tests/test_core.py"],
                    source_rels=["pkg/core.py"], level="L2")


# ---------------------------------------------------------------- 作用域


def test_python_mutation_symbol_is_enclosing_function() -> None:
    root = py_repo(CORE_PY, KILL_TEST_PY)
    muts = find_mutations(root / "pkg" / "core.py", None)
    expect(bool(muts), "Python 源码里找得到变异点")
    expect(bool(muts) and muts[0].symbol == "classify",
           f"变异点的作用域是包围它的函数（实际 "
           f"{muts[0].symbol if muts else '?'}）")


def test_python_scope_counts_imported_names() -> None:
    root = py_repo(CORE_PY, KILL_TEST_PY)
    scope, reachable = python_analysis(
        KILL_TEST_PY, ["pkg/core.py"], root)
    expect("classify" in scope,
           f"钉住用例引用的符号里有 classify（实际 {sorted(scope)}）")
    expect("pkg/core.py" in reachable,
           f"钉住用例可达的源文件里有 pkg/core.py（实际 {sorted(reachable)}）")


def test_python_scope_includes_class_methods() -> None:
    root = py_repo(CLASS_PY, CLASS_TEST_PY)
    scope, _ = python_analysis(CLASS_TEST_PY, ["pkg/core.py"], root)
    expect("classify" in scope,
           f"import 一个类 ⇒ 作用域含它的方法（实际 {sorted(scope)}）")


def test_pytest_node_id_matches_pinned_case() -> None:
    expect(match_case("test_classifies_zero",
                      "tests/test_core.py::test_classifies_zero"),
           "pytest 的 node id 能匹配到钉住的那个用例")
    expect(match_case("test_classifies_zero",
                      "tests/test_core.py::test_classifies_zero[param]"),
           "参数化用例的 node id 也能匹配")
    expect(not match_case("test_other",
                           "tests/test_core.py::test_classifies_zero"),
           "别的用例红了不算命中")


# ---------------------------------------------------------------- 端到端


def test_python_dpsp_passes_when_pin_kills_mutant() -> None:
    res = dpsp(py_repo(CORE_PY, KILL_TEST_PY))
    verdicts = res.get("verdicts") or []
    expect(bool(verdicts), "有一份结论")
    expect(bool(verdicts) and verdicts[0]["status"] == "pass",
           f"钉能杀死变异 ⇒ pass（实际 "
           f"{verdicts[0]['status'] if verdicts else '?'}："
           f"{verdicts[0]['detail'] if verdicts else ''}）")


def test_python_dpsp_fails_when_pin_is_weak() -> None:
    res = dpsp(py_repo(CORE_PY, WEAK_TEST_PY))
    verdicts = res.get("verdicts") or []
    expect(bool(verdicts), "有一份结论")
    expect(bool(verdicts) and verdicts[0]["status"] == "fail",
           f"弱断言杀不死变异 ⇒ fail（实际 "
           f"{verdicts[0]['status'] if verdicts else '?'}："
           f"{verdicts[0]['detail'] if verdicts else ''}）")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    if importlib.util.find_spec("pytest") is None:
        print("跳过：当前解释器没有 pytest（端到端两条跑不了）")
        test_python_mutation_symbol_is_enclosing_function()
        test_python_scope_counts_imported_names()
        test_python_scope_includes_class_methods()
        test_pytest_node_id_matches_pinned_case()
    else:
        test_python_mutation_symbol_is_enclosing_function()
        test_python_scope_counts_imported_names()
        test_python_scope_includes_class_methods()
        test_pytest_node_id_matches_pinned_case()
        test_python_dpsp_passes_when_pin_kills_mutant()
        test_python_dpsp_fails_when_pin_is_weak()

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
