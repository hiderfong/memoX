import { Suspense, lazy, useContext, useEffect, useState, type FC, type ReactNode } from 'react';
import { Spin } from 'antd';
import { useNavigate, Routes, Route, Navigate } from 'react-router-dom';

import { AuthContext, TOKEN_KEY, USER_KEY, api, type AuthUser } from './shared';

import { AppLayout } from './components/AppLayout';

const LoginPage = lazy(() => import('./pages/LoginPage').then(({ LoginPage }) => ({ default: LoginPage })));
const DocumentsPage = lazy(() => import('./pages/DocumentsPage').then(({ DocumentsPage }) => ({ default: DocumentsPage })));
const ChatPage = lazy(() => import('./pages/ChatPage').then(({ ChatPage }) => ({ default: ChatPage })));
const TasksPage = lazy(() => import('./pages/TasksPage').then(({ TasksPage }) => ({ default: TasksPage })));
const ScheduledTasksPage = lazy(() => import('./pages/ScheduledTasksPage').then(({ ScheduledTasksPage }) => ({ default: ScheduledTasksPage })));
const WorkflowsPage = lazy(() => import('./pages/WorkflowsPage').then(({ WorkflowsPage }) => ({ default: WorkflowsPage })));
const WorkersPage = lazy(() => import('./pages/WorkersPage').then(({ WorkersPage }) => ({ default: WorkersPage })));
const SystemStatusPage = lazy(() => import('./pages/SystemStatusPage').then(({ SystemStatusPage }) => ({ default: SystemStatusPage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then(({ SettingsPage }) => ({ default: SettingsPage })));

// ==================== 受保护路由 ====================

const RequireAuth: FC<{ children: ReactNode }> = ({ children }) => {
  const { user } = useContext(AuthContext);
  const navigate = useNavigate();

  useEffect(() => {
    if (!user) navigate('/login', { replace: true });
  }, [user, navigate]);

  if (!user) return null;
  return <>{children}</>;
};

const PageFallback: FC = () => (
  <div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}>
    <Spin />
  </div>
);

const lazyRoute = (element: ReactNode) => (
  <Suspense fallback={<PageFallback />}>{element}</Suspense>
);


// ==================== 主应用 ====================

const App: FC = () => {
  const [user, setUser] = useState<AuthUser | null>(() => {
    try {
      const stored = localStorage.getItem(USER_KEY);
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  });
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(TOKEN_KEY)
  );

  const login = async (username: string, password: string) => {
    const res = await api.login(username, password);
    const { token: t, user: u } = res.data;
    localStorage.setItem(TOKEN_KEY, t);
    localStorage.setItem(USER_KEY, JSON.stringify(u));
    setToken(t);
    setUser(u);
  };

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  };

  // 启动时验证 token 是否仍然有效
  useEffect(() => {
    if (token) {
      api.me().catch(() => logout());
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, login, logout }}>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/documents" replace /> : lazyRoute(<LoginPage />)} />
        <Route path="/*" element={
          <RequireAuth>
            <AppLayout>
              <Routes>
                <Route path="/" element={<Navigate to="/documents" replace />} />
                <Route path="/documents" element={lazyRoute(<DocumentsPage />)} />
                <Route path="/chat" element={lazyRoute(<ChatPage />)} />
                <Route path="/tasks" element={lazyRoute(<TasksPage />)} />
                <Route path="/scheduled-tasks" element={lazyRoute(<ScheduledTasksPage />)} />
                <Route path="/workflows" element={lazyRoute(<WorkflowsPage />)} />
                <Route path="/workers" element={lazyRoute(<WorkersPage />)} />
                <Route path="/system" element={lazyRoute(<SystemStatusPage />)} />
                <Route path="/settings" element={lazyRoute(<SettingsPage />)} />
              </Routes>
            </AppLayout>
          </RequireAuth>
        } />
      </Routes>
    </AuthContext.Provider>
  );
};

export default App;
