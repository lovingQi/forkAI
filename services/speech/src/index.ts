/**
 * Speech adapter (V1): pluggable ASR/TTS.
 * Default mock returns empty ASR and no audio; gateway still speaks via text events.
 * Replace internals with offline ASR/TTS engines without changing HTTP contract.
 */
import express from 'express'
import cors from 'cors'

const port = Number(process.env.SPEECH_PORT || 19001)
const app = express()
app.use(cors())
app.use(express.json({ limit: '8mb' }))

app.get('/health', (_req, res) => {
  res.json({ ok: true, asr: 'mock', tts: 'mock' })
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
 * Offline TTS placeholder. Returns no audioBase64; clients use Web Speech or wait for real engine.
 * style: ok | fail | wake — real engine should adjust rate.
 */
app.post('/tts/speak', (req, res) => {
  const text = String(req.body?.text || '')
  const style = String(req.body?.style || 'ok')
  const rate = style === 'fail' ? 0.9 : style === 'wake' ? 1.1 : 1.05
  res.json({
    text,
    style,
    rate,
    voice: 'calm-female-zh',
    audioBase64: null,
    engine: 'mock',
    note: 'No PCM yet; UI may fallback to speechSynthesis'
  })
})

app.listen(port, '0.0.0.0', () => {
  console.log(`[forkai-speech] http://0.0.0.0:${port} (mock ASR/TTS)`)
})
