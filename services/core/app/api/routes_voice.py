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

from ..asr.cloud import (
    ASRError,
    asr_display_name,
    cloud_asr_enabled,
    current_asr_model,
    list_asr_models,
    load_probe_wav,
    probe_asr_model,
    transcribe,
)
from ..asr.pcm_wav import pcm16_stats, pcm16_to_wav
from ..config import now_ms, save_runtime_models
from ..nlu.llm import LLM_CATALOG, LLM_CATALOG_IDS, current_llm_model
from ..nlu.router import parse_intent
from ..nlu.rules import correct_asr, match_wake_word, wake_word_pattern
from ..speak import render, resolve_target
from ..tts.service import synthesize
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
                "ttsEngine": spoken["engine"],
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
                    "ttsEngine": spoken["engine"],
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
                "ttsEngine": spoken["engine"],
                "clientId": client_id,
            },
        )
        out = {
            "succeed": result["ok"],
            "intent": _wire_intent(intent),
            "errorCode": result.get("error_code"),
            "utterance": utterance,
            "audioBase64": spoken["audio_base64"],
            "ttsEngine": spoken["engine"],
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


# ---- 模型菜单：GET 列表/探测，PUT 记住所选 ----


@router.get("/api/voice/providers")
async def voice_providers(request: Request, client_id: str = Depends(auth)):
    cfg = request.app.state.cfg
    probe = str(request.query_params.get("probe") or "") in ("1", "true", "yes")
    asr_selected = current_asr_model(cfg)
    llm_selected = current_llm_model(cfg)
    asr_items: list[dict] = []
    try:
        asr_ids = await list_asr_models(cfg)
    except ASRError:
        asr_ids = [asr_selected] if asr_selected else []
    if probe:
        wav = load_probe_wav()
        tasks = [probe_asr_model(cfg, mid, wav) for mid in asr_ids]
        asr_items = list(await asyncio.gather(*tasks)) if tasks else []
    else:
        asr_items = [
            {"id": mid, "name": asr_display_name(mid), "latencyMs": None, "error": None}
            for mid in asr_ids
        ]
    llm = getattr(request.app.state, "llm", None)
    if probe and llm is not None:
        llm_items = list(
            await asyncio.gather(*(llm.probe_model(item["id"]) for item in LLM_CATALOG))
        )
    else:
        llm_items = [
            {"id": item["id"], "name": item["name"], "latencyMs": None, "error": None}
            for item in LLM_CATALOG
        ]
    return {
        "asr": {"selected": asr_selected, "items": asr_items},
        "llm": {"selected": llm_selected, "items": llm_items},
    }


@router.put("/api/voice/providers")
async def voice_providers_put(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    cfg = request.app.state.cfg
    asr_model = str(body.get("asrModel") or current_asr_model(cfg)).strip()
    llm_model = str(body.get("llmModel") or current_llm_model(cfg)).strip()
    if not asr_model:
        return JSONResponse(status_code=400, content={"succeed": False, "error": "empty_asr"})
    if llm_model not in LLM_CATALOG_IDS:
        return JSONResponse(status_code=400, content={"succeed": False, "error": "bad_llm"})
    cfg.setdefault("asr", {}).setdefault("cloud", {})["model"] = asr_model
    cfg.setdefault("llm", {})["model"] = llm_model
    save_runtime_models(asr_model, llm_model)
    return {"succeed": True, "asrModel": asr_model, "llmModel": llm_model}


# ---- WS /ws/audio：二进制 PCM16 16kHz mono 缓冲 → 云端整句 ASR → run_utterance ----


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
    channel = "ptt"
    cfg = ws.app.state.cfg
    bus = ws.app.state.bus
    pcm_buf = bytearray()

    async def skip_empty_asr(reason: str) -> None:
        print(
            f"[forkai-core] ASR skip reason={reason} model={current_asr_model(cfg)}",
            flush=True,
        )
        await _ws_send_json(
            ws,
            {"type": "final", "text": "", "succeed": False, "errorCode": "asr_empty"},
        )

    async def speak_asr_fail() -> dict:
        utterance = render("fail_asr")
        spoken = await synthesize(utterance, "fail", cfg)
        bus.broadcast(
            "tts",
            {
                "text": spoken["text"],
                "style": "fail",
                "target": "device",
                "audioBase64": spoken["audio_base64"],
                "ttsEngine": spoken["engine"],
                "clientId": client_id,
            },
        )
        out = {
            "succeed": False,
            "intent": {"name": "UNKNOWN", "slots": {}, "rawText": ""},
            "errorCode": "asr_failed",
            "utterance": utterance,
            "audioBase64": spoken["audio_base64"],
            "ttsEngine": spoken["engine"],
            "target": "device",
        }
        if out["audioBase64"] is None:
            del out["audioBase64"]
        return out

    async def run_final(text: str) -> None:
        text = text.strip()
        if not text:
            await skip_empty_asr("empty_transcription")
            return
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
                pcm_buf.extend(data_bytes)
                continue
            raw_text = msg.get("text")
            if not raw_text:
                continue
            try:
                data = json.loads(raw_text)
            except Exception:
                continue
            if not (isinstance(data, dict) and data.get("event") == "end"):
                continue
            sr = int((cfg.get("asr") or {}).get("sample_rate", 16000))
            pcm = bytes(pcm_buf)
            pcm_buf.clear()
            stats = pcm16_stats(pcm, sr)
            model = current_asr_model(cfg)
            print(
                f"[forkai-core] ASR submit model={model} pcm_ms={stats['ms']} "
                f"pcm_bytes={stats['bytes']} peak={stats['peak']}",
                flush=True,
            )
            wav = pcm16_to_wav(pcm, sr)
            if stats["bytes"] == 0:
                await skip_empty_asr("empty_audio")
                continue
            if not cloud_asr_enabled(cfg):
                print("[forkai-core] ASR fail reason=disabled_or_no_key", flush=True)
                out = await speak_asr_fail()
                await _ws_send_json(ws, {"type": "final", "text": "", **out})
                continue
            try:
                text = await transcribe(cfg, wav)
            except ASRError as e:
                reason = str(e)
                if reason in ("empty_audio", "empty_transcription"):
                    await skip_empty_asr(reason)
                    continue
                print(
                    f"[forkai-core] ASR fail reason={reason} model={model} "
                    f"pcm_ms={stats['ms']} peak={stats['peak']}",
                    flush=True,
                )
                out = await speak_asr_fail()
                await _ws_send_json(ws, {"type": "final", "text": "", **out})
                continue
            await run_final(text)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        pass
