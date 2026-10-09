param(
    [string]$Output = (Join-Path $PSScriptRoot "demo.gif")
)

# Purpose: show one meow run request as a compact, ordinary terminal session.
# This is a deterministic preview, not a live model transcript.
$duration = 10
$width = 1200
$height = 500
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
    "drawbox=x=0:y=0:w=${width}:h=${height}:color=0x111318:t=fill",
    "drawbox=x=0:y=0:w=${width}:h=42:color=0x161b22:t=fill",
    "drawbox=x=0:y=41:w=${width}:h=1:color=0x30363d:t=fill"
)

$filters += New-TextFilter -Text "●" -Color "ff7b72" -Size 16 -X 20 -Y 10
$filters += New-TextFilter -Text "●" -Color "e3b341" -Size 16 -X 42 -Y 10
$filters += New-TextFilter -Text "●" -Color "7ee787" -Size 16 -X 64 -Y 10
$filters += New-TextFilter -Text "PowerShell" -Color "8b949e" -Size 16 -X 96 -Y 13
$filters += New-TextFilter -Text "PS C:/project>" -Color "79c0ff" -Size 22 -X 56 -Y 76 -Bold

$command = 'meow run "Add CSV export" --name csv-export'
$filters += New-TextFilter -Text $command -Color "f0f6fc" -Size 22 -X 250 -Y 76

$rows = @(
    @{ label = "context + templates"; y = 145; start = 1.45; activeEnd = 2.45; done = "ready" },
    @{ label = "plan + Claude Agent SDK"; y = 187; start = 2.65; activeEnd = 4.15; done = "ready" },
    @{ label = "checks + review"; y = 229; start = 4.35; activeEnd = 5.85; done = "passed" },
    @{ label = "finished branch"; y = 271; start = 6.05; activeEnd = 7.25; done = "csv-export" }
)

foreach ($row in $rows) {
    $visible = "gte(t,$($row.start))"
    $active = "between(t,$($row.start),$($row.activeEnd))"
    $done = "gte(t,$($row.activeEnd))"
    $filters += New-TextFilter -Text ("  " + $row.label) -Color "c9d1d9" -Size 21 -X 56 -Y $row.y -Enable $visible
    $filters += New-TextFilter -Text "..." -Color "8b949e" -Size 21 -X 650 -Y $row.y -Enable $active
    $filters += New-TextFilter -Text $row.done -Color "7ee787" -Size 21 -X 650 -Y $row.y -Enable $done -Bold
}

$filters += New-TextFilter -Text "Done. Ready to inspect or deliver." -Color "7ee787" -Size 22 -X 56 -Y 340 -Enable "gte(t,7.8)" -Bold
$filters += New-TextFilter -Text "PS C:/project>" -Color "79c0ff" -Size 22 -X 56 -Y 395 -Enable "gte(t,7.8)" -Bold

$filterGraph = $filters -join ","
$outputDirectory = Split-Path -Parent $Output
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$video = Join-Path $work "demo.mp4"
$palette = Join-Path $work "palette.png"
$complex = "[0:v]$filterGraph[out]"

& $ffmpeg -y -f lavfi -i "color=c=0x111318:s=${width}x${height}:r=12:d=$duration" -filter_complex $complex -map "[out]" -t $duration -an -c:v libx264 -pix_fmt yuv420p $video
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
