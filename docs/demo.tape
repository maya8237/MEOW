# Reproducible README demo for MEOW's one-command harness-engineering surface.
#
# The tape uses a disposable temporary fixture so it never changes this checkout.
# It demonstrates the real queue command and the real run entry point without
# starting a live Claude Agent SDK request during documentation generation.
# Prefer rendering from the repository root with:
#   vhs docs/demo.tape
#
# On Windows, if the VHS/ttyd capture produces only the terminal chrome, use
# the deterministic fallback instead:
#   pwsh -ExecutionPolicy Bypass -File docs/render-demo.ps1
# The checked-in GIF is labeled as a harness preview because it does not run
# a live model request while the documentation asset is being generated.

Output docs/demo.gif

Set Shell "pwsh"
Set FontSize 20
Set FontFamily "Consolas"
Set Width 1200
Set Height 650
Set Theme "Dracula"
Set Padding 24
Set WindowBar Colorful
Set TypingSpeed 48ms

Hide
Type '$demo = Join-Path $env:TEMP "meow-readme-demo"; Remove-Item -LiteralPath $demo -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Path $demo -Force | Out-Null'
Enter
Sleep 500ms
Show

Type '& "C:\Users\m\AppData\Roaming\Python\Python314\Scripts\meow.exe" queue "Add CSV export" --name csv-export --working-dir $demo'
Sleep 500ms
Enter
Sleep 1800ms

Type '& "C:\Users\m\AppData\Roaming\Python\Python314\Scripts\meow.exe" queue "Upgrade the API client" --name api-client --working-dir $demo'
Sleep 500ms
Enter
Sleep 1800ms

Type '& "C:\Users\m\AppData\Roaming\Python\Python314\Scripts\meow.exe" run --help'
Sleep 500ms
Enter
Sleep 4500ms
