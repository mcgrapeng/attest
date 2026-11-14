# 测试计划：订单导出（示例计划 —— 钉指向的文件**并不存在**）

> 这份计划是 eval 的输入：它声称覆盖了订单导出，但钉指向的测试文件在仓库里
> 根本不存在。`req_plan.py check` 必须把它报红。

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 导出结果包含本月的全部已完成订单 | src/orders/export.test.ts::导出包含全部已完成订单 |
| I2 | 导出对超过 5000 条的订单分页 | src/orders/never_written.test.ts::分页导出 |

## 覆盖矩阵

| 关键流程 | 落点 | 车道 |
| --- | --- | --- |
| 主干导出 | src/orders/export.test.ts | 单测 |
| 导出失败重试 | src/orders/missing_case.test.ts | 单测 |

## 核心逻辑细节

| 维度 | 落点 | 备注 |
| --- | --- | --- |
| 边界 | src/orders/export.test.ts | 0 条订单 |
