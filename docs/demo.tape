# Reproducible README demo for MEOW's one-command harness-engineering surface.
#
# The tape is a deterministic terminal preview so it never changes this checkout
# or starts a live Claude Agent SDK request during documentation generation.
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
Type 'Write-Host "> /meow:run Add CSV export"'
Enter
Show
Sleep 1100ms
Hide
Type 'Write-Host "01  context + templates       ready" -ForegroundColor Green'
Enter
Show
Sleep 1400ms
Hide
Type 'Write-Host "02  plan + Claude Agent SDK  ready" -ForegroundColor Green'
Enter
Show
Sleep 2200ms
Hide
Type 'Write-Host "03  checks + review          passed" -ForegroundColor Green'
Enter
Show
Sleep 2200ms
Hide
Type 'Write-Host "04  finished branch          csv-export" -ForegroundColor Green'
Enter
Show
Sleep 1600ms
Hide
Type 'Write-Host "finished branch ready to inspect or deliver" -ForegroundColor Cyan'
Enter
Show
Sleep 1500ms
