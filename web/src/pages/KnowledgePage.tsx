import { DeleteOutlined, PlusOutlined, SearchOutlined, UploadOutlined } from '@ant-design/icons'
import { Button, Card, Empty, Form, Input, List, Modal, Select, Space, Tag, Typography, Upload, message } from 'antd'
import { useEffect, useState } from 'react'
import { documentApi, type DocumentItem, type SearchHit } from '@/api/documents'
import { knowledgeBaseApi, type KnowledgeBase } from '@/api/knowledgeBases'

export default function KnowledgePage() {
  const [bases, setBases] = useState<KnowledgeBase[]>([])
  const [selectedId, setSelectedId] = useState<string>()
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [hits, setHits] = useState<SearchHit[]>([])
  const [query, setQuery] = useState('')
  const [newBaseOpen, setNewBaseOpen] = useState(false)
  const [baseName, setBaseName] = useState('')

  const loadBases = async () => {
    const { data } = await knowledgeBaseApi.list()
    setBases(data)
    setSelectedId((current) => current || data[0]?.id)
  }

  const loadDocuments = async (kbId?: string) => {
    if (!kbId) return setDocuments([])
    const { data } = await documentApi.list(1, 100, undefined, kbId)
    setDocuments(data.items)
  }

  useEffect(() => {
    loadBases().catch((error) => message.error((error as Error).message))
  }, [])

  useEffect(() => {
    loadDocuments(selectedId).catch((error) => message.error((error as Error).message))
  }, [selectedId])

  const createBase = async () => {
    if (!baseName.trim()) return
    await knowledgeBaseApi.create({ name: baseName.trim() })
    setBaseName('')
    setNewBaseOpen(false)
    await loadBases()
  }

  const upload = async (file: File) => {
    try {
      await documentApi.upload(file, selectedId)
      message.success('已提交解析任务')
      await loadDocuments(selectedId)
    } catch (error) {
      message.error((error as Error).message)
    }
    return false
  }

  const search = async () => {
    if (!query.trim()) return
    try {
      const { data } = await documentApi.search(query.trim(), 5)
      setHits(data)
    } catch (error) {
      message.error((error as Error).message)
    }
  }

  const remove = async (id: string) => {
    await documentApi.remove(id)
    await loadDocuments(selectedId)
  }

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Typography.Title level={2} style={{ margin: 0 }}>知识库 RAG</Typography.Title>
          <Typography.Text type="secondary">上传文档、观察解析状态并直接验证检索结果</Typography.Text>
        </div>
        <Button icon={<PlusOutlined />} onClick={() => setNewBaseOpen(true)}>新建知识库</Button>
      </Space>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            style={{ width: 240 }}
            placeholder="选择知识库"
            value={selectedId}
            options={bases.map((base) => ({ label: base.name, value: base.id }))}
            onChange={setSelectedId}
          />
          <Upload beforeUpload={upload} showUploadList={false} disabled={!selectedId}>
            <Button type="primary" icon={<UploadOutlined />} disabled={!selectedId}>上传文档</Button>
          </Upload>
          <Input
            style={{ width: 360 }}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onPressEnter={search}
            placeholder="输入问题，验证 Top-5 检索"
            suffix={<SearchOutlined onClick={search} />}
          />
        </Space>
      </Card>

      <Card title="文档任务" style={{ marginBottom: 16 }}>
        <List
          locale={{ emptyText: <Empty description="当前知识库没有文档" /> }}
          dataSource={documents}
          renderItem={(item) => (
            <List.Item actions={[<Button key="delete" danger type="text" icon={<DeleteOutlined />} onClick={() => remove(item.id)}>删除</Button>]}>
              <List.Item.Meta title={item.file_name} description={`${item.chunk_num} 个子块`} />
              <Tag color={item.status === 'done' ? 'green' : item.status === 'failed' ? 'red' : 'blue'}>{item.status}</Tag>
            </List.Item>
          )}
        />
      </Card>

      <Card title="检索结果">
        <List
          locale={{ emptyText: '输入问题后查看检索证据' }}
          dataSource={hits}
          renderItem={(hit) => (
            <List.Item>
              <List.Item.Meta
                title={<Space><span>{hit.doc_name || '未命名文档'}</span><Tag>score {hit.score.toFixed(4)}</Tag></Space>}
                description={hit.content}
              />
            </List.Item>
          )}
        />
      </Card>

      <Modal title="新建知识库" open={newBaseOpen} onCancel={() => setNewBaseOpen(false)} onOk={createBase}>
        <Form layout="vertical">
          <Form.Item label="名称" required>
            <Input value={baseName} onChange={(event) => setBaseName(event.target.value)} maxLength={128} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
