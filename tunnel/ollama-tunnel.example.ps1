# Ollama 反向隧道守护脚本模板（复制为 ollama-tunnel.ps1 并填入自己的环境）
# 职责：1) ollama serve 保活（本机 11434）  2) ssh -R 反向隧道常驻（两条独立循环，互不阻塞）
# 服务器侧映射：<TUNNEL_USER>@<SERVER_IP> 的 127.0.0.1:8081 -> 本机 127.0.0.1:11434
# 服务器 sshd 用 Match User 限权：只允许 remote forward 且只能监听 8081（见 README）

$ErrorActionPreference = "SilentlyContinue"

$key       = "$env:USERPROFILE\.ssh\<YOUR_SSH_KEY>"
$ssh       = "$env:SystemRoot\System32\OpenSSH\ssh.exe"
$ollamaExe = "E:\Ollama\ollama.exe"   # 改成你的 ollama.exe 路径

function Test-Port($port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect("127.0.0.1", $port); return $c.Connected } catch { return $false } finally { $c.Close() }
}

# ---- Ollama 保活（后台 Job，每 15 秒检查）----
Start-Job -ScriptBlock {
    $ollamaExe = "E:\Ollama\ollama.exe"
    while ($true) {
        $up = $false
        $c = New-Object Net.Sockets.TcpClient
        try { $c.Connect("127.0.0.1", 11434); $up = $c.Connected } catch {} finally { $c.Close() }
        if (-not $up -and (Test-Path $ollamaExe)) {
            Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
        }
        Start-Sleep -Seconds 15
    }
} | Out-Null

# ---- 隧道主循环（掉线 5 秒后自动重连）----
while ($true) {
    if (Test-Path $key) {
        & $ssh -i $key `
            -o BatchMode=yes -o ConnectTimeout=15 -o ExitOnForwardFailure=yes `
            -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new `
            -N -R 127.0.0.1:8081:127.0.0.1:11434 <TUNNEL_USER>@<SERVER_IP>
    }
    Start-Sleep -Seconds 5
}
