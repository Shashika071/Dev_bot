// Compatibility entry point. Uses the shared strategy and completed candles only.
process.argv.push('--days', '0.5')
await import('./backtest.mjs')
