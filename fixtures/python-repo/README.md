# fixture：python-repo

**期望判定**

| 判据 | 期望 |
| --- | --- |
| 退出码 | `0` |
| 覆盖率 | 出现「核心逻辑测试到位」，**不**出现「缺少判据」、**不**出现「没拿到 Python 覆盖率」⇒ 覆盖率是**实测**出来的，不是 skipped |
| 依赖清单 | 出现「个第三方依赖」，**不**出现「模块没有第三方依赖」⇒ Python 的 `import x` / `from x import y` 被抽出来了 |
| 不过度抽象 | 出现「都有多个调用点」，**不**出现「0 个导出符号都有多个调用点」⇒ 导出集非空，是真结论不是空集假绿 |
| 没有重复造轮子 | **不**出现「没有可分析的导出符号」⇒ 要么给了真结论，要么明确说零分析不给结论 |
| 导出引用判定 | 出现「导出的逻辑都有测试」，**不**出现「零引用」 |

**为什么有它**

Python 仓是工具历史上翻车最多的地方，评测里记了四条：

1. 依赖抽取只认 `from '…'` ⇒ Python 模块的依赖恒为空 ⇒ 「使用主流开源库」恒报
   "模块没有第三方依赖"（事实错误的结论）。
2. 覆盖率 include glob 硬编码 `**/*.ts` ⇒ JavaScript 项目拿不到覆盖率。
3. 「具备扩展性」/「没有重复造轮子」用 TS-only 的导出抽取 ⇒ Python 下导出集为空
   ⇒ 输出「0 个导出符号都有多个调用点」「模块的导出符号在全仓都是独一份」
   —— 零分析的正面结论。
4. Python 仓读不到仓库自己的闸门（只读 `package.json`）⇒ 只能用通用阈值 80/70
   把仓库明确允许的文件判红。

这个 fixture 同时压住这四条：它有第三方依赖（`requests`，只在 `TYPE_CHECKING` 下出现，
运行期不需要装）、有仓库自己的覆盖率口径（`pyproject.toml` 的 `fail_under`）、
有非空的导出集。

**怎么跑**

```bash
bash fixtures/run_fixtures.sh
```

单独跑：

```bash
cd fixtures/python-repo
python3 ../../scripts/readiness.py src --repo .
```

**已知的环境依赖**

需要 `pytest` **和** `pytest-cov`（工具用 `pytest --cov=pkg --cov-report=json` 实测）。
两者缺一 ⇒ 覆盖率判据判 skipped（"没找到 pytest" / "没拿到 Python 覆盖率"），
fixture 报「环境缺失（未判定）」而不是「不符」。
CI 里会 `pip install pytest pytest-cov`。
