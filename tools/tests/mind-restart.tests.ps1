# 本番を止めたり開いたりせず、処理対象と診断表示を確かめる。
param([string]$ScriptPath = (Join-Path $PSScriptRoot '..\mind-restart.ps1'))
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw '再起動道具の構文に問題があります' }
$wanted = @('Test-WindowProcess', 'Test-MindProcess', 'Get-StartupSummary', 'Read-Since',
  'Pause-IfInteractive', 'Stop-Here', 'Prepare-DetachedWindowTask', 'Start-DetachedWindow')
$functions = @($ast.FindAll({ param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in $wanted
}, $true))
if ($functions.Count -ne $wanted.Count) { throw '確かめる関数が足りません' }
foreach ($function in $functions) { Invoke-Expression $function.Extent.Text }
$Server = 'D:\Products\Nirai\mind\app\server.py'
$WindowProfile = 'C:\Users\test\AppData\Local\Nirai\window'
$script:checks = 0
function Assert-That([bool]$condition, [string]$label) {
  if (-not $condition) { throw "確認に失敗：$label" }
  $script:checks++
}
Assert-That ((Get-Content -LiteralPath $ScriptPath -Raw) -match '\[Console\]::IsInputRedirected') '無人の手はEnter待ちをしない'
Assert-That ($ast.Extent.Text -match 'Get-Command Get-FileHash') '精神を止める前にSHA-256コマンドを確かめる'
Assert-That ($ast.Extent.Text -match '(?m)^\s*exit 0\s*$') '正常終了は0'
Assert-That ($ast.Extent.Text -match '(?m)^\s*exit 1\s*$') '異常終了は1'
Assert-That ((Get-Command Start-DetachedWindow).Definition -match 'Start-ScheduledTask') '窓は手の子孫で起こさない'
Assert-That ((Get-Command Prepare-DetachedWindowTask).Definition -match 'LogonType Interactive') '窓は対話ユーザーから起こす'
Assert-That (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='"C:\Python\python.exe" "D:\Products\Nirai\mind\app\server.py"'})) '本物の起動引数'
Assert-That (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='C:/Python/python.exe D:/Products/Nirai/mind/app/server.py'})) 'スラッシュの起動引数'
Assert-That (-not (Test-MindProcess ([pscustomobject]@{Name='powershell.exe'; CommandLine='powershell -Command "inspect D:\Products\Nirai\mind\app\server.py"'}))) '調査PowerShellは止めない'
Assert-That (-not (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='python.exe -c "inspect D:\Products\Nirai\mind\app\server.py"'}))) '調査Pythonは止めない'
Assert-That (-not (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='python.exe D:\Products\Other\mind\app\server.py'}))) '別の精神は止めない'
Assert-That (-not (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='python.exe D:\Products\Nirai\mind\app\server.py.backup'}))) '似た名前は止めない'
Assert-That (Test-WindowProcess ([pscustomobject]@{Name='chrome.exe'; CommandLine='chrome.exe --user-data-dir=C:\Users\test\AppData\Local\Nirai\window --app=http://127.0.0.1:47810'})) '本物の窓'
Assert-That (Test-WindowProcess ([pscustomobject]@{Name='chrome.exe'; CommandLine='chrome.exe "--user-data-dir=C:\Users\test\AppData\Local\Nirai\window"'})) '引数全体が引用された窓'
Assert-That (Test-WindowProcess ([pscustomobject]@{Name='chrome.exe'; CommandLine='chrome.exe --user-data-dir="C:\Users\test\AppData\Local\Nirai\window"'})) '値が引用された窓'
Assert-That (-not (Test-WindowProcess ([pscustomobject]@{Name='chrome.exe'; CommandLine='chrome.exe --user-data-dir=C:\Users\test\AppData\Local\Nirai\window-other'}))) '似た名前の窓は止めない'
Assert-That (-not (Test-WindowProcess ([pscustomobject]@{Name='chrome.exe'; CommandLine='chrome.exe --user-data-dir=C:\Users\test\AppData\Local\Nirai\window\sub'}))) '子の置き場の窓は止めない'
Assert-That (-not (Test-WindowProcess ([pscustomobject]@{Name='powershell.exe'; CommandLine='inspect --user-data-dir=C:\Users\test\AppData\Local\Nirai\window'}))) '窓の調査コマンドは止めない'
$sample = "INFO: 眠り：「PERSONAL_TITLE_CANARY」を書いた`nTraceback (most recent call last):`n  PERSONAL_TEXT_CANARY`nValueError: PERSONAL_ERROR_CANARY`nrequests.exceptions.ReadTimeout: PERSONAL_TIMEOUT_CANARY"
$summary = Get-StartupSummary $sample
Assert-That ($summary -notmatch 'PERSONAL_') '診断に題も本文も説明文も出さない'
Assert-That ($summary -match 'Traceback 1 件' -and $summary -match 'ValueError' -and $summary -match 'requests.exceptions.ReadTimeout') '件数と例外の型は残す'
$emptySummary = Get-StartupSummary 'INFO: normal startup'
Assert-That ($emptySummary -match 'Traceback 0 件' -and $emptySummary -match 'なし') '正常起動の診断'
$taskTempRoot = [System.IO.Path]::GetTempPath()
$taskTestTemp = Join-Path $taskTempRoot ('nirai-restart-check-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $taskTestTemp
try {
  $log = Join-Path $taskTestTemp 'synthetic.log'
  [System.IO.File]::WriteAllText($log, "old`n", (New-Object System.Text.UTF8Encoding($false)))
  $offset = (Get-Item -LiteralPath $log).Length
  [System.IO.File]::AppendAllText($log, $sample, (New-Object System.Text.UTF8Encoding($false)))
  Assert-That ((Read-Since $log $offset) -eq $sample) '今回の起動以降だけ読む'
  [System.IO.File]::WriteAllText($log, 'new', (New-Object System.Text.UTF8Encoding($false)))
  Assert-That ((Read-Since $log 99) -eq 'new') '記録ファイルが短くなった場合'
} finally {
  $taskResolvedTemp = [System.IO.Path]::GetFullPath($taskTestTemp)
  if (-not $taskResolvedTemp.StartsWith($taskTempRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw '検証用フォルダー以外は片付けません' }
  Remove-Item -LiteralPath $taskResolvedTemp -Recurse -Force
}
# 手（pwsh 7）から cmd を挟むと、PS7版のUtilityモジュールの道が
# Windows PowerShell 5.1 に混じる。存在しない住人を指定し1/4だけ確認する。
$cmdPath = Join-Path (Split-Path -Parent $ScriptPath) 'mind-restart.cmd'
$psModulePathBefore = $env:PSModulePath
try {
  $pwshCommand = Get-Command pwsh -ErrorAction SilentlyContinue
  if ($pwshCommand) {
    $ps7Home = (& $pwshCommand.Source -NoProfile -Command '$PSHOME').Trim()
    $env:PSModulePath = (Join-Path $ps7Home 'Modules') + ';' + $psModulePathBefore
  } else {
    $dirty = Join-Path $env:TEMP ('nirai-fake-module-' + [guid]::NewGuid().ToString('N'))
    $fakeUtility = Join-Path $dirty 'Microsoft.PowerShell.Utility'
    New-Item -ItemType Directory -Path $fakeUtility -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $fakeUtility 'Microsoft.PowerShell.Utility.psd1') -Encoding UTF8 -Value '@{ ModuleVersion = ''99.0''; RootModule = ''Microsoft.PowerShell.Utility.psm1'' }'
    Set-Content -LiteralPath (Join-Path $fakeUtility 'Microsoft.PowerShell.Utility.psm1') -Encoding UTF8 -Value ''
    $env:PSModulePath = $dirty + ';' + $psModulePathBefore
  }
  $missingResident = 'NiraiMissingTest' + [guid]::NewGuid().ToString('N')
  # 標準入力を明示して渡し、Read-Host待ちになる退行も捕まえる。
  $output = @('' | cmd /c $cmdPath -Resident $missingResident 2>&1)
  Assert-That (($output -join "`n") -match 'イデアがありません') '汚れたPSModulePathでも入口は5.1で照合可能、存在しない住人なら停止'
} finally {
  $env:PSModulePath = $psModulePathBefore
  if ($dirty -and (Test-Path -LiteralPath $dirty)) { Remove-Item -LiteralPath $dirty -Recurse -Force }
}
Write-Output "mind-restart: $($script:checks) checks passed (Windows PowerShell $($PSVersionTable.PSVersion))"
