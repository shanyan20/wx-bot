param([string]$Config = "config/deepseek.toml")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentPrefix = Join-Path $projectRoot ".conda"
if (-not (Test-Path -LiteralPath (Join-Path $environmentPrefix "conda-meta"))) {
    throw "请先按 README 创建项目 Conda 环境"
}
$condaExe = if ($env:CONDA_EXE) { $env:CONDA_EXE } else {
    (Get-Command conda -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}
Push-Location $projectRoot
try {
    & $condaExe run --no-capture-output --prefix $environmentPrefix python -m wechat_bot --config $Config run
    exit $LASTEXITCODE
} finally { Pop-Location }
