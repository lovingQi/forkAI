import type { IntentName, ParsedIntent } from '@forkai/shared'

function norm(text: string): string {
  return text
    .replace(/\s+/g, '')
    .replace(/[，,。.!！？?]/g, '')
    .toLowerCase()
}

const RULES: Array<{ name: IntentName; patterns: RegExp[]; slot?: (m: RegExpMatchArray) => Record<string, string | number> }> = [
  { name: 'STOP', patterns: [/停止/, /停下/, /停车/, /急停/, /^停$/] },
  { name: 'MOVE_FWD', patterns: [/前进/, /往前/, /向前/, /走吧/, /^走$/] },
  { name: 'MOVE_BACK', patterns: [/后退/, /往后/, /向后/, /倒车/] },
  { name: 'TURN_LEFT', patterns: [/左转/, /向左/, /往左/] },
  { name: 'TURN_RIGHT', patterns: [/右转/, /向右/, /往右/] },
  {
    name: 'SPEED_SET',
    patterns: [/速度(?:调到|设为|到)?(\d{1,3})%?/, /(\d{1,3})\s*%/],
    slot: (m) => ({ n: Math.min(100, Number(m[1] || m[0])) })
  },
  { name: 'SPEED_UP', patterns: [/快点/, /加速/, /快一点/] },
  { name: 'SPEED_DOWN', patterns: [/慢点/, /减速/, /慢一点/] },
  { name: 'IDLE', patterns: [/空闲/, /待机/] },
  { name: 'DOCK', patterns: [/回充/, /去充电/, /充电/] },
  {
    name: 'GOTO_GOAL',
    patterns: [/去(?:往|到)?(.+?)(?:站点|点)?$/, /前往(.+)$/],
    slot: (m) => ({ goal: String(m[1] || '').replace(/(站点|点)$/, '') })
  },
  { name: 'QUERY_BATTERY', patterns: [/电量/, /还有多少电/, /电池/] },
  { name: 'QUERY_MODE', patterns: [/什么模式/, /当前模式/, /模式/] },
  { name: 'QUERY_POSE', patterns: [/位置/, /在哪/, /坐标/] },
  { name: 'QUERY_FORK_HEIGHT', patterns: [/叉高/, /货叉多高/, /货叉高度/] },
  { name: 'QUERY_MOTOR', patterns: [/电机/, /使能/] },
  { name: 'QUERY_ALARM', patterns: [/告警/, /报警/, /怎么了/, /为什么停/] }
]

export function parseIntent(rawText: string): ParsedIntent {
  const text = norm(rawText)
  if (!text) {
    return { name: 'UNKNOWN', slots: {}, rawText }
  }
  for (const rule of RULES) {
    for (const re of rule.patterns) {
      const m = text.match(re)
      if (m) {
        return {
          name: rule.name,
          slots: rule.slot ? rule.slot(m) : {},
          rawText
        }
      }
    }
  }
  return { name: 'UNKNOWN', slots: {}, rawText }
}

export function matchWakeWord(rawText: string, wakeWords: string[]): boolean {
  const text = norm(rawText)
  return wakeWords.some((w) => text.includes(norm(w)))
}
