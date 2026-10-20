# irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.ps1 | iex
& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $temporary = $null
    Write-Host @'

       /\_/\
      ( o.o )   meow
       > ^ <
      /     \
     (       )__
      \_____/___)

'@
    try {
        $python = $env:MEOW_PYTHON
        if (-not $python) {
            $python = (Get-Command python -ErrorAction SilentlyContinue).Source
        }
        if (-not $python) { throw 'Python 3.12+ is required; set MEOW_PYTHON to its path.' }
        & $python -c 'import sys;sys.exit(sys.version_info < (3,12))'
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required; set MEOW_PYTHON to its path.' }
        $script = $null
        if ($PSScriptRoot) { $script = Join-Path $PSScriptRoot 'uninstall.py' }
        if (-not $script -or -not (Test-Path -LiteralPath $script)) {
            $temporary = [IO.Path]::GetTempFileName()
            Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.py' -OutFile $temporary
            $script = $temporary
        }
        & $python $script
        if ($LASTEXITCODE -eq 130) { return }
        if ($LASTEXITCODE -ne 0) { throw 'MEOW uninstall did not finish; see the error above.' }
    } catch {
        Write-Host "MEOW uninstall failed: $($_.Exception.Message)" -ForegroundColor Red
        $global:LASTEXITCODE = 1
        if ($PSCommandPath) { exit 1 }
    } finally {
        if ($temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}
