import client from './client'

interface Wrapped<T> { code: number; message: string; data: T }

export interface ResearchArtifact {
  id: string
  title: string
  content: string
  kb_id: string | null
  conversation_id: string | null
  citations: Array<Record<string, unknown>>
  source_snapshot: Array<Record<string, unknown>>
  version: number
  created_at: string | null
  updated_at: string | null
}

export interface ResearchCitation {
  evidence_id?: string
  source_id: string
  source_type: string | null
  doc_name: string | null
  chunk_id?: string | null
  content?: string
  score?: number | null
}

export const researchApi = {
  list() {
    return client.get<unknown, Wrapped<ResearchArtifact[]>>('/research/artifacts')
  },
  create(input: {
    title: string; content: string; conversation_id?: string; citations: ResearchCitation[]
  }) {
    return client.post<unknown, Wrapped<ResearchArtifact>>('/research/artifacts', input)
  },
  update(id: string, input: { title: string; content: string; expected_version: number }) {
    return client.put<unknown, Wrapped<ResearchArtifact>>(`/research/artifacts/${id}`, input)
  },
}
