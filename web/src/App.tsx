import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import RequireAuth from './components/RequireAuth'
import ShellLayout from './layouts/ShellLayout'
import AuthPage from './pages/AuthPage'
import DashboardPage from './pages/DashboardPage'
import GraphPage from './pages/GraphPage'
import KnowledgePage from './pages/KnowledgePage'
import ModelConfigPage from './pages/ModelConfigPage'
import SimpleChatPage from './pages/SimpleChatPage'
import SimpleMemoryPage from './pages/SimpleMemoryPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<AuthPage />} />
        <Route path="/" element={<RequireAuth><ShellLayout /></RequireAuth>}>
          <Route index element={<DashboardPage />} />
          <Route path="chat" element={<SimpleChatPage />} />
          <Route path="knowledge" element={<KnowledgePage />} />
          <Route path="memory" element={<SimpleMemoryPage />} />
          <Route path="graph" element={<GraphPage />} />
          <Route path="settings/models" element={<ModelConfigPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
