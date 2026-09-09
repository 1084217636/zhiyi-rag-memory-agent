import { BookOutlined, DatabaseOutlined, MessageOutlined } from '@ant-design/icons'
import { Card, Col, Row, Typography } from 'antd'

const capabilities = [
  {
    icon: <BookOutlined />,
    title: '知识库 RAG',
    text: '文档异步解析、父子分块、BM25 与向量混合检索，并返回真实来源。',
  },
  {
    icon: <DatabaseOutlined />,
    title: '长期记忆',
    text: '从对话抽取 Statement、Entity、Relation 和 Event，支持召回与溯源。',
  },
  {
    icon: <MessageOutlined />,
    title: 'Agent 工具调用',
    text: '模型按需调用知识库和记忆工具，后端负责权限、参数校验和循环控制。',
  },
]

export default function DashboardPage() {
  return (
    <div style={{ maxWidth: 1080, margin: '0 auto' }}>
      <Typography.Title level={2}>知忆 AI</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ fontSize: 16 }}>
        聚焦 RAG、长期记忆和可控 Agent 的个人 AI 问答系统。
      </Typography.Paragraph>
      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        {capabilities.map((item) => (
          <Col xs={24} md={8} key={item.title}>
            <Card style={{ height: '100%' }}>
              <div style={{ color: '#155eef', fontSize: 28 }}>{item.icon}</div>
              <Typography.Title level={4}>{item.title}</Typography.Title>
              <Typography.Paragraph type="secondary">{item.text}</Typography.Paragraph>
            </Card>
          </Col>
        ))}
      </Row>
      <Card title="核心链路" style={{ marginTop: 16 }}>
        <Typography.Paragraph>
          上传文档 → 异步解析与父子分块 → Elasticsearch 混合检索 → Agent 选择工具 →
          结合证据回答并保存对话 → 异步抽取长期记忆。
        </Typography.Paragraph>
      </Card>
    </div>
  )
}
