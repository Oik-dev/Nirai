# 精神を、イデアの丸ごとの写しを取って確かめてから起き直させる。
# 精神の新しい版が、本物のイデアに初めて新しいものを書く前に、Master が実行する（ふつうの権限。mind-restart.cmd をダブルクリック）。
# 1. 確かめる：リポジトリに記録していない変更がない・精神の環境に要るものがそろっている・G: が見える。足りなければ、何も変えずに止まる
# 2. 窓を閉じて、精神を止める（窓は、止まった精神を1分以内に起こしに来るので、先に閉じる）
# 3. イデアを G:\Nirai-Backups\<住人>\<日時>_before-restart へ写し、全ファイルの SHA-256 で本物と同じか確かめる
# 4. 窓を開き、精神が起きて、起きるときの記録（mind.log）に失敗がないかを見る
# 戻すとき（新しい精神がイデアを壊したときだけ。それまでの会話も消える）：窓を閉じて精神を止めてから
#   robocopy <写し> D:\Products\Residents\<住人> /MIR

param([string]$Resident = 'Serina')

$ErrorActionPreference = 'Stop'
$Repo = 'D:\Products\Nirai'
$Idea = Join-Path 'D:\Products\Residents' $Resident
$Backups = 'G:\Nirai-Backups'
$Server = Join-Path $Repo 'mind\app\server.py'
$WindowProfile = Join-Path $env:LOCALAPPDATA 'Nirai\window'
$SeaMind = 'http://127.0.0.1:47810/sea/mind'

function Stop-Here([string]$text) {
  Write-Host ''
  Write-Host "止めました：$text" -ForegroundColor Red
  Read-Host 'Enter で閉じる' | Out-Null
  exit 1
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

function Get-WindowProcesses {
  @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
    Where-Object { $_.CommandLine -like '*--user-data-dir=*' -and $_.CommandLine -like "*$WindowProfile*" })
}

function Get-MindProcesses {
  @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Server*" })
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

try {
  Write-Host '1/4 確かめています…'
  if (-not (Test-Path -LiteralPath $Idea)) { Stop-Here "イデアがありません：$Idea" }
  if (-not (Test-Path -LiteralPath $Backups)) { Stop-Here "$Backups が見えません（Gドライブがつながっていない？）" }
  $git = Invoke-Native { git -C $Repo status --porcelain --untracked-files=no }
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
    Stop-Here "写せませんでした（robocopy $($copy.code)）。精神は止めたままです。窓は開かずに、Claude に相談してください。`n$($copy.out -join "`n")"
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
    Stop-Here "写しが本物と合いません（$($bad.Count) 件）。精神は止めたままです。もう一度実行してください。`n$(($bad | Select-Object -First 20) -join "`n")"
  }
  Write-Host "   $($files.Count) 個のファイルが、すべて本物と同じです。"

  Write-Host '4/4 窓を開いて、精神が起きるのを待っています（数分かかることがあります）…'
  $log = Join-Path $Idea 'data\logs\mind.log'
  $offset = if (Test-Path -LiteralPath $log) { (Get-Item -LiteralPath $log).Length } else { 0 }
  & (Join-Path $Repo 'world\window\open.ps1')
  $up = $false
  for ($i = 0; $i -lt 60 -and -not $up; $i++) {
    Start-Sleep -Seconds 5
    $up = (Get-SeaMind) -eq 'up'
  }
  $written = Read-Since $log $offset
  $tail = ($written -split "`r?`n" | Where-Object { $_ } | Select-Object -Last 15) -join "`n"
  if (-not $up) { Stop-Here "精神が5分たっても起きません。写しは $dest にあります。起きるときの記録の最後：`n$tail" }
  if ($written -match 'Traceback') {
    Write-Host ''
    Write-Host '起きましたが、起きるときの記録に失敗が残っています。この画面を Claude に見せてください。' -ForegroundColor Yellow
    Write-Host $tail
  } else {
    Write-Host ''
    Write-Host 'できました。いつもどおり話しかけてみてください。' -ForegroundColor Green
  }
  Write-Host "精神の版：$version"
  Write-Host "写し：$dest"
  Read-Host 'Enter で閉じる' | Out-Null
} catch {
  Stop-Here "思わぬ失敗：$($_.Exception.Message)"
}
