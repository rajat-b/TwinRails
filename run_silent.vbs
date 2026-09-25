' Silent background launcher for TwinRails.
' Resolve Python from the user's PATH or the Windows Python Launcher instead
' of assuming the author's installation folder.

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strPath = fso.GetParentFolderName(WScript.ScriptFullName)

pythonPath = FindPythonw()
If pythonPath = "" Then
    MsgBox "Python 3.10 or later was not found. Install it from python.org, then run start_widget.bat once to install this widget's packages.", vbExclamation, "TwinRails"
    WScript.Quit 1
End If

pythonConsolePath = fso.BuildPath(fso.GetParentFolderName(pythonPath), "python.exe")
If Not fso.FileExists(pythonConsolePath) Then
    MsgBox "Python was found, but its python.exe companion is missing. Run start_widget.bat after repairing your Python installation.", vbExclamation, "TwinRails"
    WScript.Quit 1
End If

If Not DependenciesInstalled(pythonConsolePath) Then
    MsgBox "This widget's packages are not installed yet. Double-click start_widget.bat once; it installs them and then starts the widget.", vbExclamation, "TwinRails"
    WScript.Quit 1
End If

cmd = """" & pythonPath & """ """ & strPath & "\main.py"" --bar"
WshShell.Run cmd, 0, False
Set WshShell = Nothing
Set fso = Nothing

Function FindPythonw()
    On Error Resume Next
    Set result = WshShell.Exec("where pythonw.exe")
    pathFromPath = Trim(result.StdOut.ReadLine)
    If fso.FileExists(pathFromPath) And HasSupportedPython(pathFromPath) Then
        FindPythonw = pathFromPath
        Exit Function
    End If

    Set result = WshShell.Exec("py -3 -c ""import pathlib, sys; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))""")
    pathFromLauncher = Trim(result.StdOut.ReadLine)
    If fso.FileExists(pathFromLauncher) And HasSupportedPython(pathFromLauncher) Then
        FindPythonw = pathFromLauncher
    Else
        FindPythonw = ""
    End If
End Function

Function HasSupportedPython(pythonwExe)
    consoleExe = fso.BuildPath(fso.GetParentFolderName(pythonwExe), "python.exe")
    If Not fso.FileExists(consoleExe) Then
        HasSupportedPython = False
        Exit Function
    End If

    versionCommand = """" & consoleExe & """ -c ""import sys; raise SystemExit(sys.version_info < (3, 10))"""
    Set result = WshShell.Exec(versionCommand)
    Do While result.Status = 0
        WScript.Sleep 50
    Loop
    HasSupportedPython = (result.ExitCode = 0)
End Function

Function DependenciesInstalled(pythonExe)
    checkCommand = """" & pythonExe & """ -c ""import sys; assert sys.version_info >= (3, 10); import pystray, PIL, curl_cffi, requests, urllib3, psutil"""
    Set result = WshShell.Exec(checkCommand)
    Do While result.Status = 0
        WScript.Sleep 50
    Loop
    DependenciesInstalled = (result.ExitCode = 0)
End Function
