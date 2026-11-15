# 原型链放行 —— 测试计划

目标模块：`src`

## 不变式清单

| 编号 | 不变式 | 钉 |
| --- | --- | --- |
| I1 | 角色表查询只能命中自有键，不能命中原型链上的键 | `src/a.test.ts::lookup returns the role of an admin` |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 分支 | src/a.ts#lookup#branch | 查表分支（key in ROLES） | I1 | src/a.test.ts::lookup returns the role of an admin |  |

## 人工确认

2026-11-15 由 fixture 维护者确认：角色表只能用自有键查询；用例里刻意没有 `lookup("toString")`，
用来验证工具能报出「这条钉发现不了这类缺陷」。
