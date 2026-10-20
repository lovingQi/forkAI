import { defineStore } from 'pinia'
import { activeMappingTask, mappingActivity, mappingMaps, mappingProfiles, mappingStatus, type MappingTask } from '@/api/mapping'

export const useMappingStore = defineStore('mapping', {
  state: () => ({
    status: null as any,
    profiles: [] as any[],
    maps: [] as any[],
    task: null as MappingTask | null,
    diagnostics: [] as string[],
    connected: false
  }),
  actions: {
    async refresh() {
      const [status, profiles, maps] = await Promise.all([mappingStatus(), mappingProfiles(), mappingMaps()])
      this.status = status
      this.profiles = profiles
      this.maps = maps
      this.task = await activeMappingTask()
    },
    async pollActivity(after: number, generation: { value: number }, current: number) {
      if (!this.task) return after
      try {
        const page = await mappingActivity(this.task.id, after)
        if (generation.value !== current) return after
        if (page.reset) {
          this.diagnostics = []
          after = 0
        }
        for (const event of page.events) {
          if (event.type === 'diagnostic' && event.data?.message) {
            this.diagnostics = [...this.diagnostics, String(event.data.message)].slice(-20)
          }
        }
        const latest = await activeMappingTask()
        if (generation.value !== current) return page.latestSequence
        if (latest && latest.id === this.task.id) this.task = latest
        else if (!latest && this.task && !['completed', 'failed', 'cancelled', 'interrupted'].includes(this.task.status)) {
          this.task = latest
        }
        return page.latestSequence
      } catch {
        return after
      }
    }
  }
})
