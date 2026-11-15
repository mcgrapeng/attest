# 黄金 fixture —— 钉死的期望判定

每个 fixture 是一个**自包含的小仓库**（有 `package.json` 或 `pyproject.toml`、源码、测试），
外面套着一层"钉死的期望判定"。它们是这份 skill 的**可复现证据**：
每一条断言都能被机器核对，不需要人读报告再判断"这算不算假绿"。

## 怎么跑

```bash
bash fixtures/run_fixtures.sh
```

退出码：

| 码 | 含义 |
| --- | --- |
| 0 | 10 个 fixture 的期望判定全部命中 |
| 1 | 有 fixture 与期望不符（输出里会列出**期望 vs 实际**） |
| 3 | 没有不符，但有 fixture 因环境缺失未判定（例如没装 `pytest-cov`） |

每个 fixture 都会先**复制到临时目录**再跑 —— pytest 会写 `.coverage` 与
`__pycache__`，仓库里有这些会被 CI 的"存在 `.pyc` 即失败"闸门抓住。

## 期望判定表

| fixture | 期望 | 钉死的判据 |
| --- | --- | --- |
| `ts-colocated` | 健康仓库（TS，测试 colocated） | 退出码 `0`；出现「单测 1 个」（测试被发现了）；出现「导出的逻辑都有测试」且**不**出现「零引用」（被测到的导出不算零引用）；出现「没有失败用例」（测试真跑过且真的绿） |
| `python-repo` | Python 仓（pytest 系） | 退出码 `0`；覆盖率**不是** skipped（不出现「缺少判据」「没拿到 Python 覆盖率」）；依赖清单不为空（出现「个第三方依赖」，不出现「模块没有第三方依赖」）；「不过度抽象」给出**真结论**（出现「都有多个调用点」，不出现「0 个导出符号都有多个调用点」）；「没有重复造轮子」不是零分析（不出现「没有可分析的导出符号」） |
| `rust-repo` | Rust 仓（工具认不出来） | 退出码 `2`；输出说清只实现了「TS/JS/Python」；**不**出现「✓」（不是满屏打勾） |
| `english-reconciler` | 跨存储对账脚本**输出英文** | 退出码 `1`；出现「数据流漂移」（漂移被如实认出来了）；**不**出现「生产租户无漂移」（不许假绿） |
| `no-tests` | 模块**一个测试都没有** | 结论**不**是「结论：达到交付条件」；「测试全绿」这条**不**是 ✓（不出现「✓ 测试全绿」）；**不**出现「没有失败用例」；判据仍在（出现「测试全绿」） |

每个 fixture 目录里还有一份 `README.md`，写明**为什么有它** ——
它挡的是评审里的哪一条质疑。

## 缺陷集 fixture（判定点引擎）

上面五条钉的是"仓库长什么样"。下面五条钉的是**判定点引擎有没有真的判出东西**：
每一条都自带一份 `plan.md`（判定点粒度的测试计划）和一处**刻意留下的缺陷**，
期望工具能指认它 —— 并且**不**误报别的地方。

| fixture | 缺陷形态 | 期望报出 |
| --- | --- | --- |
| `defect-silent-drop` | `filter` 静默丢掉缺 id / 缺正文的行 | `plan-alignment` 红：「机器推导出、计划里没有：1 条」（计划漏了第一道过滤） |
| `defect-shared-client` | 进程内共享的 client 被关掉 | `decision-coverage` 红：高危 `resource` 判定点「既没有归属不变式，也没有豁免理由」 |
| `defect-stale-cache` | 连接关了、缓存没清 | 需求覆盖红（钉指向的文件不存在）＋ DPSP **untested** 档：「1 条判不了（环境没就绪 / 输出认不出）」 |
| `defect-prototype-class` | `x in MAP` 放行原型链上的键 | DPSP **fail** 档：「1 条不变式的钉发现不了这类缺陷」，JSON 里报出存活的算子 `in-to-hasown` |
| `defect-mixed-subject` | 不变式的钉指向一个跟目标模块无关的测试文件 | `plan-alignment` 零覆盖：「1 条钉不直接引用目标模块」＋ DPSP **not-applicable** 档：「没有可判的变异点」 |

DPSP 的**四档**（`pass` / `fail` / `untested` / `not-applicable`）各自都被至少一个 fixture 覆盖 ——
四档退化成一档就说明这套判定只会说一句话。

这五条 fixture 的结论**依赖 TypeScript 后端（L3）**：L2（词法回退）下三条新判据
一律不硬判（缺能力绝不报绿），期望退出码会从 1 变成 0。缺 TS 时按 `have_ts_backend`
跳过 ⇒ 退出码 3（跳过，不是"不符"）。

## 断言的写法

`run_fixtures.sh` 里每个 fixture 的期望从 stdin 读，每行一条：

| 写法 | 含义 |
| --- | --- |
| `+文本` | 输出里必须出现 |
| `-文本` | 输出里必须**不**出现 |
| `?A;;B` | 至少出现 A、B 中的一个（候选之间用 `;;` 分隔） |

期望尽量用**稳定的字符串**（退出码、判据标题、关键提示语），
不用行号、不用百分比 —— 报告格式一改就全飘。

## 环境依赖

| fixture | 需要 |
| --- | --- |
| `ts-colocated` | `node`（22+，能直接跑 `.ts`） |
| `python-repo` | `pytest` **和** `pytest-cov`（工具用 `pytest --cov=pkg --cov-report=json` 实测覆盖率） |
| `rust-repo` | 无 |
| `english-reconciler` | `node`（跑 colocated 测试 + 跑对账脚本） |
| `no-tests` | 无 |
| 五条 `defect-*` | `node` ＋ **TypeScript 后端**（仓库内 `node_modules/typescript` 或全局 `npm i -g typescript`） |

缺依赖时 fixture 报「环境不满足（未判定）」，**不算不符** ——
"跑不起来"和"不达标"是两回事（同一条规矩）。

CI 里会 `pip install pytest pytest-cov`、`npm i -g typescript@5` 并带上 Node 22，
所以 CI 上全部都会被判定。
本地要判 `python-repo`，把一个装了 `pytest-cov` 的 venv 放进 PATH 即可：

```bash
PATH="/path/to/venv/bin:$PATH" bash fixtures/run_fixtures.sh
```
