# 关闭后缓存没清 —— 测试计划

目标模块：`src`

## 不变式清单

| 编号 | 不变式 | 钉 |
| --- | --- | --- |
| I1 | 连接关闭之后，缓存里的连接必须失效 | `src/cache.test.ts::closeClient drops the cached connection` |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 分支 | src/a.ts#getClient#branch | 缓存命中分支 | I1 | src/a.test.ts::getClient returns a live connection |  |
| 资源释放 | src/a.ts#closeClient#resource | 关闭连接 |  | src/cache.test.ts::closeClient drops the cached connection |  |

## 人工确认

2026-11-15 由 fixture 维护者确认：关闭连接后必须清缓存，否则缓存握死连接；
本计划刻意把 I1 钉到一个还没写的测试文件（src/cache.test.ts），用来验证工具能报出「钉判不了」
与「高危判定点没人管」。
