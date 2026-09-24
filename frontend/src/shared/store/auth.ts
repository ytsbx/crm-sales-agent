import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import type { CurrentUser } from '../types'

interface AuthState {
  token: string | null
  user: CurrentUser | null
  setAuth: (token: string, user: CurrentUser | null) => void
  setUser: (user: CurrentUser) => void
  clear: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      setAuth: (token, user) => set({ token, user }),
      setUser: (user) => set({ user }),
      clear: () => set({ token: null, user: null }),
    }),
    { name: 'crm-auth' },
  ),
)
