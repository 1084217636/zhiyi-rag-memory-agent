import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { Button, Card, Form, Input, Segmented, Typography, message } from 'antd'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'

interface AuthForm {
  username: string
  password: string
}

export default function AuthPage() {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [loading, setLoading] = useState(false)
  const login = useAuthStore((state) => state.login)
  const register = useAuthStore((state) => state.register)
  const navigate = useNavigate()

  const submit = async (values: AuthForm) => {
    setLoading(true)
    try {
      if (mode === 'register') {
        await register(values.username, values.password)
        message.success('注册成功，请登录')
        setMode('login')
        return
      }
      await login(values.username, values.password)
      navigate('/', { replace: true })
    } catch (error) {
      message.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f5f7fb' }}>
      <Card style={{ width: 380, boxShadow: '0 18px 50px rgba(30, 55, 90, .10)' }}>
        <Typography.Title level={2} style={{ marginBottom: 4 }}>知忆 AI</Typography.Title>
        <Typography.Paragraph type="secondary">
          个人知识库与长期记忆问答系统
        </Typography.Paragraph>
        <Segmented
          block
          value={mode}
          options={[{ label: '登录', value: 'login' }, { label: '注册', value: 'register' }]}
          onChange={(value) => setMode(value as 'login' | 'register')}
          style={{ marginBottom: 20 }}
        />
        <Form<AuthForm> layout="vertical" onFinish={submit}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, min: 6, message: '密码至少 6 位' }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
          </Form.Item>
          <Button block type="primary" htmlType="submit" loading={loading}>
            {mode === 'login' ? '登录' : '创建账号'}
          </Button>
        </Form>
      </Card>
    </div>
  )
}
