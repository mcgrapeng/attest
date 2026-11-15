# fixture：defect-prototype-class

用 `in` 查表 ⇒ **原型链上的键**被放行（`lookup("toString")` 返回函数而不是 `undefined`）。

**期望判定**

| 判据 | 期望 |
| --- | --- |
| 退出码 | `1` |
| 钉有区分力 | 出现「1 条不变式的钉发现不了这类缺陷」⇒ 钉**有区分力**这条判据变红（**fail** 档） |
| 变异证据 | 出现「1 个变异全部存活 ⇒ 这条钉发现不了这类缺陷」⇒ 说清是**存活**，不是"没跑起来" |
| 变异算子 | JSON 里 `discriminating.survivors[].mutants[].op` 为 `in-to-hasown` ⇒ 报的是**这一类**变异 |
| 误报为 0 | **不**出现「1 条判不了」⇒ 别把"钉没判力"退化成"环境没就绪" |

**为什么有它**

这条 fixture 覆盖**实测类判据能红**这件事 —— 前面几个 fixture 的红都是
静态信号（计划对不对、覆盖到没覆盖到），只有这条是**把源码改坏、真跑测试、
看测试抓不抓得住**。

它抓的是一类真实缺陷：`x in MAP` 会命中原型链（`toString` / `constructor`），
正确写法是 `Object.hasOwn(MAP, x)`。用例里只测了 `admin` 和一个不存在的 `ghost`，
**没有** `lookup("toString")` ⇒ 把 `in` 改成 `Object.hasOwn` 测试照样绿 ⇒
这条钉对这类缺陷**没有区分力**。

工具要能报出"这条钉发现不了这类缺陷"，而不是给一句"测试全绿"。

**怎么跑**

```bash
bash fixtures/run_fixtures.sh          # 全部 fixture
```

单独跑这一个（要看变异明细就加 `--json`）：

```bash
cd fixtures/defect-prototype-class
python3 ../../scripts/readiness.py src --repo . --plan plan.md --json
```

**已知的环境依赖**

需要 `node`（22+）。判定点推导**需要** TypeScript 后端（仓库内或全局 `tsc`）——
`in` 不是边界算子，L2 词法层看不见它，落到 L2 时这条 fixture 的结论会退化。
