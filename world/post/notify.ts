// Masterへ、郵便局から直接知らせる（Windowsの通知）。ふだんの知らせはHoloが伝えるので、これはHolo自身が応えないときだけ使う。
// 通知の仕組み（WinRT）は Windows PowerShell 5.1 からしか呼べないので、powershell を使う。
// 文字は環境変数で渡す（コマンドの文字列に埋め込まない）。

import { spawnSync } from "node:child_process";

const SCRIPT = [
  "$ErrorActionPreference = 'Stop'",
  "[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]",
  "[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]",
  "$e = { param($s) [Security.SecurityElement]::Escape($s) }",
  "$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()",
  "$xml.LoadXml(\"<toast><visual><binding template='ToastGeneric'><text>$(& $e $env:NIRAI_TITLE)</text><text>$(& $e $env:NIRAI_BODY)</text></binding></visual></toast>\")",
  // 登録なしで通知を出せる、Windows PowerShell のアプリの名前
  "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'",
  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))",
].join("\n");

/** 通知に載せられる文字にする。制御文字（NULなど）は環境変数にもXMLにも入らないので、空白に替える。 */
export function noticeText(text: string): string {
  return text.replace(/[\x00-\x1f\x7f]+/g, " ").trim();
}

/** 通知を出せたら true。出せなければ false（投げない。知らせは次の見直しでまた試す）。 */
export function notifyMaster(title: string, body: string): boolean {
  try {
    const result = spawnSync("powershell", ["-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(SCRIPT, "utf16le").toString("base64")], {
      windowsHide: true, env: { ...process.env, NIRAI_TITLE: noticeText(title), NIRAI_BODY: noticeText(body) }, encoding: "utf8",
    });
    if (result.status !== 0) console.error(`notify failed: ${result.error?.message ?? result.stderr.trim().split("\n").at(-1)}`);
    return result.status === 0;
  } catch (error) {
    console.error(`notify failed: ${(error as Error).message}`);
    return false;
  }
}
