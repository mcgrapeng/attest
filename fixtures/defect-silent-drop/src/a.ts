/** 适配器把缺 id / 缺正文的行静默丢掉 —— fixture 的被测模块。 */

export type Row = { id: string; text: string };

export function insertRow(rows: Row[], row: Row): Row[] {
  const withId = rows.filter((r) => r.id !== "");
  const withText = withId.filter((r) => r.text !== "");
  return [...withText, row];
}
