import { MOTION_INTENTS, type ParsedIntent, type VoiceChannel } from '@forkai/shared'
import type { JarvisClient } from './jarvis.js'
import type { SessionManager } from './session.js'
import type { MotionWatchdog } from './watchdog.js'
import type { GatewayConfig } from './config.js'

export interface HandleContext {
  clientId: string
  channel: VoiceChannel
  wakeOk?: boolean
}

export interface HandleResult {
  ok: boolean
  errorCode?: string
  utteranceKey: string
  utteranceParams?: Record<string, string | number>
  speakKind: 'move' | 'query' | 'wake' | 'fail'
  speakStyle: 'ok' | 'fail' | 'wake'
}

function ok(key: string, kind: HandleResult['speakKind']): HandleResult {
  return { ok: true, utteranceKey: key, speakKind: kind, speakStyle: 'ok' }
}

function fail(code: string, key: string): HandleResult {
  return { ok: false, errorCode: code, utteranceKey: key, speakKind: 'fail', speakStyle: 'fail' }
}

export class IntentExecutor {
  constructor(
    private cfg: GatewayConfig,
    private sessions: SessionManager,
    private jarvis: JarvisClient,
    private watchdog: MotionWatchdog
  ) {}

  async handle(intent: ParsedIntent, ctx: HandleContext): Promise<HandleResult> {
    if (intent.name === 'UNKNOWN') return fail('unknown', 'fail_unknown')

    if (intent.name === 'STOP') {
      try {
        await this.watchdog.stop(false)
      } catch {
        /* still acknowledge stop */
      }
      this.sessions.clearWakeArm()
      return ok('stop', 'move')
    }

    const needsSite = (MOTION_INTENTS as string[]).includes(intent.name)
    if (needsSite) {
      if (!this.sessions.hasSite(ctx.clientId)) return fail('no_site', 'fail_no_site')
      if (ctx.channel === 'cabin' && !ctx.wakeOk && !this.sessions.isWakeArmed(ctx.clientId)) {
        return {
          ok: false,
          errorCode: 'not_armed',
          utteranceKey: 'fail_generic',
          utteranceParams: { reason: '请先说玖物玖物' },
          speakKind: 'fail',
          speakStyle: 'fail'
        }
      }
    }

    if (intent.name === 'MOVE_FWD') {
      try {
        await this.watchdog.drive(1, 0)
      } catch (e: any) {
        return {
          ok: false,
          utteranceKey: 'fail_generic',
          utteranceParams: { reason: e?.message || '车端不可达' },
          speakKind: 'fail',
          speakStyle: 'fail'
        }
      }
      return ok('move_fwd', 'move')
    }
    if (intent.name === 'MOVE_BACK') {
      try {
        await this.watchdog.drive(-1, 0)
      } catch (e: any) {
        return {
          ok: false,
          utteranceKey: 'fail_generic',
          utteranceParams: { reason: e?.message || '车端不可达' },
          speakKind: 'fail',
          speakStyle: 'fail'
        }
      }
      return ok('move_back', 'move')
    }
    if (intent.name === 'TURN_LEFT') {
      try {
        await this.watchdog.drive(0, 1)
      } catch (e: any) {
        return {
          ok: false,
          utteranceKey: 'fail_generic',
          utteranceParams: { reason: e?.message || '车端不可达' },
          speakKind: 'fail',
          speakStyle: 'fail'
        }
      }
      return ok('turn_left', 'move')
    }
    if (intent.name === 'TURN_RIGHT') {
      try {
        await this.watchdog.drive(0, -1)
      } catch (e: any) {
        return {
          ok: false,
          utteranceKey: 'fail_generic',
          utteranceParams: { reason: e?.message || '车端不可达' },
          speakKind: 'fail',
          speakStyle: 'fail'
        }
      }
      return ok('turn_right', 'move')
    }
    if (intent.name === 'SPEED_UP') {
      this.watchdog.bumpSpeed(this.cfg.speed.step)
      return ok('speed_up', 'move')
    }
    if (intent.name === 'SPEED_DOWN') {
      this.watchdog.bumpSpeed(-this.cfg.speed.step)
      return ok('speed_down', 'move')
    }
    if (intent.name === 'SPEED_SET') {
      this.watchdog.setSpeed(Number(intent.slots.n || this.watchdog.speed))
      return {
        ok: true,
        utteranceKey: 'speed_set',
        utteranceParams: { n: this.watchdog.speed },
        speakKind: 'move',
        speakStyle: 'ok'
      }
    }
    if (intent.name === 'IDLE') {
      await this.jarvis.control('idle')
      return ok('idle', 'move')
    }
    if (intent.name === 'DOCK') {
      await this.jarvis.control('dock')
      return ok('dock', 'move')
    }
    if (intent.name === 'GOTO_GOAL') {
      const goal = String(intent.slots.goal || '').trim()
      if (!goal) return fail('goto', 'fail_goto')
      const res = await this.jarvis.control('goto', { target: 'goal', goal })
      if (res && res.succeed === false) return fail('goto', 'fail_goto')
      return {
        ok: true,
        utteranceKey: 'goto',
        utteranceParams: { goal },
        speakKind: 'move',
        speakStyle: 'ok'
      }
    }
    return this.handleQuery(intent.name)
  }

  private async handleQuery(name: ParsedIntent['name']): Promise<HandleResult> {
    let state: any = {}
    try {
      state = await this.jarvis.getState()
    } catch {
      return {
        ok: false,
        utteranceKey: 'fail_generic',
        utteranceParams: { reason: '无法读取车况' },
        speakKind: 'fail',
        speakStyle: 'fail'
      }
    }
    if (name === 'QUERY_BATTERY') {
      return {
        ok: true,
        utteranceKey: 'query_battery',
        utteranceParams: { n: state.battery ?? 0 },
        speakKind: 'query',
        speakStyle: 'ok'
      }
    }
    if (name === 'QUERY_MODE') {
      return {
        ok: true,
        utteranceKey: 'query_mode',
        utteranceParams: { mode: state.status || state.mode || '未知' },
        speakKind: 'query',
        speakStyle: 'ok'
      }
    }
    if (name === 'QUERY_POSE') {
      const pose = String(state.pose || '0,0,0').split(',')
      return {
        ok: true,
        utteranceKey: 'query_pose',
        utteranceParams: {
          x: Math.round(Number(pose[0] || 0)),
          y: Math.round(Number(pose[1] || 0))
        },
        speakKind: 'query',
        speakStyle: 'ok'
      }
    }
    if (name === 'QUERY_FORK_HEIGHT') {
      return {
        ok: true,
        utteranceKey: 'query_fork',
        utteranceParams: { n: state.fork_info?.fork_height ?? 0 },
        speakKind: 'query',
        speakStyle: 'ok'
      }
    }
    if (name === 'QUERY_MOTOR') {
      return {
        ok: true,
        utteranceKey: 'query_motor',
        utteranceParams: { state: state.motor ? '已使能' : '已断开' },
        speakKind: 'query',
        speakStyle: 'ok'
      }
    }
    if (name === 'QUERY_ALARM') {
      const alarm = state.alarm || 'normal'
      const map: Record<string, string> = {
        normal: '正常',
        estop: '急停',
        lost: '定位丢失',
        stuck: '受困'
      }
      return {
        ok: true,
        utteranceKey: 'query_alarm',
        utteranceParams: { alarm: map[alarm] || alarm },
        speakKind: 'query',
        speakStyle: 'ok'
      }
    }
    return fail('unknown', 'fail_unknown')
  }
}
