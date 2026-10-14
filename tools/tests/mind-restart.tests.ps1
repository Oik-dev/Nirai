# 本番を止めたり開いたりせず、処理対象と診断表示を確かめる。
param([string]$ScriptPath = (Join-Path $PSScriptRoot '..\mind-restart.ps1'))
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw '再起動道具の構文に問題があります' }
$wanted = @('Test-MindProcess', 'Get-StartupSummary', 'Read-Since')
$functions = @($ast.FindAll({ param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in $wanted
}, $true))
if ($functions.Count -ne $wanted.Count) { throw '確かめる関数が足りません' }
foreach ($function in $functions) { Invoke-Expression $function.Extent.Text }
$Server = 'D:\Products\Nirai\mind\app\server.py'
$script:checks = 0
function Assert-That([bool]$condition, [string]$label) {
  if (-not $condition) { throw "確認に失敗：$label" }
  $script:checks++
}
Assert-That (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='"C:\Python\python.exe" "D:\Products\Nirai\mind\app\server.py"'})) '本物の起動引数'
Assert-That (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='C:/Python/python.exe D:/Products/Nirai/mind/app/server.py'})) 'スラッシュの起動引数'
Assert-That (-not (Test-MindProcess ([pscustomobject]@{Name='powershell.exe'; CommandLine='powershell -Command "inspect D:\Products\Nirai\mind\app\server.py"'}))) '調査PowerShellは止めない'
Assert-That (-not (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='python.exe -c "inspect D:\Products\Nirai\mind\app\server.py"'}))) '調査Pythonは止めない'
Assert-That (-not (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='python.exe D:\Products\Other\mind\app\server.py'}))) '別の精神は止めない'
Assert-That (-not (Test-MindProcess ([pscustomobject]@{Name='python.exe'; CommandLine='python.exe D:\Products\Nirai\mind\app\server.py.backup'}))) '似た名前は止めない'
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
Write-Output "mind-restart: $($script:checks) checks passed (Windows PowerShell $($PSVersionTable.PSVersion))"
