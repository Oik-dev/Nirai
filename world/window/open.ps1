$ErrorActionPreference = 'Stop'

$programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
$candidates = @(
  (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe')
  $(if ($programFilesX86) { Join-Path $programFilesX86 'Google\Chrome\Application\chrome.exe' })
  (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
) | Where-Object { $_ -and (Test-Path $_) }
$chrome = @($candidates)[0]
if (-not $chrome) {
  throw 'Google Chrome が見つかりません。Niraiの窓は外向き通信0件を確認したChromeだけで開きます。'
}

$profile = Join-Path $env:LOCALAPPDATA 'Nirai\window'
New-Item -ItemType Directory -Force $profile | Out-Null

$arguments = @(
  '--app=http://127.0.0.1:47810/'
  "--user-data-dir=$profile"
  '--proxy-server=http://127.0.0.1:9'
  '--disable-background-networking'
  '--disable-component-update'
  '--disable-sync'
  '--disable-extensions'
  '--disable-default-apps'
  '--disable-domain-reliability'
  '--disable-client-side-phishing-detection'
  '--safebrowsing-disable-auto-update'
  '--metrics-recording-only'
  '--no-pings'
  '--disable-features=OptimizationHints,AutofillServerCommunication,MediaRouter,Translate,NetworkTimeService'
  '--no-first-run'
  '--no-default-browser-check'
)

Start-Process -FilePath $chrome -ArgumentList $arguments
