# Stop t650d and remove its autostart shortcut. The pad returns to native firmware mode.
# Usage: powershell -ExecutionPolicy Bypass -File uninstall.ps1
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Get-Command python.exe -ErrorAction SilentlyContinue
if ($py) { & $py.Source (Join-Path $here 't650d.py') --stop }
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 't650d.lnk'
if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "Removed $lnk" }
