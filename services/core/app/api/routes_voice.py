"""语音文本路由（run_utterance 主流程 1:1 对齐 index.ts 144-208 行）。

cabin 通道先匹配唤醒词 → arm_wake + TTS wake_ack(target=vehicle) + broadcast tts/wake_armed
→ 若剥离唤醒词后为空则只应声不执行意图；否则 parse_intent → executor.handle
→ render 话术 → resolve_target → speak → broadcast intent + tts → 返回结果。
/api/voice/stop 等价于以 ptt 通道跑 run_utterance("停止")。
"""
import asyncio
import json
import re

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from ..asr.engine import SherpaASR
from ..asr.stream import ASRStream
from ..config import now_ms
from ..nlu.router import parse_intent
from ..nlu.rules import correct_asr, match_wake_word, wake_word_pattern
from ..speak import render, resolve_target
from ..tts.piper import synthesize
from .deps import auth, read_body

router = APIRouter()

# 唤醒词剥离：对齐 index.ts 169 行，但按同音字等价组匹配（见 nlu.rules）。
_WAKE_STRIP_BASE = wake_word_pattern("玖物，玖物") + "|" + wake_word_pattern("九物九物")


def _strip_wake_words(text: str, wake_words: list) -> str:
    patterns = [_WAKE_STRIP_BASE] + [wake_word_pattern(str(w)) for w in wake_words]
    return re.sub("|".join(patterns), "", text)


def _wire_intent(intent: dict) -> dict:
    """内部 dict(name/slots/raw_text) → 线格式 ParsedIntent(name/slots/rawText)。"""
    return {
        "name": intent["name"],
        "slots": intent.get("slots", {}),
        "rawText": intent.get("raw_text", ""),
    }


async def run_utterance(request: Request, client_id: str, channel: str, text: str) -> dict:
    cfg = request.app.state.cfg
    sessions = request.app.state.sessions
    bus = request.app.state.bus
    executor = request.app.state.executor

    wake_ok = False
    if channel == "cabin" and match_wake_word(text, cfg["wakeWords"]):
        sessions.arm_wake(client_id)
        wake_ok = True
        wake_text = render("wake_ack")
        spoken = await synthesize(wake_text, "wake", cfg)
        bus.broadcast(
            "tts",
            {
                "text": spoken["text"],
                "style": "wake",
                "target": "vehicle",
                "audioBase64": spoken["audio_base64"],
                "clientId": client_id,
            },
        )
        bus.broadcast(
            "wake_armed", {"clientId": client_id, "until": now_ms() + cfg["wakeArmMs"]}
        )
        # if utterance is ONLY wake word, stop here
        stripped = _strip_wake_words(text, cfg["wakeWords"]).strip()
        if not stripped:
            return {"succeed": True, "intent": {"name": "WAKE"}, "utterance": wake_text}
    elif match_wake_word(text, cfg["wakeWords"]):
        # ptt 通道唤醒词短路：纯唤醒词只应声，不进 NLU/LLM
        # （否则 0.5B 会把"玖物玖物"硬映射成 QUERY_FORK_HEIGHT 等错误意图）；
        # 混合指令剥离唤醒词后继续解析
        stripped = _strip_wake_words(text, cfg["wakeWords"]).strip()
        if not stripped:
            wake_text = render("wake_ack")
            spoken = await synthesize(wake_text, "wake", cfg)
            bus.broadcast(
                "tts",
                {
                    "text": spoken["text"],
                    "style": "wake",
                    "target": "device",
                    "audioBase64": spoken["audio_base64"],
                    "clientId": client_id,
                },
            )
            return {"succeed": True, "intent": {"name": "WAKE"}, "utterance": wake_text}
        text = stripped

    corrected = correct_asr(text)
    intent_list = await parse_intent(corrected, getattr(request.app.state, "llm", None))
    ctx = {"client_id": client_id, "channel": channel, "wake_ok": wake_ok, "text": corrected}
    wire_intents = [_wire_intent(i) for i in intent_list]
    out: dict | None = None
    # 复合指令：按顺序逐个执行，任一失败即中断并播报该失败；
    # 遇到 awaiting（追问/待确认挂起）也中断，等用户下一轮应答；
    # 每个意图的 TTS 话术依次广播（保持原 broadcast 节奏）
    for intent in intent_list:
        result = await executor.handle(intent, ctx)
        # speak_text 为动态话术（参数追问等，不进 utterances 模板）
        utterance = result.get("speak_text") or render(
            result.get("utterance_key") or "", result.get("utterance_params") or {}
        )
        target = resolve_target(channel, result["speak_kind"], cfg)
        spoken = await synthesize(utterance, result["speak_style"], cfg)
        bus.broadcast(
            "intent",
            {
                "clientId": client_id,
                "channel": channel,
                "intent": _wire_intent(intent),
                "ok": result["ok"],
                "errorCode": result.get("error_code"),
                "utterance": utterance,
            },
        )
        bus.broadcast(
            "tts",
            {
                "text": spoken["text"],
                "style": result["speak_style"],
                "target": target,
                "audioBase64": spoken["audio_base64"],
                "clientId": client_id,
            },
        )
        out = {
            "succeed": result["ok"],
            "intent": _wire_intent(intent),
            "errorCode": result.get("error_code"),
            "utterance": utterance,
            "audioBase64": spoken["audio_base64"],
            "target": target,
        }
        if out["errorCode"] is None:
            del out["errorCode"]
        if out["audioBase64"] is None:
            del out["audioBase64"]
        if not result["ok"] or result.get("awaiting"):
            break
    if out is None:  # 理论上不会发生（router 至少返回 [UNKNOWN]）
        out = {"succeed": False, "intent": {"name": "UNKNOWN", "slots": {}, "rawText": text}}
    # 连续对话 30s 免唤醒（步骤33）：cabin 通道成功处理非纯唤醒意图后滚动续期
    # （纯唤醒词分支已在前面 return；wakeArmMs 已调为 30000）
    if channel == "cabin" and out.get("succeed"):
        sessions.arm_wake(client_id)
        bus.broadcast(
            "wake_armed", {"clientId": client_id, "until": now_ms() + cfg["wakeArmMs"]}
        )
    out["intents"] = wire_intents
    return out


@router.post("/api/voice/text")
async def voice_text(request: Request, client_id: str = Depends(auth)):
    try:
        body = await read_body(request)
        channel = "cabin" if body.get("channel") == "cabin" else "ptt"
        text = str(body.get("text") or "").strip()
        if not text:
            return JSONResponse(status_code=400, content={"succeed": False, "error": "empty"})
        return await run_utterance(request, client_id, channel, text)
    except Exception as e:
        return JSONResponse(status_code=500, content={"succeed": False, "error": str(e)})


@router.post("/api/voice/stop")
async def voice_stop(request: Request, client_id: str = Depends(auth)):
    try:
        return await run_utterance(request, client_id, "ptt", "停止")
    except Exception as e:
        return JSONResponse(status_code=500, content={"succeed": False, "error": str(e)})


# ---- WS /ws/audio：二进制 PCM16 16kHz mono → ASR → run_utterance ----


async def _ws_send_json(ws: WebSocket, obj: dict) -> None:
    await ws.send_text(json.dumps(obj, ensure_ascii=False))


@router.websocket("/ws/audio")
async def ws_audio(ws: WebSocket):
    await ws.accept()
    # 第一条消息必须为 JSON：{"pairToken": "...", "channel": "ptt"|"cabin"}
    try:
        raw_hello = await asyncio.wait_for(ws.receive_text(), timeout=10)
        hello = json.loads(raw_hello)
    except Exception:
        await ws.close(code=4401)
        return
    if not isinstance(hello, dict):
        await ws.close(code=4401)
        return
    rec = ws.app.state.sessions.resolve_token(str(hello.get("pairToken") or ""))
    if not rec:
        await ws.close(code=4401)
        return
    client_id = rec["clientId"]
    channel = "cabin" if hello.get("channel") == "cabin" else "ptt"

    try:
        asr = await SherpaASR.instance(ws.app.state.cfg)
    except Exception as e:
        # ASR 不可用：告知后关闭（前端降级文本输入）
        try:
            await _ws_send_json(ws, {"type": "error", "error": f"asr_unavailable: {e}"})
        finally:
            await ws.close(code=1011)
        return

    stream = ASRStream(asr)
    bus = ws.app.state.bus

    async def run_final(text: str) -> None:
        text = text.strip()
        if not text:
            return
        # TTS 打断基础：前端收到 asr_final 停播当前音频
        bus.broadcast(
            "asr_final", {"clientId": client_id, "channel": channel, "text": text}
        )
        out = await run_utterance(ws, client_id, channel, text)
        await _ws_send_json(ws, {"type": "final", "text": text, **out})

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data_bytes = msg.get("bytes")
            if data_bytes is not None:
                events = await stream.feed(data_bytes)
                for is_final, text in events:
                    if is_final:
                        await run_final(text)
                    else:
                        await _ws_send_json(ws, {"type": "partial", "text": text})
            else:
                raw_text = msg.get("text")
                if not raw_text:
                    continue
                try:
                    data = json.loads(raw_text)
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("event") == "end":
                    final = await stream.flush()
                    if final:
                        await run_final(final)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        pass
    finally:
        await stream.close()
