import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: {
      // 与 tsconfig 的 "@/*": ["./src/*"] 保持一致，否则测试里 import '@/lib/...' 解析失败
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['src/test/setup.ts'],
    restoreMocks: true,
    // 本机 Windows 下多 worker 并行会触发临时文件 EPERM（见开发日志 H-11），串行执行规避；
    // 前端用例总数在几十个量级，串行耗时仍可接受。
    fileParallelism: false,
    // 组件渲染较慢，给 20s；单测本身仍应是毫秒级
    testTimeout: 20_000,
  },
});
