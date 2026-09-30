# 调用自建 LLM 网关的最小示例（零依赖，纯标准库）
#
# 先加载环境变量（见 llm-env.example.sh / .ps1）:
#   source examples/llm-env.sh
#   python examples/chat.py "帮我写一句今日待办"
#   LLM_MODEL=qwen3:8b python examples/chat.py "..."   # 指定模型
#
# BASE_URL 取值优先级: LLM_BASE > OPENAI_BASE_URL > OPENAI_API_BASE
import json
import os
import sys
import urllib.request

BASE = (os.environ.get("LLM_BASE")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or "https://llm.example.com/v1")
MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")


def chat(question: str) -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if "example.com" in BASE or not key:
        raise SystemExit("请先设置 OPENAI_API_KEY 与网关地址（参考 examples/llm-env.example.sh）")
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": question}],
        }).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法: python examples/chat.py \"你的问题\"")
    print(chat(" ".join(sys.argv[1:])))
