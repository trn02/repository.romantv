[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$Version
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path '.git')) {
    throw 'Ez a mappa nincs git repóként inicializálva.'
}

if (-not (Test-Path 'repo')) {
    throw 'A repo mappa nem található.'
}

$releaseArgs = @('release_update.py')
if ($Version) {
    $releaseArgs += @('--version', $Version)
}

python @releaseArgs
if (-not $?) {
    throw 'A release generalasa sikertelen.'
}

git add plugin.video.hdmozi/addon.xml repo index.html release_update.py publish_update.py publish-update.ps1

$hasChanges = git diff --cached --name-only
if (-not $hasChanges) {
    Write-Host 'Nincs commitolható release valtozas.'
    exit 0
}

git commit -m $Message
git push origin main
