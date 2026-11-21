"""共享叶子模块的回归：一份实现，各处共用。

背景：Tier 2 去重扫描判出 `decision_points._strip_js_code` 与
`mutation_check._strip_js_code` 逐字重复（同一正则、同一 _blank，只有 docstring 措辞不同），
两个 docstring 里自己写着「改一处要改两处」；`readiness._module_tokens` 与
`selfcheck._module_tokens` 也各写了一份（selfcheck 还把 src 布局识别重写了一遍）。
⇒ 抽到叶子模块 `code_strip` / `module_names`，各调用方共用同一实现。

第二轮（2026-11-21，SkillEvaluator Tier 2 去重扫描）又判出两处重复：
`mutation_check._link_deps` 与 `selfcheck._link_deps`（软链依赖目录，docstring 近乎逐字
相同）、`test_readiness._blocking` 与 `test_readiness_r2._blocking`（解析结论行的阻塞项数）。
⇒ 前者抽到叶子模块 `dep_links`；后者只在 `test_readiness.py` 里留一份，r2 从那里导入。

本测试要钉住两件事：
1. 行为不变（偏移量与行号保持、字符串在前、Python 用 tokenize）。
2. **各调用方真的是同一个函数对象** —— 以后再有人复制一份实现，这里会红。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import code_strip  # noqa: E402
import decision_points  # noqa: E402
import dep_links  # noqa: E402
import discriminate  # noqa: E402
import module_names  # noqa: E402
import mutation_check  # noqa: E402
import readiness  # noqa: E402
import selfcheck  # noqa: E402
import test_readiness  # noqa: E402
import test_readiness_r2  # noqa: E402


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)
    print(f"  ok   {msg}")


def main() -> int:
    print("\n共享实现：各调用方用的是同一个函数对象")
    expect(decision_points.strip_js_code is code_strip.strip_js_code,
           "decision_points 用的是 code_strip.strip_js_code")
    expect(mutation_check.strip_js_code is code_strip.strip_js_code,
           "mutation_check 用的是 code_strip.strip_js_code")
    expect(discriminate.strip_js_code is code_strip.strip_js_code,
           "discriminate 用的是 code_strip.strip_js_code")
    expect(readiness.module_tokens is module_names.module_tokens,
           "readiness 用的是 module_names.module_tokens")
    expect(selfcheck.module_tokens is module_names.module_tokens,
           "selfcheck 用的是 module_names.module_tokens")
    expect(mutation_check.link_deps is dep_links.link_deps,
           "mutation_check 用的是 dep_links.link_deps")
    expect(selfcheck.link_deps is dep_links.link_deps,
           "selfcheck 用的是 dep_links.link_deps")
    expect(test_readiness_r2.blocking_count is test_readiness.blocking_count,
           "test_readiness_r2 用的是 test_readiness.blocking_count")

    print("\nstrip_js_code：字符串与注释换成空格，偏移与行号不变")
    src = 'const a = "http://x/y"; // 注释\nlet b = `tpl`; /* multi\nline */\n'
    out = code_strip.strip_js_code(src)
    expect(len(out) == len(src), "长度不变")
    expect(out.count("\n") == src.count("\n"), "换行数不变")
    expect("http://x/y" not in out, "字符串内容被清空")
    expect("// 注释" not in out, "行注释被清空")
    expect("multi" not in out and "line" not in out, "块注释被清空")
    expect(out.splitlines()[2].strip() == "", "注释所在行整体变成空白（行号不变）")

    print("\nstrip_js_code：字符串里的 // 不当注释")
    src2 = 'const u = "http://a"; const v = 1;'
    out2 = code_strip.strip_js_code(src2)
    expect("http://a" not in out2, "字符串里的 // 没有把后面的代码吃掉")
    expect(out2.index("const v") == src2.index("const v"),
           "字符串之后的代码位置不变（偏移不变）")

    print("\nstrip_py_code：tokenize 版，偏移不变")
    pysrc = 'def f():\n    s = "a # b"  # 注释\n    return s\n'
    pyout = code_strip.strip_py_code(pysrc)
    expect(len(pyout) == len(pysrc), "长度不变")
    expect(pyout.count("\n") == pysrc.count("\n"), "换行数不变")
    expect("# b" not in pyout, "字符串里的 # 被清空")
    expect("return s" in pyout, "代码本身保留")

    print("\npython_package_dir：src 布局 / 包目录本身")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src" / "pkg").mkdir(parents=True)
        (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        expect(module_names.python_package_dir(root / "src") == root / "src" / "pkg",
               "src/<pkg> 能认出包目录")
        expect(module_names.python_package_dir(root / "src" / "pkg") == root / "src" / "pkg",
               "包目录本身能认出")
        (root / "plain").mkdir()
        expect(module_names.python_package_dir(root / "plain") is None,
               "不是包就返回 None")

    print("\nmodule_tokens：相对路径 + 目录名 + 包名，去重")
    expect(module_names.module_tokens("src/pkg", Path("/x/src/pkg")) == ["src/pkg", "pkg"],
           "相对路径与包名去重后保留两个")
    expect(module_names.module_tokens("src/pkg", None) == ["src/pkg", "pkg"],
           "没有包目录时只给相对路径与目录名")
    expect(module_names.module_tokens("", None) == [],
           "根目录没有名字")
    expect(module_names.module_tokens(".", None) == [],
           "「.」不算名字")

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
