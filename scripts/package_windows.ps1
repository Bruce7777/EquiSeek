$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ApplicationPath = Join-Path $ProjectDir "dist\EquiSeekLegacy"
$ArchivePath = Join-Path $ProjectDir "dist\EquiSeekLegacy-Windows.zip"

if (-not (Test-Path $ApplicationPath -PathType Container)) {
    throw "Missing application directory: $ApplicationPath. Run 'make desktop-legacy-build' first."
}

Compress-Archive -Path "$ApplicationPath\*" -DestinationPath $ArchivePath -Force
Write-Output $ArchivePath
