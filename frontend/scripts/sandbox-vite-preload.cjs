/**
 * DSH 沙箱（Windows）构建兼容补丁 —— 仅用于本仓库在沙箱环境内验证构建，用户本机不需要。
 *
 * 背景：Vite 8 在 Windows 上首次解析真实路径时会执行 `net use` 命令（探测网络驱动器）。
 * 当前沙箱禁止子进程管道，`child_process.exec` 会抛 `spawn EPERM`，导致 `vite build` 失败。
 *
 * 处理：把 exec 替换为「没有网络驱动器」的空实现，等价于 Vite 在该分支的兜底逻辑
 * （safeRealpathSync 回退为 fs.realpathSync.native），不影响构建产物与前端代码。
 *
 * 用法（在 frontend/ 目录）：
 *   $env:NODE_OPTIONS="--require <本文件绝对路径>"
 *   node <pnpm.mjs 路径> run build
 *
 * 在普通开发机（无沙箱限制）上直接 `pnpm build` 即可，无需本文件。
 */
const childProcess = require('node:child_process')

childProcess.exec = function sandboxExec(_command, options, callback) {
  const done = typeof options === 'function' ? options : callback
  const fakeProcess = { on() {}, once() {}, kill() {}, stdout: null, stderr: null }
  if (typeof done === 'function') {
    process.nextTick(() => done(null, '', ''))
  }
  return fakeProcess
}
