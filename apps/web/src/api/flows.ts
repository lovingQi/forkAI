/** 任务流 API 封装（步骤42；复用 http.ts 的 axios 实例，Bearer 拦截已带） */
import { http } from './http'

export interface FlowNode {
  id: string
  type: string
  params: Record<string, any>
}

export interface FlowEdge {
  from: string
  to: string
  on: 'success' | 'fail'
}

export interface FlowDef {
  id?: string
  name: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  parallel_groups?: string[][]
  options?: Record<string, any>
  ui?: { positions?: Record<string, { x: number; y: number }>; viewport?: any }
}

export interface FlowSummary {
  id: string
  name: string
  nodes: number
}

export interface EngineStatus {
  flowId: string | null
  flowStatus: string
  currentNodeId: string | null
  nodeStates: Record<string, string>
}

export async function listFlows(): Promise<FlowSummary[]> {
  const { data } = await http.get('/flows')
  return data.flows
}

export async function getFlow(id: string): Promise<FlowDef> {
  const { data } = await http.get(`/flows/${id}`)
  return data
}

export async function createFlow(flow: FlowDef) {
  const { data } = await http.post('/flows', flow)
  return data as { succeed: boolean; id: string }
}

export async function updateFlow(id: string, flow: FlowDef) {
  const { data } = await http.put(`/flows/${id}`, flow)
  return data
}

export async function deleteFlow(id: string) {
  const { data } = await http.delete(`/flows/${id}`)
  return data
}

export async function startFlow(id: string) {
  const { data } = await http.post(`/flows/${id}/start`)
  return data
}

export async function pauseFlow() {
  const { data } = await http.post('/flow-engine/pause')
  return data
}

export async function resumeFlow() {
  const { data } = await http.post('/flow-engine/resume')
  return data
}

export async function cancelFlow() {
  const { data } = await http.post('/flow-engine/cancel')
  return data
}

export async function getEngineStatus(): Promise<EngineStatus> {
  const { data } = await http.get('/flow-engine/status')
  return data
}

export interface ParamSpec {
  type?: string
  required?: boolean
  safety?: boolean
  default?: any
  ask?: string
}

export interface NodeSchema {
  route_cmd: string
  params: Record<string, ParamSpec>
}

export async function getTaskSchemas(): Promise<Record<string, NodeSchema>> {
  const { data } = await http.get('/tasks/schemas')
  return data.schemas
}
