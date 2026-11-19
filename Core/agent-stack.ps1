[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "restart", "status", "logs", "validate", "plan")]
    [string]$Action = "start",
    [string]$Registry = "agent-stack/registry.json",
    [string]$Config = "config.yaml",
    [string]$Service = "",
    [int]$StartupTimeoutSeconds = 0,
    [ValidateSet("auto", "tabs", "windows")]
    [string]$TerminalMode = "auto",
    [switch]$Hidden
)

$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

$sourcePath = Join-Path $PSScriptRoot "agent-stack/launcher.txt"
if (-not (Test-Path $sourcePath -PathType Leaf)) {
    throw "Launcher source is missing: $sourcePath"
}

$launcher = [scriptblock]::Create((Get-Content $sourcePath -Raw -Encoding UTF8))
& $launcher `
    -Action $Action `
    -Registry $Registry `
    -Config $Config `
    -Service $Service `
    -StartupTimeoutSeconds $StartupTimeoutSeconds `
    -TerminalMode $TerminalMode `
    -Hidden:$Hidden `
    -ProjectRoot $PSScriptRoot