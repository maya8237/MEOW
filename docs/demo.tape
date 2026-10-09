# Reproducible README demo for MEOW's ordinary CLI surface.
# The visible transcript is deterministic and does not start a live model
# request while documentation assets are being generated.
# Prefer rendering from the repository root with:
#   vhs docs/demo.tape
#
# The checked-in fallback is docs/render-demo.ps1 for environments without VHS.

Output docs/demo.gif

Set Shell "pwsh"
Set FontSize 18
Set FontFamily "Consolas"
Set Width 1200
Set Height 650
Set Theme "Dracula"
Set Padding 24
Set TypingSpeed 30ms
Set WindowBar "Colorful"
Set BorderRadius 8

Hide
Type "Write-Host 'PS C:\project> meow run Add-CSV-export --name csv-export'"
Enter
Show
Sleep 1s

Hide
Type "Write-Host '  context + templates                 ready' -ForegroundColor Green"
Enter
Show
Sleep 1200ms

Hide
Type "Write-Host '  plan + Claude Agent SDK             ready' -ForegroundColor Green"
Enter
Show
Sleep 1800ms

Hide
Type "Write-Host '  checks + review                     passed' -ForegroundColor Green"
Enter
Show
Sleep 1800ms

Hide
Type "Write-Host '  finished branch                     csv-export' -ForegroundColor Green"
Enter
Show
Sleep 1400ms

Hide
Type "Write-Host 'Done. Ready to inspect or deliver.' -ForegroundColor Green"
Enter
Show
Sleep 1800ms
