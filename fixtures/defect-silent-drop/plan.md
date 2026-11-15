# 适配器静默丢行 —— 测试计划

目标模块：`src`

## 不变式清单

| 编号 | 不变式 | 钉 |
| --- | --- | --- |
| I1 | 缺 id 的行必须被挡住，且不能连累正常行 | `src/a.test.ts::insertRow keeps a well-formed row` |
| I2 | 缺正文的行必须被挡住 | `src/a.test.ts::insertRow drops a row without an id` |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 静默丢弃 | src/a.ts#insertRow#boundary#1 | 过滤缺 id 的行（r.id !== ""） | I1 | src/a.test.ts::insertRow keeps a well-formed row |  |
| 静默丢弃 | src/a.ts#insertRow#drop#2 | 过滤缺正文的行（withId.filter） | I2 | src/a.test.ts::insertRow drops a row without an id |  |
| 静默丢弃 | src/a.ts#insertRow#boundary#2 | 过滤缺正文的行（r.text !== ""） | I2 | src/a.test.ts::insertRow drops a row without an id |  |

## 人工确认

2026-11-15 由 fixture 维护者确认：适配器允许丢行，但每一道过滤都必须归属一条不变式并钉住一个用例；
本计划刻意漏掉第一道过滤（缺 id 的行）所在的判定点，用来验证工具能报出「机器推导出、计划里没有」。
