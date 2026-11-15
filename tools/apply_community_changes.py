#!/usr/bin/env python3
"""把本轮（市场清单 / 自测报告 / 文档）的改动落到一个目录里。

用法：python3 tools/apply_community_changes.py <仓库目录>

两个分支共用同一份内容：只有 README 里"回归套件数"那一行随分支不同
（main 是 25，DPE 分支是 27）—— 那行**不碰**，由各分支自己的 README 决定。
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()

EDITS = [
    # (文件, 原文, 新文)
    ("README.md",
     "| `module` | — | The module to check, relative to the repository root (`src/auth`, or `.` for the whole repository) |",
     "| `module` | — | The module to check, relative to the repository root (`src/auth`). Point it at the **module**, not at the repository root: with `.` the tool cannot see the repository's own `test/` directory |"),
    ("README.zh-CN.md",
     "| `module` | — | 要检查的模块路径，相对仓库根（`src/auth`，整仓则填 `.`） |",
     "| `module` | — | 要检查的模块路径，相对仓库根（`src/auth`）。要指向**模块**而不是仓库根：填 `.` 时工具看不见仓库自己的 `test/` 目录 |"),
    ("README.md",
     "| Reverse verification | Whether a deliberately injected defect turns the criteria red | Passed |",
     "| Reverse verification | Whether a deliberately injected defect turns the criteria red | Passed |\n\n"
     "Run against five public repositories, attest's own verdicts — including the\n"
     "four defects that run found in the tool itself — are published in the\n"
     "[self-test report](docs/self-test-report.md)."),
    ("README.zh-CN.md",
     "| 反向验证 | 故意注入缺陷后判据会不会翻红 | 通过 |",
     "| 反向验证 | 故意注入缺陷后判据会不会翻红 | 通过 |\n\n"
     "拿五个公开仓库跑一遍，attest 自己的结论——**包括这一轮在工具自身发现的四个缺陷**——公开在\n"
     "[自测报告](docs/self-test-report.md)里。"),
    ("docs/roadmap.md",
     """- [ ] **Marketplace submissions** — skills.sh, Anthropic's official skills
      marketplace, and the OpenCode and Codex plugin marketplaces.""",
     """- [x] **Marketplace submissions** — skills.sh indexed (it needs the skill in a
      subdirectory, which is why the layout moved); Anthropic's official skills
      repository carries a Partner Skills entry (PR
      <https://github.com/anthropics/skills/pull/2224>); `.claude-plugin/marketplace.json`
      makes the repository a marketplace for **Claude Code and Codex** — both
      verified by installing the plugin from it. OpenCode has no skill
      marketplace; it reads `.agents/skills/`, which the one-line install
      already populates."""),
    ("docs/roadmap.md",
     """- [ ] **Dogfooding reports** — run attest on 5–10 public repositories and
      publish the reports verbatim, including the ones where it found nothing.""",
     """- [x] **Dogfooding reports** — five public repositories, published verbatim in
      [docs/self-test-report.md](self-test-report.md), including the four
      defects that run found in the tool itself (three filed as issues)."""),
    ("CHANGELOG.md",
     "### 分发与共建：让工具能被找到、被一起建",
     """### 自测报告与市场收录（2026-11-15 追加）

**自测报告**：拿 5 个公开仓库（chalk / express / type-fest / click / itsdangerous，
外加一条负对照）跑判据，把**原样输出**与结论公开在
[docs/self-test-report.md](docs/self-test-report.md)。这一轮在工具自身发现 4 个缺陷：
测试文件无后缀不被发现（→ #1，误报阻塞项）、逐文件测试命令追加到复合命令末尾（→ #2）、
`.d.ts` + `.js` 被当成派生存储（→ #3）、Python 覆盖率报告路径找不到（未立案，待诊断）。

**市场收录**：仓库加 `.claude-plugin/marketplace.json`（Claude Code 与 Codex 认同同一份
清单 ✓ 两端都实测装得上、skill 路径能解析 ✓）与 `skills.sh.json`（仓库页分组）。
Anthropic 官方 skills 仓库的 **Partner Skills** 段已提 PR（anthropics/skills#2224）。
OpenCode 没有 skill 市场 —— 它读 `.agents/skills/`，一行安装已经覆盖 ✓。

**topics / description**：topics 扩到 12 个（加 attestation / ci / github-actions /
mutation-testing / requirements）；description 改成
"An evidence list for delivery readiness, built from your repo's own tests, coverage
gates and reconciliation scripts — in CI or as an agent skill."（146 字符）。

### 分发与共建：让工具能被找到、被一起建"""),
]

for rel, old, new in EDITS:
    p = root / rel
    t = p.read_text(encoding="utf-8")
    if new in t:
        print(f"  skip（已改过）{rel}")
        continue
    assert t.count(old) == 1, f"{rel}: 原文匹配 {t.count(old)} 次"
    p.write_text(t.replace(old, new), encoding="utf-8")
    print(f"  改了 {rel}")
