import { DeleteOutlined, SearchOutlined } from '@ant-design/icons'
import { Button, Card, Empty, Input, List, Space, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import { memoryApi, type MemoryHit, type MemoryItem } from '@/api/memories'

export default function SimpleMemoryPage() {
  const [text, setText] = useState('')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<MemoryItem[]>([])
  const [hits, setHits] = useState<MemoryHit[]>([])

  const load = async () => {
    const { data } = await memoryApi.list(1, 30)
    setItems(data.items)
  }

  useEffect(() => {
    load().catch((error) => message.error((error as Error).message))
  }, [])

  const remember = async () => {
    if (!text.trim()) return
    try {
      await memoryApi.remember(text.trim())
      setText('')
      message.success('已提交记忆抽取任务')
      await load()
    } catch (error) {
      message.error((error as Error).message)
    }
  }

  const search = async () => {
    if (!query.trim()) return
    try {
      const { data } = await memoryApi.search(query.trim(), 5)
      setHits(data)
    } catch (error) {
      message.error((error as Error).message)
    }
  }

  const remove = async (id: string) => {
    await memoryApi.remove(id)
    await load()
  }

  return (
    <div style={{ maxWidth: 1050, margin: '0 auto' }}>
      <Typography.Title level={2}>长期记忆</Typography.Title>
      <Typography.Paragraph type="secondary">
        手动提交事实，观察异步抽取状态，并验证实体和关系召回。
      </Typography.Paragraph>
      <Card title="写入一条记忆" style={{ marginBottom: 16 }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input value={text} onChange={(event) => setText(event.target.value)} onPressEnter={remember} placeholder="例如：我正在准备 AI 应用工程师面试" />
          <Button type="primary" onClick={remember}>开始抽取</Button>
        </Space.Compact>
      </Card>
      <Card title="原始记忆任务" style={{ marginBottom: 16 }}>
        <List
          locale={{ emptyText: <Empty description="还没有记忆" /> }}
          dataSource={items}
          renderItem={(item) => (
            <List.Item actions={[<Button key="delete" danger type="text" icon={<DeleteOutlined />} onClick={() => remove(item.id)}>删除</Button>]}>
              <List.Item.Meta title={item.raw_text} description={item.error_msg || new Date(item.created_at).toLocaleString()} />
              <Tag color={item.status === 'done' ? 'green' : item.status === 'failed' ? 'red' : 'blue'}>{item.status}</Tag>
            </List.Item>
          )}
        />
      </Card>
      <Card title="召回测试">
        <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
          <Input value={query} onChange={(event) => setQuery(event.target.value)} onPressEnter={search} placeholder="输入一个与历史事实相关的问题" />
          <Button icon={<SearchOutlined />} onClick={search}>检索</Button>
        </Space.Compact>
        <List
          locale={{ emptyText: '还没有召回结果' }}
          dataSource={hits}
          renderItem={(hit) => (
            <List.Item>
              <List.Item.Meta title={<Space><span>{hit.name}</span><Tag>{hit.type}</Tag><Tag>score {hit.score.toFixed(4)}</Tag></Space>} description={hit.description || hit.relations.map((relation) => `${relation.predicate} ${relation.object_name || ''}`).join('；')} />
            </List.Item>
          )}
        />
      </Card>
    </div>
  )
}
