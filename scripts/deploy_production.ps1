param(
    [string]$ProxmoxHost = $(if ($env:PROXMOX_HOST) { $env:PROXMOX_HOST } else { "root@192.168.1.15" }),
    [string]$SshKey = $(if ($env:PROXMOX_SSH_KEY) { $env:PROXMOX_SSH_KEY } else { "" })
)

# Deploy Veyron POS to Proxmox CT 102 and seed investor demo tenant.
# Requires SSH to Proxmox on your LAN (192.168.1.15 by default).
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Archive = Join-Path $env:TEMP "veyron-pos-deploy.tgz"

function Invoke-OrExit {
    param([scriptblock]$Block, [string]$Step)
    Write-Host "==> $Step"
    & $Block
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed (exit $LASTEXITCODE). Are you on the LAN with SSH access to Proxmox?"
    }
}

Invoke-OrExit -Step "Building deploy archive" -Block {
    if (Test-Path $Archive) { Remove-Item $Archive -Force }
    Push-Location $RepoRoot
    tar -czf $Archive `
      --exclude=.git `
      --exclude=.venv `
      --exclude=venv `
      --exclude=node_modules `
      --exclude=instance `
      --exclude=pos.db `
      --exclude=.env `
      --exclude=__pycache__ `
      --exclude=*.pyc `
      app static templates migrations sql tools scripts tests `
      docker docker-compose.yml docker-compose.proxmox.yml Dockerfile requirements.txt wsgi.py veyron-pos.py .dockerignore Caddyfile
    Pop-Location
}

function Get-SshArgs {
    $sshArgs = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
    if ($SshKey -and (Test-Path $SshKey)) {
        $sshArgs += @("-i", $SshKey, "-o", "IdentitiesOnly=yes")
    } elseif (Test-Path "$env:USERPROFILE\.ssh\printair_proxmox_ed25519") {
        $sshArgs += @("-i", "$env:USERPROFILE\.ssh\printair_proxmox_ed25519", "-o", "IdentitiesOnly=yes")
    }
    return $sshArgs
}

function Test-ProxmoxReachable {
    if ($ProxmoxHost -match '@(.+)$') { $hostOnly = $Matches[1] } else { $hostOnly = $ProxmoxHost }
    $tcp = Test-NetConnection -ComputerName $hostOnly -Port 22 -WarningAction SilentlyContinue
    if (-not $tcp.TcpTestSucceeded) {
        throw "Cannot reach ${hostOnly}:22. Connect to your home LAN or set `$env:PROXMOX_HOST to a VPN/Tailscale IP."
    }
}

Test-ProxmoxReachable
$sshBase = Get-SshArgs

Invoke-OrExit -Step "Uploading to Proxmox" -Block {
    $scpArgs = @($sshBase) + @($Archive, "${ProxmoxHost}:/tmp/veyron-pos-deploy.tgz")
    & scp @scpArgs
}

Invoke-OrExit -Step "Extracting on CT 102, rebuilding app, seeding demo tenant" -Block {
    # Single-line bash -lc avoids CRLF breakage when PowerShell passes multiline strings to ssh.
    $ctScript = @(
        "set -e"
        "cd /opt/veyron-pos"
        "if test -f .env; then cp -a .env /tmp/veyron-pos.env.bak; fi"
        "tar -xzf /tmp/veyron-pos-deploy.tgz"
        "if test ! -f .env && test -f /tmp/veyron-pos.env.bak; then cp /tmp/veyron-pos.env.bak .env; fi"
        "/bin/docker compose -f /opt/veyron-pos/docker-compose.yml up -d --build app"
        "bash scripts/seed_via_console.sh"
        "curl -fsS http://127.0.0.1:8000/healthz"
        "curl -fsS -o /dev/null -w 'order/5 HTTP %{http_code}\n' http://127.0.0.1:8000/order/5"
    ) -join "; "
    $remote = "pct push 102 /tmp/veyron-pos-deploy.tgz /tmp/veyron-pos-deploy.tgz && pct exec 102 -- bash -lc '$ctScript'"
    & ssh @sshBase $ProxmoxHost $remote
}

Write-Host "==> Public verification"
curl.exe -fsS https://veyronpos.guma.one/healthz
Write-Host ""
curl.exe -fsS -o NUL -w "order/5 HTTP %{http_code}`n" https://veyronpos.guma.one/order/5
Write-Host "Deploy complete."
