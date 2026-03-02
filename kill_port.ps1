$conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
    $procId = $c.OwningProcess
    Write-Output "Killing PID: $procId"
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
try {
    $check = Get-NetTCPConnection -LocalPort 8000 -ErrorAction Stop
    Write-Output "STILL BUSY"
} catch {
    Write-Output "Port 8000 is FREE"
}
