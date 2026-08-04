import { randomInt } from 'node:crypto'
import { v4 as uuid } from 'uuid'
import type { GatewayConfig } from './config.js'

export interface PairPending {
  code: string
  expiresAt: number
}

export interface PairTokenRecord {
  clientId: string
  expiresAt: number
}

export interface SiteSession {
  siteSessionId: string
  holderClientId: string
  expiresAt: number
}

export class SessionManager {
  private pairPending: PairPending | null = null
  private tokens = new Map<string, PairTokenRecord>()
  private site: SiteSession | null = null
  private siteNonce: string
  private siteCode: string
  private wakeArmedUntil = 0
  private wakeClientId: string | null = null

  constructor(private cfg: GatewayConfig) {
    this.siteNonce = uuid()
    this.siteCode = this.genCode()
  }

  private genCode(): string {
    return String(randomInt(100000, 999999))
  }

  startPair(): PairPending {
    this.pairPending = {
      code: this.genCode(),
      expiresAt: Date.now() + this.cfg.pairCodeTtlMs
    }
    return this.pairPending
  }

  getPairPending(): PairPending | null {
    if (this.pairPending && this.pairPending.expiresAt < Date.now()) {
      this.pairPending = null
    }
    return this.pairPending
  }

  confirmPair(code: string): { pairToken: string; expiresAt: number; clientId: string } | null {
    const pending = this.getPairPending()
    if (!pending || pending.code !== code) return null
    const clientId = uuid()
    const pairToken = uuid()
    const expiresAt = Date.now() + this.cfg.pairTokenTtlMs
    this.tokens.set(pairToken, { clientId, expiresAt })
    this.pairPending = null
    return { pairToken, expiresAt, clientId }
  }

  resolveToken(pairToken: string | undefined): PairTokenRecord | null {
    if (!pairToken) return null
    const rec = this.tokens.get(pairToken)
    if (!rec) return null
    if (rec.expiresAt < Date.now()) {
      this.tokens.delete(pairToken)
      return null
    }
    return rec
  }

  rotateSiteCodes() {
    this.siteNonce = uuid()
    this.siteCode = this.genCode()
  }

  getSitePublic() {
    this.ensureSiteValid()
    return {
      vehicleId: this.cfg.vehicleId,
      nonce: this.siteNonce,
      code: this.siteCode,
      session: this.site
        ? {
            siteSessionId: this.site.siteSessionId,
            holderClientId: this.site.holderClientId,
            expiresAt: this.site.expiresAt
          }
        : null
    }
  }

  private ensureSiteValid() {
    if (this.site && this.site.expiresAt < Date.now()) {
      this.site = null
    }
  }

  unlockSite(
    clientId: string,
    opts: { nonce?: string; code?: string; force?: boolean }
  ):
    | { ok: true; session: SiteSession; stolen?: boolean }
    | { ok: false; error: 'invalid' | 'held'; holderClientId?: string } {
    this.ensureSiteValid()
    const okNonce = opts.nonce && opts.nonce === this.siteNonce
    const okCode = opts.code && opts.code === this.siteCode
    if (!okNonce && !okCode) return { ok: false, error: 'invalid' }

    if (this.site && this.site.holderClientId !== clientId) {
      if (!opts.force) {
        return { ok: false, error: 'held', holderClientId: this.site.holderClientId }
      }
    }

    const stolen = !!(this.site && this.site.holderClientId !== clientId)
    this.site = {
      siteSessionId: uuid(),
      holderClientId: clientId,
      expiresAt: Date.now() + this.cfg.siteSessionTtlMs
    }
    this.rotateSiteCodes()
    return { ok: true, session: this.site, stolen }
  }

  endSite(clientId: string, force = false): boolean {
    this.ensureSiteValid()
    if (!this.site) return true
    if (!force && this.site.holderClientId !== clientId) return false
    this.site = null
    return true
  }

  hasSite(clientId: string): boolean {
    this.ensureSiteValid()
    return !!(this.site && this.site.holderClientId === clientId)
  }

  hasAnySite(): boolean {
    this.ensureSiteValid()
    return !!this.site
  }

  getSiteHolder(): string | null {
    this.ensureSiteValid()
    return this.site?.holderClientId ?? null
  }

  armWake(clientId: string) {
    this.wakeArmedUntil = Date.now() + this.cfg.wakeArmMs
    this.wakeClientId = clientId
  }

  isWakeArmed(clientId: string): boolean {
    if (Date.now() > this.wakeArmedUntil) {
      this.wakeArmedUntil = 0
      this.wakeClientId = null
      return false
    }
    return this.wakeClientId === clientId
  }

  clearWakeArm() {
    this.wakeArmedUntil = 0
    this.wakeClientId = null
  }
}
