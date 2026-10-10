# Streamable MEOW installer. Run with:
# irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryUrl = "git@github.com:maya8237/MEOW.git"
$originPyprojectUrl = "https://raw.githubusercontent.com/maya8237/MEOW/main/pyproject.toml"
$defaultParent = "C:/Projects"

function Get-VersionFromText {
    param([AllowNull()][string]$Text)

    if ($Text -match '(?<version>\d+(?:\.\d+)+)') {
        return $Matches.version
    }
    return $null
}

function Get-OriginMainVersion {
    try {
        $content = (Invoke-WebRequest -Uri $originPyprojectUrl -UseBasicParsing -ErrorAction Stop).Content
    } catch {
        return $null
    }
    if ($content -match '(?m)^\s*version\s*=\s*["''](?<version>[^"'']+)["'']') {
        return $Matches.version
    }
    return $null
}

function Test-VersionOlder {
    param(
        [Parameter(Mandatory = $true)][string]$Installed,
        [Parameter(Mandatory = $true)][string]$Latest
    )

    try {
        return ([Version]::Parse($Installed) -lt [Version]::Parse($Latest))
    } catch {
        return $null
    }
}

function Test-Python312 {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @()
    )

    try {
        & $Executable @Arguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null
    } catch {
        return $false
    }
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

function Find-ExistingMeowCheckout {
    $candidates = @(
        @{ Executable = "py"; Arguments = @("-3") },
        @{ Executable = "py"; Arguments = @("-3.15") },
        @{ Executable = "py"; Arguments = @("-3.14") },
        @{ Executable = "py"; Arguments = @("-3.13") },
        @{ Executable = "py"; Arguments = @("-3.12") },
        @{ Executable = "python3.15"; Arguments = @() },
        @{ Executable = "python3.14"; Arguments = @() },
        @{ Executable = "python3.13"; Arguments = @() },
        @{ Executable = "python3.12"; Arguments = @() },
        @{ Executable = "python3"; Arguments = @() },
        @{ Executable = "python"; Arguments = @() }
    )
    $query = 'from pathlib import Path; import meow; package=Path(meow.__file__).resolve(); print(next((str(root) for root in (package.parents[2], package.parents[3]) if (root/''.git'').exists() and (root/''skills'').is_dir()), ''''))'

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Executable -ErrorAction SilentlyContinue)) {
            continue
        }
        if (-not (Test-Python312 -Executable $candidate.Executable -Arguments $candidate.Arguments)) {
            continue
        }

        try {
            $checkoutOutput = & $candidate.Executable @($candidate.Arguments) -c $query 2>$null
            $checkoutExitCode = $LASTEXITCODE
        } catch {
            continue
        }
        $checkout = $checkoutOutput | Select-Object -First 1
        if (($checkoutExitCode -eq 0) -and -not [string]::IsNullOrWhiteSpace([string]$checkout)) {
            return @{
                Executable = $candidate.Executable
                Arguments = @($candidate.Arguments)
                Checkout = ([string]$checkout).Trim()
            }
        }
    }
    return $null
}

function Stop-ForExistingMeow {
    $existing = Get-Command meow -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $existing) {
        return $false
    }

    $existingPath = if ($existing.Path) { $existing.Path } else { $existing.Name }
    $versionOutput = & $existing.Name --version 2>$null
    $installedVersion = Get-VersionFromText (($versionOutput | Select-Object -First 1) -as [string])
    $originVersion = Get-OriginMainVersion
    $comparison = if ($installedVersion -and $originVersion) {
        Test-VersionOlder -Installed $installedVersion -Latest $originVersion
    } else {
        $null
    }

    if ($comparison -eq $true) {
        Write-Warning "Installed MEOW $installedVersion at $existingPath is older than origin/main $originVersion. Using the existing installation without reinstalling it."
    } elseif ($null -eq $comparison) {
        Write-Warning "MEOW is already installed at $existingPath, but its version could not be compared with origin/main. Using the existing installation without reinstalling it."
    } else {
        Write-Host "MEOW is already installed at $existingPath; using the existing installation without reinstalling it."
    }

    $existingSetup = Find-ExistingMeowCheckout
    if ($null -eq $existingSetup) {
        Write-Warning "Could not locate the existing MEOW checkout; plugin registration and project onboarding were skipped."
        Write-Host "To update MEOW manually, run:"
        Write-Host "  py -3 -m pip install --upgrade git+https://github.com/maya8237/MEOW.git"
        return $true
    }

    $pythonCommand = (@($existingSetup.Executable) + @($existingSetup.Arguments)) -join " "
    $updateCommand = "git -C `"$($existingSetup.Checkout)`" pull --ff-only origin main; if (`$?) { $pythonCommand -m pip install -e `"$($existingSetup.Checkout)`" }"
    try {
        $updateAnswer = Read-Host "Update MEOW now? [y/N]"
    } catch {
        $updateAnswer = ""
    }
    if ($updateAnswer -match "^(y|yes)$") {
        Write-Host "Updating MEOW from existing checkout: $($existingSetup.Checkout)"
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            throw "Git is required to update the existing MEOW checkout."
        }
        & git -C $existingSetup.Checkout pull --ff-only origin main
        if ($LASTEXITCODE -ne 0) {
            throw "MEOW source update failed."
        }
        & $existingSetup.Executable @($existingSetup.Arguments) -m pip install -e $existingSetup.Checkout
        if ($LASTEXITCODE -ne 0) {
            throw "Editable MEOW update failed."
        }
        Write-Host "MEOW updated successfully."
    } else {
        Write-Host "MEOW was not updated. To update it manually, run:"
        Write-Host "  $updateCommand"
    }

    Write-Host "Configuring Claude and onboarding projects using existing MEOW checkout: $($existingSetup.Checkout)"
    & $existingSetup.Executable @($existingSetup.Arguments) -m meow.installer --repo-dir $existingSetup.Checkout
    if ($LASTEXITCODE -ne 0) {
        throw "MEOW post-install setup failed."
    }
    return $true
}

if (Stop-ForExistingMeow) {
    return
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
