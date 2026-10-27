$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
foreach ($serviceKind in @('school', 'demo')) {
    $portNumber = if ($serviceKind -eq 'demo') { 8766 } else { 8765 }
    $codePath = Join-Path $projectPath "data\$serviceKind\laererkode.txt"
    if (Test-Path -LiteralPath $codePath) {
        $teacherCode = (Get-Content -LiteralPath $codePath -Raw).Trim()
        try {
            $null = Invoke-RestMethod -Uri "http://localhost:$portNumber/api/service/stop" -Method Post -ContentType 'application/json' -Body '{}' -Headers @{Authorization="Bearer $teacherCode"} -TimeoutSec 5
            Write-Host "Printvaerksted ($serviceKind) er stoppet. Et fysisk print fortsaetter paa printeren."
        } catch {
            Write-Host "Printvaerksted ($serviceKind) svarer ikke eller koerer ikke."
        }
    }
}
