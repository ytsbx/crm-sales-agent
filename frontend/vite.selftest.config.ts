import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/**
 * **只给冒烟脚本用**的第二份构建配置：把自测页单独打成一个生产产物。
 *
 * 为什么另开一份：自测页刻意**不进主包**（`index.html` 不引用它），而主构建
 * 只以 `index.html` 为入口，产物里根本没有这个页面。复核方的要求是
 * "在生产构建产物上也验证一次"，防止"局部重试"这套只在开发服务里有效 ——
 * 那就得真的把自测页打成生产产物、再用静态服务托管着验一遍。
 *
 * 产物目录由 `SELFTEST_OUT` 指定（脚本给临时目录），不落在仓库里。
 */
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: process.env.SELFTEST_OUT || 'dist-selftest',
    emptyOutDir: true,
    rollupOptions: {
      input: { 'selftest-lazy-region': 'selftest-lazy-region.html' },
    },
  },
})
