/**
 * Loud multi-beep signal alert using Web Audio API (no external file needed).
 */

let sharedCtx: AudioContext | null = null;

function getCtx(): AudioContext | null {
  try {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AC) return null;
    if (!sharedCtx) sharedCtx = new AC();
    return sharedCtx;
  } catch {
    return null;
  }
}

/** Unlock audio on a user gesture (browsers block autoplay until then). */
export async function unlockSignalAudio(): Promise<void> {
  const ctx = getCtx();
  if (!ctx) return;
  if (ctx.state === 'suspended') {
    try { await ctx.resume(); } catch { /* ignore */ }
  }
}

function tone(ctx: AudioContext, freq: number, start: number, dur: number, gain = 0.55) {
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = 'square';
  osc.frequency.value = freq;
  g.gain.setValueAtTime(0.0001, start);
  g.gain.exponentialRampToValueAtTime(gain, start + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  osc.connect(g);
  g.connect(ctx.destination);
  osc.start(start);
  osc.stop(start + dur + 0.02);
}

/** Big, hard-to-miss alert: 3 rising beeps, repeated twice. */
export async function playSignalAlertSound(): Promise<void> {
  const ctx = getCtx();
  if (!ctx) return;
  if (ctx.state === 'suspended') {
    try { await ctx.resume(); } catch { /* ignore */ }
  }
  const t0 = ctx.currentTime + 0.02;
  // Round 1
  tone(ctx, 880, t0, 0.18, 0.65);
  tone(ctx, 1175, t0 + 0.22, 0.18, 0.7);
  tone(ctx, 1568, t0 + 0.44, 0.28, 0.75);
  // Round 2 (louder / longer)
  tone(ctx, 880, t0 + 0.95, 0.2, 0.7);
  tone(ctx, 1175, t0 + 1.2, 0.2, 0.75);
  tone(ctx, 1760, t0 + 1.45, 0.4, 0.85);
}
