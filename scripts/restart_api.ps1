param(
    [switch]$Reload
)

$repoRoot = Split-Path -Parent $PSScriptRoot

# Kill any existing Veerox API process tree so the new server starts from the
# current source and the current `.env`.
$targets = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*apps.api.main:app*' -and $_.CommandLine -like '*8002*' }

foreach ($target in $targets) {
    Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1

if ($Reload) {
    $command = "Set-Location '$repoRoot'; .\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --host 0.0.0.0 --port 8002"
} else {
    $command = "Set-Location '$repoRoot'; .\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8002"
}

$args = @('-NoProfile', '-Command', $command)

Start-Process -WindowStyle Hidden -FilePath powershell.exe -ArgumentList $args
