# Streamable MEOW installer. Run with:
# irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryUrl = "git@github.com:maya8237/MEOW.git"
$defaultParent = "C:/Projects"

function Test-Python312 {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @()
    )

    & $Executable @Arguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null
    return $LASTEXITCODE -eq 0
}

function Select-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        if (Test-Python312 -Executable "py" -Arguments @("-3")) {
            return @{ Executable = "py"; Arguments = @("-3") }
        }
        foreach ($minor in @(15, 14, 13, 12)) {
            if (Test-Python312 -Executable "py" -Arguments @("-3.$minor")) {
                return @{ Executable = "py"; Arguments = @("-3.$minor") }
            }
        }
    }

    foreach ($candidate in @("python3.15", "python3.14", "python3.13", "python3.12", "python3", "python")) {
        if ((Get-Command $candidate -ErrorAction SilentlyContinue) -and (Test-Python312 -Executable $candidate)) {
            return @{ Executable = $candidate; Arguments = @() }
        }
    }

    throw "MEOW requires system Python 3.12 or newer. Install it and run this installer again."
}

$destination = Read-Host "Parent directory for MEOW [$defaultParent]"
if ([string]::IsNullOrWhiteSpace($destination)) {
    $destination = $defaultParent
}
$destination = [Environment]::ExpandEnvironmentVariables($destination.Trim().Trim('"'))
$destination = [IO.Path]::GetFullPath($destination)
$trimmedDestination = $destination.TrimEnd([char[]]@('\', '/'))
$leaf = [IO.Path]::GetFileName($trimmedDestination)
$clonePath = if ($leaf -ieq "MEOW") { $destination } else { Join-Path $destination "MEOW" }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required. Install Git and run this installer again."
}

if (Test-Path -LiteralPath $clonePath) {
    if (-not (Test-Path -LiteralPath (Join-Path $clonePath ".git"))) {
        throw "Refusing to overwrite existing non-MEOW directory: $clonePath"
    }
    Write-Host "Using existing MEOW checkout: $clonePath"
} else {
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Write-Host "Cloning MEOW over SSH into $clonePath"
    & git clone $repositoryUrl $clonePath
    if ($LASTEXITCODE -ne 0) {
        throw "SSH clone failed. Check that GitHub SSH authentication works with: ssh -T git@github.com"
    }
}

$python = Select-Python
Write-Host "Installing MEOW with $($python.Executable) -m pip install -e ..."
& $python.Executable @($python.Arguments) -m pip install -e $clonePath
if ($LASTEXITCODE -ne 0) {
    throw "Editable MEOW installation failed."
}

Write-Host "Configuring Claude and onboarding projects..."
& $python.Executable @($python.Arguments) -m meow.installer --repo-dir $clonePath
if ($LASTEXITCODE -ne 0) {
    throw "MEOW post-install setup failed."
}
