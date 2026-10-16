/** Safely read the token persisted by the auth store. */
export function getPersistedAuthToken(): string | null {
  try {
    const raw = window.localStorage.getItem('crm-auth')
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    const state = (parsed as { state?: unknown }).state
    if (!state || typeof state !== 'object') return null
    const token = (state as { token?: unknown }).token
    return typeof token === 'string' && token ? token : null
  } catch {
    return null
  }
}
