# irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex
[CmdletBinding()]
param([switch]${non-interactive}, [switch]${no-setup}, [switch]$setup, [switch]$bun, [switch]${install-missing}, [switch]${latest-pypi}, [switch]$beta, [string]$version = '', [string]$mcp = '')

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
    # Newest Python 3.12+ from `where python` and the py launcher (or MEOW_PYTHON).
    # Venvs resolve to their base interpreter; the Store placeholder fails the probe.
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
        if (-not $best) {
            throw 'MEOW needs Python 3.12 or newer. Install it (winget install Python.Python.3.13) or set MEOW_PYTHON.'
        }
        return $best.Path
    }
    function Invoke-Checked([string]$failure) {
        # Native commands do not honor $ErrorActionPreference; check explicitly.
        if ($LASTEXITCODE -ne 0) { throw $failure }
    }
    try {
        $root = Join-Path $HOME '.meow'
        $indexUrl = 'https://pypi.org/simple/meow-harness/'
        if ($env:MEOW_PYPI_INDEX_URL) { $indexUrl = $env:MEOW_PYPI_INDEX_URL }
        Write-Host 'Locating Python 3.12+...'
        $python = Find-Python
        Write-Host "Using $python"
        Write-Host 'Fetching the latest release from PyPI...'
        $release = $null
        try {
            $pypi = Invoke-RestMethod `
                -Headers @{ Accept = 'application/vnd.pypi.simple.v1+json' } `
                $indexUrl
            # A beta sorts before the stable release it precedes.
            $release = @(
                $pypi.files |
                    ForEach-Object {
                        $filename = "$($_.filename)"
                        if ($filename -notmatch '^meow[-_]harness-(\d+)\.(\d+)\.(\d+)(b(\d+))?-[^/]+\.whl$') { return }
                        if ($_.yanked) { return }
                        # Capture now: the checksum match below replaces $Matches.
                        $base = "$($Matches[1]).$($Matches[2]).$($Matches[3])"
                        $suffix = "$($Matches[4])"
                        $isBeta = [bool]$suffix
                        $number = [int]("0$($Matches[5])")
                        $candidateTag = "$base$suffix"
                        if ($version) {
                            if ($candidateTag -cne $version) { return }
                        } elseif ((-not $isBeta -and $beta) -or ($isBeta -and -not ${latest-pypi} -and -not $beta)) {
                            return
                        }
                        $digest = "$($_.hashes.sha256)"
                        if ($digest -notmatch '^[0-9a-f]{64}$') { return }
                        [PSCustomObject]@{
                            Tag = $candidateTag
                            Version = [version]$base
                            Stable = [int](-not $isBeta)
                            Beta = $number
                            Digest = $digest
                        }
                    } |
                    Sort-Object Version, Stable, Beta |
                    Select-Object -Last 1
            )
        } catch {
            $release = $null
        }
        if ($release) {
            $tag = $release.Tag
            $digest = $release.Digest
        } elseif ($beta -or ${latest-pypi} -or $version -or $env:MEOW_PYPI_INDEX_URL) {
            throw 'No published MEOW release was found on PyPI.'
        } else {
            Write-Host 'PyPI Index API unavailable; trying the legacy JSON API...'
            $legacy = Invoke-RestMethod 'https://pypi.org/pypi/meow-harness/json'
            $tag = "$($legacy.info.version)"
            if ($tag -notmatch '^\d+\.\d+\.\d+$') { throw 'PyPI legacy JSON has no stable project version.' }
            $wheel = @(
                $legacy.urls |
                    Where-Object {
                        $_.packagetype -eq 'bdist_wheel' -and
                        -not $_.yanked -and
                        "$($_.digests.sha256)" -match '^[0-9a-f]{64}$'
                    } |
                    Select-Object -First 1
            )
            if ($wheel.Count -eq 0) { throw 'PyPI legacy JSON has no stable wheel with a SHA-256 checksum.' }
            $digest = "$($wheel[0].digests.sha256)"
        }
        $versions = Join-Path $root 'versions'
        $destination = Join-Path $versions $tag
        New-Item -ItemType Directory -Force -Path $versions | Out-Null
        $temporary = Join-Path $versions ('.install-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $temporary | Out-Null
        # Install exactly the published wheel (pip checks its hash) into a staging
        # venv, then let that release finish its own install from the staged
        # package: pinned dependencies, launcher, plugin, and an atomic swap into
        # versions\. An existing version is replaced, so rerunning also repairs
        # one built by another Python.
        $bundle = Join-Path $temporary 'bundle'
        New-Item -ItemType Directory -Path $bundle | Out-Null
        Write-Host "Creating environment and installing MEOW $tag (pip output follows)..."
        $runtime = Join-Path $bundle 'runtime'
        $staged = Join-Path $runtime 'Scripts\python.exe'
        $requirement = Join-Path $temporary 'wheel.txt'
        Set-Content -LiteralPath $requirement -Value "meow-harness==$tag --hash=sha256:$digest" -Encoding ASCII
        # Use uv only if it is already installed; any uv failure falls back to pip.
        $installed = $false
        $uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($uv) {
            $previousPreference = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & $uv.Source venv --quiet --seed --python $python $runtime 2>$null
            if ($LASTEXITCODE -eq 0) {
                & $uv.Source pip install --quiet --python $staged --no-deps --require-hashes --only-binary :all: -r $requirement 2>$null
                $installed = ($LASTEXITCODE -eq 0)
            }
            $ErrorActionPreference = $previousPreference
            if (-not $installed) {
                Remove-Item -LiteralPath $runtime -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        if (-not $installed) {
            & $python -m venv $runtime
            Invoke-Checked 'Python could not create a virtual environment.'
            & $staged -m pip install --disable-pip-version-check --no-input --no-deps --require-hashes --only-binary :all: -r $requirement
            Invoke-Checked "Could not install MEOW $tag from the package index (pip failed)."
        }
        $purelib = "$(& $staged -c 'import sysconfig;print(sysconfig.get_path(''purelib''))')".Trim()
        Invoke-Checked 'Could not inspect the staged MEOW install.'
        Write-Host 'Installing pinned dependencies and launcher (this can take minutes)...'
        $previousPath = $env:PYTHONPATH
        $env:PYTHONPATH = $purelib
        try {
            & $python -m meow.installer.releases --finish $destination --bundle $bundle
            Invoke-Checked 'MEOW install failed. Rerun the installer to retry.'
        } finally {
            $env:PYTHONPATH = $previousPath
        }
        $setupArgs = @('--activate', $destination)
        $interactive = -not ${non-interactive} -and -not ${no-setup} -and -not [Console]::IsInputRedirected
        if (($setup -or $interactive) -and -not ${non-interactive}) { $setupArgs += '--setup' }
        if ($bun) { $setupArgs += '--bun' }
        & (Join-Path $destination 'runtime\Scripts\python.exe') -m meow.installer.releases @setupArgs
        if ($LASTEXITCODE -eq 130) { return }
        Invoke-Checked 'MEOW setup failed. Rerun the installer to retry.'
        if ($mcp) {
            $mcpArgs = @('mcp', $mcp)
            if (${install-missing}) { $mcpArgs += '--install-missing' }
            & (Join-Path $destination 'runtime\Scripts\python.exe') -m meow @mcpArgs
            Invoke-Checked 'MCP setup failed. Rerun `meow mcp` to retry.'
        }
        $bin = Join-Path $root 'bin'
        $env:Path = "$bin;$env:Path"
        Write-Host 'MEOW is ready. Restart Claude Code to load /meow:* skills. Update later with meow -u.'
    } catch {
        Write-Host "MEOW installation failed: $($_.Exception.Message)" -ForegroundColor Red
        $global:LASTEXITCODE = 1
        if ($PSCommandPath) { exit 1 }
    } finally {
        if ($temporary -and (Test-Path -LiteralPath $temporary)) {
            # Only delete the exact random directory created under this install root.
            $resolved = [IO.Path]::GetFullPath($temporary)
            $parent = [IO.Path]::GetFullPath($versions).TrimEnd('\') + '\'
            if ($resolved.StartsWith($parent, [StringComparison]::OrdinalIgnoreCase)) {
                Remove-Item -LiteralPath $resolved -Recurse -Force
            }
        }
    }
}
