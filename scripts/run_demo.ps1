<#
.SYNOPSIS
    AI 模拟面试与简历诊断助手 · 一键单进程演示脚本（阶段 5）

.DESCRIPTION
    1. 检查项目 .venv 与后端核心依赖；
    2. frontend/dist 缺失时，优先使用 DSH 自带 Node/pnpm 构建前端
       （node_modules / store / cache 均固定在项目内；普通新电脑可手动构建后用 -SkipBuild）；
    3. 启动 uvicorn，由 FastAPI 单进程同时托管 /api 与前端构建产物（SPA 回退）。

.PARAMETER Port
    监听端口，默认 8000。

.PARAMETER SkipBuild
    跳过前端构建，直接使用已有 frontend/dist。

.EXAMPLE
    powershell -NoProfile -File scripts/run_demo.ps1

.EXAMPLE
    powershell -NoProfile -File scripts/run_demo.ps1 -Port 8080 -SkipBuild

.NOTES
    Windows PowerShell 5.1 与 PowerShell 7（pwsh）均可运行；本文件为 UTF-8 with BOM 编码，
    以保证中文提示在 Windows PowerShell 5.1 下正确解析。
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,

    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$DistIndex = Join-Path $FrontendDir 'dist\index.html'
$DshNode = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
$DshPnpm = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\pnpm\bin\pnpm.mjs'

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Stop-WithError {
    param([string]$Message)
    Write-Host "[错误] $Message" -ForegroundColor Red
    exit 1
}

Write-Host 'AI 模拟面试与简历诊断助手 · 单进程演示' -ForegroundColor Green
Write-Host "项目根目录：$ProjectRoot"
Write-Host ''

# ------------------------------------------------------------------ #
# 1. Python 虚拟环境与后端核心依赖
# ------------------------------------------------------------------ #
Write-Step '检查 Python 虚拟环境与后端依赖'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Stop-WithError "未找到 .venv：$VenvPython。请先在项目根目录执行：python -m venv .venv，然后 $VenvPython -m pip install -r requirements.txt"
}
& $VenvPython -c "import fastapi, uvicorn, pydantic, yaml, openai, tiktoken" 2>$null
if ($LASTEXITCODE -ne 0) {
    Stop-WithError "虚拟环境缺少后端依赖。请执行：$VenvPython -m pip install -r requirements.txt"
}
Write-Host '    venv 与核心依赖 OK'

# ------------------------------------------------------------------ #
# 2. 前端构建产物（缺失时用 DSH Node/pnpm 构建）
# ------------------------------------------------------------------ #
Write-Step '检查前端构建产物 frontend/dist'
if ($SkipBuild) {
    Write-Host '    已指定 -SkipBuild：跳过前端构建'
    if (-not (Test-Path -LiteralPath $DistIndex)) {
        Write-Warning 'frontend/dist/index.html 不存在：本次仅提供 /api 与 /health，访问 / 将返回 404。'
    }
}
elseif (Test-Path -LiteralPath $DistIndex) {
    Write-Host "    已检测到 frontend/dist/index.html，跳过构建（如需重建请先删除 frontend/dist 后重跑）"
}
else {
    Write-Host '    未检测到 frontend/dist/index.html，开始构建前端（优先使用 DSH 内置 Node/pnpm）...'
    if (-not (Test-Path -LiteralPath $DshNode)) {
        Stop-WithError "未找到可用 Node（本脚本优先使用 DSH 内置 Node）。新电脑请安装 Node.js 20.19+ 后在 frontend/ 手动执行 pnpm install 与 pnpm build（或用 npm install --no-package-lock + npm run build），再运行本脚本并加 -SkipBuild。详见项目根目录《指南.md》。"
    }
    if (-not (Test-Path -LiteralPath $DshPnpm)) {
        Stop-WithError "未找到 pnpm（本脚本优先使用 DSH 内置 pnpm）。请在 frontend/ 手动执行 pnpm install 与 pnpm build，再运行本脚本并加 -SkipBuild。详见项目根目录《指南.md》。"
    }
    $env:npm_config_cache = Join-Path $FrontendDir '.npm-cache'
    Write-Host "    node : $DshNode"
    Write-Host "    pnpm : $DshPnpm"
    & $DshNode $DshPnpm --dir $FrontendDir install --store-dir (Join-Path $FrontendDir '.pnpm-store')
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError '前端依赖安装失败（pnpm install）。请检查网络或 frontend/ 配置，或使用 -SkipBuild 跳过构建。'
    }
    & $DshNode $DshPnpm --dir $FrontendDir build
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError '前端构建失败（pnpm build）。请检查 frontend/ 源码与上方构建日志。'
    }
    if (-not (Test-Path -LiteralPath $DistIndex)) {
        Stop-WithError "构建结束但未生成 $DistIndex。请检查 frontend/vite.config.js 与构建输出目录。"
    }
    Write-Host '    前端构建完成'
}

# ------------------------------------------------------------------ #
# 3. 环境变量提示（只提示文件是否存在，不读取 / 打印任何 Key）
# ------------------------------------------------------------------ #
$EnvFile = Join-Path $ProjectRoot '.env'
if (Test-Path -LiteralPath $EnvFile) {
    Write-Host '    环境变量文件 .env 已存在（脚本不会读取或打印其中的 Key）'
}
else {
    Write-Warning '未找到 .env：模型类接口会返回 code=2001（缺少 Key）。请复制 .env.example 为 .env 后填写（.env 不进库）。'
}

# ------------------------------------------------------------------ #
# 4. 端口占用检查
# ------------------------------------------------------------------ #
Write-Step "检查端口 $Port 是否可用"
$portInUse = $false
try {
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
    $portInUse = [bool]$connections
}
catch {
    $portInUse = $false
}
if ($portInUse) {
    Stop-WithError "端口 $Port 已被占用，请使用 -Port 指定其他端口（例如 -Port 8080）。"
}
Write-Host "    端口 $Port 可用"

# ------------------------------------------------------------------ #
# 5. 启动单进程服务（FastAPI 同时托管 API 与前端构建产物）
# ------------------------------------------------------------------ #
$Url = "http://127.0.0.1:$Port"
Write-Host ''
Write-Host '========== 演示服务已就绪 ==========' -ForegroundColor Green
Write-Host "  页面地址 : $Url/"
Write-Host "  接口文档 : $Url/docs"
Write-Host "  健康检查 : $Url/health"
Write-Host '  停止服务 : 在本终端按 Ctrl+C'
Write-Host '====================================' -ForegroundColor Green
Write-Host ''

Set-Location -LiteralPath $ProjectRoot
& $VenvPython -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
$exitCode = $LASTEXITCODE
# Ctrl+C 终止控制台进程（0xC000013A / -1073741510）视为正常停止
if ($exitCode -ne 0 -and $exitCode -ne -1073741510) {
    Stop-WithError "uvicorn 退出异常（exit=$exitCode），请检查上方日志。"
}
Write-Host '演示服务已停止。' -ForegroundColor Green
