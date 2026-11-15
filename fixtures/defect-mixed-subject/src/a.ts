/** 第一人称豁免把整句吞掉 —— fixture 的被测模块。 */

export function isThirdParty(text: string): boolean {
  return !text.startsWith("我");
}

export function exemptFirstPerson(text: string): string {
  if (isThirdParty(text)) {
    return text;
  }
  return "";
}
