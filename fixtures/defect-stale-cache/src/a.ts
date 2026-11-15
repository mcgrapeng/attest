/** 关闭后缓存没清 ⇒ 缓存握死连接 —— fixture 的被测模块。 */

export type Conn = { closed: boolean; close(): void };

const cache = new Map<string, Conn>();

export function getClient(name: string): Conn {
  const cached = cache.get(name);
  if (!cached) {
    const conn: Conn = {
      closed: false,
      close() {
        this.closed = true;
      },
    };
    cache.set(name, conn);
    return conn;
  }
  return cached;
}

export function closeClient(name: string): void {
  const conn = getClient(name);
  conn.close();
}
