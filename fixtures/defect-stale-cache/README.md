# fixture：defect-stale-cache

连接关掉了，**缓存没清** —— 缓存握死一个已关闭的连接。

**期望判定**

| 判据 | 期望 |
| --- | --- |
| 退出码 | `1` |
| 需求覆盖 | 出现「需求覆盖到位」这条判据**变红**（「1/1 条不变式没落到真实用例」）⇒ 钉指向的文件不存在 |
| 钉有区分力 | 出现「1 条判不了（环境没就绪 / 输出认不出）」⇒ 钉判不了（**untested** 档） |
| 钉的证据 | 出现「钉的文件不存在：src/cache.test.ts」⇒ 说清是**文件不存在**，不是"测试没覆盖到" |
| 高危缺口 | 出现「resource 1」⇒ `closeClient` 的 `resource` 判定点没人管 |

**为什么有它**

这条 fixture 覆盖的是四档里最容易被**糊弄过去**的一档：`untested`。

一条不变式的钉指向 `src/cache.test.ts` —— 这个文件还没写。
如果工具只报"通过"，那就是**假绿**：需求看着有钉、看着有覆盖，其实一行都没跑。
反过来，如果工具报"失败"，也偏了 —— 用例还没写，不是用例判出了缺陷。
正确口径是**判不了**（untested），并说清为什么判不了。

四档（`pass` / `fail` / `untested` / `not-applicable`）必须各自能被区分，
否则"判不了"和"判红"会退化成同一句话。

**怎么跑**

```bash
bash fixtures/run_fixtures.sh          # 全部 fixture
```

单独跑这一个：

```bash
cd fixtures/defect-stale-cache
python3 ../../scripts/readiness.py src --repo . --plan plan.md
```

**已知的环境依赖**

需要 `node`（22+）。判定点推导需要 TypeScript 后端（仓库内或全局 `tsc`）；
没有时推导等级降到 L2（词法层），本 fixture 的 DP 集在 L2/L3 下相同 ⇒ 结论不变。
