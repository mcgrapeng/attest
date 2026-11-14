# fixture：english-reconciler

**期望判定**

| 判据 | 期望 |
| --- | --- |
| 退出码 | `1`（漂移是阻塞项） |
| 漂移被识别 | 输出里出现「数据流漂移」⇒ 英文输出里的漂移被如实认出来了 |
| 不给假绿 | 输出里**不**出现「生产租户无漂移」 |

**为什么有它**

评审会上的原话是：

> 「你的工具对账层只懂一种中文方言 —— 对账脚本输出英文，你的工具报"生产租户无漂移"。」

这是**假绿**：工具认不出的漂移，被当成"没有漂移"。比假红更糟 ——
假红会让人去查，假绿让人签字放行。

这个 fixture 的对账脚本输出英文表格，其中一个**生产租户**（`acme-prod`）
有漂移（`DRIFT`），另一个租户一致。期望是漂移被认出来。

模块同时碰 PG（`pgTable('memories')`）与 milvus 向量库两个存储，
所以「数据流一致」这条判据会被真正判定（而不是 skipped）——
**这条前提本身也是 fixture 要守的**：如果工具认不出派生存储，
这条判据会变成 skipped，fixture 里的漂移就没人验了。

**怎么跑**

```bash
bash fixtures/run_fixtures.sh
```

单独跑：

```bash
cd fixtures/english-reconciler
python3 ../../scripts/readiness.py src --repo .
```

**已知的环境依赖**

需要 `node`（跑 colocated 的测试车道 + 跑对账脚本）。
