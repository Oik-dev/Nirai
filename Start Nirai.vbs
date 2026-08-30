Option Explicit

Dim shell
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "D:\Products\Nirai"
shell.Environment("PROCESS")("NIRAI_WORLD_DEV") = "0"
shell.Run "pythonw.exe -m core", 0, False
