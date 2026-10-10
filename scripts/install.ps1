# Streamable MEOW installer. Run with:
# irm https://raw.githubusercontent.com/maya8237/MEOW/main/scripts/install.ps1 | iex
#
# With an existing `meow` on PATH it offers to update that checkout and re-runs
# the post-install setup; otherwise it clones MEOW over SSH, installs it
# editable, and runs the post-install setup.
#
# Everything runs inside one script block so that, under `| iex`, the helper
# functions and the strict error settings do not leak into the caller's session.

& {
    $ErrorActionPreference = "Stop"
    Set-StrictMode -Version Latest

    $repositoryUrl = "git@github.com:maya8237/MEOW.git"
    $originPyprojectUrl = "https://raw.githubusercontent.com/maya8237/MEOW/main/pyproject.toml"
    $defaultParent = "C:/Projects"
    # The interpreter that ran the post-install setup, kept so the closing guidance
    # can be shown after "Done!".
    $setupState = @{ Python = $null }

    function Show-Banner {
        Write-Host ""
        Write-Host "  +-----------------------------------------------------+" -ForegroundColor Cyan
        Write-Host "  |                   MEOW INSTALLER                    |" -ForegroundColor Cyan
        Write-Host "  |  Management, Execution & Optimization of Workflows  |" -ForegroundColor Cyan
        Write-Host "  +-----------------------------------------------------+" -ForegroundColor Cyan
        Write-Host ""
    }

    # Runs a script block in a background job with a spinner. The job's output is
    # hidden on success and printed on failure.
    function Invoke-WithSpinner {
        param(
            [Parameter(Mandatory = $true)][scriptblock]$ScriptBlock,
            [Parameter(Mandatory = $true)][string]$Message
        )

        $animate = -not [Console]::IsOutputRedirected
        $job = Start-Job -ScriptBlock $ScriptBlock
        $spinner = @('|', '/', '-', '\')
        $index = 0

        try {
            if (-not $animate) {
                Write-Host "  $Message..."
            }
            while ($job.State -in @("NotStarted", "Running")) {
                if ($animate) {
                    Write-Host -NoNewline ("`r  [{0}] {1}" -f $spinner[$index], $Message)
                    $index = ($index + 1) % $spinner.Count
                }
                Start-Sleep -Milliseconds 100
                $job = Get-Job -Id $job.Id
            }

            $output = @(Receive-Job -Job $job -ErrorAction SilentlyContinue 2>&1)
            $clear = if ($animate) { "`r" } else { "" }
            if ($job.State -ne "Completed") {
                $reason = $job.ChildJobs[0].JobStateInfo.Reason
                Write-Host ("{0}  [FAIL] {1}          " -f $clear, $Message) -ForegroundColor Red
                $output | ForEach-Object { Write-Host "$_" }
                if ($null -ne $reason) {
                    throw $reason.Message
                }
                throw "$Message failed."
            }
            Write-Host ("{0}  [OK] {1}          " -f $clear, $Message) -ForegroundColor Green
        } finally {
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        }
    }

    # Reads a directory path. On an interactive console Tab completes (and cycles
    # through) matching directories; with redirected input it is just Read-Host.
    function Read-PathInput {
        param([Parameter(Mandatory = $true)][string]$Prompt)

        if ([Console]::IsInputRedirected) {
            return (Read-Host $Prompt)
        }

        Write-Host -NoNewline ($Prompt + " (Tab autocompletes paths): ")
        $text = ""
        $choices = @()
        $choiceIndex = 0
        $erase = { param($count) if ($count -gt 0) { Write-Host -NoNewline (("`b" * $count) + (" " * $count) + ("`b" * $count)) } }

        while ($true) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq [ConsoleKey]::Enter) {
                Write-Host ""
                return $text
            }
            if ($key.Key -eq [ConsoleKey]::Backspace) {
                if ($text.Length -gt 0) {
                    & $erase 1
                    $text = $text.Substring(0, $text.Length - 1)
                }
                $choices = @()
            } elseif ($key.Key -eq [ConsoleKey]::Tab) {
                if ($choices.Count -eq 0) {
                    $expanded = [Environment]::ExpandEnvironmentVariables($text)
                    $endsWithSeparator = $expanded -match '[\\/]$'
                    $directory = if ($endsWithSeparator) { $expanded } else { Split-Path -Path $expanded -Parent }
                    $prefix = if ($endsWithSeparator) { "" } else { Split-Path -Path $expanded -Leaf }
                    if ([string]::IsNullOrEmpty($directory)) { $directory = "." }
                    $pattern = [WildcardPattern]::Escape($prefix) + "*"
                    $choices = @(
                        Get-ChildItem -LiteralPath $directory -Directory -Filter $pattern -ErrorAction SilentlyContinue |
                            Sort-Object Name |
                            ForEach-Object { ($_.FullName -replace '\\', '/') + "/" }
                    )
                    $choiceIndex = 0
                }
                if ($choices.Count -gt 0) {
                    & $erase $text.Length
                    $text = $choices[$choiceIndex % $choices.Count]
                    $choiceIndex++
                    Write-Host -NoNewline $text
                }
            } elseif (-not [char]::IsControl($key.KeyChar)) {
                $text += $key.KeyChar
                Write-Host -NoNewline $key.KeyChar
                $choices = @()
            }
        }
    }

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
        if ($content -match '(?m)^\s*version\s*=\s*["\x27](?<version>[^"\x27]+)["\x27]') {
            return $Matches.version
        }
        return $null
    }

    # $true when Installed is older than Latest, $false when not, $null when the
    # versions cannot be compared.
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

        if (-not (Get-Command $Executable -ErrorAction SilentlyContinue)) {
            return $false
        }
        try {
            & $Executable @Arguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null
        } catch {
            return $false
        }
        return $LASTEXITCODE -eq 0
    }

    # Interpreters to try, newest preference first: the py launcher, then PATH names.
    function Get-PythonCandidates {
        $candidates = @(@{ Executable = "py"; Arguments = @("-3") })
        foreach ($minor in @(15, 14, 13, 12)) {
            $candidates += @{ Executable = "py"; Arguments = @("-3.$minor") }
        }
        foreach ($name in @("python3.15", "python3.14", "python3.13", "python3.12", "python3", "python")) {
            $candidates += @{ Executable = $name; Arguments = @() }
        }
        return $candidates
    }

    function Select-Python {
        foreach ($candidate in Get-PythonCandidates) {
            if (Test-Python312 -Executable $candidate.Executable -Arguments $candidate.Arguments) {
                return $candidate
            }
        }
        throw "MEOW requires system Python 3.12 or newer. Install it and run this installer again."
    }

    function Format-PythonCommand {
        param([Parameter(Mandatory = $true)][hashtable]$Python)

        return ((@($Python.Executable) + @($Python.Arguments)) -join " ")
    }

    # Finds the interpreter and git checkout that provide the importable `meow`.
    function Find-ExistingMeowCheckout {
        $query = @'
from pathlib import Path; import meow; package=Path(meow.__file__).resolve(); print(next((str(root) for root in (package.parents[2], package.parents[3]) if (root/'.git').exists() and (root/'skills').is_dir()), ''))
'@

        foreach ($candidate in Get-PythonCandidates) {
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

    function Update-ExistingCheckout {
        param([Parameter(Mandatory = $true)][hashtable]$Setup)

        Write-Host "Updating MEOW from existing checkout: $($Setup.Checkout)"
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            throw "Git is required to update the existing MEOW checkout."
        }
        Invoke-WithSpinner -Message "Pulling MEOW source" -ScriptBlock {
            $setup = $using:Setup
            & git -C $setup.Checkout pull --ff-only origin main
            if ($LASTEXITCODE -ne 0) {
                throw "MEOW source update failed."
            }
        }
        Invoke-WithSpinner -Message "Installing editable MEOW update" -ScriptBlock {
            $setup = $using:Setup
            & $setup.Executable @($setup.Arguments) -m pip install -e $setup.Checkout
            if ($LASTEXITCODE -ne 0) {
                throw "Editable MEOW update failed."
            }
        }
        Write-Host "MEOW updated successfully."
    }

    # Runs the post-install setup attached to the console (not captured by the
    # pipeline) so its prompts, Tab completion and output behave interactively.
    function Invoke-MeowSetup {
        param([Parameter(Mandatory = $true)][hashtable]$Python, [Parameter(Mandatory = $true)][string]$Checkout)

        $setupState.Python = $Python
        $arguments = @($Python.Arguments) + @("-m", "meow.installer", "--repo-dir", ('"' + $Checkout + '"'))
        $process = Start-Process -FilePath $Python.Executable -ArgumentList $arguments -NoNewWindow -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            throw "MEOW post-install setup failed."
        }
    }

    # Handles a MEOW that is already installed: a `meow` command on PATH or an
    # importable `meow` package. Returns $false when there is neither.
    function Use-ExistingMeow {
        $existing = Get-Command meow -ErrorAction SilentlyContinue | Select-Object -First 1
        $existingSetup = Find-ExistingMeowCheckout
        if (($null -eq $existing) -and ($null -eq $existingSetup)) {
            return $false
        }

        if ($null -ne $existing) {
            $existingPath = if ($existing.Path) { $existing.Path } else { $existing.Name }
            $versionOutput = & $existing.Name --version 2>$null
        } else {
            $existingPath = $existingSetup.Checkout
            $versionOutput = & $existingSetup.Executable @($existingSetup.Arguments) -c "import importlib.metadata as m; print(m.version('meow'))" 2>$null
        }
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
            Write-Host ("MEOW $installedVersion is up to date " + [char]0x2713) -ForegroundColor Green
        }

        if ($null -eq $existingSetup) {
            Write-Warning "Could not locate the existing MEOW checkout; plugin registration and project onboarding were skipped."
            Write-Host "To update MEOW manually, run:"
            try {
                $manualPythonCommand = Format-PythonCommand -Python (Select-Python)
            } catch {
                $manualPythonCommand = "<python>"
            }
            Write-Host "  $manualPythonCommand -m pip install --upgrade git+https://github.com/maya8237/MEOW.git"
            return $true
        }

        if ($comparison -ne $false) {
            $updateCommand = (
                'git -C "' + $existingSetup.Checkout +
                '" pull --ff-only origin main; if ($?) { ' +
                (Format-PythonCommand -Python $existingSetup) + ' -m pip install -e "' +
                $existingSetup.Checkout + '" }'
            )
            try {
                $updateAnswer = Read-Host "Update MEOW now? [y/N]"
            } catch {
                $updateAnswer = ""
            }
            if ($updateAnswer -match "^(y|yes)$") {
                Update-ExistingCheckout -Setup $existingSetup
            } else {
                Write-Host "MEOW was not updated. To update it manually, run:"
                Write-Host "  $updateCommand"
            }
        }

        Write-Host "Configuring Claude and onboarding projects using existing MEOW checkout: $($existingSetup.Checkout)"
        Invoke-MeowSetup -Python $existingSetup -Checkout $existingSetup.Checkout
        return $true
    }

    function Install-FreshMeow {
        $destination = Read-PathInput "Parent directory for MEOW [$defaultParent]"
        if ([string]::IsNullOrWhiteSpace($destination)) {
            $destination = $defaultParent
        }
        $destination = [Environment]::ExpandEnvironmentVariables($destination.Trim().Trim([char]34))
        if ($destination -eq "~" -or $destination -match '^~[\\/]') {
            $destination = Join-Path $HOME $destination.Substring(1).TrimStart([char[]]@("\", "/"))
        }
        $destination = [IO.Path]::GetFullPath($destination)
        $trimmedDestination = $destination.TrimEnd([char[]]@("\", "/"))
        $leaf = [IO.Path]::GetFileName($trimmedDestination)
        $clonePath = if ($leaf -ieq "MEOW") { $destination } else { Join-Path $destination "MEOW" }

        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            throw "Git is required. Install Git and run this installer again."
        }
        $python = Select-Python

        if (Test-Path -LiteralPath $clonePath) {
            if (-not (Test-Path -LiteralPath (Join-Path $clonePath ".git"))) {
                throw "Refusing to overwrite existing non-MEOW directory: $clonePath"
            }
            Write-Host "Using existing MEOW checkout: $clonePath"
        } else {
            New-Item -ItemType Directory -Force -Path (Split-Path -Path $clonePath -Parent) | Out-Null
            Invoke-WithSpinner -Message "Cloning MEOW over SSH into $clonePath" -ScriptBlock {
                & git clone $using:repositoryUrl $using:clonePath
                if ($LASTEXITCODE -ne 0) {
                    throw "SSH clone failed. Check that GitHub SSH authentication works with: ssh -T git@github.com"
                }
            }
        }

        Invoke-WithSpinner -Message "Installing MEOW with $(Format-PythonCommand -Python $python) -m pip install -e ..." -ScriptBlock {
            $selectedPython = $using:python
            & $selectedPython.Executable @($selectedPython.Arguments) -m pip install -e $using:clonePath
            if ($LASTEXITCODE -ne 0) {
                throw "Editable MEOW installation failed."
            }
        }

        Write-Host "Configuring Claude and onboarding projects..."
        Invoke-MeowSetup -Python $python -Checkout $clonePath
    }

    try {
        Show-Banner
        if (-not (Use-ExistingMeow)) {
            Install-FreshMeow
        }
        Write-Host "Done!" -ForegroundColor Green
        if ($null -ne $setupState.Python) {
            & $setupState.Python.Executable @($setupState.Python.Arguments) -m meow.installer --next-steps | Out-Host
        }
    } catch {
        Write-Host ("Failed: " + $_.Exception.Message) -ForegroundColor Red
        # `exit` would close the caller's terminal when streamed with `| iex`, so
        # only use it when running as a script file.
        if (Get-Variable -Name PSCommandPath -ValueOnly -ErrorAction SilentlyContinue) {
            exit 1
        }
        $global:LASTEXITCODE = 1
    }
}
