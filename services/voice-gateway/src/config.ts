import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import yaml from 'yaml'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export interface GatewayConfig {
  jarvis: { baseUrl: string }
  speech: { baseUrl: string }
  server: { host: string; port: number }
  vehicleId: string
  watchdogMs: number
  siteSessionTtlMs: number
  pairCodeTtlMs: number
  pairTokenTtlMs: number
  wakeArmMs: number
  speed: { default: number; max: number; step: number }
  wakeWords: string[]
  speak: {
    cabinMove: 'vehicle' | 'device' | 'both'
    pttMove: 'vehicle' | 'device' | 'both'
    query: 'vehicle' | 'device' | 'both'
  }
}

export function loadConfig(): GatewayConfig {
  const configPath =
    process.env.FORKAI_GATEWAY_CONFIG ||
    path.resolve(__dirname, '../config/gateway.config.yaml')
  const raw = fs.readFileSync(configPath, 'utf8')
  const cfg = yaml.parse(raw) as GatewayConfig
  if (process.env.JARVIS_BASE_URL) cfg.jarvis.baseUrl = process.env.JARVIS_BASE_URL
  if (process.env.SPEECH_BASE_URL) cfg.speech.baseUrl = process.env.SPEECH_BASE_URL
  if (process.env.FORKAI_PORT) cfg.server.port = Number(process.env.FORKAI_PORT)
  if (process.env.VEHICLE_ID) cfg.vehicleId = process.env.VEHICLE_ID
  return cfg
}
