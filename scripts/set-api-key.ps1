# 交互式更新密钥；只保存 DPAPI 密文，避免明文落盘和进入 PowerShell 命令历史。
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security
$projectRoot = Split-Path -Parent $PSScriptRoot
$secretDirectory = Join-Path $projectRoot ".secrets"
New-Item -ItemType Directory -Force -Path $secretDirectory | Out-Null
$secretValue = Read-Host "DeepSeek API key" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretValue)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $bytes = [Text.Encoding]::UTF8.GetBytes($plain)
    $cipher = [Security.Cryptography.ProtectedData]::Protect(
        $bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [IO.File]::WriteAllBytes((Join-Path $secretDirectory "deepseek-api-key.dpapi"), $cipher)
    Write-Host "已保存当前 Windows 用户专属的密文凭证。"
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    if ($bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
    $plain = $null
    $secretValue.Dispose()
}
