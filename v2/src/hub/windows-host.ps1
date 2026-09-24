param([Parameter(Mandatory=$true)][string]$RequestPath,[switch]$Recover)
$ErrorActionPreference = 'Stop'
# Do not inherit a PowerShell 7 module search path into Windows PowerShell 5.
$env:PSModulePath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules'
Add-Type -Path (Join-Path $PSScriptRoot 'windows-host.cs') -ReferencedAssemblies System.Web.Extensions
if ($Recover) { [NiraiLocal.Host]::Recover($RequestPath) } else { [NiraiLocal.Host]::Run($RequestPath) }
