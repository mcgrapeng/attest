# req-test-plan 开源发布条件评测

评测日期：2026-11-14
评测对象：`~/.agents/skills/req-test-plan/`（SKILL.md 464 行 / readiness.py 1816 行 / req_plan.py 1913 行 / 14 个回归脚本）
评测手段：SkillSpector 静态扫描 + 语义审查 + 合成第三方仓库端到端实测 + plugin-eval 锚定 rubric + 独立评审子代理
所有"实跑确认"的论断都附了可复现的命令；纯静态推导的单独标注。

## 结论

**NEEDS_WORK_BEFORE_PUBLISH**。可以发，但不能按现状发。

技术内核是真的：判据驱动实测、门槛归仓库所有、跑不起来与不达标分离、租户口径、跨存储字段级比对。这些别人抄不走，值得共享。问题在两处：

1. **交付结论会在什么都没测的时候给出肯定答案**（实跑复现，退出码 0）。
2. **`scaffold` 有两条可直接利用的写入漏洞**（实跑复现，路径穿越 + 代码注入）。

这两类都不是"再打磨一下"能解决的，是发布前必须改掉的。其余是打磨项。

---

## 一、一票否决级（4 条，全部实跑复现）

### B1 零测试的模块被判「达到交付条件」，退出码 0

`readiness.py:1170-1179` 的 `if not failed:` 直接返回 `pass`；`run_coverage` 找不到测试文件时
返回的 `failed` 也是空（`readiness.py:430-431`）。

复现（模块 1 个 .ts 文件、无任何测试、无任何导出）：

```
源码 1 文件 / 单测 0 个 / itest 0 个
  ✓ 测试全绿 — 没有失败用例
  ✓ 具备扩展性（不过度抽象） — 0 个导出符号都有多个调用点
  ✓ 没有重复造轮子 — 模块的导出符号在全仓都是独一份
  — 核心逻辑测试到位 — 没有找到该模块的测试文件
结论：达到交付条件（1 项待人工确认）
EXIT=0
```

这个工具的全部价值就是那句交付结论，而它在零测试时给的是肯定答案。另外两行 ✓ 也是
空集假绿：「0 个导出符号都有多个调用点」读起来像肯定结论，实际什么都没分析。

`test_readiness.py` 每个场景都先建了 `*.test.ts`（该文件 53-64、140-143、206-209 行），
没有任何一条用例覆盖「模块根本没有测试」。

### B2 测试发现是模块目录内作用域，健康仓库被判不可交付

`discover_module` 只 walk 模块目录（`readiness.py:104`）。测试放在 `test/` 时：

```
源码 1 文件 / 单测 0 个
✗ 导出的逻辑都有测试 — 3/3 个运行时导出在测试里零引用
      · add（src/calc.js）        ← 实际被 test/calc.test.js 引用了
```

把同一个测试文件挪到 `src/` 下，立刻变成「单测 1 个」「2/3 个导出零引用」，`add` 不再被点名。
独立 `test/` `tests/` `spec/` 是大量项目的常规布局。

### B3 `scaffold` 路径穿越：写到仓库外

`req_plan.py:1715` 是 `target = out_dir / fpath`，钉的字符类 `_PIN_RE`
（`req_plan.py:45-48`）允许 `..` 和 `/`，没有任何规范化校验。

复现：计划里写 `../../../../tmp/vz-pwned/x.test.ts::用例甲`，跑

```
python3 req_plan.py scaffold docs/plan.md --repo $R --out-dir /tmp/vz-scaffold-out
已生成 1 个新测试文件：
  + ../../../../tmp/vz-pwned/x.test.ts
```

文件确实落在 `/tmp/vz-pwned/x.test.ts`，在仓库和 `--out-dir` 之外。工具还把它当成功
打印出来。

### B4 `scaffold` 代码注入：计划 markdown 变成可执行代码

用例名原样插进 `it('...')`（`req_plan.py:1556`），单引号不转义。

复现：用例名取

```
x', () => {}); process.stdout.write('PWNED_MARKER\n'); it('y
```

生成物那一行：

```js
it('x', () => {}); process.stdout.write('PWNED_MARKER\n'); it('y', () => {
```

`node --test` 执行生成物，输出里出现 `PWNED_MARKER`，2 个用例 pass。

含义：一份计划 markdown（可能是别人提交、评审后合并的）经 scaffold 就成了待执行代码。

## 二、文档与实现的矛盾（5 条）

| # | 文档说 | 代码做 | 位置 |
|---|---|---|---|
| C1 | 「通用阈值（行 80）在这里是错的…别拿通用阈值去覆盖仓库的口径」 | 无仓库闸门时（即多数仓库）用 `line < 80 or branch < 70` 直接 `fail` ⇒ 退出码 1。文档称其错误的判定，代码当硬门槛用 | `readiness.py:1421-1422`（TS）、`1359-1360`（Python） |
| C2 | 「`--include-itest` 才合并算」 | `if itest_scripts and (args.include_itest or m.itests)` ⇒ 模块里存在 `.itest.` 文件就跑 itest 车道（每文件超时 900s），不传 `--include-itest` 也跑 | `readiness.py:1733` vs `SKILL.md:87,145-147` |
| C3 | 「只读代码，不改任何东西」 | `scaffold` 写文件 | `req_plan.py:9` vs `1562-1563` |
| C4 | 「模块真实依赖清单（排除路径别名）」 | 抽取只认带引号的 `from '…'`（正则 `from\s+['"]([^'"]+)['"]`）。`require('x')`、`import 'x'`、Python 的 `import x` / `from x import y` 全部漏掉 ⇒ Python 模块的依赖恒为空 | `readiness.py:140-152` |
| C5 | 判据表 12 条 | 代码产出 11 个 Check。「字段级跨存储比对」被并进「数据流一致」 | `SKILL.md:27-38` vs `readiness.py:1700-1760` |

C4 的连带后果：Python 仓库上「使用主流开源库」恒返回 `warn: 模块没有第三方依赖`，
是事实错误的结论。

## 三、名不副实或空转的判据（4 条）

| 判据 | 实现 | 问题 |
|---|---|---|
| 使用主流开源库 | `readiness.py:1686-1694`：有依赖就 `pass` | 从不判断"主流"或"开源"，永远打勾 |
| 具备扩展性 | `readiness.py:1647-1659`，用 TS-only 的 `module_exports()` | ①名字与实际测的"规则之三/不过度抽象"相反；②Python 下导出集为空 ⇒ `pass` 并打印「0 个导出符号都有多个调用点」 |
| 没有重复造轮子 | `readiness.py:1662-1683`，同一个 `module_exports()` | Python 下同样空集 `pass`，并打印「模块的导出符号在全仓都是独一份」 |
| 表设计符合主流 | `readiness.py:1527-1587` | ①实为自家五条规则，名称说"主流"；②租户列与唯一约束无条件要求（`1551-1552`），单租户项目、全局配置表、日志表必假红；③向量集合的领域惯例完全没查 |

更根本的一点：`Check.status` 只有 `pass/fail/warn/skipped`（`readiness.py:59`），
**没有"未实测"这个态**。而 11 条判据里有 5 条是纯静态正则/词频分析，和实测判据用同一
套 ✓/✗/·/— 记号排在同一张表里。SKILL.md:22-23 承诺「每条判据要么有实测证据，要么明说
『未实测』」，这个态在实现里不存在。

## 四、对账层：只懂一种中文方言

`readiness.py:806-807` 判漂移靠字面量：

```python
drift = [ln.strip() for ln in text.splitlines()
         if "漂移" in ln and "❌" in ln]
```

配套还有：租户取 `|` 表格第一列（`934-939`）、表头排除中文"一致性"（`810`）、输出目录靠
lumen 专属的 `MEMORY_RECONCILE_OUT_DIR`（`783-784`）、测试租户前缀表写死 lumen 的
`itest-/memtest-/mock-/eval-`（`911-914`）。

两个方向的不可靠：

- **假绿**：对账脚本输出英文 ⇒ 漂移数 0 ⇒ 「生产租户无漂移」。
- **假红**：`compare_csv_fields` 对输出目录里的 CSV **两两配对**（`readiness.py:847-848`），
  3 个 CSV 会产生 3 组比较（含无关文件对），且比对**所有同名列**——包括天然不同的
  `created_at`，以及 SKILL.md:58 自己说"正文不一致要人判断"的正文字段。生产租户上直接判 fail。

顺带：`run_reconciler` 会往被调用的对账命令里塞 `--experimental-test-coverage`（`794-795`）
或 `--run`（`796-797`），改写被调脚本的语义。

## 五、scaffold 的语言感知

`detect_conventions` 只遍历 `{.ts,.tsx,.js,.mjs}`（`req_plan.py:838`），其余语言落回默认
`framework="node:test"` / `assertion="node:assert"`（`815-816`）。

Python 仓库实测：计划里钉 `src/pkg/test_mod.py::test_add`，生成物是

```js
import assert from 'node:assert';
import { describe, it } from 'node:test';
describe('测试计划：py', () => { it('test_add', () => { ... }); });
```

写进了 `.py` 路径，**不报错**，静默产出坏文件。SKILL.md:378-380 承诺「先认出这个仓库的
测试约定…再按约定生成」，对 Python/Rust/Go 不成立。

另有一处范围不一致：`_symbol_exists` 只扫 TS 扩展名（`req_plan.py:789`），
`_symbol_uses` 却含 `.py/.rs/.go`（`770`）。

## 六、安全线

### SkillSpector

| 状态 | 评分 | 建议 |
|---|---|---|
| 含 `scripts/__pycache__/` | 100 / CRITICAL | DO_NOT_INSTALL |
| 删掉 `__pycache__` 后 | 65 / HIGH | DO_NOT_INSTALL |

清理前的 5 个 HIGH 是 `readiness.cpython-315.pyc`（121 KB）和 `req_plan.cpython-315.pyc`
（105 KB）（规则 SC8/SC9）。这不是误报：打包进去就是给用户塞字节码，还泄漏作者环境
（macOS + Python 3.15）。删掉即可。

清理后残留的 22 个 HIGH 全是同一个 pattern：`subprocess.run(..., env={**os.environ,
"PYTHONUTF8": "1"})`（规则 E2）。逐处核对确认是必要行为：子进程要 PATH、DATABASE_URL、
POSTGRES_URL 才能跑目标仓库自己的测试。代码里不存在把这些环境变量发往网络位置的路径。

### 语义审查

| 维度 | 评估 |
|---|---|
| 目的契合 | 合规。本地自检工具，读代码 + 跑仓库自己的测试。无网络出口、无凭据文件读取。 |
| 外部传输 | 工具自身零外发，但把完整环境 + 连接串交给仓库定义的脚本。工具的安全水位 = 它所运行的每一个脚本的水位。 |
| 执行风险 | 最高的一项。脚本选取是**子串匹配** package.json 脚本名（`readiness.py:635` 认 `coverage`、`753` 认 `reconcile`、`689` 认 `integration`/`itest`、`1044-1046` 认 `baseline`+`check`），无白名单、无 `--dry-run`、无 `--no-exec`；子进程继承完整父环境（`447/515/540/714/783/1061`）。名为 `reconcile:reset` 的脚本会被执行。 |
| 凭据暴露 | `psql` 的连接串以 argv 传入（`readiness.py:552-556`）⇒ 含口令的连接串出现在进程表，本机其他用户可见。 |
| 写入面 | B3 路径穿越 + B4 代码注入。 |
| 持久化 | 基本干净。无 cron / daemon / shell rc 改写 / 全局安装。`cmd_new` 默认不覆盖（`req_plan.py:1834-1837`）。 |
| Prompt 风险 | 计划 markdown 是半可信输入，被直接转成可执行代码与文件路径（见 B3、B4）。 |
| 触发风险 | description 以 "when starting a new feature" 触发（`SKILL.md:3`）⇒ 多数开发任务会命中，而命中后可能写文件进仓库并跑仓库脚本，description 对此零提示，也没写交付判定命令与语言覆盖范围。 |
| 用户控制 | 有 `--json/--plan/--test-tenant/--include-itest/--verbose/--force`；没有 `--dry-run`、没有 `--no-exec`，用户在跑之前无法知道它会执行哪些仓库脚本。 |

### 判定：**CAUTION**

不是 APPROVE：任意脚本执行 + 全环境继承、`scaffold` 的两条写入漏洞、零测试假绿。
不是 REJECT：无网络出口、只依赖标准库、不读凭据文件、除仓库内文件外无持久化；核心逻辑
（计划解析、钉核对、矩阵校验）在自己的域内设计克制且有测试覆盖。

SkillSpector 的数字不是判据依据：它 100/CRITICAL 完全由字节码造成（分发卫生问题，不是
行为风险）；去掉字节码后唯一剩余的 HIGH 模式是传递父环境给子进程，这是 Python 里最标准的
写法，本身不泄露父进程没有的东西。也就是说，这个数值**高估**了真实风险模式，又**完全没有
覆盖**真实风险（任意命令执行、路径穿越、代码注入、假绿）。两个方向都不准。

## 七、可移植性

**回归套件不独立。** `test_req_plan_r3.py`（122、142、173 行）与 `test_req_plan_r4.py`
（14、16 行）硬编码 `/Users/zhangpeng/.agents/skills/ai-testing-system/scripts/`。

干净临时目录复现：

- r3：`ModuleNotFoundError: No module named 'testkit_audit'` ⇒ 整个文件崩溃
- r4：5 项断言失败（a11y / eval-gate scaffold / TS 侧补齐命令 / cargo mutants / cargo deny）

r4 更难发现：那 5 条断言测的是 `ai-testing-system` 的脚本，与本 skill 的行为无关。

**调用路径写死。** SKILL.md 有 9 处 `python3 ~/.agents/skills/req-test-plan/scripts/...`
（18、194、198、374、390、444、448、452、455 行）。装到插件目录或市场目录就全失效。

**缺发布骨架。** 无 LICENSE 文件（frontmatter 写了 `license: MIT`，`SKILL.md:4`，却没有正文）、
无 README、无 CHANGELOG、无 `evals/`、无 CI 配置、无打包元数据。

## 八、plugin-eval 四轴评分（锚定 rubric，0-1）

| 轴 | 分 | 依据 |
|---|---|---|
| 触发准确度 | 0.65 | description 完全没提 readiness 交付判定这个头号能力；description 英文、body 全中文，中文提问关键词零重叠；"when starting a new feature" 触发面过宽；与 `ai-testing-system`（项目级）、`testkit-audit` 的边界没在 description 里说清 |
| 编排适配度 | 0.55 | 契约清楚（退出码、判据表、计划格式），但本质是编排型 skill（9 步工作流 + 驱动子进程），不是纯 worker；价值主体在约 3700 行 Python，prose 主要是解释与计划撰写指导 |
| 输出质量 | 0.72 | 计划生成与 check 质量高（全新仓库实测 `new` 骨架可用、`check` 正确报红并提示缺失维度）；readiness 报告有 B1、B2 与三条空判据共五处会误导读者；"别测实现细节 / 断言要具体 / 失败要能定位"三条最值钱的原则只有散文、没有机器检查 |
| 范围校准 | 0.42 | 最低分项，见下 |

范围校准问题集中在两句话与实现之间的落差：

- `SKILL.md:22-23`：「回答『这个模块能不能直接交付给客户』」
- `SKILL.md:40`：「任何一项『不通过』⇒ 结论是**不可交付**，退出码 1」

实际判的是 11 条结构判据（测试绿、覆盖率、导出引用、表结构、对账、计划覆盖、测试卫生等），
完全没有覆盖：需求本身是否被正确理解、产品正确性、性能、安全、UX、运维就绪。
二值结论建立在部分证据上。

建议改写：结论改成三档（阻塞项 / 待人工确认 / 无信号），把"能不能交付客户"降级为
「这 11 项机械判据的实测结论，交付决策仍需人承担」。

## 九、敌意 OSS 评审会提的六个问题

按严重度。

1. **「你的『测试全绿』在一个测试都没跑的时候是绿的。」**
   `readiness.py:1170-1179` + `430-431`。实测退出码 0，结论"达到交付条件"。
2. **「在标准 `test/` 目录布局的仓库上，你的工具把健康代码判成不可交付。」**
   `readiness.py:104` 的模块内作用域。demo 仓库实测复现。
3. **「你的 `scaffold` 能往仓库外写文件，还能把计划 markdown 变成可执行代码。」**
   实测：写到 `/tmp/vz-pwned/x.test.ts`；生成的测试文件执行后打出 `PWNED_MARKER`。
4. **「你有两条永远打勾的判据，两条对 Python 空转。」**
   `check_open_source`（`1686-1694`）从不判断主流与否；`check_extensibility`（`1647-1659`）
   与 `check_no_wheels`（`1662-1683`）用 TS-only 的 `module_exports()`，Python 下空集 pass
   并打印「模块的导出符号在全仓都是独一份」。
5. **「文档说通用阈值是错的，代码把它当门槛用；文档说 itest 要 `--include-itest`，代码默认就跑。」**
   `1421-1422` / `1359-1360` / `1733`。
6. **「这不是一个可发布的制品。」**
   无 LICENSE / README / CHANGELOG / evals / CI；`__pycache__` 里躺着两个编译字节码；
   回归套件硬编码 `/Users/zhangpeng/`；SKILL.md 正文全中文而 description 是英文，
   英文用户拿不到任何用法说明。

## 十、发布前改动清单

### P0（不做就不能发）

| # | 改动 | 验证方式 |
|---|---|---|
| 1 | 删 `scripts/__pycache__/` 与 `.deepeval/`，加 `.gitignore`，CI 加一条"存在 `.pyc` 即失败" | `skillspector scan --no-llm` 分数应从 100 降到 65 附近 |
| 2 | B1：`check_tests_green` 在"没找到任何测试文件"时判 `skipped` 或 `fail`；`test_readiness.py` 增加"零测试模块不得判达到交付条件"的回归用例 | 复现脚本 A 期望 EXIT≠0 且结论不是"达到交付条件" |
| 3 | B2：测试发现扩到仓库级（`test/` `tests/` `spec/`，或 `--test-dir`） | demo 仓库期望「单测 1 个」，`add` 不再被点名为零引用 |
| 4 | B3：`scaffold` 校验 `fpath` 规范化后仍在 `out_dir` 之下，拒绝含 `..` 与绝对路径的钉 | 用 `../../../../tmp/x.test.ts::用例甲` 复现，期望拒绝并报红 |
| 5 | B4：插值进 `it('...')` 的用例名做单引号转义（或改用双引号包裹并转义） | 用注入载荷复现，期望生成的代码不含可执行副作用 |
| 6 | `scaffold` 语言感知：`detect_conventions` 增加 Python/Rust/Go 分支；在实现之前，遇到无 JS/TS 测试文件的仓库**拒绝生成并报错** | Python 仓库复现，期望不生成 `.py` 里的 TS |
| 7 | 补 LICENSE（MIT 正文，与 frontmatter 一致）、README（安装、4 条命令、语言支持矩阵、Security 一节：目标仓库的 test 脚本会拿到你的环境变量）、CHANGELOG | 能从 README 独立跑通 demo 仓库 |
| 8 | r3/r4 的绝对路径改为从 `__file__` 推导；`ai-testing-system` 缺失时 skip 并打印清晰提示；随后加 CI 跑全部 14 个脚本 | 在删掉 `ai-testing-system` 的机器上跑全套，全绿或明确 skip |
| 9 | 改写 `SKILL.md:22-23` 与 `40` 的范围声明 | 声明与实现一致 |

### P1

| # | 改动 | 验证方式 |
|---|---|---|
| 10 | 消掉 80/70 阈值的文档—代码矛盾：改成"无仓库闸门 ⇒ `skipped` 并说明缺什么"，或改成显式 opt-in（`--min-line-coverage`）并把数字写进 SKILL.md | 无闸门仓库不再因通用阈值被判不可交付 |
| 11 | 统一 itest 默认行为：要么默认不跑，要么文档改口 | `readiness.py:1733` 与 `SKILL.md:87,145-147` 一致 |
| 12 | `check_extensibility` / `check_no_wheels` 加 Python 分支（复用 `python_exports`）；在此之前 Python 必须 `skipped`，不得输出零分析的正面结论 | Python 仓库上这两行不再是 ✓ |
| 13 | `check_open_source` 要么真的评估，要么改名（建议"依赖清单可读"）；修依赖抽取覆盖 `require(...)`、`import 'x'`、Python `import x` / `from x import y` | Python 仓库不再报"模块没有第三方依赖" |
| 14 | 对账层参数化：漂移/报告解析支持 JSON 输出或用户提供的正则；用工具自设自读的通用变量替换 `MEMORY_RECONCILE_OUT_DIR`；找不到可比对的 CSV 对时报"无法比对"而不是"0 对无差异"；CSV 配对从两两配对改成按声明的对照关系配对，并排除时间戳与正文类字段 | 输出英文的对账脚本 fixture 不再假绿；生产租户不再因 `created_at` 假红 |
| 15 | 脚本选取从子串匹配改成显式白名单或用户确认；加 `--dry-run`（只列候选脚本与将执行的命令）与 `--exec` 开关；子进程环境收敛为白名单 + 显式透传 | 名为 `reconcile:reset` 的脚本不再被自动执行 |
| 16 | `psql` 不用 argv 传连接串（改 `PGPASSWORD` / `.pgpass` / `PG*` 环境变量） | `ps` 看不到连接串 |
| 17 | 表设计判据改名并给租户列/唯一约束加豁免条件（单租户仓库不要求） | 单租户仓库不再假红 |
| 18 | "具备扩展性"改名"不过度抽象" | 判据名与实际测的一致 |
| 19 | `req_plan.py new` 在无存储时不生成"数据一致性"节 | `new` 后直接 `check` 不报该项红 |
| 20 | 判据数对齐：文档 12 条 vs 代码 11 个 Check，二选一 | 文档条数 == 报告行数 |
| 21 | `req_plan.py:9` 的"只读代码，不改任何东西"改准确（`scaffold` 会写文件） | 文档与实现一致 |
| 22 | SKILL.md 第 85-87 行与 145-147 行是近乎逐字重复的覆盖率说明，删掉一处 | 文本去重 |

### P2

| # | 改动 | 验证方式 |
|---|---|---|
| 23 | description 扩写：点名交付判定命令、声明语言覆盖、明确"会执行仓库自己的脚本、会向仓库写文件"；补中文表述 | skill-creator 的 trigger eval 集跑分 |
| 24 | 9 处 `~/.agents/skills/req-test-plan/scripts/...` 改成"解析本 skill 所在目录" | 装到任意目录都能跑 |
| 25 | 建 `evals/`：至少 5 个黄金 fixture —— TS 仓、Python 仓、Rust 仓（应得退出码 2）、**输出英文的对账脚本**（应暴露漂移而非假绿）、**无测试模块**（应不得判"达到交付条件"） | 每条声明都可证伪 |
| 26 | CI：Node 22 + Python 3.11/3.13 跑全部 14 个脚本，再跑打包冒烟（装进临时目录跑 `new|check` 与 `readiness.py --json`） | 绿标 |
| 27 | 把 lumen 私货从正文移出（`SKILL.md:42、100-101、131、133-134、152、344`）到 `examples/lumen.md`；脱敏 `memtest-r5-skip` / `~/workspace/zhiman/innies-memory` 等内部标识 | 全文搜索无内部项目名 |
| 28 | 出英文版正文（或 EN + ZH 双份）+ 术语表 | 英文社区可读 |
| 29 | 打包元数据（skill-creator 的 `package_skill.py`） | 产出可安装的 `.skill` |

## 十一、评测局限与一处分歧

- 第三方仓库用合成仓库代替，没跑真实公开项目。lumen 与 innies-memory 上的行为由交接文档
  背书，本轮未复验。
- 触发准确度没实测（skill-creator 的 `run_loop` 需要 `claude` CLI），四轴分是按
  plugin-eval rubric 的人工判断。
- plugin-eval 自带的 `eval-judge` 子代理因模型不可用（`anthropic/claude-sonnet-5`）未能完成，
  另起的一路独立评审返回了详细发现，本报告已逐条实跑验证后并入。
- **与独立评审的一处分歧**：评审认为「Rust/Go 仓库上退出码 2 违反 SKILL.md:75-76 承诺的
  skipped 降级」。读了原文，`SKILL.md:74-76` 是两个并列子句：「识别不出源码文件 ⇒ 退出码 2
  + 说清原因」与「语言不是 JS/TS/Python ⇒ 覆盖率与导出引用判据判 skipped」。Rust/Go 命中
  前者，退出码 2 是文档写明的行为。这条不成立，但文档确实容易读成一条，值得重写。
- 未验证 Windows / 非 bash 环境的兼容性。
- 所有"假绿/假红"结论都基于代码路径推导 + 合成仓库复现。若某个仓库恰好命中了那些方言
  （对账输出就是中文 `漂移` + `❌`、脚本恰好读 `MEMORY_RECONCILE_OUT_DIR`），假绿假红会消失，
  但那只说明该仓碰巧能用，不构成通用性。
