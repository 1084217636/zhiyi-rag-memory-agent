import { BookOutlined, DatabaseOutlined, DeploymentUnitOutlined, LogoutOutlined, MessageOutlined, SettingOutlined } from '@ant-design/icons'
import { Button, Layout, Menu, Space, Typography, message } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'

const { Header, Sider, Content } = Layout

const items = [
  { key: '/', icon: <DatabaseOutlined />, label: '项目概览' },
  { key: '/chat', icon: <MessageOutlined />, label: 'Agent 对话' },
  { key: '/knowledge', icon: <BookOutlined />, label: '知识库 RAG' },
  { key: '/memory', icon: <DatabaseOutlined />, label: '长期记忆' },
  { key: '/graph', icon: <DeploymentUnitOutlined />, label: '记忆图谱' },
  { key: '/settings/models', icon: <SettingOutlined />, label: '模型配置' },
]

export default function ShellLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)

  const onLogout = async () => {
    await logout()
    message.success('已退出登录')
    navigate('/login', { replace: true })
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={224} theme="light" style={{ borderRight: '1px solid #eef0f4' }}>
        <div style={{ height: 72, padding: '18px 20px' }}>
          <Typography.Title level={4} style={{ margin: 0, color: '#155eef' }}>
            知忆 AI
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            RAG · Memory · Agent
          </Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={items}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff',
            borderBottom: '1px solid #eef0f4',
            display: 'flex',
            justifyContent: 'flex-end',
            alignItems: 'center',
            paddingInline: 24,
          }}
        >
          <Space>
            <Typography.Text>{user?.nickname || user?.username}</Typography.Text>
            <Button type="text" icon={<LogoutOutlined />} onClick={onLogout}>
              退出
            </Button>
          </Space>
        </Header>
        <Content style={{ padding: 24, overflow: 'auto' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
