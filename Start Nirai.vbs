Option Explicit

Dim shell, fso, root, launcher
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = root & "\v2\Launch Nirai v2.vbs"

If Not fso.FileExists(launcher) Then
    MsgBox "Nirai v2 launcher was not found." & vbCrLf & launcher, 16, "Nirai startup failed"
    WScript.Quit 2
End If

shell.CurrentDirectory = root & "\v2"
shell.Run Chr(34) & launcher & Chr(34), 1, False
