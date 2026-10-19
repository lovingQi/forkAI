import { defineStore } from 'pinia'
import { getTrace, type Mission, type TraceEntry, type TraceMetric } from '@/api/trace'

const MAX = 500

export const useTraceStore = defineStore('trace', {
  state: () => ({
    entries: [] as TraceEntry[],
    mission: null as Mission | null,
    hudVisible: true
  }),
  getters: {
    caption(state): TraceEntry | null {
      for (let i = state.entries.length - 1; i >= 0; i--) {
        if (state.entries[i].cat !== 'OBS') return state.entries[i]
      }
      return null
    },
    metricSeries(state): { cat: string; ms: number; ok: boolean }[] {
      const out: { cat: string; ms: number; ok: boolean }[] = []
      for (const e of state.entries) {
        const ms = e.data?.metric?.ms
        if (typeof ms === 'number') {
          out.push({ cat: e.cat, ms, ok: e.data?.metric?.ok !== false && e.status !== 'fail' })
        }
      }
      return out.slice(-24)
    }
  },
  actions: {
    lastMetric(kind: TraceMetric['kind']): TraceEntry | null {
      for (let i = this.entries.length - 1; i >= 0; i--) {
        if (this.entries[i].data?.metric?.kind === kind) return this.entries[i]
      }
      return null
    },
    onTrace(entry: TraceEntry) {
      if (!entry || typeof entry.id !== 'number') return
      if (this.entries.some((e) => e.id === entry.id)) return
      this.entries.push(entry)
      this.entries.sort((a, b) => a.id - b.id)
      if (this.entries.length > MAX) this.entries.splice(0, this.entries.length - MAX)
    },
    onMission(mission: Mission | null) {
      this.mission = mission
    },
    async loadHistory() {
      try {
        const data = await getTrace(300)
        for (const e of data.entries) this.onTrace(e)
        if (data.mission) this.mission = data.mission
      } catch {
        /* 未配对或通道未就绪 */
      }
    }
  }
})
