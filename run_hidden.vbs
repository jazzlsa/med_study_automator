Set WshShell = CreateObject("WScript.Shell")
If WScript.Arguments.Count > 0 Then
    Dim fullCommand, i
    fullCommand = ""
    For i = 0 To WScript.Arguments.Count - 1
        fullCommand = fullCommand & WScript.Arguments(i) & " "
    Next
    WshShell.Run Trim(fullCommand), 0, False
End If
