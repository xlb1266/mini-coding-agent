import { get } from './request'

export interface HealthResponse {
  status: string
  app: string
  version: string
  database: string
}

export function getHealth(): Promise<HealthResponse> {
  return get<HealthResponse>('/api/health')
}
