#!/usr/bin/env python3
"""跑 evals/evals.json 里的 eval —— 每条断言都机检。

    python3 evals/run_evals.py            # 跑全部
    python3 evals/run_evals.py --json     # 机器可读

每条 eval 声明：
  · `fixture`  —— 用仓库里哪个黄金 fixture 当被测仓库（fixtures/<名字>）
  · `command`  —— 要跑的命令（占位符 $SKILL_DIR / $REPO / $PLAN 会替换）
  · `files`    —— eval 的输入文件（从 evals/files/ 复制到工作目录）
  · `assertions` —— 断言 DSL：
      exit_code=N          退出码必须等于 N
      exit_code_any=0|1    退出码在给定集合里
      contains:文本        输出里必须出现
      not_contains:文本    输出里必须不出现
      any_contains:A|B|C   至少出现其中一个

退出码：0 = 全部通过；1 = 有断言不通过；2 = 配置坏了（找不到 fixture / 命令没跑起来）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals" / "evals.json"
FIXTURES = ROOT / "fixtures"
EVAL_FILES = ROOT / "evals" / "files"


def materialize(eval_spec: dict, work: Path) -> tuple[Path, Path]:
    """把 fixture 与输入文件摊到临时目录 ⇒ (仓库根, 计划文件路径)。"""
    repo = work / "repo"
    fixture = eval_spec.get("fixture")
    if fixture:
        src = FIXTURES / fixture
        if not src.is_dir():
            raise SystemExit(f"找不到 fixture：{src}")
        shutil.copytree(src, repo)
    else:
        repo.mkdir(parents=True, exist_ok=True)
    plan = work / "plan.md"
    for name in eval_spec.get("files") or []:
        src = EVAL_FILES / name
        if not src.is_file():
            raise SystemExit(f"找不到 eval 输入文件：{src}")
        # 输入文件都放进被测仓库里（命令用 $PLAN 指它）
        dst = repo / Path(name).name
        shutil.copy(src, dst)
        plan = dst
    return repo, plan


def fill(cmd: list[str], repo: Path, plan: Path) -> list[str]:
    out = []
    for a in cmd:
        a = a.replace("$SKILL_DIR", str(ROOT))
        a = a.replace("$REPO", str(repo))
        a = a.replace("$PLAN", str(plan))
        out.append(a)
    return out


def check(assertion: str, proc: subprocess.CompletedProcess,
          extra_text: str = "") -> tuple[bool, str]:
    """返回 (是否通过, 说明)。"""
    out = (proc.stdout or "") + (proc.stderr or "") + extra_text
    a = assertion.strip()
    if a.startswith("exit_code_any="):
        allowed = {int(x) for x in a.split("=", 1)[1].split("|") if x.strip()}
        ok = proc.returncode in allowed
        return ok, f"退出码 {proc.returncode}（期望在 {sorted(allowed)} 里）"
    if a.startswith("exit_code="):
        want = int(a.split("=", 1)[1])
        ok = proc.returncode == want
        return ok, f"退出码 {proc.returncode}（期望 {want}）"
    if a.startswith("not_contains:"):
        want = a.split(":", 1)[1]
        ok = want not in out
        return ok, f"输出里{'没有' if ok else '有'}「{want}」"
    if a.startswith("any_contains:"):
        alts = [x for x in a.split(":", 1)[1].split("|") if x]
        hit = [x for x in alts if x in out]
        ok = bool(hit)
        return ok, ("出现了 " + "、".join(hit)) if ok else ("一个都没出现：" + "、".join(alts))
    if a.startswith("contains:"):
        want = a.split(":", 1)[1]
        ok = want in out
        return ok, f"输出里{'有' if ok else '没有'}「{want}」"
    return False, f"不认识的断言：{a}"


def run_one(eval_spec: dict, work_root: Path) -> dict:
    work = work_root / f"eval-{eval_spec['id']}"
    work.mkdir(parents=True, exist_ok=True)
    repo, plan = materialize(eval_spec, work)
    cmd = fill(eval_spec["command"], repo, plan)
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", timeout=600)
    # 有些 eval 的断言指的是**产物文件**（比如 `new` 生成的计划里有什么章节），
    # 不是命令行输出。声明 check_file ⇒ 断言同时看输出与那个文件。
    extra = ""
    check_file = eval_spec.get("check_file")
    if check_file:
        target = Path(fill([check_file], repo, plan)[0])
        if not target.is_file():
            return {
                "id": eval_spec["id"],
                "prompt": eval_spec.get("prompt", "")[:80],
                "command": " ".join(cmd),
                "returncode": proc.returncode,
                "assertions": [{"assertion": f"check_file 存在：{check_file}",
                                "passed": False,
                                "detail": f"文件不存在：{target}"}],
                "passed": False,
                "output_tail": (proc.stdout or "")[-600:],
            }
        extra = target.read_text(encoding="utf-8", errors="replace")
    results = []
    for a in eval_spec.get("assertions") or []:
        ok, detail = check(a, proc, extra)
        results.append({"assertion": a, "passed": ok, "detail": detail})
    return {
        "id": eval_spec["id"],
        "prompt": eval_spec.get("prompt", "")[:80],
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "assertions": results,
        "passed": all(r["passed"] for r in results),
        "output_tail": (proc.stdout or "")[-600:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", type=int, default=None, help="只跑某一条 eval")
    args = ap.parse_args()

    data = json.loads(EVALS.read_text(encoding="utf-8"))
    evals = data.get("evals") or []
    if args.only is not None:
        evals = [e for e in evals if e.get("id") == args.only]
    if not evals:
        print("没有可跑的 eval", file=sys.stderr)
        return 2

    work_root = Path(tempfile.mkdtemp(prefix="req-plan-evals."))
    results = []
    try:
        for e in evals:
            res = run_one(e, work_root)
            results.append(res)
            if not args.json:
                mark = "通过" if res["passed"] else "不通过"
                print(f"\n=== eval {res['id']}：{mark} ===")
                print(f"  $ {res['command']}")
                for r in res["assertions"]:
                    print(f"  {'ok  ' if r['passed'] else 'FAIL'} {r['assertion']}"
                          f" —— {r['detail']}")
                if not res["passed"]:
                    print("  --- 输出尾部 ---")
                    print(res["output_tail"])
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    failed = [r for r in results if not r["passed"]]
    if args.json:
        print(json.dumps({"results": results, "failed": len(failed)},
                         ensure_ascii=False, indent=2))
    else:
        print()
        print(f"共 {len(results)} 条 eval：通过 {len(results) - len(failed)}"
              f" / 不通过 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
