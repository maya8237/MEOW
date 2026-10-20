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
    try {
        $base = 'https://github.com/maya8237/MEOW/releases'
        if ($env:MEOW_RELEASE_BASE_URL) { $base = $env:MEOW_RELEASE_BASE_URL }
        $root = Join-Path $HOME '.meow'
        $architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
        $architecture = switch ($architecture) { 'x64' { 'x86_64' } default { throw "Unsupported CPU: $_. MEOW requires Windows x64." } }
        $manifestUrl = 'https://raw.githubusercontent.com/maya8237/MEOW/release-channel/latest.json'
        if ($env:MEOW_RELEASE_MANIFEST_URL) { $manifestUrl = $env:MEOW_RELEASE_MANIFEST_URL }
        $release = Invoke-RestMethod $manifestUrl
        $tag = $release.tag_name
        if ($tag -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw 'No published stable MEOW release was found.' }
        $asset = "meow-windows-$architecture.zip"
        $versions = Join-Path $root 'versions'
        $destination = Join-Path $versions $tag
        New-Item -ItemType Directory -Force -Path $versions | Out-Null
        $temporary = Join-Path $versions ('.install-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $temporary | Out-Null
        if (-not (Test-Path -LiteralPath $destination)) {
            Write-Host "Downloading MEOW $tag for Windows/$architecture..."
            $archive = Join-Path $temporary $asset
            Invoke-WebRequest "$base/download/$tag/$asset" -OutFile $archive -UseBasicParsing
            $checksumUrl = "$base/download/$tag/$asset.txt"
            try {
                $checksum = (Invoke-WebRequest $checksumUrl -UseBasicParsing).Content
            } catch {
                if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) {
                    throw "Release $tag is missing its checksum file: $asset.txt. Installation stopped because the download cannot be verified. URL: $checksumUrl"
                }
                throw "Could not download checksum file $asset.txt for release $tag. URL: $checksumUrl. $($_.Exception.Message)"
            }
            if ($checksum -is [byte[]]) { $checksum = [Text.Encoding]::UTF8.GetString($checksum) }
            $expected = ($checksum.Trim() -split '\s+')[0]
            $sha = [Security.Cryptography.SHA256]::Create()
            $stream = [IO.File]::OpenRead($archive)
            try { $actual = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
            finally { $stream.Dispose(); $sha.Dispose() }
            if ($actual -ine $expected) {
                throw 'MEOW release checksum mismatch; installation was not changed.'
            }
            $staging = Join-Path $temporary 'release'
            [Reflection.Assembly]::LoadWithPartialName('System.IO.Compression.FileSystem') | Out-Null
            [IO.Compression.ZipFile]::ExtractToDirectory($archive, $staging)
            Move-Item -LiteralPath $staging -Destination $destination
        }
        & (Join-Path $destination 'runtime/python.exe') -m meow.installer.releases --activate $destination --setup
        if ($LASTEXITCODE -eq 130) { return }
        if ($LASTEXITCODE -ne 0) { throw 'MEOW setup failed. Rerun the installer to retry.' }
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
