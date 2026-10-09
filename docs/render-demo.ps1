param(
    [string]$Output = (Join-Path $PSScriptRoot "demo.gif")
)

$ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
if (-not $ffmpeg) {
    throw "ffmpeg is required. Install it, then run docs/render-demo.ps1 again."
}

$work = Join-Path $env:TEMP "meow-demo-render"
Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $work -Force | Out-Null

$states = @(
    @{ name = "01-request"; start = 0; end = 2.5; lines = @(
        "MEOW / HARNESS ENGINEERING", "", 'PS> meow queue "Add CSV export"', "",
        "Simple automation starts with one request."
    ) },
    @{ name = "02-queue"; start = 2.5; end = 5; lines = @(
        "MEOW / HARNESS ENGINEERING", "", "Queued task: Add CSV export", "",
        'PS> meow queue "Upgrade the API client"'
    ) },
    @{ name = "03-run"; start = 5; end = 7.5; lines = @(
        "MEOW / HARNESS ENGINEERING", "", "Queued task: Upgrade the API client", "",
        'PS> meow run "Add CSV export" --name csv-export'
    ) },
    @{ name = "04-harness"; start = 7.5; end = 11; lines = @(
        "MEOW / HARNESS ENGINEERING", "", "The MEOW harness carries the repetitive path:", "",
        "  templates + context", "  plan -> implement -> verify -> review", "  checkpoint -> finished branch"
    ) },
    @{ name = "05-done"; start = 11; end = 14; lines = @(
        "MEOW / HARNESS ENGINEERING", "", "Ready for inspection or delivery.", "",
        "One simple command. Done."
    ) }
)

$font = "C:/Windows/Fonts/consola.ttf"
if (-not (Test-Path $font)) {
    $font = "C:/Windows/Fonts/cour.ttf"
}

$filters = @(
    "drawbox=x=0:y=0:w=1200:h=650:color=0x1f2430:t=fill",
    "drawbox=x=0:y=0:w=1200:h=58:color=0x343a4d:t=fill",
    "drawbox=x=30:y=20:w=14:h=14:color=0xff5f56:t=fill",
    "drawbox=x=52:y=20:w=14:h=14:color=0xffbd2e:t=fill",
    "drawbox=x=74:y=20:w=14:h=14:color=0x27c93f:t=fill"
)

foreach ($state in $states) {
    $fontPath = $font.Replace(":", "\:")
    for ($index = 0; $index -lt $state.lines.Count; $index++) {
        $line = $state.lines[$index].Replace(":", "\:").Replace("'", "\'")
        $y = 100 + ($index * 42)
        $filters += "drawtext=fontfile='$fontPath':text='$line':fontcolor=0xf2f4fb:fontsize=29:x=72:y=${y}:enable='between(t,$($state.start),$($state.end))'"
    }
}

$filterGraph = $filters -join ","
$outputDirectory = Split-Path -Parent $Output
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

& $ffmpeg -y -f lavfi -i "color=c=0x1f2430:s=1200x650:r=10:d=14" -vf $filterGraph -loop 0 $Output
if ($LASTEXITCODE -ne 0) {
    throw "ffmpeg could not render $Output"
}

Write-Host "Rendered $Output"
