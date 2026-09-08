# 仅监督子进程退出；不杀死可能正在发送消息的活跃进程，不自动扫码或重启微信。
# TODO(T06/T07)：独立 UIA 进程的超时隔离和外部告警尚待实现。
param([string]$Config = "config/deepseek.toml", [int]$MaxRestarts = 3)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentPrefix = Join-Path $projectRoot ".conda"
if (-not (Test-Path -LiteralPath (Join-Path $environmentPrefix "conda-meta"))) {
    throw "请先创建项目 Conda 环境"
}
$condaExe = if ($env:CONDA_EXE) { $env:CONDA_EXE } else {
    (Get-Command conda -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}
Push-Location $projectRoot
try {
    for ($attempt = 0; $attempt -le $MaxRestarts; $attempt++) {
        & $condaExe run --no-capture-output --prefix $environmentPrefix python -m wechat_bot --config $Config run
        $botExitCode = $LASTEXITCODE
        if ($botExitCode -eq 0 -or $botExitCode -eq 130) { exit $botExitCode }
        if ($attempt -lt $MaxRestarts) { Start-Sleep -Seconds ([Math]::Min(30, 5 * ($attempt + 1))) }
    }
    throw "机器人连续异常退出，已停止自动重启。请检查日志和 uncertain 任务。"
} finally { Pop-Location }
