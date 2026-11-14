//! 简单算术 —— fixture 的被测模块。
//!
//! 工具当前只实现了 TS/JS/Python 的文件约定 ⇒ 这个模块识别不出来。

/// 两数相加。
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

/// 两数相减。
pub fn subtract(a: i32, b: i32) -> i32 {
    a - b
}
