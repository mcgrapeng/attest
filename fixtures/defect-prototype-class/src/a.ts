/** 用 in 查表 ⇒ 原型链上的键被放行 —— fixture 的被测模块。 */

const ROLES: Record<string, string> = { admin: "admin", user: "user" };

export function lookup(key: string): string | undefined {
  if (key in ROLES) {
    return ROLES[key];
  }
  return undefined;
}
