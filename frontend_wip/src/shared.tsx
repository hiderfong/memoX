import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { Layout, Menu, Typography, Card, Button, Upload, List, Space, Avatar, Input, message, Spin, Tag, Progress, Badge, Drawer, Timeline, Alert, Empty, Tooltip, Form, Divider, Checkbox, Modal, Tabs, Table, Select, Slider, InputNumber, AutoComplete, Switch, Segmented } from 'antd';
import { UploadOutlined, FileTextOutlined, RobotOutlined, MessageOutlined, TeamOutlined, SettingOutlined, CloudUploadOutlined, DeleteOutlined, SendOutlined, LoadingOutlined, BulbOutlined, ThunderboltOutlined, ClockCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, InboxOutlined, UserOutlined, LockOutlined, LogoutOutlined, SafetyCertificateOutlined, LinkOutlined, FolderOpenOutlined, MailOutlined, LineChartOutlined, FileSearchOutlined, EyeOutlined, SaveOutlined, DownOutlined, UpOutlined, PlusOutlined, EditOutlined, DownloadOutlined, BgColorsOutlined, ReloadOutlined, RollbackOutlined, ExclamationCircleOutlined, ToolOutlined, DeploymentUnitOutlined } from '@ant-design/icons';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip as RTooltip, Legend, ResponsiveContainer } from 'recharts';
import { useNavigate, useLocation, Routes, Route, Link, Navigate } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';
import { I2VModal } from './components/I2VModal';
import { WorkflowsPage } from './pages/WorkflowsPage';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;
const { TextArea } = Input;
const { Dragger } = Upload;

// ==================== 响应式 Hook ====================

export const MOBILE_BREAKPOINT = 768;

export function useIsMobile() {
  const [isMobile, setIsMobile] = useState(window.innerWidth < MOBILE_BREAKPOINT);
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, []);
  return isMobile;
}

// ==================== API 配置 ====================

export const API_BASE = '/api';

// ==================== 分组类型 ====================

export interface KnowledgeGroup {
  id: string;
  name: string;
  color: string;
  created_at: string;
  doc_count: number;
}

export type ReadinessStatus = 'ok' | 'warning' | 'error' | string;

export interface SystemCheck {
  name: string;
  status: ReadinessStatus;
  message: string;
  details?: Record<string, any>;
  duration_ms?: number;
}

export interface SystemHealthReport {
  ok: boolean;
  status: ReadinessStatus;
  root: string;
  config: string;
  checks: SystemCheck[];
  runtime?: Record<string, any>;
  ops?: Record<string, any>;
}

export interface BackupArchiveSummary {
  name: string;
  archive: string;
  status: ReadinessStatus;
  message?: string;
  size_bytes?: number;
  modified_at?: string;
  created_at?: string;
  entry_count?: number;
  metadata_valid?: boolean;
}

export interface OpsEvent {
  id: number;
  event_type: string;
  status: ReadinessStatus;
  action?: string;
  message?: string;
  details?: Record<string, any>;
  created_at: string;
}

export interface OpsEventsResponse {
  event_type?: string | null;
  status?: string | null;
  limit: number;
  offset: number;
  count: number;
  total: number;
  events: OpsEvent[];
}

export interface LifecycleCleanupResult {
  ok: boolean;
  status: ReadinessStatus;
  action: string;
  message?: string;
  dry_run: boolean;
  policy?: Record<string, number>;
  tables?: Array<{
    name: string;
    retention_days: number;
    cutoff: string;
    eligible_count: number;
    deleted_count: number;
  }>;
  diagnostics?: {
    candidate_count: number;
    eligible_count: number;
    deleted_count: number;
    eligible_bytes: number;
    eligible?: Array<{
      path: string;
      name: string;
      size_bytes: number;
      modified_at: string;
      reason: string;
    }>;
    deleted?: Array<{
      path: string;
      name: string;
      size_bytes: number;
      modified_at: string;
      reason: string;
    }>;
  };
  summary?: {
    eligible_records: number;
    deleted_records: number;
    eligible_files: number;
    deleted_files: number;
    eligible_bytes: number;
    core_user_data_deleted: boolean;
  };
  event_id?: number;
  recorded_at?: string;
}

export const statusTagColor = (status?: ReadinessStatus) => {
  if (status === 'ok') return 'green';
  if (status === 'warning') return 'orange';
  if (status === 'error') return 'red';
  return 'default';
};

export const statusBadge = (status?: ReadinessStatus): 'success' | 'warning' | 'error' | 'default' => {
  if (status === 'ok') return 'success';
  if (status === 'warning') return 'warning';
  if (status === 'error') return 'error';
  return 'default';
};

export const statusLabel = (status?: ReadinessStatus) => {
  if (status === 'ok') return '正常';
  if (status === 'warning') return '警告';
  if (status === 'error') return '错误';
  return status || '未知';
};

export const opsEventLabel = (eventType?: string) => {
  if (eventType === 'backup_maintenance') return '备份维护';
  if (eventType === 'diagnostics_export') return '诊断导出';
  if (eventType === 'index_repair') return '索引修复';
  if (eventType === 'restore_preflight') return '恢复预检';
  if (eventType === 'restore_execute') return '真实恢复';
  if (eventType === 'restore_drill') return '恢复演练';
  if (eventType === 'lifecycle_cleanup') return '生命周期清理';
  return eventType || '运维事件';
};

export const opsEventTypeOptions = [
  'backup_maintenance',
  'diagnostics_export',
  'index_repair',
  'restore_preflight',
  'restore_execute',
  'restore_drill',
  'lifecycle_cleanup',
];

export const opsEventStatusOptions: ReadinessStatus[] = ['ok', 'warning', 'error'];

export const opsEventActorLabel = (event: OpsEvent) => {
  const actor = event.details?.actor;
  if (!actor) return '-';
  if (actor.display_name && actor.username && actor.display_name !== actor.username) {
    return `${actor.display_name} (${actor.username})`;
  }
  return actor.username || actor.display_name || '-';
};

export const formatBytes = (bytes?: number) => {
  if (typeof bytes !== 'number' || Number.isNaN(bytes)) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
};

export const formatDuration = (ms?: number) => {
  if (typeof ms !== 'number') return '-';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
};

// ==================== 认证状态 ====================

export interface AuthUser {
  username: string;
  role: string;
  display_name: string;
}

export interface AuthContextType {
  user: AuthUser | null;
  token: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextType>({
  user: null,
  token: null,
  login: async () => {},
  logout: () => {},
});

export const TOKEN_KEY = 'memox_token';
export const USER_KEY  = 'memox_user';

// Axios 请求拦截器：自动附加 Authorization header
axios.interceptors.request.use(config => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`;
  }
  return config;
});

// Axios 响应拦截器：401 自动清除登录态并强制跳转登录页
axios.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401) {
      const isLoginRequest = err.config?.url?.includes('/auth/login');
      if (!isLoginRequest) {
        // 清除本地存储，并通过 storage 事件触发 React 状态更新
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        // 直接跳转到登录页（兜底：防止 React 状态未更新时页面卡住）
        if (!window.location.pathname.includes('/login')) {
          window.location.href = '/login';
        }
      }
    }
    return Promise.reject(err);
  }
);

export const api = {
  // 文档
  listDocuments: () => axios.get(`${API_BASE}/documents`),
  uploadDocument: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return axios.post(`${API_BASE}/documents`, formData);
  },
  deleteDocument: (id: string) => axios.delete(`${API_BASE}/documents/${id}`),
  
  // 聊天
  chat: (message: string, sessionId?: string, useRag: boolean = true, activeGroupIds?: string[] | null, workerId?: string | null) =>
    axios.post(`${API_BASE}/chat`, { message, session_id: sessionId, use_rag: useRag, stream: false, active_group_ids: activeGroupIds, worker_id: workerId || undefined }),
  chatStream: (message: string, sessionId?: string, useRag: boolean = true) =>
    axios.post(`${API_BASE}/chat/stream`, { message, session_id: sessionId, use_rag: useRag, stream: true }),
  
  // 任务
  createTask: (description: string, context?: object, activeGroupIds?: string[] | null) =>
    axios.post(`${API_BASE}/tasks`, { description, context, generate_suggestions: true, active_group_ids: activeGroupIds }),
  listTasks: () => axios.get(`${API_BASE}/tasks`),
  getTask: (id: string) => axios.get(`${API_BASE}/tasks/${id}`),
  
  // 文档 URL 导入
  importUrl: (url: string) => axios.post(`${API_BASE}/documents/url`, { url }),

  // 任务文件
  getTaskFiles: (taskId: string) => axios.get(`${API_BASE}/tasks/${taskId}/files`),
  getTaskEvents: (taskId: string) => axios.get(`${API_BASE}/tasks/${taskId}/events`),

  // Workers
  listWorkers: () => axios.get(`${API_BASE}/workers`),
  getWorkerLogs: (workerId: string, limit?: number) =>
    axios.get(`${API_BASE}/workers/${workerId}/logs`, { params: limit ? { limit } : {} }),
  clearWorkerLogs: (workerId: string) =>
    axios.delete(`${API_BASE}/workers/${workerId}/logs`),
  listProviders: () => axios.get(`${API_BASE}/providers`),
  updateWorkerConfig: (id: string, config: any) => axios.put(`${API_BASE}/workers/${id}/config`, config),
  createWorker: (data: any) => axios.post(`${API_BASE}/workers`, data),
  deleteWorker: (id: string) => axios.delete(`${API_BASE}/workers/${id}`),

  // Skills
  listInstalledSkills: () => axios.get(`${API_BASE}/skills`),
  searchSkills: (q: string, limit: number = 10) =>
    axios.get(`${API_BASE}/skills/search`, { params: { q, limit } }),
  uninstallSkill: (name: string) => axios.delete(`${API_BASE}/skills/${name}`),
  
  // 系统
  health: () => axios.get(`${API_BASE}/health`),
  systemHealth: () => axios.get<SystemHealthReport>(`${API_BASE}/system/health`),
  listBackups: () => axios.get<{ backups: BackupArchiveSummary[] }>(`${API_BASE}/system/backups`),
  listOpsEvents: (options: { limit?: number; offset?: number; eventType?: string; status?: string } = {}) =>
    axios.get<OpsEventsResponse>(`${API_BASE}/system/events`, {
      params: {
        limit: options.limit ?? 12,
        offset: options.offset ?? 0,
        event_type: options.eventType || undefined,
        status: options.status || undefined,
      },
    }),
  verifyBackup: (archiveName: string) =>
    axios.post(`${API_BASE}/system/backups/${encodeURIComponent(archiveName)}/verify`),
  runRestorePreflight: (archiveName: string) =>
    axios.post(`${API_BASE}/system/backups/${encodeURIComponent(archiveName)}/restore-preflight`),
  runRestoreBackup: (
    archiveName: string,
    payload: {
      confirm_archive_name: string;
      acknowledge_overwrite: boolean;
      acknowledge_maintenance_mode: boolean;
    },
  ) => axios.post(`${API_BASE}/system/backups/${encodeURIComponent(archiveName)}/restore`, payload),
  runRestoreDrill: (archiveName: string) =>
    axios.post(`${API_BASE}/system/backups/${encodeURIComponent(archiveName)}/restore-drill`),
  runBackupMaintenance: (force: boolean = true) =>
    axios.post(`${API_BASE}/system/maintenance/backup`, null, { params: { force } }),
  runLifecycleCleanup: (dryRun: boolean = true) =>
    axios.post<LifecycleCleanupResult>(`${API_BASE}/system/maintenance/lifecycle`, null, { params: { dry_run: dryRun } }),
  repairIndexes: () => axios.post(`${API_BASE}/system/indexes/repair`),
  exportDiagnostics: () => axios.get(`${API_BASE}/system/diagnostics/export`, { responseType: 'blob' }),

  // 认证
  login: (username: string, password: string) =>
    axios.post(`${API_BASE}/auth/login`, { username, password }),
  logout: () => axios.post(`${API_BASE}/auth/logout`),
  me: () => axios.get(`${API_BASE}/auth/me`),

  // 分组
  listGroups: () => axios.get(`${API_BASE}/groups`),
  createGroup: (name: string, color: string) =>
    axios.post(`${API_BASE}/groups`, { name, color }),
  updateGroup: (id: string, data: { name?: string; color?: string }) =>
    axios.put(`${API_BASE}/groups/${id}`, data),
  deleteGroup: (id: string) => axios.delete(`${API_BASE}/groups/${id}`),
  moveDocumentGroup: (docId: string, groupId: string) =>
    axios.put(`${API_BASE}/documents/${docId}/group`, { group_id: groupId }),

  // 会话历史
  listSessions: (archived?: 'archived' | 'all') =>
    axios.get(`${API_BASE}/chat/sessions`, { params: archived ? { archived: archived === 'archived' ? '1' : 'all' } : {} }),
  getSessionMessages: (id: string) => axios.get(`${API_BASE}/chat/sessions/${id}/messages`),
  deleteSession: (id: string) => axios.delete(`${API_BASE}/chat/sessions/${id}`),
  renameSession: (id: string, title: string) =>
    axios.patch(`${API_BASE}/chat/sessions/${id}`, { title }),
  archiveSession: (id: string, archived: boolean) =>
    axios.patch(`${API_BASE}/chat/sessions/${id}`, { archived }),
  summarizeSessionAsTask: (id: string, taskType?: string) =>
    axios.post(`${API_BASE}/chat/sessions/${id}/summarize-task`, { task_type: taskType || null }),

  // 定时任务
  listScheduledTasks: () => axios.get(`${API_BASE}/scheduled-tasks`),
  createScheduledTask: (data: any) => axios.post(`${API_BASE}/scheduled-tasks`, data),
  updateScheduledTask: (id: string, data: any) =>
    axios.patch(`${API_BASE}/scheduled-tasks/${id}`, data),
  deleteScheduledTask: (id: string) => axios.delete(`${API_BASE}/scheduled-tasks/${id}`),

  // 任务取消
  cancelTask: (id: string) => axios.post(`${API_BASE}/tasks/${id}/cancel`),
  retryTask: (id: string) => axios.post(`${API_BASE}/tasks/${id}/retry`),

  // 文档 chunks + 搜索
  getDocumentChunks: (docId: string) => axios.get(`${API_BASE}/documents/${docId}/chunks`),
  searchDocuments: (q: string, groupIds?: string) =>
    axios.get(`${API_BASE}/documents/search`, { params: { q, group_ids: groupIds } }),

  // 任务反馈
  submitTaskFeedback: (taskId: string, feedback: string) =>
    axios.post(`${API_BASE}/tasks/${taskId}/feedback`, { feedback }),
};

