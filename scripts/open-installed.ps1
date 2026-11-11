param([string]$InstallDirectory = (Join-Path $env:ProgramFiles 'Printvaerksted'))
$ErrorActionPreference = 'Stop'
$installedPath = [IO.Path]::GetFullPath($InstallDirectory)
$python = Join-Path $installedPath 'runtime\pythonw.exe'
$marker = Join-Path $installedPath 'printvaerksted-package.json'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $marker)) {
    throw 'Printvaerksted blev ikke installeret. Koer START igen og godkend Windows-anmodningen.'
}
if ((Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json).app -ne 'printvaerksted') {
    throw 'Installationsmappen indeholder ikke Printvaerksted.'
}
# Called by START in the teacher's original Windows session after UAC completes.
# The service runs separately; the visible browser belongs to the signed-in user.
Start-Process -FilePath $python -ArgumentList '-m','app.desktop' -WorkingDirectory $installedPath -WindowStyle Hidden
