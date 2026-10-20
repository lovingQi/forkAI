import { createRouter, createWebHashHistory } from 'vue-router'
import { useConsoleStore } from '@/stores/console'
import { useSessionStore } from '@/stores/session'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/boot', name: 'boot', component: () => import('@/views/BootView.vue') },
    { path: '/pair', name: 'pair', component: () => import('@/views/PairView.vue') },
    { path: '/welcome', name: 'welcome', component: () => import('@/views/WelcomeView.vue') },
    { path: '/setup', name: 'setup', component: () => import('@/views/SetupWizard.vue') },
    { path: '/', name: 'dashboard', component: () => import('@/views/Dashboard.vue') },
    { path: '/mapping', name: 'mapping', component: () => import('@/views/MappingView.vue') },
    { path: '/control', name: 'control', component: () => import('@/views/ControlView.vue') },
    { path: '/params', name: 'params', component: () => import('@/views/ParamsView.vue') },
    { path: '/diagnosis', name: 'diagnosis', component: () => import('@/views/DiagnosisView.vue') },
    { path: '/flow', name: 'flow', component: () => import('@/views/FlowEditor.vue') }
  ]
})

router.beforeEach(async (to) => {
  const session = useSessionStore()
  const consoleStore = useConsoleStore()
  if (!consoleStore.booted) {
    if (to.name !== 'boot') return { name: 'boot', query: { next: to.fullPath } }
    return true
  }
  if (to.name === 'boot') return true
  if (!session.paired) {
    if (to.name !== 'pair') return { name: 'pair' }
    return true
  }
  if (consoleStore.setupCompleted === null) {
    try {
      await consoleStore.loadSetup()
    } catch {
      session.logout()
      return { name: 'pair' }
    }
  }
  const done = consoleStore.setupCompleted === true
  if (!done) {
    if (consoleStore.isKiosk) {
      if (to.name !== 'welcome' && to.name !== 'setup') return { name: 'welcome' }
      return true
    }
    if (to.name !== 'setup') return { name: 'setup' }
    return true
  }
  if (to.name === 'pair' || to.name === 'welcome' || to.name === 'boot') return { name: 'dashboard' }
  if (to.name === 'setup' && to.query.rerun !== '1') return { name: 'dashboard' }
  return true
})

export default router
