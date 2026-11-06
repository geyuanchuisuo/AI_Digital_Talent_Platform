/**
 * vitest 启动器（T-06）。
 *
 * 为什么需要它：本机 Windows 的系统临时目录 `C:\Users\Windows\AppData\Local\Temp`
 * 对当前进程不可写（Vite 打包配置时会往那里写临时文件，报 EPERM）。这与后端
 * pytest 的 H-7 是同一类环境问题，处置方式一致：**把临时目录指到项目内**。
 *
 * 该脚本跨平台：Node 自己设置 TMP/TEMP/TMPDIR，再把参数透传给 vitest。
 */

import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const tmpDir = path.join(root, '.vite-tmp');
mkdirSync(tmpDir, { recursive: true });

const env = { ...process.env, TMP: tmpDir, TEMP: tmpDir, TMPDIR: tmpDir };
const isWindows = process.platform === 'win32';
const bin = isWindows
  ? path.join(root, 'node_modules', '.bin', 'vitest.cmd')
  : path.join(root, 'node_modules', '.bin', 'vitest');

const child = spawn(bin, ['run', ...process.argv.slice(2)], {
  cwd: root,
  env,
  stdio: 'inherit',
  shell: isWindows, // .cmd 需要 shell 才能启动
});

child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  process.exit(code ?? 0);
});
