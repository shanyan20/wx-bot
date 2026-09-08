# 全部离线：不连接真实微信，不加载用户真实密钥，不调用付费模型。
param([string]$ReportDirectory = "data/test-reports")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentPrefix = Join-Path $projectRoot ".conda"
$condaExe = if ($env:CONDA_EXE) { $env:CONDA_EXE } else {
    (Get-Command conda -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}
if (-not (Test-Path -LiteralPath (Join-Path $environmentPrefix "conda-meta"))) {
    throw "项目 Conda 环境不存在，请执行 environment.yml 中的创建命令"
}
$reportRoot = if ([IO.Path]::IsPathRooted($ReportDirectory)) { $ReportDirectory } else {
    Join-Path $projectRoot $ReportDirectory
}
$startedAt = (Get-Date).ToString("o")
$reportPath = Join-Path $reportRoot (Get-Date -Format "yyyyMMdd-HHmmss-fff")
New-Item -ItemType Directory -Force -Path $reportPath | Out-Null
$stageResults = [Collections.Generic.List[object]]::new()

function Invoke-Check {
    param([string]$Name, [string[]]$PythonArguments)
    Write-Host ("Running: " + $Name)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    & $condaExe run --no-capture-output --prefix $environmentPrefix python @PythonArguments
    $checkExitCode = $LASTEXITCODE
    $timer.Stop()
    $stageResults.Add([ordered]@{
        name = $Name
        python_arguments = $PythonArguments
        exit_code = $checkExitCode
        duration_seconds = [Math]::Round($timer.Elapsed.TotalSeconds, 3)
    })
    # 每阶段落盘，后续失败仍保留结果；不存储模型凭证或聊天正文。
    $summary = [ordered]@{
        started_at = $startedAt
        environment_prefix = $environmentPrefix
        offline = $true
        checks = $stageResults.ToArray()
    }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $reportPath "summary.json")
    if ($checkExitCode -ne 0) { throw ($Name + " failed; exit code " + $checkExitCode) }
}

Push-Location $projectRoot
try {
    Write-Host ("Reports: " + $reportPath)
    Invoke-Check -Name "pytest" -PythonArguments @("-m", "pytest", "-q", "--junitxml=$(Join-Path $reportPath 'pytest.xml')")
    Invoke-Check -Name "ruff" -PythonArguments @("-m", "ruff", "check", ".")
    Invoke-Check -Name "dependencies" -PythonArguments @("-m", "pip", "check")
    Invoke-Check -Name "cli-smoke" -PythonArguments @("scripts/smoke.py")
    Write-Host "All offline checks passed."
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally { Pop-Location }
