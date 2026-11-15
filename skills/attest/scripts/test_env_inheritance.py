#!/usr/bin/env python3
"""回归测试：scripts/check_env_inheritance.py 这道闸门本身靠不靠谱。

    python3 scripts/test_env_inheritance.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "check_env_inheritance.py"
FAILURES: list[str] = []
TMPDIRS: list[Path] = []


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def mkdtemp() -> Path:
    p = Path(tempfile.mkdtemp())
    TMPDIRS.append(p)
    return p


def scan_dir(d: Path) -> subprocess.CompletedProcess:
    """把闸门脚本复制到临时目录（连同一个假的生产脚本）后跑。"""
    dst = d / "scripts"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, dst / "check_env_inheritance.py")
    return subprocess.run([sys.executable, str(dst / "check_env_inheritance.py")],
                          capture_output=True, text=True, encoding="utf-8")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    print("\n干净的生产代码 ⇒ 通过")
    d = mkdtemp()
    (d / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, d / "scripts" / "check_env_inheritance.py")
    (d / "scripts" / "prod.py").write_text(
        "import os\n"
        "def child_env(allow):\n"
        "    return {k: v for k, v in os.environ.items() if k in allow}\n",
        encoding="utf-8")
    r = subprocess.run([sys.executable, str(d / "scripts" / "check_env_inheritance.py")],
                       capture_output=True, text=True, encoding="utf-8")
    expect(r.returncode == 0, "白名单写法 ⇒ 退出码 0")
    expect("没有整份继承" in r.stdout, "说清了检查什么")

    print("\n整份继承 ⇒ 拦下")
    for label, code in (
            ("**os.environ", "BAD = {**os.environ}\n"),
            ("dict(os.environ)", "BAD = dict(os.environ)\n"),
            ("os.environ.copy()", "BAD = os.environ.copy()\n")):
        d2 = mkdtemp()
        (d2 / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy(SCRIPT, d2 / "scripts" / "check_env_inheritance.py")
        (d2 / "scripts" / "prod.py").write_text(code, encoding="utf-8")
        r2 = subprocess.run(
            [sys.executable, str(d2 / "scripts" / "check_env_inheritance.py")],
            capture_output=True, text=True, encoding="utf-8")
        expect(r2.returncode != 0, f"{label} ⇒ 退出码非 0")
        expect("整份继承" in r2.stdout, f"{label} ⇒ 点名了")

    print("\n注释与字符串里的同形写法 ⇒ 不该误报")
    d3 = mkdtemp()
    (d3 / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, d3 / "scripts" / "check_env_inheritance.py")
    (d3 / "scripts" / "prod.py").write_text(
        "# 之前是 {**os.environ}，现在走白名单\n"
        "DOC = 'dict(os.environ) 是旧写法'\n"
        "import os\n"
        "def f(allow):\n"
        "    return {k: v for k, v in os.environ.items() if k in allow}\n",
        encoding="utf-8")
    r3 = subprocess.run(
        [sys.executable, str(d3 / "scripts" / "check_env_inheritance.py")],
        capture_output=True, text=True, encoding="utf-8")
    expect(r3.returncode == 0, "注释/字符串里的同形写法不误报")

    print("\n测试文件不在检查范围内")
    d4 = mkdtemp()
    (d4 / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, d4 / "scripts" / "check_env_inheritance.py")
    (d4 / "scripts" / "test_prod.py").write_text(
        "import os\nENV = {**os.environ}\n", encoding="utf-8")
    r4 = subprocess.run(
        [sys.executable, str(d4 / "scripts" / "check_env_inheritance.py")],
        capture_output=True, text=True, encoding="utf-8")
    expect(r4.returncode == 0, "测试文件里的整份继承不拦（脚手架刻意要传环境）")

    print("\n本仓库自己过这道闸")
    r5 = subprocess.run([sys.executable, str(SCRIPT)],
                        capture_output=True, text=True, encoding="utf-8")
    expect(r5.returncode == 0, "本仓库的生产脚本没有整份继承 os.environ")
    expect("Traceback" not in r5.stderr, "没有崩")

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
