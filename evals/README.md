# 三层评测

这个仓库有三层可执行的评测，各管一件事。**不要把它们混为一谈**：
fixture 测工具的判定、eval 测命令的行为、trigger 测 description 会不会被用。

## 1. 黄金 fixture（`fixtures/`）—— 工具的判定对不对

5 个自包含的小仓库，每个钉死一个期望判定：

| fixture | 挡的是哪条质疑 |
| --- | --- |
| `ts-colocated` | 测试发现的作用域（模块内 colocated） |
| `python-repo` | Python 侧的导出引用判据不空转、依赖抽取认得出 |
| `rust-repo` | 认不出的语言要**退出码 2 + 说清**，不是满屏 ✓ |
| `english-reconciler` | 对账脚本输出英文时不得假绿 |
| `no-tests` | 零测试的模块不得被判「达到交付条件」 |

```bash
bash fixtures/run_fixtures.sh
```

退出码：0 全部命中 / 1 有不符 / 3 有 fixture 因环境缺失未判定
（`python-repo` 需要 `pytest` + `pytest-cov`）。

fixture 会被复制到临时目录再跑，不污染仓库。

## 2. 行为 eval（`evals/evals.json`）—— 命令的行为对不对

6 条 eval，每条声明用哪个 fixture 当被测仓库、跑什么命令、断言看命令输出还是产物文件。

断言 DSL：

| 写法 | 含义 |
| --- | --- |
| `exit_code=N` | 退出码必须等于 N |
| `exit_code_any=0\|1` | 退出码在给定集合里 |
| `contains:文本` | 输出里必须出现 |
| `not_contains:文本` | 输出里必须不出现 |
| `any_contains:A\|B\|C` | 至少出现其中一个 |

```bash
python3 evals/run_evals.py            # 跑全部
python3 evals/run_evals.py --only 3   # 只跑某一条
python3 evals/run_evals.py --json     # 机器可读
```

退出码：0 全部通过 / 1 有断言不通过 / 2 没有可跑的 eval。

## 3. 触发评测（`evals/trigger-evals.json`）—— description 会不会被用

20 条查询（10 条该触发 + 10 条近邻不该触发），测的是**模型有没有真的去加载这个
skill**。

```bash
python3 evals/measure_triggers.py                 # 跑全部
python3 evals/measure_triggers.py --max-turns 3   # 给模型几轮
```

**它会调用 `claude -p`，花你自己的额度**（20 条 ≈ 8 美元，取决于模型与轮数）——
所以它不在 CI 里，是发布前手动跑一次的东西。

### 为什么要自己写这个评测器

现成的 skill-creator `run_eval.py` **在这台机器上不可用**：它把 skill 做成一个斜杠命令
塞进 `.claude/commands/`，然后检测模型有没有调用那个命令。而本机 `~/.claude/skills/`
里已经装着真的 req-test-plan ⇒ 模型直接加载真 skill，探针命令一次都没被调用 ⇒
实测率全是 0.00（含"help me write a test plan"这种明显的匹配）。那是评测环境被污染，
不是真结果。

`measure_triggers.py` 测的是真机制：skill 装在 skills 目录里，模型看到 name+description，
决定用不用它 ⇒ 表现为 `Skill` 工具调用里 skill 名匹配。

### 两个容易踩的坑

1. **评测仓库不能是空目录**。查询里提到的 `src/billing/`、`docs/refund-test-plan.md`
   在空目录里不存在 ⇒ 模型探一圈发现没东西可查就跳过 skill（实测：它在思考里提到了
   req-test-plan，但没加载）。所以评测器先造一个骨架仓库。
2. **`--max-turns 1` 会让测量偏保守**。模型习惯先探索代码、再决定加载哪个 skill，
   截在第一轮就看不到触发。给 3 轮。

### 读结果

- **精确率**（不该触发的有没有触发）比召回率更要紧：误触发会让 skill 到处插手。
- 漏触发时看输出里的 `other_skills`：模型改用了别的 skill ⇒ 是"我们被更强的候选挤掉了"，
  要改 description；没有 ⇒ 它压根没想到有这回事，description 得把那个主题写进去。
