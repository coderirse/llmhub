# 环境变量加载脚本模板（复制为 llm-env.ps1 并填入自己的网关地址与 key）
# 用法:  . examples\llm-env.ps1        （注意前面有个点）
# 可加进 $PROFILE 永久生效

$env:OPENAI_API_BASE = "https://llm.your-domain.com/v1"
$env:OPENAI_BASE_URL = $env:OPENAI_API_BASE
$env:OPENAI_API_KEY  = "sk-your-gateway-token"

# 本地直连 Ollama（跳过网关，无兜底）时改成:
# $env:LLM_BASE = "http://127.0.0.1:11434/v1"

Write-Host "LLM 网关已就绪: $($env:OPENAI_API_BASE)"
