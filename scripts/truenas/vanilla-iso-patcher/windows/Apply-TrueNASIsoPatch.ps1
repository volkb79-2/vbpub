[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string] $VanillaIso,

    [Parameter(Mandatory = $true, Position = 1)]
    [ValidateNotNullOrEmpty()]
    [string] $Delta,

    [Parameter(Mandatory = $true, Position = 2)]
    [ValidateNotNullOrEmpty()]
    [string] $OutputIso,

    [Parameter()]
    [string] $ManifestPath,

    [Parameter()]
    [string] $XdeltaPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Phase {
    param([Parameter(Mandatory = $true)][string] $Message)
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Write-Host "[$stamp] $Message"
    Write-Verbose $Message
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string] $Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-Sha256 {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $Expected,
        [Parameter(Mandatory = $true)][string] $Label
    )
    if ($Expected -notmatch '^[0-9a-fA-F]{64}$') {
        throw "Manifest $Label SHA-256 is malformed."
    }
    Write-Phase "Hashing $Label`: $Path"
    $actual = Get-Sha256 -Path $Path
    if (-not [string]::Equals($actual, $Expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label SHA-256 mismatch. Expected $Expected; found $actual. No output was published."
    }
    Write-Phase "$Label SHA-256 verified: $actual"
    return $actual
}

function Get-XdeltaExecutable {
    param([string] $RequestedPath)
    if ($RequestedPath) {
        $candidate = (Resolve-Path -LiteralPath $RequestedPath -ErrorAction Stop).ProviderPath
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "The selected xdelta executable is not a file: $candidate"
        }
        return $candidate
    }

    $bundled = Join-Path -Path $PSScriptRoot -ChildPath 'xdelta3.exe'
    if (Test-Path -LiteralPath $bundled -PathType Leaf) {
        return (Resolve-Path -LiteralPath $bundled).ProviderPath
    }
    $command = Get-Command -Name 'xdelta3.exe' -CommandType Application -ErrorAction SilentlyContinue
    if (-not $command) {
        $command = Get-Command -Name 'xdelta3' -CommandType Application -ErrorAction SilentlyContinue
    }
    if (-not $command) {
        throw "xdelta3.exe was not found beside this script or on PATH. Install the Windows xdelta3 version recorded in the manifest, or pass -XdeltaPath."
    }
    return $command.Source
}

function Invoke-TrueNASIsoPatch {
    if ($env:OS -ne 'Windows_NT') {
        throw 'This apply script is intended for Windows PowerShell. Run it from Windows.'
    }
    if ($PSVersionTable.PSVersion -lt [Version]'5.1') {
        throw "PowerShell 5.1 or later is required; found $($PSVersionTable.PSVersion)."
    }

    Write-Phase "Preflight: PowerShell $($PSVersionTable.PSVersion), Windows $([Environment]::OSVersion.Version)."
    $vanillaFull = (Resolve-Path -LiteralPath $VanillaIso -ErrorAction Stop).ProviderPath
    $deltaFull = (Resolve-Path -LiteralPath $Delta -ErrorAction Stop).ProviderPath
    if (-not (Test-Path -LiteralPath $vanillaFull -PathType Leaf)) { throw "Vanilla ISO is not a file: $vanillaFull" }
    if (-not (Test-Path -LiteralPath $deltaFull -PathType Leaf)) { throw "xdelta patch is not a file: $deltaFull" }

    $outputFull = [IO.Path]::GetFullPath($OutputIso)
    $outputDirectory = [IO.Path]::GetDirectoryName($outputFull)
    if (-not $outputDirectory) { throw 'Output ISO must include a destination directory or a current-directory path.' }
    if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
        throw "Output directory does not exist: $outputDirectory"
    }
    if (Test-Path -LiteralPath $outputFull) {
        throw "Output already exists; choose a new path. Existing files are never overwritten: $outputFull"
    }

    if (-not $ManifestPath) { $ManifestPath = $deltaFull + '.json' }
    $manifestFull = (Resolve-Path -LiteralPath $ManifestPath -ErrorAction Stop).ProviderPath
    if (-not (Test-Path -LiteralPath $manifestFull -PathType Leaf)) { throw "Delta manifest is not a file: $manifestFull" }

    foreach ($candidate in @($vanillaFull, $deltaFull, $manifestFull)) {
        if ([string]::Equals($candidate, $outputFull, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Output path must not be the same as an input: $outputFull"
        }
    }

    Write-Phase "Reading delta manifest: $manifestFull"
    try {
        $manifest = Get-Content -LiteralPath $manifestFull -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Cannot parse the delta manifest '$manifestFull': $($_.Exception.Message)"
    }
    if ($manifest.schema_version -ne 1) { throw "Unsupported delta manifest schema_version '$($manifest.schema_version)'." }
    if ($manifest.format -ne 'VCDIFF') { throw "Unsupported delta format '$($manifest.format)'; expected VCDIFF." }
    if (-not $manifest.source -or -not $manifest.target -or -not $manifest.delta -or -not $manifest.xdelta) {
        throw 'Delta manifest is incomplete; required source, target, delta, and xdelta records are missing.'
    }
    foreach ($entry in @($manifest.source, $manifest.target, $manifest.delta)) {
        if ([Int64]$entry.bytes -le 0) { throw 'Delta manifest contains a missing or invalid file length.' }
        if ($entry.sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw 'Delta manifest contains a malformed SHA-256 value.' }
    }
    if ([string]::IsNullOrWhiteSpace([string]$manifest.xdelta.version) -or
        [string]$manifest.xdelta.version -notmatch '^\d+\.\d+\.\d+$') {
        throw 'Delta manifest contains a malformed xdelta version.'
    }

    $sourceBytes = (Get-Item -LiteralPath $vanillaFull).Length
    $deltaBytes = (Get-Item -LiteralPath $deltaFull).Length
    if ($sourceBytes -ne [Int64]$manifest.source.bytes) {
        throw "Vanilla ISO length mismatch. Expected $($manifest.source.bytes) bytes; found $sourceBytes."
    }
    if ($deltaBytes -ne [Int64]$manifest.delta.bytes) {
        throw "Delta length mismatch. Expected $($manifest.delta.bytes) bytes; found $deltaBytes."
    }

    [void](Assert-Sha256 -Path $vanillaFull -Expected $manifest.source.sha256 -Label 'vanilla ISO')
    [void](Assert-Sha256 -Path $deltaFull -Expected $manifest.delta.sha256 -Label 'xdelta patch')

    $xdeltaExecutable = Get-XdeltaExecutable -RequestedPath $XdeltaPath
    Write-Phase "Checking xdelta executable: $xdeltaExecutable"
    $versionText = @(& $xdeltaExecutable -V 2>&1)
    $versionExit = $LASTEXITCODE
    $versionLine = ($versionText | Out-String).Trim()
    if ($versionExit -ne 0 -or $versionLine -notmatch '(?im)^Xdelta version ([0-9]+\.[0-9]+\.[0-9]+)') {
        throw "xdelta version probe failed (exit $versionExit): $versionLine"
    }
    $actualXdeltaVersion = $Matches[1]
    if ($manifest.xdelta.version -and $actualXdeltaVersion -ne [string]$manifest.xdelta.version) {
        throw "xdelta version mismatch. Bundle requires $($manifest.xdelta.version); found $actualXdeltaVersion."
    }
    Write-Phase "xdelta3 $actualXdeltaVersion is ready."
    $configText = @(& $xdeltaExecutable 'config' 2>&1)
    $configExit = $LASTEXITCODE
    $configOutput = ($configText | Out-String)
    if ($configExit -ne 0 -or $configOutput -notmatch '(?m)^SECONDARY_LZMA=1\s*$') {
        throw "This xdelta3 build does not report built-in LZMA support. The VCDIFF patch requires SECONDARY_LZMA=1."
    }
    Write-Phase 'xdelta3 reports the required built-in LZMA decoder.'

    $requiredFreeBytes = [Int64]$manifest.target.bytes + 134217728
    try {
        $driveRoot = [IO.Path]::GetPathRoot($outputDirectory)
        $drive = [System.IO.DriveInfo]::new($driveRoot)
        if ($drive.AvailableFreeSpace -lt $requiredFreeBytes) {
            throw "Output volume has $($drive.AvailableFreeSpace) bytes free; at least $requiredFreeBytes are required."
        }
        Write-Phase "Output volume preflight passed: $($drive.AvailableFreeSpace) bytes free; $requiredFreeBytes required."
    }
    catch [System.Management.Automation.RuntimeException] {
        throw
    }
    catch {
        throw "Could not determine free space for '$outputDirectory': $($_.Exception.Message)"
    }

    $temporaryDirectory = Join-Path $outputDirectory ('.truenas-iso-patch-' + [Guid]::NewGuid().ToString('N'))
    if (Test-Path -LiteralPath $temporaryDirectory) { throw "Temporary path collision: $temporaryDirectory" }
    $null = New-Item -ItemType Directory -Path $temporaryDirectory
    $temporaryOutput = Join-Path $temporaryDirectory ([IO.Path]::GetFileName($outputFull))
    try {
        Write-Phase "Applying VCDIFF patch to a temporary file: $temporaryOutput"
        & $xdeltaExecutable '-d' '-s' $vanillaFull $deltaFull $temporaryOutput
        $decodeExit = $LASTEXITCODE
        if ($decodeExit -ne 0) { throw "xdelta decode failed with exit status $decodeExit." }
        if (-not (Test-Path -LiteralPath $temporaryOutput -PathType Leaf)) {
            throw 'xdelta returned success but did not create the reconstructed ISO.'
        }

        $targetBytes = (Get-Item -LiteralPath $temporaryOutput).Length
        if ($targetBytes -ne [Int64]$manifest.target.bytes) {
            throw "Reconstructed ISO length mismatch. Expected $($manifest.target.bytes) bytes; found $targetBytes."
        }
        [void](Assert-Sha256 -Path $temporaryOutput -Expected $manifest.target.sha256 -Label 'reconstructed ISO')
        # Detect a changed input while xdelta was reading it.
        [void](Assert-Sha256 -Path $vanillaFull -Expected $manifest.source.sha256 -Label 'vanilla ISO after decode')

        if (Test-Path -LiteralPath $outputFull) {
            throw "Output appeared during patching; refusing to overwrite: $outputFull"
        }
        Write-Phase "Publishing verified ISO without overwriting: $outputFull"
        [IO.File]::Move($temporaryOutput, $outputFull)
        try {
            [void](Assert-Sha256 -Path $outputFull -Expected $manifest.target.sha256 -Label 'published ISO')
        }
        catch {
            Remove-Item -LiteralPath $outputFull -Force -ErrorAction SilentlyContinue
            throw
        }
        Write-Phase "Success. Patched ISO created at $outputFull"
        Write-Phase "boot-pool partition size: $($manifest.target.boot_pool_size_gib) GiB"
    }
    finally {
        if (Test-Path -LiteralPath $temporaryDirectory -PathType Container) {
            Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    Invoke-TrueNASIsoPatch
}
catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
