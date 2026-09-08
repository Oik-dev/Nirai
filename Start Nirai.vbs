Option Explicit

Dim shell, fso, root, pythonw, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pythonw) Then
    MsgBox "NiraiのProject Runtimeが見つかりません。" & vbCrLf & _
           "Setup Nirai Runtime.cmd を実行してください。", 16, "Nirai startup failed"
    WScript.Quit 2
End If

shell.CurrentDirectory = root
shell.Environment("PROCESS")("NIRAI_WORLD_DEV") = "0"
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & root & "\nirai_bootstrap.py" & Chr(34)
shell.Run command, 0, False
