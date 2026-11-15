# 共享 client 释放 —— 测试计划

目标模块：`src`

## 不变式清单

| 编号 | 不变式 | 钉 |
| --- | --- | --- |
| I1 | 关闭共享 client 之后，client 的状态必须是已关闭 | `src/a.test.ts::closeClient closes the shared client` |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 分支 | src/a.ts#getClient#branch | 没有共享 client 时惰性创建 | I1 | src/a.test.ts::closeClient closes the shared client |  |
| 资源释放 | src/a.ts#closeClient#resource | 关闭进程内共享的 client |  | src/a.test.ts::closeClient closes the shared client |  |

## 人工确认

2026-11-15 由 fixture 维护者确认：共享 client 的关闭是高危判定点，必须归属一条不变式；
本计划刻意把这一行的「归属的不变式」与「豁免理由」都留空，用来验证工具能报出高危判定点没人管。
