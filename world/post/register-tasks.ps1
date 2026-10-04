# 郵便局を、Master のログオン時に起こすタスク「Nirai Post」を登録する。
# Master のふつうの権限の PowerShell で実行する（管理者は要らない）。何度実行してもよい。戻すときは -Undo。
#
# - 郵便局は --live で起こす。記録を world\runtime\post.log に残し、Holo へのトンネル（tunnel-client）も郵便局が起こす
#   （郵便局が聞き始めてから起こすので、どちらが先に起きるかの競争がない）
# - 同時に1つだけ動かし、落ちたら1分後に起こし直す
# - 画面は出さない（毎晩のバックアップと同じく、conhost の --headless で node を直接起こす。cmd を挟むと引数が崩れた）

param([switch]$Undo)

$ErrorActionPreference = 'Stop'

# 前の版の、トンネルだけのタスクは片付ける（今は郵便局がトンネルを起こす）
Unregister-ScheduledTask -TaskName 'Nirai Tunnel' -Confirm:$false -ErrorAction SilentlyContinue

if ($Undo) {
  Unregister-ScheduledTask -TaskName 'Nirai Post' -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host 'タスクを外した。'
  return
}

$world = 'D:\Products\Nirai\world'
$node = 'C:\Program Files\nodejs\node.exe'
$conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$action = New-ScheduledTaskAction -Execute $conhost -WorkingDirectory $world `
  -Argument "--headless `"$node`" --no-warnings post\server.ts --live"

Register-ScheduledTask -TaskName 'Nirai Post' -Action $action -Trigger $trigger -Settings $settings -Force `
  -Description 'Nirai: 住人どうしの手紙を届け、住人を起こす郵便局（D:\Products\Nirai\world\post）。Holoへのトンネルも起こす' | Out-Null

# いま動いている郵便局があれば止めて、タスクで起こし直す（残ったトンネルは、新しい郵便局が起きるときに止める）
Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -match 'post\\server\.ts' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-ScheduledTask -TaskName 'Nirai Post'
Write-Host 'タスク「Nirai Post」を登録して起こした。次のログオンからも自動で起きる。'
