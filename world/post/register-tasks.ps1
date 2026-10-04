# 郵便局を、Master のログオン時に起こすタスク「Nirai Post」を登録する。
# Master のふつうの権限の PowerShell で実行する（管理者は要らない）。何度実行してもよい。戻すときは -Undo。
#
# - タスクが起こすのは番人（post\keeper.ts）。番人は、残っていた前の郵便局を止め、郵便局を --live で起こし、
#   記録を world\runtime\post.log に残し、郵便局が止まったら1分後に起こし直す。Holo へのトンネル（tunnel-client）は郵便局が起こす
# - 画面は出さない（毎晩のバックアップと同じく、conhost の --headless で node を直接起こす。cmd を挟むと引数が崩れた）。
#   conhost は node が失敗しても成功を返すので、タスクスケジューラの起こし直しは使わず、番人に任せる

param([switch]$Undo)

$ErrorActionPreference = 'Stop'

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
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$action = New-ScheduledTaskAction -Execute $conhost -WorkingDirectory $world `
  -Argument "--headless `"$node`" --no-warnings post\keeper.ts"

Register-ScheduledTask -TaskName 'Nirai Post' -Action $action -Trigger $trigger -Settings $settings -Force `
  -Description 'Nirai: 住人どうしの手紙を届け、住人を起こす郵便局（D:\Products\Nirai\world\post）。Holoへのトンネルも起こす' | Out-Null

# 動いていれば止めて、起こし直す（残った郵便局とトンネルは、新しい番人と郵便局が起きるときに止める）
Stop-ScheduledTask -TaskName 'Nirai Post'
Start-ScheduledTask -TaskName 'Nirai Post'
Write-Host 'タスク「Nirai Post」を登録して起こした。次のログオンからも自動で起きる。'
