/** Browser storage is optional; privacy mode and quota policies may throw. */
export function readLocalStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeLocalStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // A preference must never prevent the application from rendering.
  }
}
