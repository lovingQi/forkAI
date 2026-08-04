/**
 * Speech adapter: pluggable ASR/TTS.
 * TTS 默认使用 piper 离线中文引擎（services/speech/piper/），返回真实 wav base64。
 * piper 不可用（二进制/模型缺失或合成失败）时回退 mock，audioBase64 为 null，
 * 前端退到浏览器 speechSynthesis。
 * ASR 仍为 mock（文本透传），可替换真引擎。
 */
import express from 'express'
import cors from 'cors'
import { execFile } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const port = Number(process.env.SPEECH_PORT || 19001)

// piper 路径（二进制在 piper/piper/，模型在 piper/）
const PIPER_DIR = path.resolve(__dirname, '../piper')
const PIPER_BIN = path.join(PIPER_DIR, 'piper', 'piper')
const PIPER_LIB = path.join(PIPER_DIR, 'piper')
const PIPER_MODEL = path.join(PIPER_DIR, 'zh_CN-huayan-medium.onnx')
const PIPER_CONFIG = path.join(PIPER_DIR, 'zh_CN-huayan-medium.onnx.json')

const piperAvailable =
  fs.existsSync(PIPER_BIN) && fs.existsSync(PIPER_MODEL) && fs.existsSync(PIPER_CONFIG)

/** 调 piper CLI 合成中文 wav，返回 base64；失败抛错由调用方回退。 */
function synthesizeWithPiper(text: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const outFile = path.join(
      os.tmpdir(),
      `forkai_tts_${Date.now()}_${Math.random().toString(36).slice(2)}.wav`
    )
    const child = execFile(
      PIPER_BIN,
      ['--model', PIPER_MODEL, '--config', PIPER_CONFIG, '--output_file', outFile],
      { env: { ...process.env, LD_LIBRARY_PATH: `${PIPER_LIB}:${process.env.LD_LIBRARY_PATH || ''}` } },
      (err) => {
        if (err) {
          fs.unlink(outFile, () => {})
          reject(err)
          return
        }
        fs.readFile(outFile, (readErr, buf) => {
          fs.unlink(outFile, () => {})
          if (readErr) reject(readErr)
          else resolve(buf.toString('base64'))
        })
      }
    )
    child.on('error', reject)
    if (child.stdin) {
      child.stdin.write(text)
      child.stdin.end()
    }
  })
}

const app = express()
app.use(cors())
app.use(express.json({ limit: '8mb' }))

app.get('/health', (_req, res) => {
  res.json({ ok: true, asr: 'mock', tts: piperAvailable ? 'piper' : 'mock' })
})

/** Final text ASR helper for debugging (browser can skip mic). */
app.post('/asr/text', (req, res) => {
  const text = String(req.body?.text || '')
  res.json({ text, partial: false, engine: 'passthrough' })
})

/**
 * Accept base64 audio; mock cannot decode — returns empty.
 * Real engine should return { text }.
 */
app.post('/asr/once', (req, res) => {
  const hint = String(req.body?.hintText || '')
  res.json({
    text: hint,
    partial: false,
    engine: 'mock',
    note: 'Replace with offline ASR; hintText used when provided'
  })
})

/**
 * TTS：piper 离线中文合成，返回真实 wav base64。
 * style: ok | fail | wake —— 通过 length_scale 微调语速。
 * piper 不可用时回退 mock（audioBase64=null，前端用 speechSynthesis）。
 */
app.post('/tts/speak', async (req, res) => {
  const text = String(req.body?.text || '')
  const style = String(req.body?.style || 'ok')
  const rate = style === 'fail' ? 0.9 : style === 'wake' ? 1.1 : 1.05

  if (piperAvailable && text) {
    try {
      const audioBase64 = await synthesizeWithPiper(text)
      res.json({ text, style, rate, voice: 'zh_CN-huayan-medium', audioBase64, engine: 'piper' })
      return
    } catch (e) {
      console.error('[forkai-speech] piper 合成失败，回退 mock:', (e as any)?.message || e)
    }
  }
  res.json({
    text,
    style,
    rate,
    voice: 'calm-female-zh',
    audioBase64: null,
    engine: 'mock',
    note: 'piper unavailable; UI fallback to speechSynthesis'
  })
})

app.listen(port, '0.0.0.0', () => {
  console.log(
    `[forkai-speech] http://0.0.0.0:${port} tts=${piperAvailable ? 'piper(zh_CN-huayan-medium)' : 'mock(无 piper)'} asr=mock`
  )
})
