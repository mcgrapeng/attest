#!/usr/bin/env python3
"""把仓库自己的行为 eval 变成 SkillEvaluator Tier 3 能跑的数据集。

    python3 evals/make_tier3_dataset.py --out /tmp/se_t3/skills/attest

为什么要有这个脚本：Tier 3 的数据集必须放在 `<skill-dir>/evals/evals.json`，
而这个仓库的行为 eval 住在仓库根的 `evals/`（那里还有跑它的 runner 与
README）。两份手工维护的清单必然漂 ⇒ 这里**从 `evals/evals.json` 生成**，
用例与期望结果仍只有一份事实源。

生成时只做三件事，每一件都是沙箱化的机械改写：

1. **把被测仓库的路径换成 staged 目录**：Tier 3 把 `evals/files/` 里的东西
   放进沙箱的 `/workspace/input/`，agent 的工作目录是 `/workspace`。
   仓库根的 fixture 会被复制成 `/workspace/input/<名字>/`。
2. **把提示里的模块路径改成 fixture 里真有的**：fixture 是几个文件的小仓库，
   提示里原来的 `src/orders/`、`src/billing` 在里面不存在。
3. **补负样本**：`evals/trigger-evals.json` 里 `should_trigger=false` 的条目
   就是「不该触发 attest」的请求 —— 直接拿来当 Tier 3 的负例。

不改的：提示的任务本身、`expected_output`（那是作者写的期望结果）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
FIXTURES = ROOT / "fixtures"

# 每条用例的沙箱化改写表：staged 目录名 + 提示里的替换。
# 键是 evals/evals.json 里的 id。
CASES: dict[int, dict] = {    1: {
        "repo": "shop-backend",
        "fixture": "ts-colocated",
        "edits": [
            ("仓库在 ~/workspace/shop-backend", "仓库在 /workspace/input/shop-backend"),
            ("代码在 src/orders/", "代码在 src/"),
        ],
    },
    2: {
        "repo": "shop-backend",
        "fixture": "ts-colocated",
        "plan": "stale-plan.md",
        "edits": [
            ("上游上周提了个需求测试计划，在 docs/refund-test-plan.md。",
             "上游上周提了个需求测试计划，仓库在 /workspace/input/shop-backend，"
             "计划文件在 docs/refund-test-plan.md。"),
        ],
    },
    3: {
        "repo": "shop-backend",
        "fixture": "ts-colocated",
        "edits": [
            ("我们要给客户交付 src/billing 这个模块了。",
             "仓库在 /workspace/input/shop-backend，我们要给客户交付 src 这个模块了。"),
        ],
    },
    4: {
        "repo": "order-data-repo",
        "fixture": "english-reconciler",
        "edits": [
            ("订单数据 PG 和向量库各存了一份，",
             "仓库在 /workspace/input/order-data-repo。订单数据 PG 和向量库各存了一份，"),
        ],
    },
    5: {
        "repo": "shop-backend",
        "fixture": "ts-colocated",
        "plan": "stale-plan.md",
        "edits": [
            ("上游说这个需求已经测过了，",
             "仓库在 /workspace/input/shop-backend。上游说这个需求已经测过了，"),
        ],
    },
    6: {
        "repo": "shop-backend",
        "fixture": "ts-colocated",
        "plan": "out-of-bounds-plan.md",
        "edits": [
            # 原来这条提示直接写了 skill 的脚本路径 —— 那等于把 skill 塞给
            # 「没有 skill」的那条臂，两边都不公平。改成描述任务本身。
            ("按这份计划生成测试骨架：python3 ~/.agents/skills/attest/scripts/req_plan.py "
             "scaffold docs/refund-test-plan.md --repo . --out-dir /tmp/out。",
             "按这份计划生成测试骨架：仓库在 /workspace/input/shop-backend，"
             "计划在 docs/refund-test-plan.md，生成目录 /tmp/out。"),
        ],
    },
}

# 负样本：这些请求不该触发 attest（取自 evals/trigger-evals.json 的负例）。
NEGATIVE_QUERIES = [
    "帮我 review 一下这个 PR 的代码风格和命名",
    "unit test 一般怎么写？我想测一个纯函数，输入两个数返回它们的和",
    "perf: 这个 SQL 查询太慢了，帮我看看怎么优化索引",
]

NEGATIVE_EXPECTED = (
    "直接回答这个请求本身，不调用 attest 的任何脚本 —— attest 是需求级测试计划的"
    "工具，不覆盖普通代码 review、单个单测怎么写、性能优化。"
)

# Tier 3 的断言是**散文行为**（由判卷的模型核对），而仓库自己的断言 DSL 是
# 命令级的（exit_code= / contains:）—— 两者不能直接互换。这里把仓库的断言
# 逐条翻成「看得见的结果」，不新造期望：命令级的断言交给 evals/run_evals.py
# 去判，Tier 3 判「结果长什么样」。
ASSERTIONS: dict[int, list[str]] = {
    1: [
        "生成的测试计划骨架里有：已有能力（复用侦察）、本模块碰到的存储、"
        "不变式清单、测试车道、覆盖矩阵、核心逻辑细节、新建能力（先论证不能复用）",
        "每个维度行都带触发证据（文件:行）",
        "命令正常结束，输出里没有 Python traceback",
    ],
    2: [
        "核对结果点名了钉指向的文件不存在，并说是哪个文件",
        "核对以非零状态退出，而不是只说一声「不通过」",
        "输出里给出了错误条数",
    ],
    3: [
        "报告按证据类别分层：已实测、静态信号分开写",
        "结论分三档（阻塞项 / 待人工确认 / 无信号）",
        "报告固定列出一段「本工具不判定」的清单",
    ],
    4: [
        "报告说出是哪个租户漂了",
        "故意注入失败态的测试租户只报不拦，没有被算成阻塞项",
    ],
    5: [
        "核对结果点名 never_written.test.ts 这个文件不存在",
        "核对以非零状态退出",
    ],
    6: [
        "生成被拒绝了，并说明是落点越出了生成目录",
        "生成目录之外一个文件都没写",
    ],
}

NEGATIVE_ASSERTIONS = [
    "回答直接对着请求本身，没有调用 attest 的任何脚本",
]


def stage_repo(out_files: Path, repo: str, fixture: str, plan: str | None) -> None:
    """把 fixture 复制成 staged 仓库；计划文件按提示里的路径放进去。"""
    dest = out_files / repo
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(FIXTURES / fixture, dest)
    if plan:
        docs = dest / "docs"
        docs.mkdir(exist_ok=True)
        shutil.copy(EVALS / "files" / plan, docs / "refund-test-plan.md")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True,
                    help="skill 目录（数据集会写进 <out>/evals/）")
    args = ap.parse_args()

    skill_dir = Path(args.out)
    evals_dir = skill_dir / "evals"
    files_dir = evals_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    src = json.loads((EVALS / "evals.json").read_text(encoding="utf-8"))
    cases: list[dict] = []
    for c in src["evals"]:
        cid = int(c["id"])
        spec = CASES.get(cid)
        if spec is None:
            print(f"用例 {cid} 没有沙箱化改写规则 —— 补上再说", file=sys.stderr)
            return 1
        prompt = c["prompt"]
        for old, new in spec["edits"]:
            if old not in prompt:
                print(f"用例 {cid}：提示里找不到待替换的片段 {old!r}", file=sys.stderr)
                return 1
            prompt = prompt.replace(old, new)
        stage_repo(files_dir, spec["repo"], spec["fixture"], spec.get("plan"))
        case = {
            "id": f"attest-{cid:03d}",
            "prompt": prompt,
            "expected_output": c["expected_output"],
            "expected_skill": "attest",
            "files": [f"files/{spec['repo']}"],
        }
        if cid in ASSERTIONS:
            case["assertions"] = ASSERTIONS[cid]
        cases.append(case)

    trig = json.loads((EVALS / "trigger-evals.json").read_text(encoding="utf-8"))
    negatives = [t["query"] for t in trig if not t.get("should_trigger", True)]
    chosen = [q for q in NEGATIVE_QUERIES if q in negatives]
    if len(chosen) != len(NEGATIVE_QUERIES):
        print("trigger-evals.json 里少了要用的负例", file=sys.stderr)
        return 1
    for i, q in enumerate(chosen, 1):
        cases.append({
            "id": f"attest-neg-{i:03d}",
            "prompt": q,
            "expected_output": NEGATIVE_EXPECTED,
            "expected_skill": None,
            "assertions": NEGATIVE_ASSERTIONS,
        })

    dataset = {"skill_name": src.get("skill_name", "attest"), "evals": cases}
    (evals_dir / "evals.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pos = sum(1 for c in cases if c["expected_skill"])
    print(f"写出 {evals_dir / 'evals.json'}：{len(cases)} 条"
          f"（{pos} 正例 + {len(cases) - pos} 负例），staged 仓库在 {files_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
