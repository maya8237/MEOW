# irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex
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
        $manifestUrl = 'https://raw.githubusercontent.com/maya8237/MEOW/release-channel/latest.json'
        if ($env:MEOW_RELEASE_MANIFEST_URL) { $manifestUrl = $env:MEOW_RELEASE_MANIFEST_URL }
        $python = Find-Python
        $release = Invoke-RestMethod $manifestUrl
        $tag = $release.tag_name
        if ($tag -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw 'No published stable MEOW release was found.' }
        $digest = "$($release.wheel_sha256)"
        if ($digest -cnotmatch '^[0-9a-f]{64}$') { throw "Release $tag has no wheel checksum; installation stopped." }
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
        Write-Host "Installing MEOW $tag with $python..."
        & $python -m venv (Join-Path $bundle 'runtime')
        Invoke-Checked 'Python could not create a virtual environment.'
        $staged = Join-Path $bundle 'runtime\Scripts\python.exe'
        Set-Content -LiteralPath (Join-Path $temporary 'wheel.txt') -Value "meow-harness==$tag --hash=sha256:$digest" -Encoding ASCII
        & $staged -m pip install --disable-pip-version-check --no-input --no-deps --require-hashes --only-binary :all: -r (Join-Path $temporary 'wheel.txt')
        Invoke-Checked "Could not install MEOW $tag from the package index (pip failed)."
        $purelib = "$(& $staged -c 'import sysconfig;print(sysconfig.get_path(''purelib''))')".Trim()
        Invoke-Checked 'Could not inspect the staged MEOW install.'
        $previousPath = $env:PYTHONPATH
        $env:PYTHONPATH = $purelib
        try {
            & $python -m meow.installer.releases --finish $destination --bundle $bundle
            Invoke-Checked 'MEOW install failed. Rerun the installer to retry.'
        } finally {
            $env:PYTHONPATH = $previousPath
        }
        & (Join-Path $destination 'runtime\Scripts\python.exe') -m meow.installer.releases --activate $destination --setup
        if ($LASTEXITCODE -eq 130) { return }
        Invoke-Checked 'MEOW setup failed. Rerun the installer to retry.'
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
