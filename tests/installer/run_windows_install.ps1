param(
    [ValidateSet("fresh", "preinstalled")]
    [string]$Installation = "fresh"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$scriptPath = Join-Path $repoDir "scripts\install.ps1"

if (-not (Test-Path -LiteralPath (Join-Path $repoDir ".git"))) {
    throw "The CI checkout is not a Git repository."
}
if (-not (Test-Path -LiteralPath (Join-Path $repoDir "skills"))) {
    throw "The CI checkout is not a MEOW repository."
}

$powershellCommand = Get-Command pwsh -ErrorAction SilentlyContinue
if ($null -eq $powershellCommand) {
    $powershellCommand = Get-Command powershell -ErrorAction SilentlyContinue
}
if ($null -eq $powershellCommand) {
    throw "PowerShell is required to run the Windows installer test."
}

$tempHome = Join-Path ([IO.Path]::GetTempPath()) ("meow-installer-" + [guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($tempHome) | Out-Null
$oldHome = $env:HOME
$oldUserProfile = $env:USERPROFILE
$oldPythonPath = $env:PYTHONPATH

try {
    $env:HOME = $tempHome
    $env:USERPROFILE = $tempHome
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    if ($Installation -eq "fresh") {
        if (Get-Command meow -ErrorAction SilentlyContinue) {
            throw "MEOW must not be on PATH before the installer runs."
        }
        & python -c "import importlib.util; assert importlib.util.find_spec('meow') is None, 'MEOW must not be importable before the installer runs.'"
        if ($LASTEXITCODE -ne 0) {
            throw "The installer test requires Python without MEOW installed."
        }
    } else {
        Get-Command meow -ErrorAction Stop | Out-Null
        & python -c "import meow"
        if ($LASTEXITCODE -ne 0) {
            throw "The existing-install test requires MEOW to be preinstalled."
        }
    }

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $powershellCommand.Path
    $escapedScriptPath = $scriptPath.Replace("'", "''")
    $streamedScript = @(
        '$ErrorActionPreference = ''Stop'''
        "[Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes('$escapedScriptPath')) | Invoke-Expression"
        'if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }'
    ) -join [Environment]::NewLine
    $startInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -Command `"$streamedScript`""
    $startInfo.WorkingDirectory = $repoDir
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()
    # Supply the checkout as the destination, then decline onboarding/updates.
    $answers = if ($Installation -eq "fresh") { "$repoDir`nn`n" } else { "n`nn`n" }
    $process.StandardInput.Write($answers)
    $process.StandardInput.Close()

    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $output = $stdoutTask.GetAwaiter().GetResult() + [Environment]::NewLine + $stderrTask.GetAwaiter().GetResult()
    Write-Output $output

    if ($process.ExitCode -ne 0) {
        throw "The Windows installer failed with exit code $($process.ExitCode)."
    }
    if (($Installation -eq "fresh") -and ($output -notmatch "Installing MEOW with")) {
        throw "The installer did not install MEOW from the checkout."
    }
    if (($Installation -eq "preinstalled") -and ($output -match "Installing ")) {
        throw "The installer attempted to reinstall MEOW."
    }
    if ($output -notmatch "Done!") {
        throw "The installer did not report success."
    }
    if ($output -match "Cloning MEOW") {
        throw "The installer attempted to clone despite the CI checkout."
    }
    if ($output -notmatch "existing MEOW checkout") {
        throw "The installer did not use the CI checkout."
    }
    & python -c "import importlib.metadata as m; from pathlib import Path; import meow, sys; assert Path(meow.__file__).resolve() == Path(sys.argv[1], 'src/meow/__init__.py').resolve(); print('Installed MEOW ' + m.version('meow'))" $repoDir
    if ($LASTEXITCODE -ne 0) {
        throw "MEOW was not installed from the CI checkout."
    }
    & meow --version
    if ($LASTEXITCODE -ne 0) {
        throw "The installed MEOW command failed."
    }
}
finally {
    if ($null -eq $oldHome) {
        Remove-Item Env:HOME -ErrorAction SilentlyContinue
    } else {
        $env:HOME = $oldHome
    }
    if ($null -eq $oldUserProfile) {
        Remove-Item Env:USERPROFILE -ErrorAction SilentlyContinue
    } else {
        $env:USERPROFILE = $oldUserProfile
    }
    if ($null -eq $oldPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $oldPythonPath
    }
    if (Test-Path -LiteralPath $tempHome) {
        $resolvedTempHome = (Resolve-Path -LiteralPath $tempHome).Path
        if (-not $resolvedTempHome.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a directory outside the temporary directory."
        }
        Remove-Item -LiteralPath $resolvedTempHome -Recurse -Force
    }
}
