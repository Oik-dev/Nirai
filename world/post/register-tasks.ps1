# 郵便局と、Holoへのトンネルを、Master のログオン時に起こすタスクを登録する。
# Master のふつうの権限の PowerShell で1回だけ実行する（管理者は要らない）。戻すときは -Undo。
#
# - 「Nirai Post」：郵便局（world\post\server.ts）。記録は world\runtime\post.log
# - 「Nirai Tunnel」：ChatGPT（Holo）から郵便局へのトンネル。保存された設定（~\.config\tunnel-client\nirai.yaml）で動く。
#   郵便局が聞き始めるまで待ってからつなぐ（MCP_STARTUP_WAIT_TIMEOUT）
# どちらも、同時に1つだけ動かし（同じトンネルは1つしか動かせない）、落ちたら1分後に起こし直す。

param([switch]$Undo)

$ErrorActionPreference = 'Stop'
$names = 'Nirai Post', 'Nirai Tunnel'

if ($Undo) {
  foreach ($name in $names) { Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue }
  Write-Host 'タスクを外した。'
  return
}

$world = 'D:\Products\Nirai\world'
$node = 'C:\Program Files\nodejs\node.exe'
$tunnel = Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\tunnel-client\tunnel-client.exe'
$conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
New-Item -ItemType Directory -Force (Join-Path $world 'runtime') | Out-Null

# 手で起こしたトンネルがあれば止める
& $tunnel runtimes stop nirai *> $null

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

$post = New-ScheduledTaskAction -Execute $conhost -WorkingDirectory $world `
  -Argument "--headless cmd.exe /c `"`"$node`" --no-warnings post\server.ts >> runtime\post.log 2>&1`""
$tun = New-ScheduledTaskAction -Execute $conhost `
  -Argument "--headless cmd.exe /c `"set MCP_STARTUP_WAIT_TIMEOUT=120s&& `"$tunnel`" run --profile nirai`""

Register-ScheduledTask -TaskName 'Nirai Post' -Action $post -Trigger $trigger -Settings $settings -Force `
  -Description 'Nirai: 住人どうしの手紙を届け、住人を起こす郵便局（D:\Products\Nirai\world\post）' | Out-Null
Register-ScheduledTask -TaskName 'Nirai Tunnel' -Action $tun -Trigger $trigger -Settings $settings -Force `
  -Description 'Nirai: ChatGPT（Holo）から郵便局へのトンネル（tunnel-client、設定は nirai）' | Out-Null
Write-Host 'タスク「Nirai Post」「Nirai Tunnel」を登録した。次のログオンから自動で起きる。'
Write-Host 'いま動いている郵便局とトンネルを止めてから、Start-ScheduledTask で起こすと、すぐに切り替わる。'
