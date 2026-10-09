param(
    [string]$Output = (Join-Path $PSScriptRoot "demo.gif")
)

$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg) {
    throw "ffmpeg is required. Install it, then run docs/render-demo.ps1 again."
}

$logo = Join-Path (Split-Path -Parent $PSScriptRoot) "logo.jfif"
if (-not (Test-Path -LiteralPath $logo)) {
    throw "MEOW's logo.jfif is required to render the demo."
}

$work = Join-Path $env:TEMP "meow-demo-render"
Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $work -Force | Out-Null

$states = @(
    @{ start = 0; end = 2.8; lines = @(
        @{ text = "One request. A finished branch."; color = "f4efe6"; size = 44; y = 235 },
        @{ text = "/meow:run Add CSV export"; color = "b088ff"; size = 27; y = 315 },
        @{ text = "Tell MEOW the outcome."; color = "b9afc4"; size = 25; y = 395 }
    ) },
    @{ start = 2.8; end = 5.6; lines = @(
        @{ text = "The harness carries the repetitive path."; color = "f4efe6"; size = 38; y = 235 },
        @{ text = "context + templates"; color = "79e0c8"; size = 27; y = 315 },
        @{ text = "state + worktree"; color = "b088ff"; size = 27; y = 365 },
        @{ text = "feedback + delivery"; color = "ff896f"; size = 27; y = 415 }
    ) },
    @{ start = 5.6; end = 8.4; lines = @(
        @{ text = "Claude Agent SDK"; color = "f4efe6"; size = 44; y = 235 },
        @{ text = "explore -> plan -> implement"; color = "b088ff"; size = 27; y = 315 },
        @{ text = "verify -> review -> respond"; color = "79e0c8"; size = 27; y = 365 },
        @{ text = "the session keeps moving"; color = "b9afc4"; size = 25; y = 430 }
    ) },
    @{ start = 8.4; end = 11.2; lines = @(
        @{ text = "You keep the product decision."; color = "f4efe6"; size = 40; y = 235 },
        @{ text = "MEOW keeps the rest in motion."; color = "79e0c8"; size = 28; y = 320 },
        @{ text = "one request -> one finished branch"; color = "b9afc4"; size = 25; y = 395 }
    ) },
    @{ start = 11.2; end = 14; lines = @(
        @{ text = "OK  verified branch: csv-export"; color = "79e0c8"; size = 34; y = 235 },
        @{ text = "Inspect it. Resume it. Ship it."; color = "f4efe6"; size = 30; y = 320 },
        @{ text = "One simple command. Done."; color = "b088ff"; size = 38; y = 410 }
    ) }
)

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

$fontPath = $font.Replace(":", "\:")
$fontBoldPath = $fontBold.Replace(":", "\:")
$filters = @(
    "drawbox=x=0:y=0:w=1200:h=650:color=0x111016:t=fill",
    "drawbox=x=0:y=0:w=1200:h=58:color=0x1c1824:t=fill",
    "drawbox=x=0:y=57:w=1200:h=1:color=0x463950:t=fill",
    "drawbox=x=32:y=21:w=14:h=14:color=0xff896f:t=fill",
    "drawbox=x=54:y=21:w=14:h=14:color=0xf4d06f:t=fill",
    "drawbox=x=76:y=21:w=14:h=14:color=0x79e0c8:t=fill",
    "drawbox=x=64:y=72:w=112:h=112:color=0x30213e:t=fill",
    "drawtext=fontfile='$fontBoldPath':text='MEOW':fontcolor=0xf4efe6:fontsize=32:x=198:y=91",
    "drawtext=fontfile='$fontPath':text='harness engineering / Claude Code':fontcolor=0xb088ff:fontsize=18:x=198:y=132",
    "drawtext=fontfile='$fontPath':text='meow / run':fontcolor=0xb9afc4:fontsize=20:x=1010:y=94",
    "drawbox=x=72:y=560:w=1056:h=1:color=0x463950:t=fill",
    "drawtext=fontfile='$fontBoldPath':text='simple automation':fontcolor=0xf4efe6:fontsize=18:x=72:y=584",
    "drawtext=fontfile='$fontPath':text='context   state   feedback   delivery':fontcolor=0xb9afc4:fontsize=18:x=820:y=584"
)

foreach ($state in $states) {
    foreach ($line in $state.lines) {
        $text = Convert-TextForFfmpeg $line.text
        $filters += "drawtext=fontfile='$fontPath':text='$text':fontcolor=0x$($line.color):fontsize=$($line.size):x=72:y=$($line.y):enable='between(t,$($state.start),$($state.end))'"
    }
}

$filterGraph = $filters -join ","
$outputDirectory = Split-Path -Parent $Output
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$video = Join-Path $work "demo.mp4"
$palette = Join-Path $work "palette.png"
$complex = "[0:v]$filterGraph[frame];[1:v]scale=96:96:force_original_aspect_ratio=decrease[mark];[frame][mark]overlay=x=72:y=80:shortest=1[out]"

& $ffmpeg -y -f lavfi -i "color=c=0x111016:s=1200x650:r=12:d=14" -loop 1 -i $logo -filter_complex $complex -map "[out]" -t 14 -an -c:v libx264 -pix_fmt yuv420p $video
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
