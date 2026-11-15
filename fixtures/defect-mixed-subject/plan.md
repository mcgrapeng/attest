# 第一人称豁免吞整句 —— 测试计划

目标模块：`src`

## 不变式清单

| 编号 | 不变式 | 钉 |
| --- | --- | --- |
| I1 | 含第一人称的整句才走豁免，纯第三方句子必须原样返回 | `src/unrelated.test.ts::unrelated helper keeps its input` |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 分支 | src/a.ts#exemptFirstPerson#branch | 第一人称豁免分支 | I1 | src/unrelated.test.ts::unrelated helper keeps its input |  |

## 人工确认

2026-11-15 由 fixture 维护者确认：第一人称豁免必须逐句判断，混句只豁免第一人称部分；
本计划刻意把不变式钉到一个与目标模块无关的测试文件，用来验证工具能报出「这条钉对目标模块零覆盖」。
