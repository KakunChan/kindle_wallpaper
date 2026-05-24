param(
    [string]$ListenAddress = "0.0.0.0",
    [int]$Port = 8080,
    [string]$Distro = ""
)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Error "Run this script from an elevated Administrator PowerShell."
    exit 1
}

if ($Distro) {
    $rawWslIps = & wsl.exe -d $Distro hostname -I
} else {
    $rawWslIps = & wsl.exe hostname -I
}

$ips = [regex]::Matches(($rawWslIps -join " "), "\b(?:\d{1,3}\.){3}\d{1,3}\b") |
    ForEach-Object { $_.Value } |
    Where-Object {
        $_ -ne "127.0.0.1" -and
        $_ -notmatch "^172\.17\." -and
        $_ -notmatch "^172\.18\."
    }

$wslIp = $ips | Select-Object -First 1

if (-not $wslIp) {
    Write-Error "Could not find the WSL IP address from: $rawWslIps"
    exit 1
}

netsh interface portproxy delete v4tov4 listenaddress=$ListenAddress listenport=$Port | Out-Null
netsh interface portproxy add v4tov4 listenaddress=$ListenAddress listenport=$Port connectaddress=$wslIp connectport=$Port

$ruleName = "Kindle Weather $Port"
$existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Remove-NetFirewallRule -DisplayName $ruleName
}

New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $Port | Out-Null

Write-Host "Forwarding http://${ListenAddress}:$Port -> http://${wslIp}:$Port"
Write-Host "Open http://${ListenAddress}:$Port/kindle"
