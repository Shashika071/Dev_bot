/** Application-level keepalive. Incoming ticks do not replace a ping acknowledgement. */
export function startResearchHeartbeat(
  request: (payload: { ping: 1 }) => Promise<unknown>,
  reconnect: (reason: string) => void,
  intervalMs = 30_000,
): () => void {
  let stopped = false
  let pending = false
  const timer = setInterval(() => {
    if (stopped || pending) return
    pending = true
    void request({ ping: 1 }).then((response) => {
      if (!stopped && (!response || typeof response !== 'object'
        || !('ping' in response) || response.ping !== 'pong')) {
        reconnect('Invalid ping acknowledgement')
      }
    }, (error: unknown) => {
      if (!stopped) reconnect(`Ping failed: ${error instanceof Error ? error.message : String(error)}`)
    }).finally(() => { pending = false })
  }, intervalMs)
  return () => { stopped = true; clearInterval(timer) }
}
