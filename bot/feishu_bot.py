# 飞书机器人：WebSocket 长连接收事件 → 调 LLM 网关（流式）→ CardKit 流式卡片回复
#
# 设计：
#   - 私聊 + 群@（群里只在被 @ 时响应，自动剥离 @ 前缀）
#   - 每个会话记住最近 10 轮（SQLite 持久化，重启不丢）
#   - 优先走 CardKit 流式卡片（打字机效果）；流式失败自动降级纯文本回复
#   - 模型/网关全部来自环境变量，无任何硬编码环境信息
#
# 配置文件 bot/feishu.env（不入库）: FEISHU_APP_ID / FEISHU_APP_SECRET / LLM_GATEWAY / LLM_API_KEY / LLM_MODEL
# 指令: /clear 清空本会话记忆  /model <name> 切换模型  /help 帮助

import json
import os
import sqlite3
import threading
import time

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
)
from lark_oapi.api.cardkit.v1 import (
    CreateCardRequest,
    CreateCardRequestBody,
    CardElementContentRequest,
    CardElementContentRequestBody,
    UpdateCardRequest,
    UpdateCardRequestBody,
)
from lark_oapi.ws import Client

# ---- 配置：读同目录 feishu.env（已 gitignore），环境变量优先 ----
for _line in open(os.path.join(os.path.dirname(__file__), "feishu.env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

APP_ID = os.environ["FEISHU_APP_ID"]
APP_SECRET = os.environ["FEISHU_APP_SECRET"]
GATEWAY = os.environ.get("LLM_GATEWAY", "http://127.0.0.1:3999/v1")
API_KEY = os.environ["LLM_API_KEY"]

DB_PATH = os.environ.get("BOT_DB", os.path.join(os.path.dirname(__file__), "memory.db"))
MAX_ROUNDS = int(os.environ.get("MAX_ROUNDS", "10"))          # 记住最近 N 轮
STREAM_CHUNK = int(os.environ.get("STREAM_CHUNK", "60"))      # 每 N 字符刷一次卡片
SYSTEM_PROMPT = "你是用户私有部署的 AI 助手，回答准确、简洁，使用与提问相同的语言。"
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")

# ---- 会话记忆（SQLite）----
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.execute("CREATE TABLE IF NOT EXISTS memory (chat_id TEXT PRIMARY KEY, history TEXT)")
_db.commit()
_lock = threading.Lock()


def load_history(chat_id: str) -> list:
    with _lock:
        row = _db.execute("SELECT history FROM memory WHERE chat_id=?", (chat_id,)).fetchone()
    return json.loads(row[0]) if row else []


def save_history(chat_id: str, history: list) -> None:
    with _lock:
        _db.execute(
            "INSERT INTO memory(chat_id, history) VALUES(?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET history=excluded.history",
            (chat_id, json.dumps(history[-MAX_ROUNDS * 2:], ensure_ascii=False)),
        )
        _db.commit()


# ---- 飞书 API 客户端 ----
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()


def reply_text(message_id: str, text: str) -> None:
    req = (ReplyMessageRequest.builder()
           .message_id(message_id)
           .request_body(ReplyMessageRequestBody.builder()
                         .msg_type("text")
                         .content(json.dumps({"text": text}))
                         .build()).build())
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        lark.logger.error(f"reply failed: {resp.code} {resp.msg}")


class StreamingCard:
    """CardKit 流式卡片：创建卡片实体 → 发卡片消息 → 按序增量更新 markdown 元素 → 收尾"""

    ELEMENT_ID = "llm_md"

    def __init__(self, model: str):
        self.sequence = 0                                   # 正式更新从 1 开始递增
        card = {
            "schema": "2.0",
            "config": {"update_multi": True, "streaming_mode": True},
            "header": {"title": {"tag": "plain_text", "content": f"回答中… ({model})"},
                       "template": "blue"},
            "body": {"elements": [{"tag": "markdown", "id": self.ELEMENT_ID, "content": ""}]},
        }
        req = (CreateCardRequest.builder()
               .request_body(CreateCardRequestBody.builder()
                             .data(json.dumps(card)).build()).build())
        resp = client.cardkit.v1.card.create(req)
        if not resp.success():
            raise RuntimeError(f"create card failed: {resp.code} {resp.msg}")
        self.card_id = resp.data.card_id

    def begin(self, chat_id: str) -> None:
        content = json.dumps({"type": "card", "data": {"card_id": self.card_id}})
        req = (CreateMessageRequest.builder()
               .receive_id_type("chat_id")
               .request_body(CreateMessageRequestBody.builder()
                             .receive_id(chat_id)
                             .msg_type("interactive")
                             .content(content).build()).build())
        resp = client.im.v1.message.create(req)
        if not resp.success():
            raise RuntimeError(f"send card failed: {resp.code} {resp.msg}")

    def update(self, content: str, streaming: bool = True) -> None:
        self.sequence += 1                                  # sequence 必须从 1 开始
        req = (CardElementContentRequest.builder()
               .card_id(self.card_id)
               .element_id(self.ELEMENT_ID)
               .request_body(CardElementContentRequestBody.builder()
                             .content(content)
                             .streaming_mode(streaming)
                             .sequence(self.sequence).build()).build())
        resp = client.cardkit.v1.card_element.content(req)
        if not resp.success():
            raise RuntimeError(f"update card failed: {resp.code} {resp.msg}")

    def finish(self, content: str, seconds: float) -> None:
        self.update(content, streaming=False)
        try:                                                # 收尾：改标题显示耗时
            card = {
                "schema": "2.0",
                "config": {"update_multi": True, "streaming_mode": False},
                "header": {"title": {"tag": "plain_text",
                                     "content": f"完成 · 耗时 {seconds:.1f}s"},
                           "template": "green"},
                "body": {"elements": [{"tag": "markdown", "id": self.ELEMENT_ID,
                                       "content": content}]},
            }
            req = (UpdateCardRequest.builder()
                   .card_id(self.card_id)
                   .request_body(UpdateCardRequestBody.builder()
                                 .data(json.dumps(card)).build()).build())
            client.cardkit.v1.card.update(req)
        except Exception as e:
            lark.logger.warning(f"finish card: {e}")


def call_llm_stream(model: str, messages: list):
    """流式调用网关，逐段产出正文增量"""
    with httpx.stream(
        "POST", f"{GATEWAY}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": model, "messages": messages, "stream": True},
        timeout=300,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload.strip() == "[DONE]":
                break
            delta = json.loads(payload)["choices"][0].get("delta", {})
            if delta.get("content"):
                yield delta["content"]


def handle(text: str, chat_id: str, reply_message_id: str) -> None:
    global DEFAULT_MODEL
    text = text.strip()
    if text in ("/help", "/帮助"):
        reply_text(reply_message_id,
                   "直接发消息即可对话（记忆最近 10 轮）\n/clear 清空本会话记忆\n/model <名称> 切换模型")
        return
    if text == "/clear":
        save_history(chat_id, [])
        reply_text(reply_message_id, "已清空本会话记忆 ✅")
        return
    if text.startswith("/model "):
        DEFAULT_MODEL = text[7:].strip() or DEFAULT_MODEL
        save_history(chat_id, [])
        reply_text(reply_message_id, f"已切换模型: {DEFAULT_MODEL}")
        return

    history = load_history(chat_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history,
                {"role": "user", "content": text}]

    card, buffer, started = None, [], time.time()
    try:
        card = StreamingCard(DEFAULT_MODEL)
        card.begin(chat_id)
    except Exception as e:
        lark.logger.warning(f"streaming card unavailable, fallback to text: {e}")

    try:
        flushed = 0
        for piece in call_llm_stream(DEFAULT_MODEL, messages):
            buffer.append(piece)
            total = sum(len(p) for p in buffer)
            if card and total - flushed >= STREAM_CHUNK:
                flushed = total
                try:
                    card.update("".join(buffer))
                except Exception as e:                      # 更新挂了就降级，结束发纯文本
                    lark.logger.warning(f"card update: {e}")
                    card = None
        answer = "".join(buffer).strip()
        if not answer:
            raise RuntimeError("empty response")
        if card:
            card.finish(answer, time.time() - started)
        else:
            reply_text(reply_message_id, answer)
        history += [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]
        save_history(chat_id, history)
    except Exception as e:
        lark.logger.error(f"llm call failed: {e}")
        try:
            if card:
                card.finish("⚠️ 调用失败，请稍后重试。", time.time() - started)
            else:
                reply_text(reply_message_id, f"⚠️ 调用失败：{e}")
        except Exception:
            pass


def on_message_received(data) -> None:
    """im.message.receive_v1 事件入口"""
    try:
        event = data.event
        msg = event.message
        if msg.message_type != "text":                      # 暂只处理文本消息
            return
        if event.sender.sender_type and event.sender.sender_type != "user":
            return                                          # 忽略其他应用/机器人，防回环

        chat_id, message_id = msg.chat_id, msg.message_id
        text = json.loads(msg.content).get("text", "")
        if msg.chat_type == "group":
            mentions = msg.mentions or []
            if not mentions:                                # 群里只响应被 @ 的消息
                return
            for m in mentions:                              # 剥离 @ 前缀（按 mention key 替换）
                if m.key:
                    text = text.replace(m.key, "")
        threading.Thread(target=handle, args=(text, chat_id, message_id), daemon=True).start()
    except Exception as e:
        lark.logger.error(f"handle event error: {e}")


event_handler = (lark.EventDispatcherHandler.builder("", "")
                 .register_p2_im_message_receive_v1(on_message_received)
                 .build())

ws = Client(APP_ID, APP_SECRET, event_handler=event_handler, log_level=lark.LogLevel.INFO)

if __name__ == "__main__":
    lark.logger.info(f"feishu bot starting, gateway={GATEWAY}, model={DEFAULT_MODEL}")
    ws.start()
