# fixture：defect-shared-client

进程内**共享**的 client 被关掉 —— 别的调用方还在用它。

**期望判定**

| 判据 | 期望 |
| --- | --- |
| 退出码 | `1` |
| 高危缺口 | 出现「高危四类缺口：drop 0 ｜ error-path 0 ｜ resource 1 ｜ state 0」⇒ 只有 `resource` 一类被报出来 |
| 缺口清单 | 出现「既没有归属不变式，也没有豁免理由」⇒ 缺口说的是**没人管**，不是"没写清" |
| 覆盖判据 | 出现「应有判定点覆盖到位」这条判据**变红** ⇒ 高危判定点必须有归属或豁免 |
| 误报为 0 | **不**出现「drop 1」「error-path 1」「state 1」 |

**为什么有它**

`close()` 是**高危判定点**：进程内共享的资源一旦被关掉，别的调用方会拿到
一个坏掉的句柄。这类动作在需求里必须有归属（"谁负责关、关完什么状态"），
或者有明确的豁免理由（"这个 client 生命周期跟进程一样，不用关"）。

这个 fixture 的计划**刻意把 `resource` 那行的「归属的不变式」与「豁免理由」都留空**，
用来证明工具会把它算成**没人管的缺口**而不是"计划里没提到"——
两者对应的修法完全不同（前者要补归属或豁免，后者要补计划）。

**怎么跑**

```bash
bash fixtures/run_fixtures.sh          # 全部 fixture
```

单独跑这一个：

```bash
cd fixtures/defect-shared-client
python3 ../../scripts/readiness.py src --repo . --plan plan.md
```

**已知的环境依赖**

需要 `node`（22+）。判定点推导需要 TypeScript 后端（仓库内或全局 `tsc`）；
没有时推导等级降到 L2（词法层），本 fixture 的 DP 集在 L2/L3 下相同 ⇒ 结论不变。
