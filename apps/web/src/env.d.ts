/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, any>
  export default component
}

interface AppConfig {
  apiBase: string
  wsBase: string
}

interface Window {
  __APP_CONFIG__?: Partial<AppConfig>
}
