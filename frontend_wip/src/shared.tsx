import { createContext, useEffect, useState } from 'react';
import axios from 'axios';

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

export type ToolAuditStatus = 'success' | 'rejected' | 'error' | string;

export interface ToolAuditEvent {
  id: number;
  timestamp: string;
  username?: string;
  user_role?: string;
  action: string;
  resource: string;
  resource_id: string;
  details?: {
    tool_name?: string;
    status?: ToolAuditStatus;
    duration_ms?: number;
    worker_id?: string;
    worker_name?: string;
    task_id?: string;
    subtask_id?: string;
    arguments?: Record<string, any>;
    result?: Record<string, any> | null;
    error?: string;
  };
}

export interface ToolAuditResponse {
  tool_name?: string | null;
  status?: string | null;
  worker_id?: string | null;
  task_id?: string | null;
  timestamp_from?: string | null;
  timestamp_to?: string | null;
  limit: number;
  offset: number;
  count: number;
  total: number;
  summary: Record<'success' | 'rejected' | 'error', number>;
  events: ToolAuditEvent[];
}

export interface ToolPolicyDataSource {
  name: string;
  connection_string: string;
  redacted?: boolean;
}

export interface ToolPolicyResponse {
  network: {
    allow_internal_hosts: string[];
  };
  web: {
    request_timeout_seconds: number;
    max_response_bytes: number;
    max_fetch_chars: number;
    max_search_results: number;
  };
  playwright_crawler: {
    max_concurrency: number;
    queue_timeout_seconds: number;
    total_timeout_seconds: number;
    navigation_timeout_ms: number;
    selector_timeout_ms: number;
    idle_wait_ms: number;
    max_pages: number;
    max_response_bytes: number;
    max_output_chars: number;
  };
  database: {
    default_access_mode: 'read_only' | 'write' | 'admin';
    allow_raw_connection_strings: boolean;
    allow_write: boolean;
    allow_ddl: boolean;
    allow_multiple_statements: boolean;
    max_result_rows: number;
    data_sources: ToolPolicyDataSource[];
  };
}

export interface KnowledgeGraphNode {
  id: string;
  name: string;
  val: number;
  degree: number;
  evidence_count: number;
  source_doc_count: number;
  matched?: boolean;
}

export interface KnowledgeGraphLink {
  source: string | KnowledgeGraphNode;
  target: string | KnowledgeGraphNode;
  label: string;
  predicate: string;
  confidence: number;
  source_chunk_id?: string;
  source_doc_id?: string;
}

export interface KnowledgeGraphEntity {
  id: string;
  name: string;
  degree: number;
  evidence_count: number;
  source_doc_count: number;
}

export interface KnowledgeGraphPayload {
  nodes: KnowledgeGraphNode[];
  links: KnowledgeGraphLink[];
  stats: Record<string, any>;
  entities: KnowledgeGraphEntity[];
  predicates: Array<{ predicate: string; count: number }>;
  matched_entity?: string | null;
  filters: {
    entity: string;
    query: string;
    depth: number;
    limit: number;
    min_confidence: number;
    predicate: string;
  };
}

export interface KnowledgeGraphTripleMutation {
  subject: string;
  predicate: string;
  object: string;
  source_chunk_id?: string;
  confidence?: number;
}

export type KnowledgeGraphQualityCandidateType =
  | 'duplicate_entity'
  | 'ambiguous_entity'
  | 'low_confidence_relation'
  | 'isolated_relation'
  | 'conflicting_relation'
  | string;

export interface KnowledgeGraphQualityCandidate {
  id: string;
  fingerprint: string;
  type: KnowledgeGraphQualityCandidateType;
  severity: 'high' | 'medium' | 'low' | string;
  score: number;
  title: string;
  description: string;
  entities?: string[];
  triple?: KnowledgeGraphTripleMutation;
  related_triples?: KnowledgeGraphTripleMutation[];
  action?: {
    type: string;
    source?: string;
    target?: string;
    new_entity?: string;
    triple?: KnowledgeGraphTripleMutation;
    triples?: KnowledgeGraphTripleMutation[];
  };
  reasons?: string[];
  decision?: KnowledgeGraphReviewDecision;
  stale_decision?: KnowledgeGraphReviewDecision;
}

export type KnowledgeGraphReviewStatus = 'open' | 'accepted' | 'ignored' | 'snoozed';

export interface KnowledgeGraphReviewDecision {
  candidate_id: string;
  status: KnowledgeGraphReviewStatus;
  note: string;
  details: Record<string, any>;
  username: string;
  user_role: string;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeGraphQualityGateViolation {
  level: 'warning' | 'error' | string;
  code: string;
  title: string;
  message: string;
  value: number;
  threshold: number;
}

export interface KnowledgeGraphQualityGate {
  enabled: boolean;
  passed: boolean;
  status: ReadinessStatus;
  message: string;
  violations: KnowledgeGraphQualityGateViolation[];
  thresholds: Record<string, number | boolean>;
  metrics?: Record<string, number>;
}

export interface KnowledgeGraphQualityTrigger {
  action: string;
  doc_id: string;
  filename: string;
  document_action?: string;
  chunk_count?: number;
  relation_count?: number;
  previous_health_score?: number | null;
  current_health_score?: number;
  health_drop?: number;
}

export interface KnowledgeGraphQualityMetrics {
  health_score: number;
  risk_level: 'low' | 'medium' | 'high' | string;
  relation_count: number;
  entity_count: number;
  source_doc_count: number;
  source_chunk_count: number;
  triples_per_source_chunk: number;
  candidate_count: number;
  duplicate_entity_ratio: number;
  low_confidence_ratio: number;
  isolated_relation_ratio: number;
  conflicting_relation_ratio: number;
  ambiguous_entity_ratio?: number;
  review_backlog_ratio: number;
  open_candidate_count?: number;
  decided_candidate_count?: number;
  open_review_backlog_ratio?: number;
  alerts?: KnowledgeGraphQualityAlert[];
  quality_gate?: KnowledgeGraphQualityGate;
  trigger?: KnowledgeGraphQualityTrigger;
}

export interface KnowledgeGraphQualityAlert {
  level: 'warning' | 'error' | string;
  code: string;
  title: string;
  message: string;
  value: number;
  threshold: number;
  action: string;
}

export interface KnowledgeGraphQualitySnapshot extends KnowledgeGraphQualityMetrics {
  id: number;
  metrics: KnowledgeGraphQualityMetrics;
  created_at: string;
}

export interface KnowledgeGraphQualityPayload {
  summary: {
    total_candidates: number;
    returned_candidates: number;
    duplicate_entity_count: number;
    low_confidence_relation_count: number;
    isolated_relation_count: number;
    conflicting_relation_count: number;
    ambiguous_entity_count?: number;
    average_confidence: number;
    quality_metrics?: KnowledgeGraphQualityMetrics;
    quality_gate?: KnowledgeGraphQualityGate;
    trigger?: KnowledgeGraphQualityTrigger;
    latest_snapshot_id?: number;
    hidden_decided_count?: number;
    stale_decision_count?: number;
    nodes?: number;
    edges?: number;
  };
  candidates: KnowledgeGraphQualityCandidate[];
  thresholds: {
    confidence_threshold: number;
    limit: number;
  };
  filters?: {
    status?: string;
  };
}

export interface KnowledgeGraphQualityHistoryPayload {
  snapshots: KnowledgeGraphQualitySnapshot[];
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
  if (eventType === 'knowledge_graph_quality_alert') return '图谱质量';
  if (eventType === 'knowledge_graph_governance_task') return '图谱治理';
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
  'knowledge_graph_quality_alert',
  'knowledge_graph_governance_task',
];

export const opsEventStatusOptions: ReadinessStatus[] = ['ok', 'warning', 'error'];

export const toolAuditStatusOptions: ToolAuditStatus[] = ['success', 'rejected', 'error'];

export const toolAuditStatusLabel = (status?: ToolAuditStatus) => {
  if (status === 'success') return '成功';
  if (status === 'rejected') return '策略拒绝';
  if (status === 'error') return '错误';
  return status || '未知';
};

export const toolAuditStatusColor = (status?: ToolAuditStatus) => {
  if (status === 'success') return 'green';
  if (status === 'rejected') return 'orange';
  if (status === 'error') return 'red';
  return 'default';
};

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
  getTaskTrace: (taskId: string, params?: object) => axios.get(`${API_BASE}/tasks/${taskId}/trace`, { params }),
  getTaskDiagnosis: (taskId: string) => axios.get(`${API_BASE}/tasks/${taskId}/diagnosis`),
  getTaskRetrySuggestion: (taskId: string) => axios.get(`${API_BASE}/tasks/${taskId}/retry-suggestion`),
  getTaskDiagnosisReport: (taskId: string) => axios.get(`${API_BASE}/tasks/${taskId}/diagnosis-report`),

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
  listToolAudit: (options: {
    limit?: number;
    offset?: number;
    toolName?: string;
    status?: string;
    workerId?: string;
    taskId?: string;
  } = {}) =>
    axios.get<ToolAuditResponse>(`${API_BASE}/system/tool-audit`, {
      params: {
        limit: options.limit ?? 12,
        offset: options.offset ?? 0,
        tool_name: options.toolName || undefined,
        status: options.status || undefined,
        worker_id: options.workerId || undefined,
        task_id: options.taskId || undefined,
      },
    }),
  getToolPolicy: () => axios.get<ToolPolicyResponse>(`${API_BASE}/system/tool-policy`),
  updateToolPolicy: (payload: ToolPolicyResponse) =>
    axios.put<{ success: boolean; message: string; tool_policy: ToolPolicyResponse }>(
      `${API_BASE}/system/tool-policy`,
      payload,
    ),
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
  retryTask: (id: string, force: boolean = false) => axios.post(`${API_BASE}/tasks/${id}/retry`, { force }),

  // 文档 chunks + 搜索
  getDocumentChunks: (docId: string) => axios.get(`${API_BASE}/documents/${docId}/chunks`),
  getDocumentMediaAssets: (docId: string) => axios.get(`${API_BASE}/documents/${docId}/media-assets`),
  searchDocuments: (q: string, groupIds?: string) =>
    axios.get(`${API_BASE}/documents/search`, { params: { q, group_ids: groupIds } }),
  getKnowledgeGraph: (params?: {
    entity?: string;
    q?: string;
    depth?: number;
    limit?: number;
    min_confidence?: number;
    predicate?: string;
  }) => axios.get<KnowledgeGraphPayload>(`${API_BASE}/knowledge/graph`, { params }),
  getKnowledgeGraphQuality: (params?: { confidence_threshold?: number; limit?: number; status?: string }) =>
    axios.get<KnowledgeGraphQualityPayload>(`${API_BASE}/knowledge/graph/quality`, { params }),
  getKnowledgeGraphQualityHistory: (params?: { limit?: number }) =>
    axios.get<KnowledgeGraphQualityHistoryPayload>(`${API_BASE}/knowledge/graph/quality/history`, { params }),
  setKnowledgeGraphQualityDecision: (data: {
    candidate_id: string;
    status: KnowledgeGraphReviewStatus;
    note?: string;
    details?: Record<string, any>;
  }) => axios.post<{ success: boolean; decision: KnowledgeGraphReviewDecision }>(
    `${API_BASE}/knowledge/graph/quality/decisions`,
    data,
  ),
  setKnowledgeGraphQualityDecisions: (data: {
    decisions: Array<{
      candidate_id: string;
      status: KnowledgeGraphReviewStatus;
      note?: string;
      details?: Record<string, any>;
    }>;
  }) => axios.post<{ success: boolean; updated: number; decisions: KnowledgeGraphReviewDecision[] }>(
    `${API_BASE}/knowledge/graph/quality/decisions/batch`,
    data,
  ),
  mergeKnowledgeGraphEntities: (data: { source: string; target: string }) =>
    axios.post(`${API_BASE}/knowledge/graph/entities/merge`, data),
  splitKnowledgeGraphEntity: (data: {
    source: string;
    new_entity: string;
    triples: KnowledgeGraphTripleMutation[];
  }) => axios.post(`${API_BASE}/knowledge/graph/entities/split`, data),
  updateKnowledgeGraphTriple: (data: {
    old: KnowledgeGraphTripleMutation;
    new: KnowledgeGraphTripleMutation;
  }) => axios.put(`${API_BASE}/knowledge/graph/triples`, data),
  deleteKnowledgeGraphTriple: (data: KnowledgeGraphTripleMutation) =>
    axios.post(`${API_BASE}/knowledge/graph/triples/delete`, data),
  generateI2V: (data: any) => axios.post(`${API_BASE}/videos/i2v`, data),
  enqueueI2VJob: (data: any) => axios.post(`${API_BASE}/videos/i2v/jobs`, data),
  generateI2VBatch: (items: any[]) => axios.post(`${API_BASE}/videos/i2v/batch`, { items }),
  enqueueI2VBatchJobs: (items: any[]) => axios.post(`${API_BASE}/videos/i2v/batch/jobs`, { items }),
  editVideo: (data: any) => axios.post(`${API_BASE}/videos/edit`, data),
  enqueueVideoEditJob: (data: any) => axios.post(`${API_BASE}/videos/edit/jobs`, data),
  listVideoAssets: (params?: any) => axios.get(`${API_BASE}/videos/assets`, { params }),
  getVideoJobsStatus: () => axios.get(`${API_BASE}/videos/jobs/status`),
  getVideoAsset: (assetId: string) => axios.get(`${API_BASE}/videos/assets/${assetId}`),
  retryVideoAsset: (assetId: string) => axios.post(`${API_BASE}/videos/assets/${assetId}/retry`),
  deleteVideoAsset: (assetId: string) => axios.delete(`${API_BASE}/videos/assets/${assetId}`),

  // 任务反馈
  submitTaskFeedback: (taskId: string, feedback: string) =>
    axios.post(`${API_BASE}/tasks/${taskId}/feedback`, { feedback }),
};
