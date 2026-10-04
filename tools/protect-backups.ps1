# 毎晩のバックアップ（G:\Nirai-Backups\daily）を、毎晩のバックアップのタスクのほかは書けない形にする。
# ふつうの権限で動くもの（チームの脳、Master のふだんの道具、Claude）は、読めるが、書けも消せもしない。
# 管理者の PowerShell で Master が1回だけ実行する。戻すときは -Undo を付けて実行する。
#
# - タスク「Nirai Idea Backup」を、管理者の権限（最上位の特権）で動かす
# - daily の持ち主を Administrators にし、親からの引き継ぎを切って、SYSTEM と Administrators だけが書ける形にする。
#   持ち主を替えるのは、持ち主なら自分で権限を書き換えられてしまうため
# - 当てる前の権限は G:\Nirai-Backups\acl\ に写す

param([switch]$Undo)

$ErrorActionPreference = 'Stop'
$Daily = 'G:\Nirai-Backups\daily'
$TaskName = 'Nirai Idea Backup'

$me = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$me).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw '管理者の PowerShell で実行してください。'
}

if ($Undo) {
  icacls $Daily /reset /T /C /Q | Out-Null
  icacls $Daily /setowner $me.User.Translate([Security.Principal.NTAccount]).Value /T /C /Q | Out-Null
  $task = Get-ScheduledTask -TaskName $TaskName
  $task.Principal.RunLevel = 'Limited'
  Set-ScheduledTask -InputObject $task | Out-Null
  Write-Host '元に戻した（daily は親から権限を引き継ぎ、タスクはふつうの権限で動く）。'
  return
}

$backup = Join-Path 'G:\Nirai-Backups\acl' ("daily_" + (Get-Date -Format 'yyyy-MM-dd_HHmmss') + '.acl')
New-Item -ItemType Directory -Force (Split-Path $backup) | Out-Null
icacls $Daily /save $backup /T /C /Q | Out-Null
Write-Host "当てる前の権限: $backup"

$task = Get-ScheduledTask -TaskName $TaskName
$task.Principal.RunLevel = 'Highest'
Set-ScheduledTask -InputObject $task | Out-Null
Write-Host "タスク「$TaskName」を管理者の権限で動かすようにした。"

icacls $Daily /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
icacls $Daily /reset /T /C /Q | Out-Null   # 中のファイルとフォルダーに個別の権限を残さず、daily から引き継ぐだけにする
icacls $Daily /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)(F)' '*S-1-5-32-544:(OI)(CI)(F)' "*$($me.User.Value):(OI)(CI)(RX)" /C /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw '権限を当てられなかった。-Undo で戻して、Claude に知らせてください。' }

icacls $Daily
Write-Host '済んだ。確かめは Claude がふつうの権限で行う（書けない・読める）。'
