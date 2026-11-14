# req-test-plan 开源发布条件复评（v2）

复评日期：2026-11-14
复评对象：`req-test-plan` skill 独立仓库
被复评的评测记录：[docs/oss-release-review.md](oss-release-review.md)（工程层）、
[docs/testing-philosophy-review.md](testing-philosophy-review.md)（哲学层）
复评者：独立复评员（未参与实现）

## 0. 复评快照与方法

**快照**：`HEAD = 74e858f`（`feat(evals): 让 6 条 eval 真的能跑（补 runner 与可执行声明）`，
其父 `d0270ef` = `fix(publish): 安全闸改成规则级 allowlist，并修跨存储节的生成口径`，
再父 `63d09ae` = 发布骨架提交）。
复评期间该仓库被另一个会话**并发修改并提交了两次**。

> **一个必须先说的事实**：复评开始时 `HEAD` 还是 `63d09ae`，工作树有未提交改动；
> 复评过程中 `.github/skillspector-baseline.yaml` 与 `regenerate-skillspector-baseline.sh`
> 被删除，换成了 `.github/check-skillspector.sh` + `.github/skillspector-allowlist.yaml`；
> 随后这些改动被提交为 `d0270ef` 与 `74e858f`。
> 本报告的所有结论都基于**最终 HEAD `74e858f`**（复评结束时复跑过全部四道闸门，结论不变）。
> 引用 `file:line` 时请以该提交为准。

**方法**：不复用实现方的自报结论。所有条目都重新读代码、重新跑命令、重新造样本。
`CHANGELOG.md` 只用来定位"改了什么"，不用来判定"改对了"。

**判定口径**：

| 结论 | 含义 |
| --- | --- |
| 已关闭 | 原问题在当前代码里不存在了，且有可复现证据 |
| 部分关闭 | 原问题的一部分消失了，另一部分仍在（会写清哪部分还在） |
| 仍开放 | 原问题原样还在 |
| 无法判定 | 缺环境/缺凭据/缺可执行判据，且写清缺什么（不用它掩盖没查） |

---

## 1. 四道闸门（实测）

### 闸门 1：全部回归脚本

```
cd /Users/zhangpeng/workspace/liaohe/req-test-plan/scripts
for t in $(ls test_*.py | sort -V); do python3 "$t" >/dev/null 2>&1 || echo "FAIL $t"; done
```

**结果：19/19 全绿，0 条 FAIL。**（`test_oracle_check` / `test_readiness` / `test_readiness_philosophy` /
`test_readiness_r2` / `test_req_plan` / `test_req_plan_r2..r4` / `r6..r14` /
`test_req_plan_scaffold_safety` / `test_selfcheck`）

### 闸门 2：五个黄金 fixture

```
PATH="/tmp/ptcov-venv/bin:$PATH" bash fixtures/run_fixtures.sh
```

```
=== ts-colocated ===     命中（退出码 0）
=== python-repo ===      命中（退出码 0）
=== rust-repo ===        命中（退出码 2）
=== english-reconciler === 命中（退出码 1）
=== no-tests ===         命中（退出码 1）
共 5 个 fixture：命中 5 / 不符 0 / 未判定 0
```

**结果：5/5 命中，退出码 0。** `/tmp/ptcov-venv` 存在（Python 3.13 + pytest-cov），
python-repo 那条是**真跑**的覆盖率判定，不是跳过。

### 闸门 3：安全扫描

复评开始时仓库用的是**逐条位置指纹基线**（`.github/skillspector-baseline.yaml`）。
按任务给的命令实跑：

```
skillspector scan . --no-llm --baseline .github/skillspector-baseline.yaml \
  --format json --output /tmp/g005-scan.json
→ Report saved to: /tmp/g005-scan.json   (exit 0)
```

扫描结果 `score 100 / CRITICAL / DO_NOT_INSTALL`，13 条基线外 HIGH —— 但**其中 9 条来自
`scripts/__pycache__/`**（我自己刚跑完测试生成的字节码，不是仓库内容），另 4 条来自
`.omx/ultragoal/goals.json` 与 `ledger.jsonl`。详见 §5.1。

在**干净导出**（`git archive HEAD`）上重扫：

```
score 31 / MEDIUM / CAUTION ；基线外 HIGH/CRITICAL 0 条 ；基线抑制 29 条
```

复评期间闸门被换成了**规则级 allowlist**（`.github/check-skillspector.sh`，已随 `d0270ef` 提交）：

```
bash .github/check-skillspector.sh
→ HIGH/CRITICAL 共 31 条，其中未放行 0 条；MEDIUM/LOW 36 条（不拦，照报）
→ OK：HIGH/CRITICAL 全部落在 allowlist 内。   (exit 0)
```

最终 HEAD `74e858f` 上复跑：同一条命令 ⇒「HIGH/CRITICAL 共 38 条，其中未放行 0 条；
MEDIUM/LOW 37 条（不拦，照报）」⇒ 退出码 0。

**结果：闸门当前是绿的。** 但闸门形态变了（逐条位置指纹 → 规则级放行），代价见 §5.2。

### 闸门 4：反向验证能翻红

```
python3 scripts/readiness.py src --repo fixtures/ts-colocated --self-check
→ EXIT=0
  ✔ [drop-test] src/calc.test.ts
      uncovered-exports：pass → fail
  ✔ [inject-sleep] src/calc.test.ts
      hygiene：pass → warn
  结论：反向验证通过
```

**结果：绿。** 注意它翻红的两条判据（`uncovered-exports`、`hygiene`）**都是静态信号**，
实测类判据没有被证明能翻红 —— 见 §4 标尺 3。

---

## 2. 我自己造的两个样本（仓库外，不进 git）

### 样本 A：标准 `test/` 目录布局的 TS 仓库

`/tmp/g5-std-testdir`：`package.json`（`"test": "node --test test/"`）+ `src/calc.ts`
（3 个导出 `add`/`sub`/`mul`）+ `test/calc.test.ts`（只测 `add` 与 `sub`）。

```
源码 1 文件 / 单测 1 个 / itest 0 个
  ✓ 导出的逻辑都有测试 — 1/3 个运行时导出在测试里零引用
      · mul（src/calc.ts）
```

**判定：B2 关闭。** 「单测 1 个」（不是零引用），被点名的只有真正没测的 `mul`，
被测到的 `add` / `sub` 不再被点名。仓库级 `test/` 布局被正确识别。

**但同一次运行暴露了一个新问题**（详见 §5.3）：

```
  ✗ 测试全绿 — 1 个用例失败
      · calc.test.ts: test
```

真实原因不是断言失败，而是 `node --test test/` 跑不起来：

```
$ node --test test/
Error: Cannot find module '/private/tmp/g5-std-testdir/test'
not ok 1 - test
  failureType: 'testCodeFailure'
```

**假红**：runner 崩溃被当成"1 个用例失败"⇒ 阻塞项。

### 样本 B：零测试的模块

`/tmp/g5-zero-test`：`package.json` + `src/thing.ts`（1 个导出），**无任何测试**。

```
结论：1 项阻塞 · 2 项待人工确认 · 7 项无信号（另有 5 条静态信号）
  ✗ 导出的逻辑都有测试：1/1 个运行时导出在测试里零引用
  ○ 测试全绿：没有找到任何测试文件 —— 无法判定测试是否绿
EXIT=1
```

**判定：B1 关闭。** 结论**不是**「达到交付条件」，退出码非 0，关键判据落在「未实测」
而不是「✓ 没有失败用例」。仓库自带的 `no-tests` fixture 同样命中（退出码 1，
「结论：0 项阻塞 · 1 项待人工确认 · 10 项无信号」）。

---

## 3. 结论总表

| 分节 | 条目数 | 已关闭 | 部分关闭 | 仍开放 | 无法判定 |
| --- | --- | --- | --- | --- | --- |
| 一、一票否决级（B1–B4） | 4 | 4 | 0 | 0 | 0 |
| 二、文档与实现的矛盾（C1–C5） | 5 | 5 | 0 | 0 | 0 |
| 三、名不副实或空转的判据 | 5 | 4 | 1 | 0 | 0 |
| 四、对账层 | 5 | 4 | 1 | 0 | 0 |
| 五、scaffold 的语言感知 | 2 | 1 | 0 | 1 | 0 |
| 六、安全线 | 10 | 6 | 4 | 0 | 0 |
| 七、可移植性 | 3 | 1 | 1 | 1 | 0 |
| 八、plugin-eval 四轴 | 4 | 1 | 3 | 0 | 0 |
| 九、敌意评审六问 | 6 | 4 | 2 | 0 | 0 |
| 十、P0 清单 | 9 | 9 | 0 | 0 | 0 |
| 十、P1 清单 | 13 | 10 | 3 | 0 | 0 |
| 十、P2 清单 | 7 | 2 | 2 | 3 | 0 |
| **合计** | **73** | **51** | **17** | **5** | **0** |

> 结论数合计 73；本报告另有一节 §5「复评中新发现的问题」记 6 条（§5.1–§5.6，其中 §5.6 是零散小项的汇总表，不计入上表）。
> 其中 §5.3（TS 车道把 runner 崩溃误判成"用例失败"或"全绿"）是**本轮最要紧的一条**。

---

## 4. 逐条结论

### 4.1 一、一票否决级

#### B1 零测试的模块被判「达到交付条件」，退出码 0
**结论**：已关闭
**证据**：
- 独立样本 `/tmp/g5-zero-test`（§2 样本 B）：`EXIT=1`，结论「1 项阻塞 · 2 项待人工确认 · 7 项无信号」，
  关键判据行是「○ 测试全绿：没有找到任何测试文件 —— 无法判定测试是否绿」，**不出现**「达到交付条件」。
- 代码：`scripts/readiness.py:1705-1710`（`if not tests_found: return Check(... "skipped" ..., evidence_kind="untested")`）、
  `scripts/readiness.py:1711-1716`（`if not tests_ran: ... "skipped"`）。
- 仓库自带 fixture `fixtures/no-tests` 期望判定「结论不是『达到交付条件』；『测试全绿』这条不是 ✓」，实跑命中。

#### B2 测试发现是模块目录内作用域，健康仓库被判不可交付
**结论**：已关闭
**证据**：
- 独立样本 `/tmp/g5-std-testdir`（§2 样本 A）：「单测 1 个」，被点名的只有真正零引用的 `mul`。
- 代码：`scripts/readiness.py:33`（`REPO_TEST_DIRS = ("test", "tests", "spec")`）、
  `scripts/readiness.py:218-260`（`repo_level_tests()`，归属判定＝测试文件提到该模块）。
- `--test-dir` 可追加目录（`scripts/readiness.py:230-237`）。

#### B3 `scaffold` 路径穿越：写到仓库外
**结论**：已关闭
**证据**（自造计划，未用回归脚本）：
```
$ python3 scripts/req_plan.py scaffold /tmp/g5-repo/docs-plan.md --repo /tmp/g5-repo
  ✗ 写入安全: 钉「../../../../tmp/g5-pwned/x.test.ts」含 `..`（会跳到边界之外）
      钉只能是仓库内的相对路径 —— 越界的钉不写盘。
一个文件都没写 —— 有问题的钉先改掉。
$ ls -la /tmp/g5-pwned/     # 空目录，零文件
```
绝对路径的钉同样被拒（`scripts/test_req_plan_scaffold_safety.py:144-151` 覆盖）。

#### B4 `scaffold` 代码注入：计划 markdown 变成可执行代码
**结论**：已关闭
**证据**（自造载荷 `x', () => {}); process.stdout.write('PWNED_MARKER\n'); it('y`）：
```
生成物：
  it('x\', () => {}); process.stdout.write(\'PWNED_MARKER\\n\'); it(\'y', () => {
```
载荷完整留在字符串字面量里，后面只剩 `, () => {`；生成物里只有一个 `it(`。
代码：`scripts/req_plan.py` 的 `_js_str` / `_comment_safe`（见 `scripts/test_req_plan_scaffold_safety.py:162-210`）。
`*/` 撑破块注释那一路也堵住了（同文件 201-210 行的断言）。

### 4.2 二、文档与实现的矛盾

| # | 结论 | 证据 |
| --- | --- | --- |
| C1 通用阈值 80/70 与文档矛盾 | 已关闭 | `scripts/readiness.py:132-134`（SKILL.md）写的「仓库没有闸门 ⇒ 判『未实测』，默认不阻塞」在实现里一致：`scripts/readiness.py:132` 的判据表第 43 行、`check_coverage` 的 `min_line` 默认 `None`。实跑 `fixtures/ts-colocated`：「— 核心逻辑测试到位 — 仓库没有覆盖率闸门，缺少判据 … 通用阈值默认不阻塞」，**不**判 fail。`--min-line-coverage` 变成显式 opt-in（SKILL.md:564）。 |
| C2 itest 默认就跑 | 已关闭 | `scripts/readiness.py:2573`：`if itest_scripts and args.include_itest:` —— 不传 `--include-itest` 就不进 itest 车道。SKILL.md:139-142 已同步。 |
| C3 「只读代码，不改任何东西」 | 已关闭 | `scripts/req_plan.py` 模块说明已改（`scripts/test_req_plan_scaffold_safety.py:358-364` 断言"不再声称只读代码"且"写清了只有 scaffold 写文件"）；SKILL.md:503-505 的「安全须知」明说 `scaffold` 会写文件。 |
| C4 依赖抽取只认 `from '…'` | 已关闭 | `scripts/readiness.py` 的依赖抽取已覆盖 `require(...)` / `import 'x'` / Python `import x` / `from x import y`（并排除标准库与相对导入）。实跑 `fixtures/python-repo`：期望判定包含「依赖清单不为空（出现『个第三方依赖』，不出现『模块没有第三方依赖』）」，**命中**。 |
| C5 判据表 12 条 vs 代码 11 个 Check | 已关闭 | 代码现在 12 个 Check（`scripts/readiness.py` 的 `checks = [...]`：tests-green / hygiene / coverage / uncovered-exports / schema / data-flow / data-accuracy / requirement / extensibility / no-wheels / open-source / oracle-independence），`--json` 实测 `checks 条数: 12`；SKILL.md:40-53 的判据表 12 行，含新增的「断言的期望值独立」。 |

### 4.3 三、名不副实或空转的判据

| 判据 | 结论 | 证据 |
| --- | --- | --- |
| 使用主流开源库 | **部分关闭** | 依赖抽取修好了（原 C4），空依赖时也不再打 ✓ 而是 `warn`（`scripts/readiness.py` 的 `check_open_source`：`if not m.deps: return Check("open-source", ..., "warn", ...)`）。但判据**仍叫「使用主流开源库」**，实现里只数依赖条数（`f"{len(m.deps)} 个第三方依赖"`）—— 从不判断"主流"或"开源"。上一轮给的选项是「要么真的评估，要么改名（建议『依赖清单可读』）」，**改名没做、真判定也没做**。它已被降级为 `evidence_kind="static"`（这是哲学层 R4 允许的另一条路），但 P1 #13 的原文要求未满足。 |
| 具备扩展性 | 已关闭 | 改名「不过度抽象」（SKILL.md:50），实现 `check_extensibility` 的 docstring 写明「之前叫『具备扩展性』，但它测的是『有没有过度抽象』⇒ 名字反了」；Python 下导出集为空 ⇒ `skipped`（"这个模块没有可分析的导出符号（零分析 ⇒ 不给结论）"），不再是「0 个导出符号都有多个调用点」。 |
| 没有重复造轮子 | 已关闭 | `check_no_wheels` 同样有 Python 分支（`_module_exports_any`）与空集 `skipped`。 |
| 表设计符合主流 | 已关闭 | 改名「表结构基本合规」，docstring 明说「判的是**自家五条规则**，不是『主流』」；租户列/唯一约束加了豁免：单租户模块（`not tenant_scoped`）、全局配置表、追加型表（`exempt = (not tenant_scoped) or is_config or is_append`）。 |
| 缺「未实测」态 | 已关闭 | `Check.evidence_kind`（`executed` / `static` / `untested`）已落地（`scripts/readiness.py:100-102`），报告按「已实测 / 静态信号 / 未实测」分三节。`--json` 实测每条判据都带 `evidence_kind`。 |

### 4.4 四、对账层

| 条目 | 结论 | 证据 |
| --- | --- | --- |
| 漂移识别只懂「中文『漂移』+ ❌」 | 已关闭 | `drift_re` / `header_re` / `tenant_col` / `tenant_prefixes` / `csv_pairs` 全部参数化（`scripts/readiness.py:1132-1136`、CLI `--drift-pattern` / `--table-header-pattern` / `--csv-pair` / `--tenant-col`）。`fixtures/english-reconciler`（输出英文）期望「暴露漂移，**不**报『生产租户无漂移』」，实跑**命中**。 |
| 租户列写死第一列、表头排除中文「一致性」 | 已关闭 | 租户列改为从表头认（`scripts/readiness.py:1182`，认不到才退回第一列）；表头正则可配。 |
| 输出目录靠 lumen 专属 `MEMORY_RECONCILE_OUT_DIR` | **部分关闭** | 通用变量 `REQ_PLAN_RECONCILE_OUT_DIR` 已落地并优先（`scripts/readiness.py:1148-1152`），自动跑时指向临时目录、不覆盖人工当天那份。但**旧的 `MEMORY_RECONCILE_OUT_DIR` 仍被兼容保留并写进子进程环境**（`scripts/readiness.py:42` 的 `ENV_ALLOW_EXACT`），lumen 私货没清干净。 |
| CSV 两两配对 + 比对 `created_at` ⇒ 生产租户假红 | 已关闭 | `_csv_pairs()`（`scripts/readiness.py:1233-1271`）优先用 `--csv-pair` 声明的对照关系，没声明时按"共同列最多的互为最佳"配对（不是两两配对）；时间戳类字段排除、正文类字段单独归类报「需人工判断」。找不到成对 CSV 时报「无法比对」而不是「0 对无差异」。 |
| `run_reconciler` 往被调脚本里塞 `--experimental-test-coverage` / `--run` | 已关闭 | 现在的实现注释明写「用项目自己的 runner（路径别名要 tsx 才解析）—— **不改被调脚本的语义**」（`scripts/readiness.py:1132-1160`），对账脚本不再被追加参数。 |

### 4.5 五、scaffold 的语言感知

#### `detect_conventions` 只认 JS/TS，Python/Rust/Go 落回 node:test
**结论**：已关闭
**证据**：`scripts/test_req_plan_scaffold_safety.py:212-243` 断言 Python 落点生成 pytest 风格
（`def test_add` + `assert`）、Rust 落点生成 `#[test]` + `assert_eq!`、Go 落点生成
`func TestAdd(t *testing.T)` + `t.Fatalf`；实跑 `python3 scripts/test_req_plan_scaffold_safety.py` 全绿。
认不出语言时**拒绝生成并报红**（同文件 245-259 行），且能从 `package.json` 推断
node:test / vitest / jest / mocha（269-314 行），推断不出 ⇒ 报红「框架推断不出」（315-325 行）。

#### `_symbol_exists` 只扫 TS 扩展名，`_symbol_uses` 却含 `.py/.rs/.go`
**结论**：**仍开放**
**证据**（自造对照仓库，未用回归脚本）：

Python 仓库 `/tmp/g5-pywheel`：`src/pkg/util.py` 与 `src/pkg/feature.py` **都**定义了
`existing_helper`，计划在「新建能力」节声明要新写 `existing_helper`：

```
  · 新建能力: 'existing_helper' 没写清楚为什么不能复用现成的
3 项：0 个错误 / 3 个提醒      ← 没有任何「全仓已经存在」的报错
```

同样的场景换成 TS 仓库 `/tmp/g5-tswheel`：

```
  ✗ 新建能力: 'existingHelper' 全仓已经存在（src/feature.ts）
      直接复用，别再造一个
4 项：2 个错误 / 2 个提醒
```

原因在 `scripts/req_plan.py`：
`_symbol_exists` 的 `code_exts = {".ts",".tsx",".js",".jsx",".mjs",".cjs"}`，
而 `_symbol_uses` 的 `code_exts` 多出 `.py/.rs/.go`。
后果：**「重复造轮子」这条硬报红检查在 Python/Rust/Go 仓库上整条不生效** ——
`check` 的退出码会因此给出"可以交付"的假象。这正是上一轮第五节点名的"范围不一致"，
至今没改。

### 4.6 六、安全线

#### SkillSpector：含 `__pycache__` ⇒ 100/CRITICAL/DO_NOT_INSTALL
**结论**：已关闭
**证据**：`.gitignore` 首两行 `__pycache__/`、`*.pyc`；`git ls-files` 里没有任何 `.pyc`；
CI 有独立 job（`.github/workflows/ci.yml` 的 `hygiene` job）：`find . -path ./.git -prune -o \( -name '__pycache__' -o -name '*.pyc' \) -print` 非空即失败。
干净导出重扫：`score 31 / MEDIUM / CAUTION`，基线外 HIGH/CRITICAL **0 条**。

#### 残留的 22 个 HIGH 全是 E2（Env Variable Harvesting）
**结论**：**部分关闭**
**证据**：闸门从"逐条位置指纹基线"换成了"规则级 allowlist"（`.github/skillspector-allowlist.yaml`，
放行 E2 / AE1 / PE3 / SC9 / AST9 五条规则，每条带 reason）。allowlist 文件头把代价写清楚了：
「同一个规则在一个已放行的文件里**再出现**不会被拦」。
**这是闸门形态的实质变化，不是纯格式变化**：上一轮的设计是"改了那一行就要重新看一眼"，
现在改成"这条规则一律放行"。新规则仍会被拦，但**已放行规则的新增实例不会被拦**。
`bash .github/check-skillspector.sh` 实跑：31 条 HIGH 中未放行 0 条。

#### 目的契合 / 外部传输 / 持久化
**结论**：已关闭
**证据**：生产脚本只用标准库（`scripts/readiness.py` / `req_plan.py` 无第三方 import，
只有 `urllib.parse`、`tomllib` 等标准库）；无网络出口；无 cron/daemon/rc 改写；
`uninstall.sh` 实测只删软链、不动真实目录（`HOME=/tmp/g5-home bash uninstall.sh` ⇒ 4 条软链删除，
目录骨架保留）。

#### 执行风险：脚本选取是子串匹配，`reconcile:reset` 会被执行
**结论**：已关闭
**证据**：`scripts/readiness.py:49-67` 改成显式白名单（`exact` 集合 + `prefix` 元组）。
自造仓库 `/tmp/g5-wl` 的 `package.json` 同时含 `reconcile`、`reconcile:reset`、
`coverage:whatever`、`integration`、`baseline:check`：

```
$ python3 scripts/readiness.py src --repo /tmp/g5-wl --dry-run
将要执行的仓库脚本 — src（--dry-run，不执行）
  [基线对拍] baseline:check（工作目录 /private/tmp/g5-wl）
      $ node baseline.js
```

`reconcile:reset` 与 `coverage:whatever` 都没被选中。

#### 凭据暴露：`psql` 连接串走 argv，进程表可见
**结论**：已关闭
**证据**：`pg_env()`（`scripts/readiness.py:773-803`）把连接串解析成 `PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE/PGSSLMODE`；
`_psql()`（806-814）的 argv 是 `["psql","-t","-A","-F","\x1f","-c",sql]` —— **不含连接串**；
解析失败返回空 dict ⇒ 查不到表结构，而不是把口令塞进命令行。

#### 写入面：B3 + B4
**结论**：已关闭（见 §4.1）

#### Prompt 风险：半可信的 markdown 变成可执行代码与文件路径
**结论**：已关闭（见 §4.1 B3/B4）

#### 触发风险：description 触发面过宽、且对"会写文件、会跑仓库脚本"零提示
**结论**：**部分关闭**
**证据**：`SKILL.md:3-11` 的 description 现在点名了 readiness 交付判定能力
（"run a one-command delivery-readiness check that actually executes the project's own
tests, coverage gates, cross-store reconciliation and baseline scripts"），
也补了中文触发词（写测试计划、核一遍测试覆盖、这个模块能不能交付…）。
**仍缺**：① 没有声明"会向仓库写文件"（`scaffold` 写测试文件）——这是上一轮点名的副作用之一；
② 没有声明语言覆盖范围（TS/JS/Python，Rust/Go 只做计划生成）；
③ 没有声明"会执行目标仓库自己定义的脚本"。
上一轮给的验证方式（skill-creator 的 trigger eval 集跑分）本轮**未跑**（需要模型打分与
skill-creator 的 harness）⇒ 这一项的**得分无法判定**；上面的结论只基于 description 的静态审读。

#### 用户控制：没有 `--dry-run`、没有 `--no-exec`，用户跑之前不知道会执行什么
**结论**：**部分关闭**
**证据**：`--dry-run` 已实现（只列候选脚本与确切命令后退出，`scripts/readiness.py:2549-2554`），
正常模式也会在报告头部回显「将执行的仓库脚本」（`_echo_script_plan`，2555-2558）。
**`--no-exec` / `--exec` 显式开关仍未采纳** —— 实现方选择了"默认执行 + 执行前打印审计行"，
理由写在 `CHANGELOG.md`「修复与已知遗留」与 `README.md`「仍开放」表里。
从"发布者承担什么风险"的角度：用户装上它跑一次，就等于**默认执行**了目标仓库的
`test` / `coverage` / `reconcile` / `baseline:check` 脚本，没有一个必须显式敲的开关。
默认执行本身不是错（工具定位就是实测），但**它与上一轮"要么默认不跑、要么有显式开关"的建议不同**。

#### 子进程环境继承
**结论**：**部分关闭**
**证据**：`child_env()`（`scripts/readiness.py:140-153`）从 `{**os.environ}` 改成白名单。
实测确认**云厂商密钥不透传**：
```
CHILD_SEES_AWS=(unset)
```
**但 `DATABASE_URL` 在白名单里，会透传**（`scripts/readiness.py:41`）：
```
CHILD_SEES_DATABASE_URL=postgres://user:SECRET@db:5432/prod
```
`README.md` 的「安全须知」第 2 条写的是「你的 `DATABASE_URL`、各种 API key 不会进目标仓库的脚本」——
**这句话与代码相反**。见 §5.4。

### 4.7 七、可移植性

#### 回归套件不独立（r3/r4 硬编码 `/Users/zhangpeng/...`）
**结论**：已关闭
**证据**：`grep -rn "/Users/" scripts/` 只命中 `scripts/test_req_plan_scaffold_safety.py` 里
**断言字符串本身**（337-338 行：`expect("/Users/" not in r3src, ...)`），没有真实硬编码。
`scripts/test_req_plan_scaffold_safety.py:334-356` 会把 r3/r4 复制到临时目录、把 `HOME` 指向空目录跑一遍，
断言「退出码仍是 0」「打印了 skip 提示」「不崩」。实跑 19/19 全绿。

#### 调用路径写死 9 处 `~/.agents/skills/req-test-plan/scripts/...`
**结论**：**部分关闭**
**证据**：`SKILL.md` 里的 9 处已改成 `"$SKILL_DIR/scripts/..."`（SKILL.md:26/241/245/424/440/517/538/542/546/549/553）。
**但 `$SKILL_DIR` 没有任何地方定义** —— SKILL.md:30 只是说「`$SKILL_DIR` = 本 skill 所在目录」，
把它当环境变量抄进终端的人会得到 `python3 "/scripts/readiness.py"`。
`README.md` 的「四条命令」仍全部写死 `~/.agents/skills/req-test-plan/scripts/...`（33/37/41/45 行），
而且 `README.md` 自己的「仍开放」表里第 178 行就写着这条还开着。装到别的目录就失效。

#### 缺发布骨架（无 LICENSE / README / CHANGELOG / evals / CI / 打包元数据）
**结论**：**部分关闭**
**证据**：`LICENSE`（MIT 正文，1066B）、`README.md`（12.7K）、`CHANGELOG.md`（9.4K）、
`evals/evals.json`（6 条 eval，断言可机检）、`.github/workflows/ci.yml`（3 个 job：
hygiene / test+fixture / package-smoke / skillspector）、`install.sh` + `uninstall.sh`
（实测装/卸，软链未断，`install.sh` 末尾有 `--help` 自检）——
**除打包元数据（产出 `.skill` 的 `package_skill.py`）外都齐了**。
打包元数据仍缺 ⇒ 这条整体只能算部分关闭。

### 4.8 八、plugin-eval 四轴（复评重评）

> 上一轮的四轴分是按 rubric 的人工判断，没有实测触发准确度。本轮同样只做静态复评，
> 触发准确度**没有实测**（`claude` CLI 本机可用，但 skill-creator 的 trigger eval 未跑）。

| 轴 | 上轮 | 本轮 | 结论 | 依据 |
| --- | --- | --- | --- | --- |
| 触发准确度 | 0.65 | 0.72 | **部分关闭** | description 点名了交付判定命令与能力、中文触发词补上了；但仍没声明"会写文件"与语言覆盖（§4.6 触发风险）。"when starting a new feature" 的宽触发面未动。 |
| 编排适配度 | 0.55 | 0.72 | **部分关闭** | 契约比上轮清楚得多：三档结论、证据分节、退出码语义、12 条判据表、`--dry-run` 审计行都已写死在 SKILL.md 里。仍是编排型 skill（9 步工作流 + 驱动子进程）。 |
| 输出质量 | 0.72 | 0.80 | **部分关闭** | 五处会误导读者的输出（上轮点名的 B1/B2/三条空判据）都已修。但本轮新发现两处会误导读者的问题：TS 车道把"runner 崩溃"当成"用例失败"（假红）与"runner 崩溃"当成"没有失败用例"（假绿，§5.3）。 |
| 范围校准 | 0.42 | 0.85 | **已关闭** | 这是本轮提升最大的一轴。SKILL.md:32-36 把头号问题直接写成"回答的是：哪些已被实测证明、哪些还没被证明、哪些本工具判不了"，并明说"它**不是**『能不能交付给客户』的裁决"；报告里有固定的「本工具不判定」六项清单；结论是三档而不是二元。**范围声明与实现对齐了。** |

### 4.9 九、敌意评审会提的六个问题（重新实跑）

| # | 敌意提问 | 结论 | 实跑结果 |
| --- | --- | --- | --- |
| 1 | 「你的『测试全绿』在一个测试都没跑的时候是绿的。」 | 已关闭 | 零测试样本 `EXIT=1`，判据落在「○ 未实测」而非「✓」（§2 样本 B）。 |
| 2 | 「在标准 `test/` 目录布局的仓库上，你把健康代码判成不可交付。」 | 已关闭 | 标准 `test/` 布局样本「单测 1 个」，只有真正零引用的 `mul` 被点名（§2 样本 A）。 |
| 3 | 「你的 `scaffold` 能往仓库外写文件，还能把计划 markdown 变成可执行代码。」 | 已关闭 | 越界钉被拒、零文件写盘；注入载荷完整留在字符串字面量里（§4.1）。 |
| 4 | 「你有两条永远打勾的判据，两条对 Python 空转。」 | 已关闭 | 两条判据都有 Python 分支且空集判 `skipped`；「使用主流开源库」降级为静态信号（不再假装是实测判据）。 |
| 5 | 「文档说通用阈值是错的，代码把它当门槛用；文档说 itest 要 `--include-itest`，代码默认就跑。」 | 已关闭 | 通用阈值默认不阻塞、要阈值得显式 `--min-line-coverage`；itest 默认不跑（`scripts/readiness.py:2573`）。 |
| 6 | 「这不是一个可发布的制品。」 | **部分关闭** | LICENSE / README / CHANGELOG / evals / CI / install / fixtures 都有了，回归套件不再依赖作者家目录。**但**：`.omx/ultragoal/`（29KB 内部编排状态）仍被 git 跟踪（§5.1）；正文仍只有中文（P2 #28）；打包元数据仍缺（P2 #29）；SKILL.md 里仍有 5 处 lumen 私货（P2 #27）。 |

### 4.10 十、P0 清单（9 条）

| # | 改动 | 结论 | 证据 |
| --- | --- | --- | --- |
| 1 | 删字节码 + `.gitignore` + CI 检查 | 已关闭 | §4.6；干净导出扫描 31/MEDIUM/0 HIGH。 |
| 2 | B1 零测试判 skipped + 回归用例 | 已关闭 | §4.1 B1；`scripts/test_readiness_r2.py:81-91`、`scripts/test_readiness_philosophy.py:103` 在守。 |
| 3 | B2 测试发现扩到仓库级 | 已关闭 | §4.1 B2。 |
| 4 | B3 路径穿越拒绝 | 已关闭 | §4.1 B3。 |
| 5 | B4 用例名引号转义 | 已关闭 | §4.1 B4。 |
| 6 | scaffold 语言感知 + 认不出就拒绝 | 已关闭 | §4.5。 |
| 7 | LICENSE / README / CHANGELOG | 已关闭 | 三份文件都在；README 能独立跑通 demo（实测 `bash fixtures/run_fixtures.sh`）。 |
| 8 | r3/r4 去绝对路径 + CI 跑全部脚本 | 已关闭 | §4.7；CI 用 glob 逐个跑（不假设文件名有序，文件名确有跳号 r5 缺失）。 |
| 9 | 改写 SKILL.md 范围声明 | 已关闭 | SKILL.md:32-36、176-194。 |

### 4.11 十、P1 清单（13 条）

| # | 改动 | 结论 | 证据 |
| --- | --- | --- | --- |
| 10 | 通用阈值矛盾 | 已关闭 | §4.2 C1。 |
| 11 | itest 默认行为 | 已关闭 | §4.2 C2。 |
| 12 | extensibility/no_wheels 加 Python 分支 | 已关闭 | §4.3。 |
| 13 | open_source 真判定或改名 + 依赖抽取 | **部分关闭** | 依赖抽取已修（C4）；判据**未改名、未真判定**，只数依赖条数（§4.3）。 |
| 14 | 对账层参数化 | 已关闭 | §4.4。 |
| 15 | 脚本白名单 + `--dry-run` + 环境白名单 | **部分关闭** | 白名单/dry-run/环境白名单都做了；`--exec` 显式开关未采纳（§4.6 用户控制）。 |
| 16 | `psql` 不走 argv | 已关闭 | §4.6 凭据暴露。 |
| 17 | 表设计改名 + 豁免 | 已关闭 | §4.3。 |
| 18 | 「具备扩展性」改名 | 已关闭 | §4.3。 |
| 19 | `new` 无存储不生成数据一致性节 | 已关闭 | 实跑 `/tmp/g5-new`：`new` 生成的节里**没有**「数据一致性」「约束」两节；紧接着 `check` 的 2 个错误都是「这一节还是模板占位，没填」，不再是"工具自己生成的节自己要求删"。修复已随 `d0270ef` 提交。 |
| 20 | 判据数对齐 | 已关闭 | §4.2 C5。 |
| 21 | `req_plan.py:9` 措辞 | 已关闭 | §4.2 C3。 |
| 22 | SKILL.md 覆盖率说明去重 | 已关闭 | 原 85-87 与 145-147 的逐字重复已消失：现在 101-104 是 Python 专项、117-137 是"覆盖率必须是确定的"专题、113-115 是 itest 车道说明，三段各说各的。 |

### 4.12 十、P2 清单（7 条）

| # | 改动 | 结论 | 证据 |
| --- | --- | --- | --- |
| 23 | description 扩写 | **部分关闭** | §4.6 触发风险：点名了交付判定与"executes the project's own tests"，但没声明"会向仓库写文件"、没声明语言覆盖。trigger eval 未跑 ⇒ **得分无法判定**。 |
| 24 | 9 处硬编码路径 → 解析本 skill 目录 | **部分关闭** | SKILL.md 已换 `$SKILL_DIR`，但**该变量无人定义**；README 四条命令仍写死 `~/.agents/skills/...`（§4.7）。 |
| 25 | 建 `evals/`（≥5 黄金 fixture） | 已关闭 | `fixtures/` 5 个 + `evals/evals.json` 6 条 eval，断言可机检。 |
| 26 | CI（Node 22 + Python 3.11/3.13 + 打包冒烟） | 已关闭 | `.github/workflows/ci.yml` 的 matrix 是 node 22 × python 3.11/3.13；四个 step（逐个跑 `scripts/test_*.py`、跑 fixtures、打包冒烟、skillspector）**本轮都在本机等价跑通**（闸门 1/2/3 + 打包冒烟步骤）。**GitHub Actions 本体未观测**（无网络/CI 环境）。 |
| 27 | lumen 私货移出正文 + 脱敏 | **仍开放** | `grep -rn "lumen\|memtest-r5-skip" SKILL.md` 命中 5 处：57 行「lumen 用 tsx」、128 行「lumen 的 service 闸阈值是行 49」、164 行「lumen agent-memory」、199 行「memtest-r5-skip」、394 行「lumen：`service/scripts/memory-data-reconcile.ts`」。没有 `examples/lumen.md`。 |
| 28 | 英文版正文 | **仍开放** | `SKILL.md`（580 行）与 `README.md`（222 行）全是中文（`grep -c "[一-鿿]"` ⇒ 395 / 134）。 |
| 29 | 打包元数据（`.skill`） | **仍开放** | 仓库里没有 `package_skill.py` / `*.skill` / 打包 manifest。 |

---

## 5. 复评中新发现的问题

### 5.1 【中】`.omx/ultragoal/` 内部编排状态被 git 跟踪，会随仓库发布，且会绕过安全闸

**现象**：`git ls-files .omx` ⇒ `.omx/ultragoal/brief.md`、`goals.json`、`ledger.jsonl`（共 29KB）。
`.gitignore` 只排除了 `.omx/state/`、`.omx/hud/`、`.omx-state-locks/`、`.omx/tmux-hook.json`，
**没有排除 `.omx/ultragoal/`**。

**精确的事实（不要夸大）**：

- 在最终 HEAD `74e858f` 上，干净导出（`git archive HEAD`）整仓扫描：
  ```
  score 100 / CRITICAL / DO_NOT_INSTALL
  HIGH/CRITICAL 31 条，按规则：E2 24 / PE3 3 / AE1 3 / SC9 1
  按顶层目录：scripts 24 / .github 4 / SKILL.md 3
  ```
  **这 31 条全部落在规则级 allowlist 内，`.omx` 在 HEAD 上贡献 0 条**
  （`git show HEAD:.omx/ultragoal/goals.json | grep -c "npmrc\|dict(os.environ)"` ⇒ 0）。
  所以"clone 下来得到 DO_NOT_INSTALL"这件事是**真的**，但成因是那 31 条已复核项，
  不是 `.omx` —— 这一点与上一轮第六节的分析一致（"SkillSpector 的数字不是判据依据"）。
- **但工作树里的 `.omx` 已经踩到线上了**：
  ```
  $ grep -c "npmrc\|dict(os.environ)" .omx/ultragoal/goals.json .omx/ultragoal/ledger.jsonl
  .omx/ultragoal/goals.json:1
  .omx/ultragoal/ledger.jsonl:1
  ```
  扫描这两个文件会报：
  ```
  PE3 HIGH .omx/ultragoal/goals.json:52  .npmrc
  PE3 HIGH .omx/ultragoal/ledger.jsonl:5  .npmrc
  E2  HIGH .omx/ultragoal/goals.json:52  dict(os.environ)
  E2  HIGH .omx/ultragoal/ledger.jsonl:5  dict(os.environ)
  ```
  这是**证据文本里恰好出现了这两个字样**，不是行为风险。但 `.omx` 的运行态是
  每次 goal checkpoint 都改的（`git diff --stat .omx/` 此刻仍有 21  insertions），
  下一次把这段状态提交进去，这 4 条 HIGH 就进 HEAD 了。

**为什么仍然要处理**：

1. **规则级 allowlist 让这类"误伤"变得不可见**。`.github/check-skillspector.sh` 用
   `--exclude='.omx'` 把它们挡在扫描外，所以本地与 CI 永远绿；
   CI 的 inline 步骤（`skillspector scan . --no-llm --format json --output scan.json`）
   **没有排除 `.omx`**，但因为 E2/PE3 是**规则级**放行，那 4 条即使进 HEAD 也**不会被拦**。
   ⇒ 上轮的"改了那一行就要重新看一眼"这条纪律，在规则级 allowlist 下对 `.omx` 完全失效。
2. **它不是这个 skill 的一部分**。`brief.md` 是实现方自己的任务 brief，
   含作者的 `/Users/zhangpeng/.agents/skills/ai-testing-system/scripts/` 绝对路径。
   对外部用户零价值，对发布者来说是噪声与内部信息外泄。
3. **它会持续漂移**。一个每次提交都在变的内部状态文件进了发布制品，
   仓库的 diff 里会混进与产品无关的噪声。

**建议**：`git rm -r --cached .omx/ultragoal/` + 把 `.omx/` 整目录加进 `.gitignore`
（`.gitignore` 里已经有 `.omx/state/` 等条目，补一行即可）。
一条 `git rm` 的事；不做的话，"发布制品"里会一直带着一份不属于它的内部运行态。

### 5.2 【中】安全闸门从"逐条位置指纹"退化为"规则级放行"

**现象**：复评期间 `.github/skillspector-baseline.yaml`（29 条位置指纹）被删除，
换成 `.github/skillspector-allowlist.yaml`（放行 5 条**规则** id：E2/AE1/PE3/SC9/AST9）。

**代价**（allowlist 文件头自己写了，但值得复述）：**同一个已放行规则再出现不会被拦**。
上一轮的设计是"改了 `scripts/` 里那一行就要重新复核并重跑生成脚本"，这是刻意设计的
"改了就要再看一眼"；现在改成"E2 一律放行"，那么**任何新增的 `os.environ` 枚举**
（包括一个真正会泄漏密钥的新代码）都不会被这个闸门拦住。

**为什么仍然可接受**：规则 E2 本身太粗（匹配所有环境变量枚举），逐条基线对开源贡献者是持续的假红。
但需要有人明确知道这个取舍 —— allowlist 文件头写了，**复评把它记在这里**：
闸门从"位置敏感"变成"规则敏感"，是**降级**而不是升级。

### 5.3 【高】TS 车道把"runner 崩溃"当成"用例失败"或"全部通过"—— 假红与假绿并存

这是本轮最要紧的发现。**Python 车道是对的，TS 车道是错的。**

**机制**：TS 侧 `run_coverage()` 收集失败用例的唯一手段是
`_failed_tests()`（`scripts/readiness.py:432-435`）—— 匹配 TAP 格式的 `^not ok \d+ - (.+)$`。
TS 侧**没有 `setup_errors` 概念**（Python 侧有，见 `scripts/readiness.py:1691-1733` 与 `2587`）。

**假红（把 setup 崩溃当成断言失败）** —— 样本 A：
```
  ✗ 测试全绿 — 1 个用例失败
      · calc.test.ts: test
真实原因（node --test test/ 的真实输出）：
  Error: Cannot find module '/private/tmp/g5-std-testdir/test'
  not ok 1 - test   failureType: 'testCodeFailure'
```
⇒ 阻塞项。而 SKILL.md:42 自己承诺「**只认断言失败**；setup 报错=环境没就绪 ⇒ 未实测」。

**假绿（把"根本没跑起来"当成"全绿"）** —— 自造 `/tmp/g5-missing-runner`
（`package.json` 的 `test` 是 `vitest run`，环境里没装 `@vitest/coverage-v8`）：
```
  ✓ 测试全绿 — 没有失败用例
  — 核心逻辑测试到位 — 测试跑了，但没解析出覆盖率表（runner 可能不支持覆盖率参数）
结论：0 项阻塞 · 1 项待人工确认 · 5 项无信号 · 1 项实测通过
EXIT=0
```
而 vitest 实际输出是：
```
 MISSING DEPENDENCY  Cannot find dependency '@vitest/coverage-v8'
```
**一个用例都没跑，工具报「测试全绿 ✓」。**

**假绿的通用形态**：任何**非 TAP 输出格式**的 runner（jest 的 `● a › a` + `Tests: 1 failed`、
vitest 默认 reporter、mocha spec reporter）都会落到这条路上。自造 `/tmp/g5-jestlike2`
模拟 jest 失败输出（`Tests: 1 failed, 1 passed`）：
```
  ✓ 测试全绿 — 没有失败用例
```
**退出码实测：`EXIT=0`。** 一个真实失败的 jest 用例被判为绿，且整个工具返回成功。

**对照：Python 车道做对了**。`/tmp/g5-py-nopytest`（pytest 装不上）：
```
  — 测试全绿 — 1 个用例 setup 报错（环境/依赖没就绪） —— 不是断言失败，判不了
      · ERROR tests/test_core.py
```
Python 侧有 `setup_errors` 通道（`scripts/readiness.py:1718-1724`、`1836`、`2578`），
TS 侧没有。

**为什么这条比上一轮的 B1 还严重**：B1 是"没找到测试文件 ⇒ 判绿"；
这一条是"**找到了测试文件、但 runner 崩了 ⇒ 判绿**"，
而且在**任何非 node --test 的仓库**上都会命中 —— jest / vitest / mocha 是 JS 生态的主流。

**建议**：TS 侧补 `setup_errors` 通道（至少：runner 非零退出且**没有解析出任何用例结果**
⇒ 判"未实测/环境没就绪"而不是"全绿"）；并把失败判定从"只认 TAP"扩到
vitest/jest 的失败摘要，或在拿不到可解析结果时**拒绝给"全绿"这个结论**。
现成的内部证据：itest 车道已经有这个判断（`scripts/readiness.py` 的 `run_itest_lane`：
`if summ["tests"] == 0: env_errors.append(...)`），TS 单元车道照抄即可。

### 5.4 【中】README 的安全承诺与代码相反：`DATABASE_URL` 会透传给目标仓库的脚本

**现象**：`README.md`「安全须知」第 2 条：
> **其余环境变量一律不透传** —— 你的 `DATABASE_URL`、各种 API key 不会进目标仓库的脚本。

**代码**：`scripts/readiness.py:41` 的 `ENV_ALLOW_EXACT` 里**有 `DATABASE_URL`**。

**实测**：
```
$ DATABASE_URL='postgres://user:SECRET@db:5432/prod' python3 -c "
  import sys; sys.path.insert(0,'scripts'); import readiness as R
  import subprocess
  print(subprocess.run(['node','-e','console.log(process.env.DATABASE_URL||\"(unset)\")'],
        env=R.child_env(), capture_output=True, text=True).stdout)"
CHILD_SEES_DATABASE_URL=postgres://user:SECRET@db:5432/prod
```
（同一次实测里 `AWS_SECRET_ACCESS_KEY` 是 `(unset)` —— 白名单本身有效，只是清单里多了这一项。）

**为什么要紧**：`DATABASE_URL` 在绝大多数项目里就是**带口令的连接串**。
上一轮把"凭据暴露"这一项判为已修（`psql` 不走 argv），但连接串换了个通道继续外流。
对外的 README 承诺与代码相反 ⇒ 用户读文档会做出错误的安全判断。

**建议**：二选一 —— 要么把 `DATABASE_URL` 从白名单里去掉（工具自己用 `POSTGRES_URL` 就够），
要么把 README 这句话改准确。**当前的写法是文档说谎，必须改。**

### 5.5 【中】`--self-check` 只证明了两条静态判据能翻红；实测类判据未被证明

**现象**：`--self-check` 注入两种故障（`drop-test` / `inject-sleep`），
期望翻红的判据是 `uncovered-exports` 与 `hygiene`（`scripts/selfcheck.py:40-43`）——
**两条都是静态信号**。实测输出的 `evidence_kind` 也证实：`uncovered-exports` = static，
`hygiene` = static。

**含义**：哲学层标尺 3 要求的是"**证明这套判据能失败**"，
尤其"实测类判据（测试是否绿）能不能被证伪"。
当前 `--self-check` 证明的是"静态判据抓得住东西"，**没有证明"我能让『测试全绿』翻红"**。
`CHANGELOG.md` 自己也承认了：「自检副本不带 `node_modules` ⇒ 多数仓库在副本里跑不动测试，
`--self-check` 证明的是『静态判据抓得住东西』，不是『整套判据（含实测项）抓得住东西』」。

**另一处小问题**：`--self-check --json` 的输出里**没有 self-check 段**
（`--json` 实测 top keys 只有 `module/tables/checks/conclusion`），
反向验证的结果拿不到机器可读的形式。

### 5.6 【低】其他小项

| 项 | 说明 |
| --- | --- |
| `--verbose` 对 TS 车道无效 | `run_coverage()` 的第三个返回值是**说明文字**不是原始输出（`scripts/readiness.py:2566`：`cov, failed, raw = run_coverage(...)`），`raw` 里没有子进程 stdout ⇒ `--verbose` 永远看不到测试输出。我复现"目标仓库脚本能不能看到 DATABASE_URL"时就是因此没抓到输出，改用 `child_env()` 直测才拿到。 |
| README「已知缺口」表不完整 | README:171-181 的「仍开放」表列了 5 条，但 `CHANGELOG.md` 还承认了另外 3 条（Python 无 itest 车道、打包元数据、`new` 的数据一致性节——后者已被工作树修掉）。三份文档的口径不同步。 |
| README:175 过时 | 「`SKILL.md` 的范围声明 ……（`SKILL.md` 不在本轮改动范围内）」——SKILL.md 实际已经改了（SKILL.md:32-36），且范围校准这一轴是本轮提升最大的。 |
| oracle 检查的单行写法漏报 | `_collect_assignments` 的第一个正则（`scripts/oracle_check.py` 的 `_ASSIGN_RES[0]`）把赋值右侧一路吃到行尾 ⇒ 写成单行（`const x = add(1,2); assert.equal(x, add(1,2));`）时整行都被算进 RHS，命中两次 SUT 调用 ⇒ `len(calls) == 1` 不成立 ⇒ 赋值被判为"不来自被测对象"，**形态 2 漏报**。正常换行的写法能报（实测 `calc3.test.ts` 报出 2 处）。属低频边界，不阻塞。 |
| `.github` 两个脚本的排除口径不一致 | `check-skillspector.sh` 排除 `.omx`，CI 的 inline 步骤不排除（§5.1 第 2 点）。 |

---

## 6. 总判定

**NEEDS_WORK_BEFORE_PUBLISH**

### 发布者会承担什么风险

一个外部用户装上它、跑在**他自己的仓库**上，最可能踩到的坑，按可能性排序：

| # | 坑 | 这个 skill 能挡吗 | 后果 |
| --- | --- | --- | --- |
| 1 | 仓库用 jest / vitest / mocha，某个用例真的失败了，工具报「✓ 测试全绿」 | **挡不住**（§5.3 假绿） | 最严重的一种：CI 绿了，报告绿了，缺陷进了主干。这正是这个 skill 存在的理由被推翻。 |
| 2 | 仓库的 `test` 脚本在用户机器上跑不起来（没装依赖 / 参数不兼容），工具把崩溃报成「1 个用例失败」 | **挡不住**（§5.3 假红） | 用户去改**产品代码**去修一个环境问题，越修越乱。 |
| 3 | 仓库的测试脚本里有 `DATABASE_URL` 的值被打印进 CI 日志 | **部分能挡**（白名单挡掉大部分密钥，但 `DATABASE_URL` 仍透传，§5.4） | 连接串泄漏到目标仓库的日志/第三方服务。 |
| 4 | 工具默认执行目标仓库的 `test`/`coverage`/`reconcile`/`baseline:check` 脚本 | **部分能挡**（白名单 + `--dry-run` + 审计行；但没有"必须显式敲才跑"的开关） | 用户第一次跑就被执行了未知脚本。 |
| 5 | 仓库的 `reconcile` 脚本会去改生产数据 | **能挡一部分**（脚本名白名单不含 `reconcile:reset`；输出目录重定向到临时目录） | 但 `reconcile` 本体就是要写库的脚本，工具会实跑它。 |
| 6 | 判据里的「跨存储对账」「表结构」判据在用户仓库上永远判不了（没连库） | **能挡**（判「未实测」，不是 fail） | 无风险。 |
| 7 | 用户把工具的结论当"可以交付"的证明 | **基本能挡**（三档结论 + 固定的「本工具不判定」六项清单 + 静态信号永不单独支撑通过） | 这是上一轮最大的问题，已基本解决。 |

### 结论

- **上一轮的四条一票否决级（B1–B4）全部关闭**，这是实打实的进步：
  零测试不再假绿、标准 `test/` 布局不再误判、路径穿越与代码注入都堵住了，
  且每一条都有可复现证据 + 回归断言在守。
- **哲学层 R1/R4 与输出契约的改造是真的**：范围校准从 0.42 拉到 0.85，
  「能不能交付客户」被降级为「哪些前提已被实测证明」，这是本工具从"判定剧场"往
  "证据工具"走的最实质一步。
- **但发布前还差一件硬事，和三件顺手该做的**：
  1. **【硬】§5.3 的 TS 车道假绿/假红** —— 这是一条能直接推翻工具存在理由的缺陷
     （jest / vitest / mocha 仓库上，一个真失败的用例被判「✓ 测试全绿」，退出码 0），
     且在主流 JS 生态上高概率命中。
  2. **【顺手】§5.4** —— README 说「`DATABASE_URL` 不会进目标仓库的脚本」，代码说会。
     文档与代码必须有一个改。
  3. **【顺手】§5.1** —— `git rm -r --cached .omx/ultragoal/` + `.gitignore` 补一行。
     一条命令的事；不做的话发布制品里会一直带着一份不属于它的内部运行态，
     而且规则级 allowlist 让它日后悄悄多出 HIGH 也不会被拦。
  4. **【顺手】§4.5 末条 + §4.7** —— `_symbol_exists` 补上 `.py/.rs/.go`；
     `$SKILL_DIR` 给出可用的定义（或 README 统一走 `~/.agents/skills/...`）。

其余仍开放的项（英文正文、打包元数据、lumen 私货）**都不构成"不能发"** ——
它们影响的是受众规模与观感，不是正确性。

**推荐发布姿势**：先修 §5.3 + §5.4 + §5.1 三条，再发；发的时候用
「一个把交付前提变成可机读、可核对、可审计的证据清单的需求级测试工具」
这个定位（而不是"判断模块能不能交付"的裁判），并在 README 首屏明说
「只支持 TS/JS/Python；runner 输出不是 TAP 格式时『测试全绿』这条会失真」。
