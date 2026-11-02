import client from './client'

interface Wrapped<T> {
  code: number
  message: string
  data: T
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  is_group?: boolean
}

export interface Citation {
  evidence_id?: string
  source_id: string
  source_type: string | null
  doc_name: string | null
  chunk_id?: string | null
  content?: string
  score: number | null
}

export interface ToolCall {
  tool: string
  query: string
}

export interface ToolResult {
  tool: string
  query: string
  status: 'success' | 'error' | string
  text?: string
  latency_ms?: number
  cached?: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  meta_data: { citations?: Citation[]; tool_calls?: ToolCall[] } | null
  created_at: string
}

export interface SendOptions {
  conversationId?: string
  message: string
  enableKnowledge: boolean
  enableMemory: boolean
  enableWebSearch: boolean
}

export interface StreamHandlers {
  onMeta?: (data: { conversation_id: string; title: string }) => void
  onToken?: (text: string) => void
  onToolStart?: (data: ToolCall) => void
  onToolResult?: (data: ToolResult) => void
  onCitation?: (citations: Citation[]) => void
  onDone?: (data: { conversation_id: string; message_id?: string }) => void
  onError?: (message: string) => void
}

export const chatApi = {
  listConversations() {
    return client.get<unknown, Wrapped<Conversation[]>>('/conversations')
  },
  createConversation(title = '新对话') {
    return client.post<unknown, Wrapped<Conversation>>('/conversations', { title })
  },
  listMessages(id: string) {
    return client.get<unknown, Wrapped<ChatMessage[]>>(`/conversations/${id}/messages`)
  },
  deleteConversation(id: string) {
    return client.delete<unknown, Wrapped<null>>(`/conversations/${id}`)
  },
}

export async function streamChat(
  options: SendOptions,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem('access_token')
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      conversation_id: options.conversationId ?? null,
      message: options.message,
      enable_knowledge: options.enableKnowledge,
      enable_memory: options.enableMemory,
      enable_web_search: options.enableWebSearch,
    }),
    signal,
  })

  if (!response.ok || !response.body) {
    throw new Error(`请求失败（HTTP ${response.status}）`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() ?? ''
    for (const block of blocks) {
      let event = 'message'
      let raw = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) raw += line.slice(5).trim()
      }
      if (!raw) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(raw) as Record<string, unknown>
      } catch {
        continue
      }
      if (event === 'meta') handlers.onMeta?.(data as unknown as { conversation_id: string; title: string })
      else if (event === 'token') handlers.onToken?.(String(data.text ?? ''))
      else if (event === 'tool_start' || event === 'tool_call') handlers.onToolStart?.(data as unknown as ToolCall)
      else if (event === 'tool_result') handlers.onToolResult?.(data as unknown as ToolResult)
      else if (event === 'citation') handlers.onCitation?.((data.citations as Citation[]) ?? [])
      else if (event === 'done') handlers.onDone?.(data as unknown as { conversation_id: string; message_id?: string })
      else if (event === 'error') handlers.onError?.(String(data.message ?? '生成失败'))
    }
  }
}
