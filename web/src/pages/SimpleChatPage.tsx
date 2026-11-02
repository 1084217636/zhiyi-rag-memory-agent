import { DeleteOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons'
import { Button, Card, Checkbox, Empty, Input, List, Space, Spin, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import { chatApi, streamChat, type ChatMessage, type Citation, type Conversation, type ToolCall } from '@/api/chat'

interface DisplayMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  tools?: ToolCall[]
}

export default function SimpleChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversationId, setConversationId] = useState<string>()
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [input, setInput] = useState('')
  const [generating, setGenerating] = useState(false)
  const [enableKnowledge, setEnableKnowledge] = useState(true)
  const [enableMemory, setEnableMemory] = useState(true)

  const loadConversations = async () => {
    const { data } = await chatApi.listConversations()
    setConversations(data.filter((item) => !item.is_group))
  }

  const loadMessages = async (id: string) => {
    const { data } = await chatApi.listMessages(id)
    setMessages(data.filter((item): item is ChatMessage & { role: 'user' | 'assistant' } => item.role !== 'system').map((item) => ({
      id: item.id,
      role: item.role,
      content: item.content,
      citations: item.meta_data?.citations,
      tools: item.meta_data?.tool_calls,
    })))
  }

  useEffect(() => {
    loadConversations().catch((error) => message.error((error as Error).message))
  }, [])

  const selectConversation = (id: string) => {
    setConversationId(id)
    loadMessages(id).catch((error) => message.error((error as Error).message))
  }

  const newConversation = () => {
    setConversationId(undefined)
    setMessages([])
  }

  const removeConversation = async (id: string) => {
    await chatApi.deleteConversation(id)
    if (id === conversationId) newConversation()
    await loadConversations()
  }

  const send = async () => {
    const content = input.trim()
    if (!content || generating) return
    const userId = crypto.randomUUID()
    const assistantId = crypto.randomUUID()
    setInput('')
    setGenerating(true)
    setMessages((current) => [...current, { id: userId, role: 'user', content }, { id: assistantId, role: 'assistant', content: '', tools: [], citations: [] }])

    try {
      await streamChat(
        { conversationId, message: content, enableKnowledge, enableMemory, enableWebSearch: false },
        {
          onMeta: (data) => setConversationId(data.conversation_id),
          onToken: (token) => setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, content: item.content + token } : item)),
          onToolStart: (tool) => setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, tools: [...(item.tools || []), tool] } : item)),
          onCitation: (citations) => setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, citations } : item)),
          onError: (error) => {
            message.error(error)
            setMessages((current) => current.map((item) => item.id === assistantId ? { ...item, content: `生成失败：${error}` } : item))
          },
        },
      )
      await loadConversations()
    } catch (error) {
      message.error((error as Error).message)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '240px minmax(0, 1fr)', gap: 16, height: 'calc(100vh - 112px)' }}>
      <Card title="对话" extra={<Button type="text" icon={<PlusOutlined />} onClick={newConversation} />} styles={{ body: { padding: 8 } }}>
        <List
          dataSource={conversations}
          locale={{ emptyText: '暂无历史对话' }}
          renderItem={(item) => (
            <List.Item
              onClick={() => selectConversation(item.id)}
              style={{ cursor: 'pointer', paddingInline: 8, background: item.id === conversationId ? '#eef4ff' : undefined }}
              actions={[<Button key="delete" type="text" danger icon={<DeleteOutlined />} onClick={(event) => { event.stopPropagation(); removeConversation(item.id) }} />]}
            >
              <Typography.Text ellipsis>{item.title}</Typography.Text>
            </List.Item>
          )}
        />
      </Card>
      <Card
        title="Agent 问答"
        extra={<Space><Checkbox checked={enableKnowledge} onChange={(event) => setEnableKnowledge(event.target.checked)}>知识库</Checkbox><Checkbox checked={enableMemory} onChange={(event) => setEnableMemory(event.target.checked)}>长期记忆</Checkbox></Space>}
        styles={{ body: { height: 'calc(100% - 58px)', display: 'flex', flexDirection: 'column' } }}
      >
        <div style={{ flex: 1, overflow: 'auto', paddingRight: 8 }}>
          {messages.length === 0 ? <Empty description="输入问题，观察模型是否调用知识库或记忆工具" /> : messages.map((item) => (
            <div key={item.id} style={{ marginBottom: 16, marginLeft: item.role === 'user' ? '20%' : 0, padding: 14, borderRadius: 10, background: item.role === 'user' ? '#155eef' : '#f5f7fb', color: item.role === 'user' ? '#fff' : undefined }}>
              <Typography.Paragraph style={{ margin: 0, color: 'inherit', whiteSpace: 'pre-wrap' }}>{item.content || (generating ? '正在生成…' : '')}</Typography.Paragraph>
              {!!item.tools?.length && <Space wrap style={{ marginTop: 10 }}>{item.tools.map((tool, index) => <Tag key={`${tool.tool}-${index}`} color="blue">工具：{tool.tool}</Tag>)}</Space>}
              {!!item.citations?.length && <div style={{ marginTop: 10 }}>{item.citations.map((citation, index) => <Typography.Text key={`${citation.source_id}-${index}`} type="secondary" style={{ display: 'block' }}>[{index + 1}] {citation.doc_name || citation.source_id}</Typography.Text>)}</div>}
            </div>
          ))}
          {generating && <Spin size="small" />}
        </div>
        <Space.Compact style={{ width: '100%', marginTop: 12 }}>
          <Input.TextArea value={input} onChange={(event) => setInput(event.target.value)} onPressEnter={(event) => { if (!event.shiftKey) { event.preventDefault(); send() } }} autoSize={{ minRows: 2, maxRows: 5 }} placeholder="输入问题，Enter 发送，Shift+Enter 换行" />
          <Button type="primary" icon={<SendOutlined />} loading={generating} onClick={send}>发送</Button>
        </Space.Compact>
      </Card>
    </div>
  )
}
