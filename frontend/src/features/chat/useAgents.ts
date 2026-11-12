import { useEffect, useState } from 'react'
import { useLang } from '@/i18n'
import { api } from '@/lib/api'

type Labels = { en: string; vi: string }
type AgentInfo = { route: string; labels: Labels; steps?: Record<string, Labels> }

/** Badge labels from `GET /api/agents` (app.js agentLabel and stepLabel): the route itself when unknown. */
export function useAgents() {
  const { lang } = useLang()
  const [agents, setAgents] = useState<Record<string, AgentInfo>>({})
  useEffect(() => {
    let alive = true
    api<AgentInfo[]>('/agents')
      .catch(() => [] as AgentInfo[])
      .then((list) => {
        if (alive && Array.isArray(list)) setAgents(Object.fromEntries(list.map((a) => [a.route, a])))
      })
    return () => {
      alive = false
    }
  }, [])
  return {
    agentLabel: (route: string) => agents[route]?.labels?.[lang] ?? route,
    stepLabel: (route: string, step: string) => agents[route]?.steps?.[step]?.[lang] ?? step,
  }
}
export type AgentLabels = ReturnType<typeof useAgents>
