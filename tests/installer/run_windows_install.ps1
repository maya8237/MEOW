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
if (-not (Get-Command meow -ErrorAction SilentlyContinue)) {
    throw "The editable MEOW install is not on PATH."
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
    $env:PYTHONPATH = Join-Path $repoDir "src"

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $powershellCommand.Path
    $startInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    $startInfo.WorkingDirectory = $repoDir
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()
    $process.StandardInput.Write("n`n")
    $process.StandardInput.Close()

    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $output = $stdoutTask.GetAwaiter().GetResult() + [Environment]::NewLine + $stderrTask.GetAwaiter().GetResult()
    Write-Output $output

    if ($process.ExitCode -ne 0) {
        throw "The Windows installer failed with exit code $($process.ExitCode)."
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
        Remove-Item -LiteralPath $tempHome -Recurse -Force
    }
}
