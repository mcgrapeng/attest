/**
 * 记忆条目 —— fixture 的被测模块。
 *
 * 这个模块同时碰两个存储：PG（权威源）与 milvus 向量库（派生层），
 * 所以「数据流一致（跨存储对账）」这条判据必须被**真正判定**，
 * 而不是 skipped。
 */

export interface MemoryRow {
  id: string;
  tenantId: string;
  body: string;
}

/** PG 表形状。工具靠 `pgTable('…')` 这个形状认出"模块碰了 PG"。 */
export const memoriesTable = pgTable("memories", {
  id: "id",
  tenantId: "tenant_id",
  body: "body",
});

/** 写进 milvus 向量库的派生副本。 */
export async function upsertMemory(
  client: MilvusClient,
  row: MemoryRow,
): Promise<void> {
  await client.upsert({ id: row.id, body: row.body });
}

/** 从 PG（权威源）读一条。 */
export async function readMemory(
  db: Db,
  id: string,
): Promise<MemoryRow | null> {
  const row = await db.get("memories", id);
  return row ?? null;
}

/* ---- fixture 用的最小桩：不连真实的 PG / 向量库 ---- */

function pgTable(name: string, columns: Record<string, string>) {
  return { name, columns };
}

interface MilvusClient {
  upsert(record: { id: string; body: string }): Promise<void>;
}

interface Db {
  get(table: string, id: string): Promise<MemoryRow | null>;
}
