// 对账脚本 —— 输出**英文**。
//
// 工具此前只认中文「漂移」+「❌」两个特征 ⇒ 英文输出的对账脚本一律被判成
// "生产租户无漂移"（假绿）。这个 fixture 钉住：英文输出必须被如实识别成漂移。
//
// 对账的是 memories 表（PG 权威源 vs 向量库派生层）。
//
// 三个租户：
//   acme-prod   —— 生产租户，漂了 ⇒ **阻塞交付**
//   globex-prod —— 生产租户，一致
//   itest-abc   —— **测试租户**（名字带 itest- 前缀），故意注入的失败态
//                  ⇒ 漂移只报不阻塞。少了它，「测试租户只报不拦」这条就没人测了。
const TENANTS = [
  {
    tenant: "acme-prod",
    status: "DRIFT",
    detail: "3 rows missing in vector store",
  },
  {
    tenant: "globex-prod",
    status: "consistent",
    detail: "0 rows missing",
  },
  {
    tenant: "itest-abc",
    status: "DRIFT",
    detail: "2 rows missing in vector store",
  },
];

console.log("| tenant | status | detail |");
console.log("| --- | --- | --- |");
for (const t of TENANTS) {
  console.log(`| ${t.tenant} | ${t.status} | ${t.detail} |`);
}
