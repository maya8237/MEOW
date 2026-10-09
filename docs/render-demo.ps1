param(
    [string]$Output = (Join-Path $PSScriptRoot "demo.gif")
)

# Purpose: show one meow run request as a normal terminal session.
# This is a deterministic preview, not a live model transcript.
$duration = 10
$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg) {
    throw "ffmpeg is required. Install it, then run docs/render-demo.ps1 again."
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
    "drawbox=x=0:y=0:w=1200:h=650:color=0x111318:t=fill",
    "drawbox=x=0:y=0:w=1200:h=44:color=0x1a1e27:t=fill",
    "drawbox=x=0:y=43:w=1200:h=1:color=0x343a46:t=fill"
)

$filters += New-TextFilter -Text "PowerShell" -Color "a9b2c2" -Size 16 -X 28 -Y 14
$filters += New-TextFilter -Text "C:\project" -Color "7f8898" -Size 16 -X 1030 -Y 14
$filters += New-TextFilter -Text "PS C:\project>" -Color "c7a7ff" -Size 23 -X 56 -Y 76 -Bold
$filters += New-TextFilter -Text 'meow run "Add CSV export" --name csv-export' -Color "f4f7fb" -Size 24 -X 252 -Y 76

$rows = @(
    @{ label = "context + templates"; y = 160; start = 1.0; activeEnd = 2.2; done = "ready" },
    @{ label = "plan + Claude Agent SDK"; y = 204; start = 2.4; activeEnd = 4.2; done = "ready" },
    @{ label = "checks + review"; y = 248; start = 4.4; activeEnd = 6.2; done = "passed" },
    @{ label = "finished branch"; y = 292; start = 6.4; activeEnd = 7.8; done = "csv-export" }
)

foreach ($row in $rows) {
    $active = "between(t,$($row.start),$($row.activeEnd))"
    $done = "gte(t,$($row.activeEnd))"
    $filters += New-TextFilter -Text ("  " + $row.label) -Color "f4f7fb" -Size 22 -X 56 -Y $row.y -Enable "gte(t,$($row.start))"
    $filters += New-TextFilter -Text "..." -Color "a9b2c2" -Size 22 -X 620 -Y $row.y -Enable $active
    $filters += New-TextFilter -Text $row.done -Color "86e1a7" -Size 22 -X 620 -Y $row.y -Enable $done -Bold
}

$filters += New-TextFilter -Text "Done. Ready to inspect or deliver." -Color "86e1a7" -Size 23 -X 56 -Y 360 -Enable "gte(t,8.0)" -Bold
$filters += New-TextFilter -Text "PS C:\project>" -Color "c7a7ff" -Size 23 -X 56 -Y 420 -Enable "gte(t,8.0)" -Bold

$filterGraph = $filters -join ","
$outputDirectory = Split-Path -Parent $Output
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$video = Join-Path $work "demo.mp4"
$palette = Join-Path $work "palette.png"

& $ffmpeg -y -f lavfi -i "color=c=0x111318:s=1200x650:r=12:d=$duration" -vf $filterGraph -t $duration -an -c:v libx264 -pix_fmt yuv420p $video
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
