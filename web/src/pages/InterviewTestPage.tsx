import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { App as AntApp, Button, Checkbox, Empty, Form, Input, Modal, Spin, Tag, Typography } from 'antd'
import { authApi } from '@/api/auth'
import {
  chatApi,
  streamChat,
  type ChatMessage,
  type Citation,
  type Conversation,
  type ToolCall,
  type ToolResult,
} from '@/api/chat'
import { documentApi, type DocumentItem } from '@/api/documents'
import { memoryApi, type MemoryItem, type PendingMemoryStatement } from '@/api/memories'
import { researchApi, type ResearchArtifact } from '@/api/research'
import './InterviewTestPage.css'

type ToolActivity = ToolCall & {
  status: 'running' | 'success' | 'error' | 'done'
  text?: string
  latency_ms?: number
}

interface ViewMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  tools: ToolActivity[]
  citations: Citation[]
  error?: string
}

const documentStatusText: Record<string, string> = {
  pending: '排队中',
  parsing: '解析中',
  done: '可检索',
  failed: '失败',
}

const memoryStatusText: Record<string, string> = {
  pending: '等待抽取',
  extracting: '抽取中',
  done: '已写入图谱',
  failed: '抽取失败',
}

function historyMessage(item: ChatMessage): ViewMessage | null {
  if (item.role !== 'user' && item.role !== 'assistant') return null
  return {
    id: item.id,
    role: item.role,
    content: item.content,
    tools: (item.meta_data?.tool_calls ?? []).map((tool) => ({ ...tool, status: 'done' })),
    citations: item.meta_data?.citations ?? [],
  }
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请检查 API 服务和配置。'
}

export default function InterviewTestPage() {
  const { message } = AntApp.useApp()
  const fileInput = useRef<HTMLInputElement>(null)
  const [authenticated, setAuthenticated] = useState(Boolean(localStorage.getItem('access_token')))
  const [checkingToken, setCheckingToken] = useState(Boolean(localStorage.getItem('access_token')))
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')
  const [authLoading, setAuthLoading] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [userLabel, setUserLabel] = useState('')

  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversationId, setConversationId] = useState<string>()
  const [messages, setMessages] = useState<ViewMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [enableKnowledge, setEnableKnowledge] = useState(true)
  const [enableMemory, setEnableMemory] = useState(true)

  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [memories, setMemories] = useState<MemoryItem[]>([])
  const [pendingMemory, setPendingMemory] = useState<PendingMemoryStatement[]>([])
  const [artifacts, setArtifacts] = useState<ResearchArtifact[]>([])
  const [saveSource, setSaveSource] = useState<ViewMessage | null>(null)
  const [editingArtifact, setEditingArtifact] = useState<ResearchArtifact | null>(null)
  const [artifactTitle, setArtifactTitle] = useState('')
  const [artifactContent, setArtifactContent] = useState('')
  const [savingArtifact, setSavingArtifact] = useState(false)
  const [uploading, setUploading] = useState(false)

  const loadConversations = useCallback(async () => {
    const { data } = await chatApi.listConversations()
    setConversations(data.filter((item) => !item.is_group))
  }, [])

  const loadDocuments = useCallback(async () => {
    const { data } = await documentApi.list(1, 8)
    setDocuments(data.items)
  }, [])

  const loadMemories = useCallback(async () => {
    const [list, policy] = await Promise.all([memoryApi.list(1, 8), memoryApi.policyStatus()])
    setMemories(list.data.items)
    setPendingMemory(policy.data.pending)
  }, [])

  const resolvePending = async (id: string, action: 'keep_both' | 'replace' | 'reject') => {
    try {
      await memoryApi.resolveStatement(id, action)
      message.success('记忆处理完成')
      await loadMemories()
    } catch (error) { message.error(errorText(error)) }
  }

  const deleteSourceMemory = async (id: string) => {
    try {
      await memoryApi.remove(id)
      message.success('来源记忆已删除')
      await loadMemories()
    } catch (error) { message.error(errorText(error)) }
  }

  const loadArtifacts = useCallback(async () => {
    const { data } = await researchApi.list()
    setArtifacts(data)
  }, [])

  const loadPageData = useCallback(async () => {
    await Promise.all([loadConversations(), loadDocuments(), loadMemories(), loadArtifacts()])
  }, [loadConversations, loadDocuments, loadMemories, loadArtifacts])

  useEffect(() => {
    if (!authenticated) {
      setCheckingToken(false)
      return
    }
    authApi.me()
      .then(({ data }) => setUserLabel(data.nickname || data.username))
      .catch(() => {
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        setAuthenticated(false)
      })
      .finally(() => setCheckingToken(false))
  }, [authenticated])

  useEffect(() => {
    if (!authenticated || checkingToken) return
    loadPageData().catch((error) => message.error(errorText(error)))
  }, [authenticated, checkingToken, loadPageData, message])

  const hasPendingWork = useMemo(
    () =>
      documents.some((item) => item.status === 'pending' || item.status === 'parsing') ||
      memories.some((item) => item.status === 'pending' || item.status === 'extracting'),
    [documents, memories],
  )

  useEffect(() => {
    if (!authenticated || !hasPendingWork) return
    const timer = window.setTimeout(() => {
      Promise.all([loadDocuments(), loadMemories()]).catch(() => undefined)
    }, 2200)
    return () => window.clearTimeout(timer)
  }, [authenticated, hasPendingWork, loadDocuments, loadMemories, documents, memories])

  const loadMessages = useCallback(async (id: string) => {
    const { data } = await chatApi.listMessages(id)
    setMessages(data.map(historyMessage).filter((item): item is ViewMessage => item !== null))
  }, [])

  const submitAuth = async () => {
    if (!username.trim() || !password) return
    setAuthLoading(true)
    try {
      if (authMode === 'register') {
        await authApi.register(username.trim(), password)
        message.success('账号创建完成，请登录。')
        setAuthMode('login')
        return
      }
      const { data } = await authApi.login(username.trim(), password)
      localStorage.setItem('access_token', data.access_token)
      localStorage.setItem('refresh_token', data.refresh_token)
      setUserLabel(username.trim())
      setAuthenticated(true)
      message.success('已连接项目 API。')
    } catch (error) {
      message.error(errorText(error))
    } finally {
      setAuthLoading(false)
    }
  }

  const logout = () => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    setAuthenticated(false)
    setConversations([])
    setDocuments([])
    setMemories([])
    setMessages([])
    setConversationId(undefined)
    setUserLabel('')
  }

  const createConversation = async () => {
    try {
      const { data } = await chatApi.createConversation('新对话')
      setConversationId(data.id)
      setMessages([])
      await loadConversations()
    } catch (error) {
      message.error(errorText(error))
    }
  }

  const selectConversation = async (item: Conversation) => {
    if (sending) return
    setConversationId(item.id)
    try {
      await loadMessages(item.id)
    } catch (error) {
      message.error(errorText(error))
    }
  }

  const uploadSelectedFile = async (file?: File) => {
    if (!file) return
    setUploading(true)
    try {
      await documentApi.upload(file)
      message.success(`已提交「${file.name}」，后台正在解析。`)
      await loadDocuments()
    } catch (error) {
      message.error(errorText(error))
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const updateAssistant = (id: string, updater: (item: ViewMessage) => ViewMessage) => {
    setMessages((current) => current.map((item) => item.id === id ? updater(item) : item))
  }

  const openSaveArtifact = (item: ViewMessage) => {
    setSaveSource(item)
    setArtifactTitle(item.content.trim().split('\n')[0].replace(/^#+\s*/, '').slice(0, 80) || '研究笔记')
    setArtifactContent(item.content)
  }

  const saveArtifact = async () => {
    if ((!saveSource && !editingArtifact) || !artifactTitle.trim() || !artifactContent.trim()) return
    setSavingArtifact(true)
    try {
      if (editingArtifact) {
        const { data } = await researchApi.update(editingArtifact.id, {
          title: artifactTitle.trim(), content: artifactContent,
          expected_version: editingArtifact.version,
        })
        setArtifacts((current) => [data, ...current.filter((row) => row.id !== data.id)])
      } else if (saveSource) {
        const { data } = await researchApi.create({
          title: artifactTitle.trim(), content: artifactContent,
          conversation_id: conversationId, citations: saveSource.citations,
        })
        setArtifacts((current) => [data, ...current])
      }
      setSaveSource(null)
      setEditingArtifact(null)
      message.success(editingArtifact ? '研究成果已更新。' : '已保存为研究成果，可在左侧重新打开。')
    } catch (error) {
      message.error(errorText(error))
    } finally {
      setSavingArtifact(false)
    }
  }

  const openArtifact = (item: ResearchArtifact) => {
    setEditingArtifact(item)
    setSaveSource(null)
    setArtifactTitle(item.title)
    setArtifactContent(item.content)
  }

  const send = async () => {
    const text = input.trim()
    if (!text || sending) return
    setSending(true)
    setInput('')

    let activeConversationId = conversationId
    try {
      if (!activeConversationId) {
        const created = await chatApi.createConversation('新对话')
        activeConversationId = created.data.id
        setConversationId(activeConversationId)
        await loadConversations()
      }

      const userMessageId = crypto.randomUUID()
      const assistantMessageId = crypto.randomUUID()
      setMessages((current) => [
        ...current,
        { id: userMessageId, role: 'user', content: text, tools: [], citations: [] },
        { id: assistantMessageId, role: 'assistant', content: '', tools: [], citations: [] },
      ])

      await streamChat(
        {
          conversationId: activeConversationId,
          message: text,
          enableKnowledge,
          enableMemory,
          enableWebSearch: false,
        },
        {
          onMeta: (event) => setConversationId(event.conversation_id),
          onToken: (token) => updateAssistant(assistantMessageId, (item) => ({ ...item, content: item.content + token })),
          onToolStart: (tool) => updateAssistant(assistantMessageId, (item) => ({
            ...item,
            tools: [...item.tools, { ...tool, status: 'running' }],
          })),
          onToolResult: (result: ToolResult) => updateAssistant(assistantMessageId, (item) => {
            const tools = [...item.tools]
            let matchIndex = -1
            for (let index = tools.length - 1; index >= 0; index -= 1) {
              const tool = tools[index]
              if (tool.tool === result.tool && tool.query === result.query && tool.status === 'running') {
                matchIndex = index
                break
              }
            }
            const activity: ToolActivity = {
              tool: result.tool,
              query: result.query,
              status: result.status === 'success' ? 'success' : 'error',
              text: result.text,
              latency_ms: result.latency_ms,
            }
            if (matchIndex >= 0) tools[matchIndex] = activity
            else tools.push(activity)
            return { ...item, tools }
          }),
          onCitation: (citations) => updateAssistant(assistantMessageId, (item) => ({ ...item, citations })),
          onError: (error) => {
            updateAssistant(assistantMessageId, (item) => ({ ...item, error }))
            message.error(error)
          },
          onDone: () => {
            void loadConversations()
            void loadMemories()
          },
        },
      )

      await Promise.all([loadMessages(activeConversationId), loadConversations(), loadMemories()])
    } catch (error) {
      message.error(errorText(error))
      if (activeConversationId) {
        await loadMessages(activeConversationId).catch(() => undefined)
      }
    } finally {
      setSending(false)
    }
  }

  if (checkingToken) {
    return <div className="interview-loading"><Spin size="large" /></div>
  }

  if (!authenticated) {
    return (
      <div className="interview-auth-page">
        <section className="interview-auth-card">
          <div className="interview-brand">知忆 AI · 功能验证</div>
          <h1>{authMode === 'login' ? '连接本地项目' : '创建测试账号'}</h1>
          <p>登录只用于调用已有 API。登录后可以上传文档、创建会话并验证问答链路。</p>
          <Form layout="vertical" onFinish={submitAuth}>
            <Form.Item label="用户名" required>
              <Input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
            </Form.Item>
            <Form.Item label="密码" required>
              <Input.Password value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={authMode === 'login' ? 'current-password' : 'new-password'} />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={authLoading}>
              {authMode === 'login' ? '登录' : '注册并继续'}
            </Button>
          </Form>
          <button className="interview-link-button" onClick={() => setAuthMode(authMode === 'login' ? 'register' : 'login')}>
            {authMode === 'login' ? '还没有账号？创建一个测试账号' : '已有账号？返回登录'}
          </button>
        </section>
      </div>
    )
  }

  return (
    <div className="interview-client">
      <header className="interview-header">
        <div>
          <div className="interview-brand">知忆 AI</div>
          <h1>项目功能验证</h1>
          <p>用一个简易客户端走通文档 RAG、跨会话记忆和 Agent 工具问答。</p>
        </div>
        <div className="interview-userbar">
          <span>{userLabel}</span>
          <Button size="small" onClick={logout}>退出</Button>
        </div>
      </header>

      <main className="interview-grid">
        <aside className="interview-sidebar">
          <section className="interview-panel">
            <div className="interview-panel-title">
              <div><b>1. 上传资料</b><small>解析完成后可用 RAG 查询</small></div>
              <Button size="small" loading={uploading} onClick={() => fileInput.current?.click()}>
                选择文件
              </Button>
              <input
                ref={fileInput}
                type="file"
                accept=".pdf,.docx,.txt,.md,.markdown,.html,.htm,.csv,.xlsx"
                hidden
                onChange={(event) => void uploadSelectedFile(event.target.files?.[0])}
              />
            </div>
            <div className="interview-list">
              {documents.length === 0 ? <div className="interview-empty-small">还没有上传资料</div> : documents.map((doc) => (
                <div className="interview-row" key={doc.id}>
                  <span className="interview-row-main" title={doc.file_name}>{doc.file_name}</span>
                  <Tag color={doc.status === 'done' ? 'green' : doc.status === 'failed' ? 'red' : 'blue'}>
                    {documentStatusText[doc.status] ?? doc.status}
                  </Tag>
                  {doc.status === 'failed' && doc.error_msg && <small className="interview-error">{doc.error_msg}</small>}
                </div>
              ))}
            </div>
          </section>

          <section className="interview-panel">
            <div className="interview-panel-title">
              <div><b>4. 研究成果</b><small>把带引用的回答保存成可继续修改的笔记</small></div>
              <Button size="small" onClick={() => void loadArtifacts()}>刷新</Button>
            </div>
            <div className="interview-list">
              {artifacts.length === 0 ? <div className="interview-empty-small">完成一次资料问答后可保存成果</div> : artifacts.map((item) => (
                <div className="interview-memory" key={item.id}>
                  <span className="interview-memory-text" title={item.title}>{item.title} · v{item.version}</span>
                  <Button size="small" onClick={() => openArtifact(item)}>打开 / 编辑</Button>
                </div>
              ))}
            </div>
          </section>

          <section className="interview-panel">
            <div className="interview-panel-title">
              <div><b>2. 会话</b><small>新会话用于验证跨会话记忆</small></div>
              <Button size="small" type="primary" onClick={() => void createConversation()}>新建</Button>
            </div>
            <div className="interview-list interview-conversation-list">
              {conversations.length === 0 ? <div className="interview-empty-small">还没有会话</div> : conversations.map((item) => (
                <button
                  key={item.id}
                  className={`interview-conversation ${item.id === conversationId ? 'selected' : ''}`}
                  disabled={sending}
                  onClick={() => void selectConversation(item)}
                  title={item.title}
                >
                  {item.title || '新对话'}
                </button>
              ))}
            </div>
          </section>

          <section className="interview-panel">
            <div className="interview-panel-title">
              <div><b>3. 记忆抽取状态</b><small>成功回答后自动提交后台抽取</small></div>
              <Button size="small" onClick={() => void loadMemories()}>刷新</Button>
            </div>
            <div className="interview-list">
              {memories.length === 0 ? <div className="interview-empty-small">先在聊天里说一条个人事实</div> : memories.map((item) => (
                <div className="interview-memory" key={item.id}>
                  <span className="interview-memory-text" title={item.raw_text}>{item.raw_text}</span>
                  <Tag color={item.status === 'done' ? 'green' : item.status === 'failed' ? 'red' : 'blue'}>
                    {memoryStatusText[item.status] ?? item.status}
                  </Tag>
                  <Button size="small" danger onClick={() => Modal.confirm({
                    title: '删除这条来源记忆？',
                    content: '会删除该来源生成的陈述和来源文本；原聊天消息仍按聊天记录管理。',
                    onOk: () => deleteSourceMemory(item.id),
                  })}>删除</Button>
                </div>
              ))}
            </div>
            {pendingMemory.length > 0 && <div className="interview-list">
              <b>待确认的冲突记忆</b>
              {pendingMemory.map((item) => <div className="interview-memory" key={item.id}>
                <span className="interview-memory-text" title={item.reason}>{item.statement}</span>
                <Button size="small" onClick={() => void resolvePending(item.id, 'keep_both')}>都保留</Button>
                <Button size="small" onClick={() => void resolvePending(item.id, 'replace')}>替换旧版</Button>
                <Button size="small" onClick={() => void resolvePending(item.id, 'reject')}>不采用</Button>
              </div>)}
            </div>}
          </section>

          <section className="interview-hint">
            <b>快速验证</b>
            <ol>
              <li>上传文件，等状态变成“可检索”，打开知识库开关后问文件内容。</li>
              <li>在聊天里说“我在研究 WiFi CSI”，等记忆变成“已写入图谱”。</li>
              <li>新建会话，问“我在研究什么？”。出现 memory_search 表示 Agent 调用了记忆工具；没出现时，回答也可能来自系统预召回的记忆背景。</li>
            </ol>
          </section>
        </aside>

        <section className="interview-chat-panel">
          <div className="interview-chat-toolbar">
            <div>
              <b>{conversationId ? conversations.find((item) => item.id === conversationId)?.title || '当前会话' : '选择或新建会话'}</b>
              <small>勾选只表示 Agent 可以使用，是否调用由模型决定；记忆还会按问题预召回相关背景</small>
            </div>
            <div className="interview-toggles">
              <Checkbox checked={enableKnowledge} onChange={(event) => setEnableKnowledge(event.target.checked)}>知识库</Checkbox>
              <Checkbox checked={enableMemory} onChange={(event) => setEnableMemory(event.target.checked)}>长期记忆</Checkbox>
            </div>
          </div>

          <div className="interview-messages">
            {messages.length === 0 ? (
              <Empty description="发一个问题开始验证" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : messages.map((item) => (
              <article className={`interview-message ${item.role}`} key={item.id}>
                <div className="interview-message-label">{item.role === 'user' ? '我' : '知忆 AI'}</div>
                <div className="interview-message-content">{item.content || (sending && item.role === 'assistant' ? '正在生成…' : '')}</div>
                {item.error && <div className="interview-error">{item.error}</div>}
                {item.role === 'assistant' && item.content && !item.error && (
                  <Button size="small" onClick={() => openSaveArtifact(item)}>保存为研究成果</Button>
                )}
                {item.tools.length > 0 && (
                  <div className="interview-tool-list">
                    {item.tools.map((tool, index) => (
                      <details key={`${tool.tool}-${index}`}>
                        <summary>
                          <Tag color={tool.status === 'error' ? 'red' : tool.status === 'running' ? 'blue' : 'green'}>{tool.tool}</Tag>
                          <span>{tool.query || '工具调用'}</span>
                          {tool.latency_ms != null && <small>{tool.latency_ms} ms</small>}
                        </summary>
                        {tool.text && <pre>{tool.text}</pre>}
                      </details>
                    ))}
                  </div>
                )}
                {item.citations.length > 0 && (
                  <div className="interview-citations">
                    <b>引用来源</b>
                    {item.citations.map((citation, index) => (
                      <span key={`${citation.source_id}-${index}`}>
                        [{index + 1}] {citation.doc_name || citation.source_id}
                      </span>
                    ))}
                  </div>
                )}
              </article>
            ))}
            {sending && <div className="interview-stream-status"><Spin size="small" /> 正在接收 SSE 流</div>}
          </div>

          <div className="interview-composer">
            <Input.TextArea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onPressEnter={(event) => {
                if (!event.shiftKey) {
                  event.preventDefault()
                  void send()
                }
              }}
              autoSize={{ minRows: 2, maxRows: 5 }}
              placeholder="输入文本问题。记忆示例：我在研究 WiFi CSI，回答请先给结论。"
              disabled={sending}
            />
            <Button type="primary" onClick={() => void send()} loading={sending} disabled={!input.trim()}>
              发送
            </Button>
          </div>
          <Typography.Text className="interview-footnote" type="secondary">
            聊天输入只支持文本。离线评测指标由项目评测脚本运行，这个页面用于手工验证交互主链。
          </Typography.Text>
        </section>
      </main>
      <Modal
        title={editingArtifact ? `编辑研究成果 · v${editingArtifact.version}` : '保存研究成果'}
        open={Boolean(saveSource || editingArtifact)}
        onCancel={() => { setSaveSource(null); setEditingArtifact(null) }}
        onOk={() => void saveArtifact()}
        confirmLoading={savingArtifact}
        okText={editingArtifact ? '保存新版本' : '保存'}
        cancelText="取消"
      >
        <Input value={artifactTitle} onChange={(event) => setArtifactTitle(event.target.value)} maxLength={200} placeholder="成果标题" />
        <Input.TextArea value={artifactContent} onChange={(event) => setArtifactContent(event.target.value)} rows={12} maxLength={100000} style={{ marginTop: 12 }} />
        <Typography.Text type="secondary">
          {editingArtifact
            ? `当前成果保留 ${editingArtifact.citations.length} 条引用快照；保存后版本号递增。`
            : `将保存当前答案及 ${saveSource?.citations.length || 0} 条引用快照。`}
        </Typography.Text>
      </Modal>
    </div>
  )
}
