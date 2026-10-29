# irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.ps1 | iex
[CmdletBinding()]
param([switch]${non-interactive})

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
    # Newest Python 3.12+ from `where python` and the py launcher (or MEOW_PYTHON),
    # found as install.ps1 does; venvs resolve to their base interpreter.
    function Find-Python {
        $probe = 'import os,sys;v=sys.version_info;print(v[0]*10000+v[1]*100+v[2],os.path.realpath(sys._base_executable))'
        $candidates = @()
        if ($env:MEOW_PYTHON) {
            $candidates = @($env:MEOW_PYTHON)
        } else {
            $ErrorActionPreference = 'Continue'
            $candidates += @(& where.exe python 2>$null)
            foreach ($line in @(& py -0p 2>$null)) {
                if ("$line" -match '[A-Za-z]:\\.*\.exe\s*$') { $candidates += $Matches[0].Trim() }
            }
        }
        $best = $null
        foreach ($candidate in $candidates) {
            $ErrorActionPreference = 'Continue'
            try { $found = "$(& $candidate -c $probe 2>$null)" } catch { continue }
            if ($LASTEXITCODE -ne 0 -or $found -notmatch '^(\d+) (.+)$') { continue }
            if ([int]$Matches[1] -ge 31200 -and (-not $best -or [int]$Matches[1] -gt $best.Key)) {
                $best = @{ Key = [int]$Matches[1]; Path = $Matches[2] }
            }
        }
        if (-not $best) { throw 'Python 3.12+ is required; set MEOW_PYTHON to its path.' }
        return $best.Path
    }
    try {
        Write-Host 'Locating Python 3.12+...'
        $python = Find-Python
        $script = $null
        if ($PSScriptRoot) { $script = Join-Path $PSScriptRoot 'uninstall.py' }
        if (-not $script -or -not (Test-Path -LiteralPath $script)) {
            Write-Host 'Downloading the uninstaller...'
            $temporary = [IO.Path]::GetTempFileName()
            Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/uninstall.py' -OutFile $temporary
            $script = $temporary
        }
        $helperArgs = @()
        if (${non-interactive}) { $helperArgs += '--non-interactive' }
        & $python $script @helperArgs
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
