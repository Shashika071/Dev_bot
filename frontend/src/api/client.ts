export const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export function wsUrl(path: string): string {
  const base = import.meta.env.VITE_WS_URL || API.replace(/^http/, 'ws');
  return `${base.replace(/\/$/, '')}${path}`;
}

export async function api<T>(path: string, opts?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return response.json();
}
