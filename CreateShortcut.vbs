Option Explicit
Dim shell, files, folder, shortcut
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
Set shortcut = shell.CreateShortcut(shell.SpecialFolders("Desktop") & "\PhoneScreen.lnk")
shortcut.TargetPath = folder & "\PhoneScreen.exe"
shortcut.WorkingDirectory = folder
shortcut.IconLocation = folder & "\PhoneScreen.exe,0"
shortcut.Save
MsgBox "PhoneScreen shortcut created on Desktop.", 64, "PhoneScreen"
