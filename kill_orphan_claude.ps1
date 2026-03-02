# Kill orphan Claude processes (keeping current session PID 2164)
$keepPid = 2164
$claudes = Get-Process claude -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $keepPid }
Write-Host "Orphan Claude processes to kill: $($claudes.Count)"
foreach ($p in $claudes) {
    Write-Host "  Killing PID $($p.Id)"
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
}
Write-Host "Remaining Claude processes:"
Get-Process claude -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  PID $($_.Id)" }
