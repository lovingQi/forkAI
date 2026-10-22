<template>
  <div class="param-editor">
    <div v-for="(meta, key) in items" :key="key" class="row" @focusin="emit('focus', String(key))" @click="emit('focus', String(key))">
      <div class="label">
        <strong>{{ meta.name_zh || key }}</strong>
        <span>{{ meta.desc_zh || meta.desc || '' }}</span>
      </div>
      <div class="field">
        <el-switch
          v-if="meta.type === 'bool'"
          :model-value="!!draft[key]"
          :disabled="!canEdit(meta)"
          @change="onBool(key, $event)"
        />
        <el-input-number
          v-else-if="meta.type === 'int' || meta.type === 'double'"
          :model-value="draft[key]"
          :disabled="!canEdit(meta)"
          :min="range(meta)[0]"
          :max="range(meta)[1]"
          :step="meta.type === 'int' ? 1 : 0.01"
          controls-position="right"
          @change="onNumber(key, $event)"
        />
        <el-input v-else :model-value="String(draft[key] ?? '')" :disabled="!canEdit(meta)" @input="onText(key, $event)" />
      </div>
    </div>
    <el-button v-if="editing" type="primary" :disabled="!diff.length" @click="dialog = true">保存</el-button>
    <el-dialog v-model="dialog" title="确认参数改动" width="560px">
      <el-table :data="diff" size="small">
        <el-table-column prop="section" label="段" width="100" />
        <el-table-column prop="name" label="参数" />
        <el-table-column prop="from" label="原值" />
        <el-table-column prop="to" label="新值" />
      </el-table>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="confirm">确认保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, watch, ref } from 'vue'
import type { ParamItem } from '@/api/console'

const props = defineProps<{
  section: string
  items: Record<string, ParamItem>
  editing: boolean
}>()
const emit = defineEmits<{
  submit: [changes: Record<string, any>]
  draft: [changes: Record<string, any>]
  focus: [key: string]
}>()
const draft = reactive<Record<string, any>>({})
const dialog = ref(false)

watch(
  () => props.items,
  (items) => {
    for (const [key, meta] of Object.entries(items || {})) draft[key] = meta.default
  },
  { immediate: true, deep: true }
)

function canEdit(meta: ParamItem) {
  return props.editing && !!meta.editable
}
function setDraft(key: string | number, value: unknown) {
  draft[String(key)] = value
}
function onBool(key: string | number, value: unknown) {
  setDraft(key, value)
}
function onNumber(key: string | number, value: unknown) {
  setDraft(key, value)
}
function onText(key: string | number, value: unknown) {
  setDraft(key, value)
}
function range(meta: ParamItem): [number | undefined, number | undefined] {
  const r = meta.ranges || []
  if (r.length >= 2) return [Number(r[0]), Number(r[1])]
  return [undefined, undefined]
}
function same(a: any, b: any) {
  return a === b || (typeof a === 'number' && typeof b === 'number' && a === b)
}

const diff = computed(() => {
  const rows: { section: string; key: string; name: string; from: any; to: any }[] = []
  for (const [key, meta] of Object.entries(props.items || {})) {
    if (!meta.editable) continue
    if (same(draft[key], meta.default)) continue
    rows.push({
      section: props.section,
      key,
      name: meta.name_zh || key,
      from: meta.default,
      to: draft[key]
    })
  }
  return rows
})

watch(diff, (rows) => {
  const changes: Record<string, any> = {}
  for (const row of rows) changes[row.key] = row.to
  emit('draft', changes)
})

function confirm() {
  const changes: Record<string, any> = {}
  for (const row of diff.value) changes[row.key] = row.to
  dialog.value = false
  emit('submit', changes)
}
</script>

<style scoped>
.param-editor {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(160px, 240px);
  gap: 12px;
  align-items: center;
}
.label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.label span {
  color: var(--hud-muted, #7f8f84);
  font-size: 12px;
}
</style>
