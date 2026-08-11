param(
    [string]$ProxmoxHost = $(if ($env:PROXMOX_HOST) { $env:PROXMOX_HOST } else { "root@192.168.1.15" }),
    [string]$SshKey = $(if ($env:PROXMOX_SSH_KEY) { $env:PROXMOX_SSH_KEY } else { "" })
)
# Seed investor demo tenant on production (no code deploy).
# Requires SSH to Proxmox host on your home/office LAN (192.168.1.15).

$ErrorActionPreference = "Stop"

function Get-SshArgs {
    $sshArgs = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
    if ($SshKey -and (Test-Path $SshKey)) {
        $sshArgs += @("-i", $SshKey, "-o", "IdentitiesOnly=yes")
    } elseif (Test-Path "$env:USERPROFILE\.ssh\printair_proxmox_ed25519") {
        $sshArgs += @("-i", "$env:USERPROFILE\.ssh\printair_proxmox_ed25519", "-o", "IdentitiesOnly=yes")
    }
    return $sshArgs
}

function Test-SshTarget {
    param([string]$HostLine)
    if ($HostLine -match '@(.+)$') { $hostOnly = $Matches[1] } else { $hostOnly = $HostLine }
    Write-Host "==> Checking TCP port 22 on $hostOnly ..."
    $tcp = Test-NetConnection -ComputerName $hostOnly -Port 22 -WarningAction SilentlyContinue
    if (-not $tcp.TcpTestSucceeded) {
        Write-Host ""
        Write-Host "Cannot reach ${hostOnly}:22 from this PC." -ForegroundColor Yellow
        Write-Host "Proxmox is on a private LAN — connect to home Wi‑Fi/VPN first." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Without SSH, use Proxmox web UI -> CT 102 -> Console:" -ForegroundColor Cyan
        Write-Host "  cd /opt/veyron-pos && bash scripts/seed_via_console.sh"
        Write-Host ""
        Write-Host "Or set: `$env:PROXMOX_HOST='root@YOUR_TAILSCALE_OR_VPN_IP'"
        exit 1
    }
}

Test-SshTarget -HostLine $ProxmoxHost
$sshArgs = Get-SshArgs

Write-Host "==> Seeding tenant 5 on CT 102 via $ProxmoxHost"
& ssh @sshArgs $ProxmoxHost "pct exec 102 -- bash -lc 'cd /opt/veyron-pos && bash scripts/seed_via_console.sh'"
if ($LASTEXITCODE -ne 0) {
    throw "SSH seed failed. Try manually: ssh -i `$env:USERPROFILE\.ssh\printair_proxmox_ed25519 $ProxmoxHost"
}

Write-Host "==> Verify public order page"
$html = curl.exe -fsS https://veyronpos.guma.one/order/5
if ($html -match "Pan de Sal|Ensaymada|Spanish Bread") {
    Write-Host "SUCCESS: menu items visible on https://veyronpos.guma.one/order/5" -ForegroundColor Green
} elseif ($html -match "no items available") {
    Write-Host "WARNING: page loads but menu still empty." -ForegroundColor Yellow
} else {
    Write-Host $html.Substring(0, [Math]::Min(300, $html.Length))
}
