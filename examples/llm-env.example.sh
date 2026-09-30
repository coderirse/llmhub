# 环境变量加载脚本模板（复制为 llm-env.sh 并填入自己的网关地址与 key）
# 用法: source examples/llm-env.sh
# 支持认 OPENAI_API_BASE / OPENAI_BASE_URL 的 CLI 工具（aider、llm、各类 agent）

export OPENAI_API_BASE="https://llm.your-domain.com/v1"
export OPENAI_BASE_URL="$OPENAI_API_BASE"
export OPENAI_API_KEY="sk-your-gateway-token"

# 本地直连 Ollama（跳过网关，无兜底）时改成:
# export LLM_BASE="http://127.0.0.1:11434/v1"

echo "LLM 网关已就绪: $OPENAI_API_BASE"
