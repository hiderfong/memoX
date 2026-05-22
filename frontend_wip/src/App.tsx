import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { Layout, Menu, Typography, Card, Button, Upload, List, Space, Avatar, Input, message, Spin, Tag, Progress, Badge, Drawer, Timeline, Alert, Empty, Tooltip, Form, Divider, Checkbox, Modal, Tabs, Table, Select, Slider, InputNumber, AutoComplete, Switch, Segmented } from 'antd';
import { UploadOutlined, FileTextOutlined, RobotOutlined, MessageOutlined, TeamOutlined, SettingOutlined, CloudUploadOutlined, DeleteOutlined, SendOutlined, LoadingOutlined, BulbOutlined, ThunderboltOutlined, ClockCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, InboxOutlined, UserOutlined, LockOutlined, LogoutOutlined, SafetyCertificateOutlined, LinkOutlined, FolderOpenOutlined, MailOutlined, LineChartOutlined, FileSearchOutlined, EyeOutlined, SaveOutlined, DownOutlined, UpOutlined, PlusOutlined, EditOutlined, DownloadOutlined, BgColorsOutlined, ReloadOutlined, RollbackOutlined, ExclamationCircleOutlined, ToolOutlined, DeploymentUnitOutlined } from '@ant-design/icons';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip as RTooltip, Legend, ResponsiveContainer } from 'recharts';
import { useNavigate, useLocation, Routes, Route, Link, Navigate, BrowserRouter } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';

import { MOBILE_BREAKPOINT, useIsMobile, API_BASE, KnowledgeGroup, ReadinessStatus, SystemCheck, SystemHealthReport, BackupArchiveSummary, OpsEvent, OpsEventsResponse, LifecycleCleanupResult, statusTagColor, statusBadge, statusLabel, opsEventLabel, opsEventTypeOptions, opsEventStatusOptions, opsEventActorLabel, formatBytes, formatDuration, AuthUser, AuthContextType, AuthContext, TOKEN_KEY, USER_KEY, api } from './shared';

import { LoginPage } from './pages/LoginPage';
import { AppLayout } from './components/AppLayout';
import { DocumentsPage } from './pages/DocumentsPage';
import { ChatPage } from './pages/ChatPage';
import { TasksPage } from './pages/TasksPage';
import { ScheduledTasksPage } from './pages/ScheduledTasksPage';
import { WorkersPage } from './pages/WorkersPage';
import { SystemStatusPage } from './pages/SystemStatusPage';
import { SettingsPage } from './pages/SettingsPage';
import { WorkflowsPage } from './pages/WorkflowsPage';

// ==================== 受保护路由 ====================

const RequireAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user } = useContext(AuthContext);
  const navigate = useNavigate();

  useEffect(() => {
    if (!user) navigate('/login', { replace: true });
  }, [user, navigate]);

  if (!user) return null;
  return <>{children}</>;
};


// ==================== 主应用 ====================

const App: React.FC = () => {
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
        <Route path="/login" element={user ? <Navigate to="/documents" replace /> : <LoginPage />} />
        <Route path="/*" element={
          <RequireAuth>
            <AppLayout>
              <Routes>
                <Route path="/" element={<Navigate to="/documents" replace />} />
                <Route path="/documents" element={<DocumentsPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/tasks" element={<TasksPage />} />
                <Route path="/scheduled-tasks" element={<ScheduledTasksPage />} />
                <Route path="/workflows" element={<WorkflowsPage />} />
                <Route path="/workers" element={<WorkersPage />} />
                <Route path="/system" element={<SystemStatusPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
            </AppLayout>
          </RequireAuth>
        } />
      </Routes>
    </AuthContext.Provider>
  );
};

export default App;
