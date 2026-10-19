"""语音文本路由（run_utterance 主流程 1:1 对齐 index.ts 144-208 行）。

cabin 通道先匹配唤醒词 → arm_wake + TTS wake_ack(target=vehicle) + broadcast tts/wake_armed
→ 若剥离唤醒词后为空则只应声不执行意图；否则 parse_intent → executor.handle
→ render 话术 → resolve_target → speak → broadcast intent + tts → 返回结果。
/api/voice/stop 等价于以 ptt 通道跑 run_utterance("停止")。
"""
import asyncio
import json
import re
import time

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
from ..nlu.router import LLM_FAIL, clamp_max_intents, parse_intent
from ..trace_labels import SENT_INTENTS, fmt_intents, intent_step
from ..nlu.rules import correct_asr, match_wake_word, wake_word_pattern
from ..speak import render, resolve_target
from ..g2a import G2A_TTS_VOICES, is_g2a_tts
from ..tts.cloud import current_tts_model, current_tts_voice, list_tts_models, list_tts_voices, tts_display_name
from ..tts.cache import cache_clear, cache_stats
from ..tts.prewarm import start_prewarm, stop_prewarm
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


_MOVE_NAMES = frozenset({"MOVE_FWD", "MOVE_BACK"})
_TURN_NAMES = frozenset({"TURN_LEFT", "TURN_RIGHT"})


def coalesce_drive_intents(intents: list) -> list:
    """相邻的前进/后退 + 左转/右转合成一条 DRIVE_COMBINED（真车单条 drive）。"""
    if not intents:
        return intents
    out: list = []
    i = 0
    while i < len(intents):
        a = intents[i]
        b = intents[i + 1] if i + 1 < len(intents) else None
        an = a.get("name")
        bn = b.get("name") if b else None
        if b and (
            (an in _MOVE_NAMES and bn in _TURN_NAMES)
            or (an in _TURN_NAMES and bn in _MOVE_NAMES)
        ):
            move = a if an in _MOVE_NAMES else b
            turn = a if an in _TURN_NAMES else b
            out.append(
                {
                    "name": "DRIVE_COMBINED",
                    "slots": {"move": move["name"], "turn": turn["name"]},
                    "raw_text": (a.get("raw_text") or "") or (b.get("raw_text") or ""),
                }
            )
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def _emit_nlu(tracer, trace_id, meta, intent_list, cfg) -> None:
    via = (meta or {}).get("via")
    ms = (meta or {}).get("ms")
    model = (meta or {}).get("model") or current_llm_model(cfg)
    preview = {
        "contentPreview": (meta or {}).get("contentPreview"),
        "reasoningPreview": (meta or {}).get("reasoningPreview"),
    }
    shown = [i for i in (intent_list or []) if i.get("name") != LLM_FAIL]
    if via == "rule":
        _emit(
            tracer,
            "NLU",
            f"规则快路径：{fmt_intents(shown)}",
            status="ok",
            dur_ms=ms,
            trace_id=trace_id,
            data={"metric": {"kind": "rules", "where": "local", "ok": True, "ms": ms}},
        )
    elif via == "llm":
        _emit(
            tracer,
            "NLU",
            f"LLM {model} 理解为：{fmt_intents(shown)}",
            status="ok",
            dur_ms=ms,
            trace_id=trace_id,
            data={
                "metric": {"kind": "llm", "model": model, "where": "cloud", "ok": True, "ms": ms},
                **preview,
            },
        )
    elif via == "llm_empty":
        _emit(
            tracer,
            "NLU",
            "LLM 判定无可执行指令（静默）",
            status="warn",
            dur_ms=ms,
            trace_id=trace_id,
            data={
                "metric": {"kind": "llm", "model": model, "where": "cloud", "ok": False, "ms": ms},
                **preview,
            },
        )
    elif via == "llm_fail":
        err = (meta or {}).get("error") or "失败"
        _emit(
            tracer,
            "NLU",
            f"LLM 失败（{err}）· 整句不执行",
            status="fail",
            dur_ms=ms,
            trace_id=trace_id,
            data={
                "metric": {"kind": "llm", "model": model, "where": "cloud", "ok": False, "ms": ms},
                **preview,
            },
        )


def _tracer_of(obj):
    app = getattr(obj, "app", None)
    if app is None:
        return None
    return getattr(app.state, "tracer", None)


def _emit(tracer, *args, **kwargs) -> None:
    if tracer is None:
        return
    try:
        tracer.emit(*args, **kwargs)
    except Exception:
        pass


def _mission(tracer, name, *args, **kwargs) -> None:
    if tracer is None:
        return
    try:
        getattr(tracer, name)(*args, **kwargs)
    except Exception:
        pass


async def run_utterance(
    request: Request, client_id: str, channel: str, text: str, trace_id: str | None = None
) -> dict:
    cfg = request.app.state.cfg
    sessions = request.app.state.sessions
    bus = request.app.state.bus
    executor = request.app.state.executor
    tracer = _tracer_of(request)
    if tracer is not None:
        if trace_id:
            tracer.bind(trace_id)
        else:
            trace_id = tracer.begin("utterance", text)
            _emit(tracer, "HEAR", f"文本输入：{text}（{channel}）", status="ok", trace_id=trace_id)

    wake_ok = False
    if channel == "cabin" and match_wake_word(text, cfg["wakeWords"]):
        sessions.arm_wake(client_id)
        wake_ok = True
        _emit(tracer, "HEAR", "唤醒词：玖物玖物", status="ok", trace_id=trace_id)
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
            _emit(tracer, "HEAR", "唤醒词：玖物玖物", status="ok", trace_id=trace_id)
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
    if corrected != text:
        _emit(tracer, "NLU", f"纠偏：{text} → {corrected}", status="info", trace_id=trace_id)
    nlu_meta: dict = {}

    def _on_llm_start() -> None:
        model = current_llm_model(cfg)
        _emit(
            tracer,
            "NLU",
            f"调用 LLM {model} 理解",
            status="running",
            trace_id=trace_id,
            data={"metric": {"kind": "llm", "model": model, "where": "cloud", "ok": False}},
        )

    intent_list = await parse_intent(
        corrected,
        getattr(request.app.state, "llm", None),
        cfg,
        nlu_meta,
        _on_llm_start,
    )
    intent_list = coalesce_drive_intents(intent_list)
    _emit_nlu(tracer, trace_id, nlu_meta, intent_list, cfg)
    ctx = {"client_id": client_id, "channel": channel, "wake_ok": wake_ok, "text": corrected}

    if not intent_list:
        unknown = {"name": "UNKNOWN", "slots": {}, "raw_text": corrected}
        return {
            "succeed": True,
            "intent": _wire_intent(unknown),
            "utterance": "",
            "intents": [],
        }

    if intent_list[0]["name"] == LLM_FAIL:
        utterance = render("fail_nlu")
        spoken = await synthesize(utterance, "fail", cfg)
        unknown = {"name": "UNKNOWN", "slots": {}, "raw_text": corrected}
        bus.broadcast(
            "intent",
            {
                "clientId": client_id,
                "channel": channel,
                "intent": _wire_intent(unknown),
                "ok": False,
                "errorCode": "nlu_fail",
                "utterance": utterance,
            },
        )
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
            "intent": _wire_intent(unknown),
            "errorCode": "nlu_fail",
            "utterance": utterance,
            "intents": [],
        }
        if spoken["audio_base64"] is not None:
            out["audioBase64"] = spoken["audio_base64"]
        if spoken["engine"]:
            out["ttsEngine"] = spoken["engine"]
        out["target"] = "device"
        return out

    wire_intents = [_wire_intent(i) for i in intent_list]
    out: dict | None = None
    steps = [intent_step(it, i) for i, it in enumerate(intent_list)]
    _mission(tracer, "mission_begin", trace_id, "voice", corrected, steps)
    calls = " → ".join(s["call"] for s in steps)
    _emit(tracer, "PLAN", f"拆解为 {len(steps)} 步：{calls}", status="ok", trace_id=trace_id)
    # 复合指令：按顺序逐个执行，任一失败即中断并播报该失败；
    # 遇到 awaiting（追问/待确认挂起）也中断，等用户下一轮应答；
    # 每个意图的 TTS 话术依次广播（保持原 broadcast 节奏）
    finished = True
    for idx, intent in enumerate(intent_list):
        _mission(tracer, "mission_step", trace_id, f"s{idx}", "running")
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
        utterance_text = utterance or ""
        if result.get("awaiting"):
            finished = False
            _mission(tracer, "mission_step", trace_id, f"s{idx}", "waiting", utterance_text)
            _emit(tracer, "ASK", utterance_text or "等待确认", status="warn", trace_id=trace_id)
            for j in range(idx + 1, len(intent_list)):
                _mission(tracer, "mission_step", trace_id, f"s{j}", "skipped")
            _mission(tracer, "mission_status", trace_id, "waiting")
            break
        if not result["ok"]:
            finished = False
            _mission(tracer, "mission_step", trace_id, f"s{idx}", "failed", utterance_text)
            code = result.get("error_code")
            if code == "no_site":
                _emit(tracer, "GUARD", "未持有现场锁，拒绝运动指令", status="fail", trace_id=trace_id)
            elif code == "flow_running":
                _emit(tracer, "GUARD", "任务流执行中，拒绝运动指令", status="fail", trace_id=trace_id)
            elif code == "not_armed":
                _emit(tracer, "GUARD", "未唤醒，拒绝运动指令", status="fail", trace_id=trace_id)
            elif code == "timeout":
                _emit(tracer, "GUARD", "等待应答超时，已放弃", status="fail", trace_id=trace_id)
            else:
                _emit(tracer, "TASK", f"执行失败：{utterance_text}", status="fail", trace_id=trace_id)
            for j in range(idx + 1, len(intent_list)):
                _mission(tracer, "mission_step", trace_id, f"s{j}", "skipped")
            _mission(tracer, "mission_status", trace_id, "failed")
            break
        step_status = "sent" if intent["name"] in SENT_INTENTS else "done"
        _mission(tracer, "mission_step", trace_id, f"s{idx}", step_status)
    if finished:
        _mission(tracer, "mission_status", trace_id, "succeeded")
    if out is None:
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
    tts_selected = current_tts_model(cfg)
    tts_voice_selected = current_tts_voice(cfg)
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
    tts_items = [
        {"id": mid, "name": tts_display_name(mid), "latencyMs": None, "error": None}
        for mid in list_tts_models(cfg)
    ]
    tts_voice_items = [
        {"id": it["id"], "name": it["name"], "latencyMs": None, "error": None}
        for it in list_tts_voices(cfg)
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
    nlu = cfg.get("nlu") or {}
    llm_cfg = cfg.get("llm") or {}
    return {
        "asr": {"selected": asr_selected, "items": asr_items},
        "tts": {"selected": tts_selected, "items": tts_items},
        "ttsVoice": {"selected": tts_voice_selected, "items": tts_voice_items},
        "llm": {"selected": llm_selected, "items": llm_items},
        "nluRulesEnabled": bool(nlu.get("rules_enabled", True)),
        "nluMaxIntents": clamp_max_intents(nlu.get("max_intents", 5)),
        "llmThinkingEnabled": bool(llm_cfg.get("thinking_enabled", False)),
    }


@router.put("/api/voice/providers")
async def voice_providers_put(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    cfg = request.app.state.cfg
    asr_model = str(body.get("asrModel") or current_asr_model(cfg)).strip()
    tts_model = str(body.get("ttsModel") or current_tts_model(cfg)).strip()
    llm_model = str(body.get("llmModel") or current_llm_model(cfg)).strip()
    if not asr_model:
        return JSONResponse(status_code=400, content={"succeed": False, "error": "empty_asr"})
    allowed_tts = set(list_tts_models(cfg))
    if tts_model not in allowed_tts:
        return JSONResponse(status_code=400, content={"succeed": False, "error": "bad_tts"})
    if llm_model not in LLM_CATALOG_IDS:
        return JSONResponse(status_code=400, content={"succeed": False, "error": "bad_llm"})
    cfg.setdefault("asr", {}).setdefault("cloud", {})["model"] = asr_model
    tts_cfg = cfg.setdefault("tts", {})
    tts_cfg["model"] = tts_model
    tts_voice = current_tts_voice(cfg)
    if is_g2a_tts(tts_model):
        allowed_voices = {oid for oid, _ in G2A_TTS_VOICES}
        requested = str(body.get("ttsVoice") or tts_voice or "").strip().lower()
        tts_voice = requested if requested in allowed_voices else "eve"
        tts_cfg["voice"] = tts_voice
    cfg.setdefault("llm", {})["model"] = llm_model
    nlu = cfg.setdefault("nlu", {})
    if "nluRulesEnabled" in body:
        nlu["rules_enabled"] = bool(body["nluRulesEnabled"])
    if "nluMaxIntents" in body:
        nlu["max_intents"] = clamp_max_intents(body["nluMaxIntents"])
    nlu.setdefault("rules_enabled", True)
    nlu.setdefault("max_intents", 5)
    nlu["max_intents"] = clamp_max_intents(nlu.get("max_intents", 5))
    llm_cfg = cfg.setdefault("llm", {})
    if "llmThinkingEnabled" in body:
        llm_cfg["thinking_enabled"] = bool(body["llmThinkingEnabled"])
    llm_cfg.setdefault("thinking_enabled", False)
    save_runtime_models(cfg)
    return {
        "succeed": True,
        "asrModel": asr_model,
        "ttsModel": tts_model,
        "ttsVoice": current_tts_voice(cfg),
        "llmModel": llm_model,
        "nluRulesEnabled": bool(nlu.get("rules_enabled", True)),
        "nluMaxIntents": clamp_max_intents(nlu.get("max_intents", 5)),
        "llmThinkingEnabled": bool(llm_cfg.get("thinking_enabled", False)),
    }


def _tts_cache_payload(request: Request) -> dict:
    cfg = request.app.state.cfg
    st = cache_stats(cfg)
    pw = getattr(request.app.state, "prewarm_stats", None) or {}
    task = getattr(request.app.state, "prewarm_task", None)
    running = bool(pw.get("running")) or (task is not None and not task.done())
    return {
        "files": st["files"],
        "bytes": st["bytes"],
        "model": current_tts_model(cfg),
        "voice": current_tts_voice(cfg),
        "prewarm": {
            "running": running,
            "total": int(pw.get("total") or 0),
            "hit": int(pw.get("hit") or 0),
            "synthesized": int(pw.get("synthesized") or 0),
            "failed": int(pw.get("failed") or 0),
        },
    }


@router.get("/api/voice/tts-cache")
async def tts_cache_get(request: Request, client_id: str = Depends(auth)):
    return _tts_cache_payload(request)


@router.post("/api/voice/tts-cache/clear")
async def tts_cache_clear(request: Request, client_id: str = Depends(auth)):
    await stop_prewarm(request.app)
    cleared = cache_clear(request.app.state.cfg)
    out = _tts_cache_payload(request)
    out["succeed"] = True
    out["removed"] = cleared["removed"]
    print(
        f"[forkai-core] TTS cache cleared removed={cleared['removed']} "
        f"filesBefore={cleared['filesBefore']}",
        flush=True,
    )
    return out


@router.post("/api/voice/tts-cache/prewarm")
async def tts_cache_prewarm(request: Request, client_id: str = Depends(auth)):
    if not start_prewarm(request.app):
        return JSONResponse(
            status_code=409,
            content={"succeed": False, "error": "prewarm_running", **_tts_cache_payload(request)},
        )
    out = _tts_cache_payload(request)
    out["succeed"] = True
    return out


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

    async def run_final(text: str, trace_id: str | None) -> None:
        text = text.strip()
        if not text:
            await skip_empty_asr("empty_transcription")
            return
        bus.broadcast(
            "asr_final", {"clientId": client_id, "channel": channel, "text": text}
        )
        out = await run_utterance(ws, client_id, channel, text, trace_id)
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
            tr = _tracer_of(ws)
            tid = tr.begin("utterance", "语音输入") if tr is not None else None
            t_asr = time.perf_counter()
            _emit(
                tr,
                "HEAR",
                f"提交识别 {stats['ms']}ms 音频（{model}）",
                status="running",
                trace_id=tid,
                data={"metric": {"kind": "asr", "model": model, "where": "cloud", "ok": False}},
            )
            print(
                f"[forkai-core] ASR submit model={model} pcm_ms={stats['ms']} "
                f"pcm_bytes={stats['bytes']} peak={stats['peak']}",
                flush=True,
            )
            wav = pcm16_to_wav(pcm, sr)
            if stats["bytes"] == 0:
                _emit(tr, "HEAR", "识别结果为空", status="warn", trace_id=tid,
                      dur_ms=int((time.perf_counter() - t_asr) * 1000))
                await skip_empty_asr("empty_audio")
                continue
            if not cloud_asr_enabled(cfg):
                print("[forkai-core] ASR fail reason=disabled_or_no_key", flush=True)
                _emit(
                    tr,
                    "HEAR",
                    "识别失败：disabled_or_no_key",
                    status="fail",
                    trace_id=tid,
                    dur_ms=int((time.perf_counter() - t_asr) * 1000),
                    data={"metric": {"kind": "asr", "model": model, "where": "cloud", "ok": False}},
                )
                out = await speak_asr_fail()
                await _ws_send_json(ws, {"type": "final", "text": "", **out})
                continue
            try:
                text = await transcribe(cfg, wav)
            except ASRError as e:
                reason = str(e)
                if reason in ("empty_audio", "empty_transcription"):
                    _emit(tr, "HEAR", "识别结果为空", status="warn", trace_id=tid,
                          dur_ms=int((time.perf_counter() - t_asr) * 1000))
                    await skip_empty_asr(reason)
                    continue
                print(
                    f"[forkai-core] ASR fail reason={reason} model={model} "
                    f"pcm_ms={stats['ms']} peak={stats['peak']}",
                    flush=True,
                )
                _emit(
                    tr,
                    "HEAR",
                    f"识别失败：{reason}",
                    status="fail",
                    trace_id=tid,
                    dur_ms=int((time.perf_counter() - t_asr) * 1000),
                    data={"metric": {"kind": "asr", "model": model, "where": "cloud", "ok": False}},
                )
                out = await speak_asr_fail()
                await _ws_send_json(ws, {"type": "final", "text": "", **out})
                continue
            heard = (text or "").strip()
            asr_ms = int((time.perf_counter() - t_asr) * 1000)
            if not heard:
                _emit(tr, "HEAR", "识别结果为空", status="warn", trace_id=tid, dur_ms=asr_ms)
            else:
                _emit(
                    tr,
                    "HEAR",
                    f"识别：{heard}",
                    status="ok",
                    trace_id=tid,
                    dur_ms=asr_ms,
                    data={"metric": {"kind": "asr", "model": model, "where": "cloud", "ok": True, "ms": asr_ms}},
                )
            await run_final(text, tid)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        pass
