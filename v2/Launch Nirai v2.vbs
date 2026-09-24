Option Explicit

Dim shell, fso, root, electron, comspec, buildCommand, launchCommand, code
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
electron = root & "\node_modules\electron\dist\electron.exe"
comspec = shell.ExpandEnvironmentStrings("%ComSpec%")

If Not fso.FileExists(electron) Then
    MsgBox "Nirai v2 Electron runtime was not found." & vbCrLf & _
           "Run npm install in " & root & " first.", 16, "Nirai v2 startup failed"
    WScript.Quit 2
End If

shell.CurrentDirectory = root
buildCommand = Chr(34) & comspec & Chr(34) & " /d /c npm.cmd run build"
code = shell.Run(buildCommand, 0, True)

If code <> 0 Then
    MsgBox "Nirai v2 build failed." & vbCrLf & _
           "Run npm run build in " & root & " to see the error.", 16, "Nirai v2 startup failed"
    WScript.Quit code
End If

launchCommand = Chr(34) & electron & Chr(34) & " . --restart"
shell.Run launchCommand, 1, False
