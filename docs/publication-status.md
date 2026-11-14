# 发布状态：能不能发，还差什么

日期：2026-11-14
仓库：`~/workspace/liaohe/req-test-plan`（分支 main）
本文是**唯一的现状结论**。前面的 `oss-release-review.md` / `oss-release-review-v2.md` /
`testing-philosophy-review.md` / `testing-philosophy-review-v2.md` 是历史评测记录，
它们的结论已逐条处理，处理结果见 `oss-release-review-v2.md` 第 6 节与本文。

## 总判定

**可以发到开源社区。** 前提是发布时把定位写对：

> 一个把交付前提变成可机读、可核对、可审计的证据清单的需求级测试工具。

**不是**"判断模块能不能交付"的裁判。它给的是「哪些前提已被实测证明、哪些只是静态信号、
哪些没测到、哪些它根本不判」，交付决策仍归人。

## 六道闸门（全部实测通过）

| 闸门 | 命令 | 结果 |
| --- | --- | --- |
| 回归套件 | `for t in scripts/test_*.py; do python3 "$t"; done` | 22/22 全绿 |
| 黄金 fixture | `bash fixtures/run_fixtures.sh` | 5/5 命中，0 不符，0 未判定 |
| 行为 eval | `python3 evals/run_evals.py` | 6/6 通过 |
| 安全扫描 | `bash .github/check-skillspector.sh` | 44 条 HIGH/CRITICAL，未放行 **0** 条 |
| 环境收口 | `python3 scripts/check_env_inheritance.py` | 生产代码没有整份继承 `os.environ` |
| 反向验证 | `readiness.py src --repo fixtures/ts-colocated --self-check` | 结论：反向验证通过 |
| 打包 | `python3 package.py` | 22 个文件，198 KB 的 `.skill` |

CI（`.github/workflows/ci.yml`）四个 job：字节码卫生 / 回归+fixture+eval+打包冒烟 /
打包冒烟 / SkillSpector 扫描。package-smoke 已本地逐条模拟通过。

## 这轮修掉的、最要紧的三条

1. **TS 车道的假绿**（复评抓到的最严重一条）。`_failed_tests` 只认 TAP 的 `not ok N -`，
   而 jest / vitest / mocha 都不吐 TAP ⇒ 那些仓库里失败的用例一个都认不出来 ⇒
   报「✓ 测试全绿」、退出码 0。**一个红着的套件拿到绿色裁决。**
   现在按「runner 退出码 + 输出」一起判：真失败⇒阻塞、环境没就绪⇒未实测、
   **输出格式认不出⇒未实测（绝不判绿）**。
   回归：`scripts/test_readiness_runners.py`（六种 runner 形态）。
2. **`scaffold` 的路径穿越与代码注入**。钉里的 `..` 能把文件写到仓库外；用例名原样
   插进 `it('...')` 能变成可执行代码。都封了（写盘面 + 读取面）。
   回归：`scripts/test_req_plan_scaffold_safety.py`（66 条）。
3. **输出契约从裁决改成证据清单**。原来输出"结论：达到交付条件 / 不可交付"——那是
   裁决措辞，而工具掌握的只是 12 条机械判据的实测状态。现在：证据分层（已实测 /
   静态信号 / 未实测）+ 三档结论（阻塞项 / 待人工确认 / 无信号）+ 固定的
   「本工具不判定」清单。

## 触发准确度（实测了，附边界）

已用**真实机制**测过：skill 装在 skills 目录里，看模型有没有真的发出
`Skill(skill=req-test-plan)` 调用（`evals/measure_triggers.py`，20 条查询，
10 正 + 10 近邻反例）。

数据与解读见 `evals/README.md`。**必读的一条**：这个数测的是"在这台机器 60 多个 skill
的竞争里我们排第几"，不是描述的绝对质量。OSS 用户装上时 skill 集合不同，数不可直接
迁移。所以它是**发布前的参考**，不是质量指标 —— 别把它当成待修的 bug 反复调
description（这正是我自己差点干的事：改一版 description 反而更差，见 git log）。

## 已知遗留（6 条，都是有意留下或范围之外）

见 `CHANGELOG.md`「已知遗留」。摘要：E2 扫描规则被显式放行（真收口在
`check_env_inheritance.py`）· `--exec` 显式开关未采纳（默认执行 + 审计行 + `--dry-run`）
· Windows 未端到端验证（运行时与安装已跨平台，仓库开发脚本要 bash）· 触发准确度的
边界如上 · 自检副本默认不带依赖（`--self-check-deps` 是出口）· Python 没有真后端
itest 车道的**全量**支持（Makefile/tox 兜底已接，但只覆盖脚本声明式的那部分）。

## 发布前要做的三件事（不是代码）

1. **建 GitHub 远端**（当前没有）。要定：仓库名、公开还是私有、要不要先打 v0.1.0 tag、
   作者联系方式。
2. **首屏定位**用上面那句，不要用"判断能不能交付"。
3. **README 的仍开放表与 CHANGELOG 对齐**（已对齐，发布前再核一遍）。

## 怎么复核这份状态

```bash
cd ~/workspace/liaohe/req-test-plan
for t in scripts/test_*.py; do python3 "$t" || echo "FAIL $t"; done
PATH="/tmp/ptcov-venv/bin:$PATH" bash fixtures/run_fixtures.sh    # 需要 pytest-cov
python3 evals/run_evals.py
bash .github/check-skillspector.sh
python3 scripts/check_env_inheritance.py
python3 package.py --check
```
