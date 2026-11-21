---
name: attest
description: "Plan and verify a requirement's test suite. Use when writing or reviewing a feature's test plan, when data drifts between two stores and you need to know which tenant drifted, when asked whether a module can ship, or when choosing which test dimensions to cover. Not for reviewing ordinary application code, and not for performance, security or ops review. Triggers: 写测试计划、覆盖哪些测试维度、这个模块能不能交付、交付就绪度、跨存储数据有没有漂、生成测试骨架。"
license: MIT
metadata:
  author: mcgrapeng <mcgrapengzhang@gmail.com>
  version: "1.0.0"
  tags: [testing, test-plan, delivery, coverage, cross-store]
---

# 需求级测试计划

## Purpose

把一条需求变成一份**可核对的**测试工具集：每条行为契约有钉、每个关键流程有测试落点、
每份产出数据有一致性核对方式、已有能力先复用；然后用一条命令把这个模块的交付前提逐条
测出来，测不出来的说清缺什么，而不是报个通过。

## Prerequisites

- **Python 3.11+，无第三方依赖**（脚本只跑目标仓库自己的工具链：node、pytest…）。
- **目标仓库的依赖要装好**：attest 跑那个仓库的 test 命令，依赖没装 ⇒ 报「未实测」。
- **跨存储的模块**：要能连上 PG 与派生存储（向量库/对象存储），否则对账判「未实测」。
- 脚本在 `$SKILL_DIR/scripts/`（装在 `~/.agents/skills/attest` 时就是那个路径）。

## 一条命令：交付就绪度判定

```bash
python3 "$SKILL_DIR/scripts/readiness.py" <模块路径> --repo <根> \
  [--plan <需求测试计划>] [--include-itest] [--test-tenant <名字或前缀>] \
  [--mutation-check] [--json]
```

`$SKILL_DIR` = 本 skill 所在目录（装在 `~/.agents/skills/attest` 时就是那个路径）。

回答的是：**这个模块的交付前提里，哪些已被实测证明、哪些还没被证明、哪些本工具判不了。**
它不是「能不能交付给客户」的裁决 —— 需求理解、产品行为、性能、安全、可用性、运维就绪
都不在判据范围（报告里固定列出）。交付决策仍归人。

每条判据要么有实测证据，要么明说「未实测（缺什么条件）」—— 不拿静态推断冒充实测：

| 判据 | 证据类别 | 怎么测（速查） |
| --- | --- | --- |
| 测试全绿 | 实测 | 跑项目自己的 test 命令 |
| 核心逻辑测试到位 | 实测 | 驱动仓库的覆盖率闸门 |
| 测试能发现缺陷 | 实测 | 改坏源码再跑：红=杀得死（`--mutation-check`） |
| 表结构基本合规 | 实测 | 查 PG catalog（主键/RLS/时区，有豁免） |
| 数据流一致（跨存储对账） | 实测 | 实跑模块自己的对账脚本 |
| 数据准确（基线对拍） | 实测 | 实跑项目的基线对拍脚本 |
| 钉有区分力 | 实测 | 钉处改坏源码、跑钉住的用例（要 `--plan`） |
| 向量集合属性自洽 | 静态 | 各调用点的维度/度量/分区键 |
| 测试卫生 | 静态 | 固定等待 + 不清理的写库测试 |
| 导出的逻辑都有测试 | 静态 | 导出符号在测试里零引用 |
| 需求覆盖到位 | 静态 | 不变式的钉 → 真实存在的用例（`--plan`） |
| 不过度抽象 | 静态 | 导出符号几乎没有调用点 |
| 没有重复造轮子 | 静态 | 全仓同名实现 |
| 使用主流开源库 | 静态 | 模块真实依赖清单 |
| 断言的期望值独立 | 静态 | 期望值取自被测对象自身 ⇒ 提醒 |
| 计划与机器推导对齐 | 静态 | 反推判定点，与计划双向对账（`--plan`） |
| 应有判定点覆盖到位 | 静态 | 高危判定点有认领或豁免（`--plan`） |
| 不变式有判定点 | 静态 | 每条不变式有判定点认领（`--plan`）；复合不变式可豁免（语法见 workflow.md） |

**证据类别说的是"这条判据靠什么得出"**（跑出来的 vs 读源码读出来的），它决定报告里落在哪
一节：**静态信号永远不能单独支撑「通过」**。类别按**每次结果**标 —— 需要数据库的判据连不
上库时标成「未实测」。

任何一项「不通过」⇒ 结论里算**阻塞项**，退出码 1。

**实测的前提**：用项目自己声明的 test 命令（有些项目用 tsx 这类 runner，路径别名要它才
解析）。裸 `node --test` 会让一堆测试假挂 —— 那测的是假象。

判据的完整口径（怎么测、测不出来时怎么算、报告怎么分层、存储类模块怎么审）：
[references/criteria.md](references/criteria.md)。

## 核心原则

**钉得实 ≠ 理解得对。** 钉住只证明"这条不变式挂在一个真实存在的用例上"，不证明"这条
不变式就是需求的正确理解"。理解错了但错得自洽时工具会全绿 —— 它明说不判这一项。所以计划
里有一节**「确认」**：谁确认过、什么时候、依据是什么（人或 agent 都算）。没填 ⇒ `check`
提醒，报告把"需求覆盖到位"降级并明说「计划未经确认 ⇒ 需求是否被正确理解未获保证」。

**没有钉的不变式等于没测。** "我测了" 不可信，"这条不变式钉在 `service.ts:427` +
`write-gate-regression.itest.ts`" 才可信。脚本能机械核对的就是这件事：钉指向的文件在不在
仓库里、矩阵引用的测试文件在不在、车道是不是编的。

## 工作流

### 0. 零人工：收到需求就一路走到报告

**不需要人参与** —— 下面是 agent 拿到「一条需求 + 目标模块」后的完整链条：

| 步 | 做什么 |
| --- | --- |
| 1 | **从需求派生不变式**：抽出「这条需求必须永远成立的性质」，一条一条写下来（方法见 references/workflow.md） |
| 2 | 跑 `new <feature> --repo . --module <路径>` 拿计划骨架（扫存储清单、helper、测试车道、判定点） |
| 3 | 把不变式与覆盖矩阵填进计划；再用 `upgrade` 把维度表换成判定点粒度 |
| 4 | 用 `assign` 批量认领判定点：拉清单 → 逐条判归属或写豁免理由 → `--apply` 写回 |
| 5 | `scaffold` 生成测试骨架，**并把断言填掉**（期望值只有读过需求的 agent 知道） |
| 5' | 跨存储的模块：`req_plan.py reconcile` 生成对账脚本骨架、填完 `<待填>` 后 readiness 会自动认出并真跑；跟「当初的快照」对拍跑 `baseline` |
| 6 | 跑 `check`，把红清掉（红的都是真的：钉错了、落点不存在、维度没答） |
| 7 | 填「确认」节（自己是谁、依据是什么）与「本工具不判定项的归属」八行 |
| 8 | 跑 `readiness.py`，读交付前置条件：三条齐 ⇒ 可以进交付流程 |

**派生不变式这一步没有工具能替你做** —— 只能靠读需求：抽出每句「必须 / 不得 / 应该」，
每个「如果…就…」落一条，每条路径都要有不变式认领，每条钉到真实存在的用例。展开写法见
references/workflow.md。

**人不需要参与，但痕迹要留下**：第 7 步的「确认」节记的是 agent 的名字与依据（模型的确认
也是一条可追溯的记录，不是"没人反对"）。那八项本工具判不了，由 agent 认领并写明依据。

### 1–9. 线A 的九步

| 步 | 做什么 |
| --- | --- |
| 1 | 影响面 + 复用侦察 + 存储清单（`new`） |
| 2 | 写不变式清单：每条契约钉到真实用例 |
| 3 | 写覆盖矩阵：边界/错误/并发/重试/降级/隔离各一行 |
| 4 | 核心逻辑细节：每维度有落点或「不适用」+理由 |
| 4b | 判定点归属（v2）：三选一 —— 归属 / 落点 / 豁免 |
| 5 | 新建能力：先证明不能复用；调用点 ≤1 ⇒ 过度设计 |
| 6 | 数据一致性（跨存储才写）：权威源单一 + 镜像 + 怎么验 |
| 7 | 约束：不引入中间件 |
| 8 | 生成测试工具包（`scaffold`），断言留 TODO（agent 填） |
| 9 | 跑 `check`：核对每一个钉 |

每一步写成什么样、哪些写法会被判红：[references/workflow.md](references/workflow.md)。

## Usage（常用命令）

脚本都直接用 `python3` 跑（没有 `run_script` 包装层）：

| Script | 用途 |
| --- | --- |
| `scripts/req_plan.py new` | 生成计划骨架（扫存储、helper、车道） |
| `scripts/req_plan.py upgrade` | 把模块级维度表换成判定点粒度 |
| `scripts/req_plan.py assign` | 拉待填判定点清单 / 校验后写回 |
| `scripts/req_plan.py scaffold` | 生成测试文件骨架（断言留 TODO） |
| `scripts/req_plan.py check` | 核对每一个钉（文件、用例名、维度、豁免） |
| `scripts/req_plan.py reconcile` | 按计划的数据一致性节生成对账骨架 |
| `scripts/req_plan.py baseline` | 按计划的数据一致性节生成基线对拍骨架 |
| `scripts/readiness.py` | 交付就绪度判定（跑仓库自己的车道与闸） |
| `scripts/selfcheck.py` | 工具自检：往副本注入已知缺陷，验证判据翻红 |

```bash
# 生成计划骨架 / 生成测试骨架 / 核对计划 / 交付就绪度判定
python3 "$SKILL_DIR/scripts/req_plan.py" new <feature> --repo <根> --module <子路径>
python3 "$SKILL_DIR/scripts/req_plan.py" scaffold <计划文件> --repo <根>
python3 "$SKILL_DIR/scripts/req_plan.py" check <计划文件> --repo <根>
python3 "$SKILL_DIR/scripts/readiness.py" <模块路径> --repo <根> [--plan <计划文件>]
```

开关的含义、`--dry-run` 看会跑什么、`--self-check` 反向验证、写入边界与安全须知：
[references/commands.md](references/commands.md)。

常见错法（会判红或产生假绿的写法）：[references/pitfalls.md](references/pitfalls.md)。

## Examples（案例）

- **跨存储模块的完整做法**（真实案例：权威源、镜像、对账脚本、判据怎么钉）：
  [references/cross-store-reconciliation.md](references/cross-store-reconciliation.md)

## 出错了怎么办

| 现象 | 多半是 | 怎么办 |
| --- | --- | --- |
| 退出码 1 | 有阻塞项，或关键判据没测到 | 报告里列了是哪些：红的先清，绿的别动 |
| 退出码 2 | 读不懂这个模块（路径错/语言不支持） | 报错里说了「按什么扩展名识别」，换路径或换工具 |
| 判据是「未实测」 | 缺前置条件（库连不上、依赖没装、没给 `--plan`） | 报告里点名缺哪个：补上再跑 |
| 脚本报「没有认出的测试车道」 | 仓库的 test 脚本没被认出来 | 车道在计划里手填，见 references/workflow.md |
| 报红但看不出该改哪 | 钉指错了地方（文件/用例名/归属） | `check` 的每条红都写了「怎么改」 |

**红的别改钉来骗过检查** —— 钉该指哪就指哪。改钉 = 把没测到的东西说成测到了。

## Limitations

- **它不是「能不能交付给客户」的裁决**：需求理解、产品行为、性能、安全、可用性、运维
  就绪、并发正确性、容量与规模行为 —— 这八项按设计不判，报告里固定列出。
- **只读 TS/JS 与 Python**：就绪度检查器只认这两族（Rust/Go 只到计划生成器）。
- **不替代覆盖率看板**：它驱动仓库自己的闸门，读那个数字；仓库没有闸门时它自己测兜底。
- **静态信号永远不能单独支撑「通过」**：需要一条实测判据通过。
- **它不写断言**：`scaffold` 只生成结构与用例名，断言留给读过需求的 agent 或人。
- **它不改你的仓库**（除 `scaffold`/`new`/`reconcile` 按你指定的位置写文件）。

## references

| 文件 | 什么时候看 |
| --- | --- |
| [criteria.md](references/criteria.md) | 某条判据到底怎么测、报告怎么分层 |
| [workflow.md](references/workflow.md) | 写计划的某一步、什么写法会被判红 |
| [commands.md](references/commands.md) | 跑命令、传开关、担心它动仓库 |
| [pitfalls.md](references/pitfalls.md) | 判红了、或者结果可疑 |
| [cross-store-reconciliation.md](references/cross-store-reconciliation.md) | 跨存储模块怎么做对账（真实案例） |
