/** 关闭时把进程内共享的 client 关掉 —— fixture 的被测模块。 */

export type Client = { closed: boolean; close(): void };

const shared: { current: Client | null } = { current: null };

export function getClient(): Client {
  if (!shared.current) {
    shared.current = {
      closed: false,
      close() {
        this.closed = true;
      },
    };
  }
  return shared.current;
}

export function closeClient(): void {
  const client = getClient();
  client.close();
}
