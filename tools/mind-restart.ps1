# 精神を、イデアの丸ごとの写しを取って確かめてから起き直させる。
# 精神の新しい版が、本物のイデアに初めて新しいものを書く前に、Master か住人（Holo の手）が実行する（ふつうの権限）。
# 1. 確かめる：リポジトリに記録していない変更がない・精神の環境に要るものがそろっている・G: が見える。足りなければ、何も変えずに止まる
# 2. 窓を閉じて、精神を止める（窓は、止まった精神を1分以内に起こしに来るので、先に閉じる）
# 3. イデアを G:\Nirai-Backups\<住人>\<日時>_before-restart へ写し、全ファイルの SHA-256 で本物と同じか確かめる
# 4. 窓を開き（Nirai.lnk）、精神が起きて、起きるときの記録（mind.log）に失敗がないかを見る
# 戻すとき（新しい精神がイデアを壊したときだけ。それまでの会話も消える）：窓を閉じて精神を止めてから
#   robocopy <写し> D:\Products\Residents\<住人> /MIR

param([ValidatePattern('^[A-Za-z][A-Za-z0-9_-]*$')][string]$Resident = 'Serina')

$ErrorActionPreference = 'Stop'
$Repo = 'D:\Products\Nirai'
$Idea = Join-Path 'D:\Products\Residents' $Resident
$Backups = 'G:\Nirai-Backups'
$Server = Join-Path $Repo 'mind\app\server.py'
$WindowProfile = Join-Path $env:LOCALAPPDATA 'Nirai\window'
$SeaMind = 'http://127.0.0.1:47810/sea/mind'
$WindowLink = Join-Path $Repo 'Nirai.lnk'

function Pause-IfInteractive {
  if (-not [Console]::IsInputRedirected) {
    Read-Host 'Enter で閉じる' | Out-Null
  }
}

function Stop-Here([string]$text) {
  Write-Host ''
  Write-Host "止めました：$text" -ForegroundColor Red
  Pause-IfInteractive
  exit 1
}

function Prepare-DetachedWindowTask {
  # 手のプロセスがタイムアウトしても窓を巻き込まないよう、タスクスケジューラから起こす。
  # 同じユーザーの対話セッションで動く永続タスクで、管理者権限・パスワードは要らない。
  $taskName = 'Nirai-Mind-Window'
  $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  $action = New-ScheduledTaskAction -Execute 'explorer.exe' -Argument ('"{0}"' -f $WindowLink)
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
  Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
}

function Start-DetachedWindow {
  Start-ScheduledTask -TaskName 'Nirai-Mind-Window'
}

# 外のコマンドは、エラー出力があっても止めずに、終わり方と出力だけを受け取る。
function Invoke-Native([scriptblock]$block) {
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $out = @(& $block 2>&1 | ForEach-Object { "$_" })
    return @{ code = $LASTEXITCODE; out = $out }
  } finally { $ErrorActionPreference = $old }
}

function Test-WindowProcess($processInfo) {
  if ($processInfo.Name -ne 'chrome.exe' -or -not $processInfo.CommandLine) { return $false }
  $profilePath = [regex]::Escape($WindowProfile)
  $pattern = '(?:^|\s)(?:"--user-data-dir=' + $profilePath + '"|--user-data-dir=(?:"' + $profilePath + '"|' + $profilePath + '))(?:\s|$)'
  return $processInfo.CommandLine.Replace('/', '\') -match $pattern
}

function Get-WindowProcesses {
  @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
    Where-Object { Test-WindowProcess $_ })
}

function Test-MindProcess($processInfo) {
  if ($processInfo.Name -notin @('python.exe', 'pythonw.exe') -or -not $processInfo.CommandLine) { return $false }
  # 起動するスクリプト引数を確かめる。調査コマンドに含まれたパスだけで、別のプロセスを止めない。
  $scriptPath = [regex]::Escape($Server)
  $pattern = '^\s*(?:"[^"]+"|\S+)\s+(?:"' + $scriptPath + '"|' + $scriptPath + ')(?:\s|$)'
  return $processInfo.CommandLine.Replace('/', '\') -match $pattern
}

function Get-MindProcesses {
  @(Get-CimInstance Win32_Process | Where-Object { Test-MindProcess $_ })
}

function Get-SeaMind {
  try { return (Invoke-RestMethod $SeaMind -TimeoutSec 3).mind } catch { return $null }
}

function Read-Since([string]$path, [long]$offset) {
  if (-not (Test-Path -LiteralPath $path)) { return '' }
  $stream = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
  try {
    if ($stream.Length -lt $offset) { $offset = 0 }
    $null = $stream.Seek($offset, 'Begin')
    return (New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)).ReadToEnd()
  } finally { $stream.Dispose() }
}

function Get-StartupSummary([string]$written) {
  # 記憶の題・人格・例外の説明文を画面へ出さない。AIへ渡してよい運用情報だけにする。
  $tracebacks = [regex]::Matches($written, 'Traceback \(most recent call last\)').Count
  $types = @([regex]::Matches($written, '(?m)^([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception|Timeout))\s*:') |
    ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
  $typeText = if ($types.Count) { $types -join ', ' } else { 'なし' }
  return "追加された記録：Traceback $tracebacks 件。例外の型：$typeText"
}

try {
  Write-Host '1/4 確かめています…'
  # 写しを照合できない環境なら、窓・精神・イデアに一切触れる前に止まる。
  if (-not (Get-Command Get-FileHash -ErrorAction SilentlyContinue)) {
    Stop-Here 'この起こし方では写しを照合できません。mind-restart.cmd から起こしてください'
  }
  if (-not (Test-Path -LiteralPath $Idea)) { Stop-Here "イデアがありません：$Idea" }
  if (-not (Test-Path -LiteralPath $Backups)) { Stop-Here "$Backups が見えません（Gドライブがつながっていない？）" }
  if (-not (Test-Path -LiteralPath $WindowLink -PathType Leaf)) { Stop-Here "窓の入口がありません：$WindowLink（精神は止めていません）" }
  $git = Invoke-Native { git -C $Repo status --porcelain --untracked-files=normal }
  if ($git.code -ne 0) { Stop-Here "git が動きません：`n$($git.out -join "`n")" }
  if ($git.out.Count) { Stop-Here "リポジトリに記録していない変更があります（精神はそれも読んでしまう）：`n$($git.out -join "`n")" }
  $version = (Invoke-Native { git -C $Repo log -1 --format=%h }).out -join ''
  $python = Join-Path $Repo 'mind\.venv\Scripts\python.exe'
  $requirements = Join-Path $Repo 'mind\requirements.txt'
  $pip = Invoke-Native { & $python -m pip install -r $requirements --dry-run --no-index --quiet --disable-pip-version-check }
  if ($pip.code -ne 0) { Stop-Here "精神の環境（mind\.venv）に足りないものがあります：`n$($pip.out -join "`n")" }
  $envFile = Join-Path $Repo 'mind\.env'
  if (-not ((Test-Path -LiteralPath $envFile) -and ((Get-Content -LiteralPath $envFile) -match '^GEMINI_API_KEY=.+'))) {
    Write-Host '   注意：mind\.env に GEMINI_API_KEY がありません。起きても Gemma だけで話します。' -ForegroundColor Yellow
  }
  # 起動経路は停止前に登録しておく。手が失敗しても精神を巻き添えにしない。
  Prepare-DetachedWindowTask

  Write-Host '2/4 窓を閉じて、精神を止めています…'
  foreach ($p in (Get-WindowProcesses)) {
    if ($p.CommandLine -notlike '*--type=*') {
      $process = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
      if ($process) { $null = $process.CloseMainWindow() }
    }
  }
  for ($i = 0; $i -lt 10 -and (Get-WindowProcesses).Count; $i++) { Start-Sleep -Seconds 1 }
  Get-WindowProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Get-MindProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  for ($i = 0; $i -lt 15 -and ((Get-MindProcesses).Count -or (Get-SeaMind) -eq 'up'); $i++) { Start-Sleep -Seconds 1 }
  if ((Get-MindProcesses).Count -or (Get-SeaMind) -eq 'up') { Stop-Here '精神が止まりません。イデアは何も変えていません。' }

  $dest = Join-Path (Join-Path $Backups $Resident.ToLower()) ((Get-Date -Format 'yyyy-MM-dd_HHmm') + '_before-restart')
  if (Test-Path -LiteralPath $dest) { Stop-Here "写し先がもうあります：$($dest)（1分おいて、もう一度実行してください）" }
  Write-Host "3/4 イデアを写しています… → $dest"
  $copy = Invoke-Native { robocopy $Idea $dest /E /COPY:DAT /DCOPY:T /R:1 /W:1 /NP /NFL /NDL /NJH /NJS }
  if ($copy.code -ge 8) {
    Stop-Here "写せませんでした（robocopy $($copy.code)）。精神は止めたままです。窓は開かずに、Master のこの作業のチャットへ知らせてください。"
  }
  Write-Host '   本物と写しを、ファイルごとに比べています…'
  $files = @(Get-ChildItem -LiteralPath $Idea -Recurse -File -Force)
  $bad = @(foreach ($f in $files) {
    $rel = $f.FullName.Substring($Idea.Length).TrimStart('\')
    $other = Join-Path $dest $rel
    if (-not (Test-Path -LiteralPath $other) -or
        (Get-FileHash -LiteralPath $f.FullName).Hash -ne (Get-FileHash -LiteralPath $other).Hash) { $rel }
  })
  if ($bad.Count) {
    Stop-Here "写しが本物と合いません（$($bad.Count) 件）。精神は止めたままです。もう一度実行してください。"
  }
  Write-Host "   $($files.Count) 個のファイルが、すべて本物と同じです。"

  Write-Host '4/4 窓を開いて、精神が起きるのを待っています（数分かかることがあります）…'
  $log = Join-Path $Idea 'data\logs\mind.log'
  $offset = if (Test-Path -LiteralPath $log) { (Get-Item -LiteralPath $log).Length } else { 0 }
  # 窓は、手のPowerShellとは別のプロセスの木から起こす。
  Start-DetachedWindow
  for ($i = 0; $i -lt 10 -and -not (Get-WindowProcesses).Count; $i++) { Start-Sleep -Seconds 1 }
  if (-not (Get-WindowProcesses).Count) {
    Stop-Here "窓が起きません。写しは $dest にあります。精神は止まったままかもしれません。"
  }
  $up = $false
  for ($i = 0; $i -lt 60 -and -not $up; $i++) {
    Start-Sleep -Seconds 5
    $up = (Get-SeaMind) -eq 'up'
  }
  $written = Read-Since $log $offset
  $summary = Get-StartupSummary $written
  if (-not $up) { Stop-Here "精神が5分たっても起きません。写しは $dest にあります。起きるときの運用情報：`n$summary" }
  if ($written -match 'Traceback') {
    Write-Host ''
    Write-Host '起きましたが、起きるときの記録に失敗が残っています。この運用情報を、この作業のチャットへ知らせてください。' -ForegroundColor Yellow
    Write-Host $summary
  } else {
    Write-Host ''
    Write-Host 'できました。いつもどおり話しかけてみてください。' -ForegroundColor Green
  }
  Write-Host "精神の版：$version"
  Write-Host "写し：$dest"
  Pause-IfInteractive
  exit 0
} catch {
  Stop-Here "思わぬ失敗（例外の型：$($_.Exception.GetType().Name)）。この作業のチャットへ知らせてください。"
}
