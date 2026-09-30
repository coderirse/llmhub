# llmhub

自托管 LLM 网关：把跑在**自己电脑 GPU** 上的本地模型，通过**反向隧道 + 云服务器网关**变成
随时随地可调用的 OpenAI 兼容 API，并带云端 API 自动兜底。

```
调用方（脚本 / App / 网页 / 机器人）
        │  OpenAI 兼容 API（https://llm.your-domain.com/v1）
        ▼
VPS: one-api 网关（:3999，鉴权 / 限流 / 用量统计 / 渠道重试）
   ├─ 渠道① laptop-ollama（优先）→ ssh -R 反向隧道 → 本地 GPU 机器 Ollama
   └─ 渠道② cloud-fallback（兜底，本地掉线自动切换，如 DeepSeek）
VPS: Open WebUI（:3998）→ 聊天网页，接同一个网关
```

## 为什么这么架构

- **模型跑在家里**：VPS（2C4G 无 GPU）跑不动推理，但本地 GPU 可以；笔记本不常开也不怕，网关自动降级到云端 API
- **服务器只做门面**：静态公网入口、统一鉴权、用量统计、多渠道调度，2C4G 绰绰有余
- **零入站端口**：公网入口全走 Cloudflare Tunnel，本地到服务器是一条 ssh 反向隧道，安全组几乎不用开

## 仓库结构

```
├── tunnel/ollama-tunnel.example.ps1   # 本地守护脚本模板（保活 ollama serve + ssh 反向隧道）
├── examples/                          # CLI 接入示例（环境变量加载 + chat 调用）
├── SERVER.md / DEPLOY.md              # 本地保留（含真实 IP/域名，已 gitignore）
└── .secrets/                          # 全部凭据（已 gitignore，永不入库）
```

> 真实环境信息（服务器 IP、域名、实例 ID）一律不进仓库：入库的只有 `*.example` 模板，
> 真实配置按模板填好后放在同名文件里，由 `.gitignore` 挡住。

## 搭建思路（要点）

1. **VPS**：one-api 单二进制 + systemd（`PORT=3999`），Open WebUI（Python ≥3.11）接它，`RetryTimes>0` + 关闭渠道自动禁用实现掉线兜底
2. **GPU 本地机**：Ollama 只监听 `127.0.0.1:11434`
3. **隧道账号**：服务器建专用用户，sshd 用 Match 块限权——只允许 remote forward、只能监听约定端口、加 ClientAlive 死链检测（半开会话 90 秒自动释放端口）：

```
Match User tunnel
    AllowTcpForwarding remote
    PermitListen 127.0.0.1:8081
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    PermitUserRC no
    ClientAliveInterval 30
    ClientAliveCountMax 3
```

4. **本地守护**：计划任务登录触发，保活 `ollama serve` + `ssh -N -R 127.0.0.1:8081:127.0.0.1:11434`（掉线 5 秒重连）
5. **公网入口**：Cloudflare Tunnel 挂两个子域 → 网关 / 聊天网页

## 使用

```bash
source examples/llm-env.sh          # 或 PowerShell: . examples\llm-env.ps1
python examples/chat.py "你好"      # 任意 OpenAI SDK/CLI 工具同理
```

模型名 = 本地模型（如 `qwen3:8b`）或云端兜底模型，网关按渠道优先级自动调度。

## 安全约定

- 凭据（API token、服务器密码、云 API key）只存本机 `.secrets/`，永不入库、永不硬编码
- 隧道用户无 shell、仅限单端口转发；sshd ClientAlive 防半开会话占端口
- 网关必须带 Bearer token 鉴权后才暴露公网
