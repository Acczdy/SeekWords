Option Explicit
Dim shell, fs, folder, command, item, result
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
If fs.FileExists(fs.BuildPath(folder, "SeekWords.exe")) Then
    command = QuoteArg(fs.BuildPath(folder, "SeekWords.exe"))
Else
    command = "pyw.exe -3 " & QuoteArg(fs.BuildPath(folder, "seekwords.pyw"))
End If
For Each item In WScript.Arguments
    command = command & " " & QuoteArg(item)
Next
On Error Resume Next
result = shell.Run(command, 1, False)
If Err.Number <> 0 Then
    Err.Clear
    command = "pythonw.exe " & QuoteArg(fs.BuildPath(folder, "seekwords.pyw"))
    For Each item In WScript.Arguments
        command = command & " " & QuoteArg(item)
    Next
    result = shell.Run(command, 1, False)
    If Err.Number <> 0 Then MsgBox "Please run SeekWords.exe, or install Python 3.10+ with Tcl/Tk.", 16, "SeekWords"
End If

Function QuoteArg(value)
    Dim i, c, slashes, output
    output = Chr(34)
    slashes = 0
    For i = 1 To Len(value)
        c = Mid(value, i, 1)
        If c = "\" Then
            slashes = slashes + 1
        Else
            If c = Chr(34) Then
                output = output & String(slashes * 2 + 1, "\") & c
            Else
                output = output & String(slashes, "\") & c
            End If
            slashes = 0
        End If
    Next
    QuoteArg = output & String(slashes * 2, "\") & Chr(34)
End Function
