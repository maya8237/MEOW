param(
    [string]$Output = (Join-Path $PSScriptRoot "demo.gif")
)

# Purpose: show one /meow:run request moving through the harness to a branch.
# This is a deterministic preview, not a live model transcript.
$duration = 11
$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg) {
    throw "ffmpeg is required. Install it, then run docs/render-demo.ps1 again."
}

$logo = Join-Path (Split-Path -Parent $PSScriptRoot) "logo.png"
if (-not (Test-Path -LiteralPath $logo)) {
    throw "MEOW's logo.png is required to render the demo."
}

$work = Join-Path $env:TEMP "meow-demo-render"
Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $work -Force | Out-Null

$font = "C:/Windows/Fonts/consola.ttf"
$fontBold = "C:/Windows/Fonts/consolab.ttf"
if (-not (Test-Path -LiteralPath $font)) {
    $font = "C:/Windows/Fonts/cour.ttf"
}
if (-not (Test-Path -LiteralPath $fontBold)) {
    $fontBold = $font
}

function Convert-TextForFfmpeg([string]$Text) {
    return $Text.Replace("\", "\\").Replace(":", "\:").Replace("'", "\'").Replace(",", "\,").Replace("%", "\%")
}

function New-TextFilter {
    param(
        [string]$Text,
        [string]$Color,
        [int]$Size,
        [int]$X,
        [int]$Y,
        [string]$Enable,
        [switch]$Bold
    )

    $fontPath = if ($Bold) { $fontBold.Replace(":", "\:") } else { $font.Replace(":", "\:") }
    $safeText = Convert-TextForFfmpeg $Text
    $enableClause = if ([string]::IsNullOrEmpty($Enable)) { "" } else { ":enable='$Enable'" }
    return "drawtext=fontfile='$fontPath':text='$safeText':fontcolor=0x${Color}:fontsize=${Size}:x=${X}:y=${Y}${enableClause}"
}

$filters = @(
    "drawbox=x=0:y=0:w=1200:h=650:color=0x0f0d13:t=fill",
    "drawbox=x=60:y=150:w=1080:h=370:color=0x17131f:t=fill",
    "drawbox=x=60:y=150:w=1080:h=370:color=0x463950:t=2",
    "drawbox=x=88:y=232:w=1024:h=1:color=0x463950:t=fill",
    "drawbox=x=88:y=307:w=1024:h=1:color=0x2b2535:t=fill",
    "drawbox=x=88:y=357:w=1024:h=1:color=0x2b2535:t=fill",
    "drawbox=x=88:y=407:w=1024:h=1:color=0x2b2535:t=fill",
    "drawbox=x=72:y=576:w=1056:h=1:color=0x463950:t=fill"
)

$filters += New-TextFilter -Text "MEOW" -Color "f4efe6" -Size 32 -X 184 -Y 76 -Bold
$filters += New-TextFilter -Text "harness engineering / Claude Code" -Color "b088ff" -Size 18 -X 184 -Y 114
$filters += New-TextFilter -Text "Claude Agent SDK" -Color "b9afc4" -Size 18 -X 900 -Y 176
$filters += New-TextFilter -Text "> /meow:run Add CSV export" -Color "f4efe6" -Size 26 -X 96 -Y 188

$rows = @(
    @{ number = "01"; label = "context + templates"; y = 278; start = 1.1; activeEnd = 2.5; active = "loading"; done = "ready" },
    @{ number = "02"; label = "plan + Claude Agent SDK"; y = 328; start = 2.5; activeEnd = 5.1; active = "working"; done = "ready" },
    @{ number = "03"; label = "checks + review"; y = 378; start = 5.1; activeEnd = 7.7; active = "running"; done = "passed" },
    @{ number = "04"; label = "finished branch"; y = 428; start = 7.7; activeEnd = 9.3; active = "creating"; done = "csv-export" }
)

foreach ($row in $rows) {
    $visible = "between(t,$($row.start),$duration)"
    $active = "between(t,$($row.start),$($row.activeEnd))"
    $done = "between(t,$($row.activeEnd),$duration)"
    $filters += New-TextFilter -Text $row.number -Color "b088ff" -Size 20 -X 96 -Y $row.y -Enable $visible -Bold
    $filters += New-TextFilter -Text $row.label -Color "f4efe6" -Size 24 -X 154 -Y $row.y -Enable $visible
    $filters += New-TextFilter -Text $row.active -Color "ff896f" -Size 20 -X 900 -Y $row.y -Enable $active
    $filters += New-TextFilter -Text $row.done -Color "79e0c8" -Size 20 -X 900 -Y $row.y -Enable $done -Bold
}

$filters += New-TextFilter -Text "finished branch ready to inspect or deliver" -Color "79e0c8" -Size 22 -X 96 -Y 475 -Enable "between(t,9.3,$duration)"
$filters += New-TextFilter -Text "one request. one verified branch." -Color "b9afc4" -Size 18 -X 72 -Y 600

$filterGraph = $filters -join ","
$outputDirectory = Split-Path -Parent $Output
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$video = Join-Path $work "demo.mp4"
$palette = Join-Path $work "palette.png"
$complex = "[0:v]$filterGraph[frame];[1:v]scale=90:90:force_original_aspect_ratio=decrease[mark];[frame][mark]overlay=x=72:y=48:shortest=1[out]"

& $ffmpeg -y -f lavfi -i "color=c=0x111016:s=1200x650:r=12:d=$duration" -loop 1 -i $logo -filter_complex $complex -map "[out]" -t $duration -an -c:v libx264 -pix_fmt yuv420p $video
if ($LASTEXITCODE -ne 0) {
    throw "ffmpeg could not render the demo video."
}

& $ffmpeg -y -i $video -vf "fps=12,scale=1000:-1:flags=lanczos,palettegen=max_colors=128" $palette
if ($LASTEXITCODE -ne 0) {
    throw "ffmpeg could not build the demo palette."
}

& $ffmpeg -y -i $video -i $palette -lavfi "fps=12,scale=1000:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=sierra2_4a" -loop 0 $Output
if ($LASTEXITCODE -ne 0) {
    throw "ffmpeg could not render $Output"
}

Write-Host "Rendered $Output"
