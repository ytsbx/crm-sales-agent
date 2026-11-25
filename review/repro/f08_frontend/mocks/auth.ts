const state = {
  token: 'repro-token',
  user: { id: 101, name: '张三', department: '销售一部', roles: ['salesperson'] },
  setAuth() {},
  setUser() {},
  clear() {},
}
export const useAuthStore = (selector?: (value: typeof state) => unknown) =>
  selector ? selector(state) : state
useAuthStore.getState = () => state
export function currentAuthEpoch() { return 1 }
