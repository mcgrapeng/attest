"""金额计算 —— fixture 的被测模块。

第三方依赖只出现在类型注解里（``TYPE_CHECKING``），
运行期不导入 ⇒ 这个 fixture 不需要装任何第三方包就能跑。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 运行期不导入
    import requests


def add(a: int, b: int) -> int:
    """两数相加。"""
    return a + b


def multiply(a: int, b: int) -> int:
    """两数相乘；有一个是 0 就直接返回 0。"""
    if a == 0 or b == 0:
        return 0
    return a * b
