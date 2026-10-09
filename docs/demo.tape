# Reproducible README demo for MEOW's ordinary CLI surface.
# Setup is hidden; the visible recording is a normal Windows terminal session.

Output docs/demo.gif

Set Shell "cmd"
Set FontSize 16
Set FontFamily "Cascadia Mono"
Set Width 1100
Set Height 480
Set Theme "GitHub Dark"
Set Padding 16
Set TypingSpeed 35ms
Set WindowBar "Colorful"
Set BorderRadius 8

# Put a deterministic demo command on PATH without running a live model request.
Hide
Type 'set "MEOW_DEMO_DIR=%TEMP%\meow-demo-bin"'
Enter
Type 'if not exist "%MEOW_DEMO_DIR%" mkdir "%MEOW_DEMO_DIR%"'
Enter
Type 'copy /Y docs\demo-session.cmd "%MEOW_DEMO_DIR%\meow.cmd" >nul'
Enter
Type 'set "PATH=%MEOW_DEMO_DIR%;%PATH%"'
Enter
Type 'prompt C:\project$G'
Enter
Type 'title PowerShell'
Enter
Show

Type 'meow run "Add CSV export" --name csv-export'
Enter
Sleep 7s
