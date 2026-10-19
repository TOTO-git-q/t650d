# Install t650d autostart: a Startup-folder shortcut running "pythonw t650d.py", then start it.
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here 't650d.py'

$pyw = $null
$cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if ($cmd) { $pyw = $cmd.Source }
if (-not $pyw) {
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($py) {
        $cand = Join-Path (Split-Path $py.Source) 'pythonw.exe'
        if (Test-Path $cand) { $pyw = $cand }
    }
}
if (-not $pyw) { Write-Host 'pythonw.exe not found. Install Python 3.9+ from python.org first.'; exit 1 }

if (Get-Process -Name SetPoint -ErrorAction SilentlyContinue) {
    Write-Host 'Logitech SetPoint is running. Uninstall it (or quit it), replug the Unifying receiver, then run this again.'
    exit 1
}

$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 't650d.lnk'
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = '"' + $script + '"'
$s.WorkingDirectory = $here
$s.Description = 'T650 touchpad driver (t650d)'
$s.Save()
Write-Host "Autostart shortcut: $lnk"

Start-Process $lnk
Write-Host 't650d started. Log: t650d.log next to t650d.py. Stop with: python t650d.py --stop'
