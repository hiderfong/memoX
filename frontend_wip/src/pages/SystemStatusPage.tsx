import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { Layout, Menu, Typography, Card, Button, Upload, List, Space, Avatar, Input, message, Spin, Tag, Progress, Badge, Drawer, Timeline, Alert, Empty, Tooltip, Form, Divider, Checkbox, Modal, Tabs, Table, Select, Slider, InputNumber, AutoComplete, Switch, Segmented } from 'antd';
import { UploadOutlined, FileTextOutlined, RobotOutlined, MessageOutlined, TeamOutlined, SettingOutlined, CloudUploadOutlined, DeleteOutlined, SendOutlined, LoadingOutlined, BulbOutlined, ThunderboltOutlined, ClockCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, InboxOutlined, UserOutlined, LockOutlined, LogoutOutlined, SafetyCertificateOutlined, LinkOutlined, FolderOpenOutlined, MailOutlined, LineChartOutlined, FileSearchOutlined, EyeOutlined, SaveOutlined, DownOutlined, UpOutlined, PlusOutlined, EditOutlined, DownloadOutlined, BgColorsOutlined, ReloadOutlined, RollbackOutlined, ExclamationCircleOutlined, ToolOutlined, DeploymentUnitOutlined } from '@ant-design/icons';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip as RTooltip, Legend, ResponsiveContainer } from 'recharts';
import { useNavigate, useLocation, Routes, Route, Link, Navigate } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';
import { I2VModal } from '../components/I2VModal';
import { WorkflowsPage } from '../pages/WorkflowsPage';
import { MOBILE_BREAKPOINT, useIsMobile, API_BASE, KnowledgeGroup, ReadinessStatus, SystemCheck, SystemHealthReport, BackupArchiveSummary, OpsEvent, OpsEventsResponse, LifecycleCleanupResult, ToolAuditEvent, statusTagColor, statusBadge, statusLabel, opsEventLabel, opsEventTypeOptions, opsEventStatusOptions, opsEventActorLabel, toolAuditStatusOptions, toolAuditStatusLabel, toolAuditStatusColor, formatBytes, formatDuration, AuthUser, AuthContextType, AuthContext, TOKEN_KEY, USER_KEY, api } from '../shared';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;
const { TextArea } = Input;
const { Dragger } = Upload;

import { TaskEventsPanel } from '../pages/TasksPage';
// ==================== 系统状态页面 ====================

export const SystemStatusPage: React.FC = () => {
  const { user } = useContext(AuthContext);
  const isMobile = useIsMobile();
  const [report, setReport] = useState<SystemHealthReport | null>(null);
  const [backups, setBackups] = useState<BackupArchiveSummary[]>([]);
  const [opsTasks, setOpsTasks] = useState<any[]>([]);
  const [opsEvents, setOpsEvents] = useState<OpsEvent[]>([]);
  const [opsEventsTotal, setOpsEventsTotal] = useState(0);
  const [opsEventsPage, setOpsEventsPage] = useState(1);
  const [opsEventsPageSize, setOpsEventsPageSize] = useState(8);
  const [opsEventTypeFilter, setOpsEventTypeFilter] = useState<string | undefined>();
  const [opsEventStatusFilter, setOpsEventStatusFilter] = useState<string | undefined>();
  const [selectedOpsEvent, setSelectedOpsEvent] = useState<OpsEvent | null>(null);
  const [toolAuditEvents, setToolAuditEvents] = useState<ToolAuditEvent[]>([]);
  const [toolAuditTotal, setToolAuditTotal] = useState(0);
  const [toolAuditSummary, setToolAuditSummary] = useState<Record<'success' | 'rejected' | 'error', number>>({
    success: 0,
    rejected: 0,
    error: 0,
  });
  const [toolAuditPage, setToolAuditPage] = useState(1);
  const [toolAuditPageSize, setToolAuditPageSize] = useState(8);
  const [toolAuditStatusFilter, setToolAuditStatusFilter] = useState<string | undefined>();
  const [toolAuditToolFilter, setToolAuditToolFilter] = useState<string | undefined>();
  const [toolAuditWorkerFilter, setToolAuditWorkerFilter] = useState('');
  const [toolAuditTaskFilter, setToolAuditTaskFilter] = useState('');
  const [selectedToolAudit, setSelectedToolAudit] = useState<ToolAuditEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [backupsLoading, setBackupsLoading] = useState(true);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [toolAuditLoading, setToolAuditLoading] = useState(true);
  const [maintenanceRunning, setMaintenanceRunning] = useState(false);
  const [cleanupRunning, setCleanupRunning] = useState(false);
  const [cleanupExecuting, setCleanupExecuting] = useState(false);
  const [cleanupPreview, setCleanupPreview] = useState<LifecycleCleanupResult | null>(null);
  const [repairingIndexes, setRepairingIndexes] = useState(false);
  const [exportingDiagnostics, setExportingDiagnostics] = useState(false);
  const [verifyingBackup, setVerifyingBackup] = useState('');
  const [preflightingBackup, setPreflightingBackup] = useState('');
  const [drillingBackup, setDrillingBackup] = useState('');
  const [restoringBackup, setRestoringBackup] = useState('');
  const [retryingOpsTaskId, setRetryingOpsTaskId] = useState('');
  const [selectedOpsTask, setSelectedOpsTask] = useState<any | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string>('');

  const fetchOpsEvents = async (
    page: number = opsEventsPage,
    pageSize: number = opsEventsPageSize,
    eventType: string | undefined = opsEventTypeFilter,
    status: string | undefined = opsEventStatusFilter,
  ) => {
    setEventsLoading(true);
    try {
      const res = await api.listOpsEvents({
        limit: pageSize,
        offset: (page - 1) * pageSize,
        eventType,
        status,
      });
      setOpsEvents(res.data?.events || []);
      setOpsEventsTotal(res.data?.total ?? res.data?.count ?? 0);
      setOpsEventsPage(page);
      setOpsEventsPageSize(pageSize);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '获取运维事件失败');
    } finally {
      setEventsLoading(false);
    }
  };

  const fetchToolAudit = async (
    page: number = toolAuditPage,
    pageSize: number = toolAuditPageSize,
    status: string | undefined = toolAuditStatusFilter,
    toolName: string | undefined = toolAuditToolFilter,
    workerId: string = toolAuditWorkerFilter,
    taskId: string = toolAuditTaskFilter,
  ) => {
    setToolAuditLoading(true);
    try {
      const res = await api.listToolAudit({
        limit: pageSize,
        offset: (page - 1) * pageSize,
        status,
        toolName,
        workerId: workerId.trim() || undefined,
        taskId: taskId.trim() || undefined,
      });
      setToolAuditEvents(res.data?.events || []);
      setToolAuditTotal(res.data?.total ?? res.data?.count ?? 0);
      setToolAuditSummary(res.data?.summary || { success: 0, rejected: 0, error: 0 });
      setToolAuditPage(page);
      setToolAuditPageSize(pageSize);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '获取工具调用审计失败');
    } finally {
      setToolAuditLoading(false);
    }
  };

  const fetchReport = async () => {
    setLoading(true);
    setBackupsLoading(true);
    setEventsLoading(true);
    setToolAuditLoading(true);
    try {
      const [healthRes, backupsRes, eventsRes, toolAuditRes, tasksRes] = await Promise.all([
        api.systemHealth(),
        api.listBackups(),
        api.listOpsEvents({
          limit: opsEventsPageSize,
          offset: (opsEventsPage - 1) * opsEventsPageSize,
          eventType: opsEventTypeFilter,
          status: opsEventStatusFilter,
        }),
        api.listToolAudit({
          limit: toolAuditPageSize,
          offset: (toolAuditPage - 1) * toolAuditPageSize,
          status: toolAuditStatusFilter,
          toolName: toolAuditToolFilter,
          workerId: toolAuditWorkerFilter.trim() || undefined,
          taskId: toolAuditTaskFilter.trim() || undefined,
        }),
        api.listTasks().catch(() => ({ data: [] })),
      ]);
      setReport(healthRes.data);
      setBackups(backupsRes.data?.backups || []);
      setOpsEvents(eventsRes.data?.events || []);
      setOpsEventsTotal(eventsRes.data?.total ?? eventsRes.data?.count ?? 0);
      setToolAuditEvents(toolAuditRes.data?.events || []);
      setToolAuditTotal(toolAuditRes.data?.total ?? toolAuditRes.data?.count ?? 0);
      setToolAuditSummary(toolAuditRes.data?.summary || { success: 0, rejected: 0, error: 0 });
      setOpsTasks(tasksRes.data || []);
      setLastUpdated(dayjs().format('YYYY-MM-DD HH:mm:ss'));
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '获取系统状态失败');
    } finally {
      setLoading(false);
      setBackupsLoading(false);
      setEventsLoading(false);
      setToolAuditLoading(false);
    }
  };

  useEffect(() => {
    if (user?.role === 'admin') fetchReport();
    else {
      setLoading(false);
      setBackupsLoading(false);
      setEventsLoading(false);
      setToolAuditLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.role]);

  const handleRunBackupMaintenance = async () => {
    setMaintenanceRunning(true);
    try {
      const res = await api.runBackupMaintenance(true);
      if (res.data?.ok) {
        message.success(res.data.action === 'created' ? '备份已创建并校验' : '备份维护已完成');
      } else {
        message.error(res.data?.message || '备份维护失败');
      }
      await fetchReport();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '备份维护失败');
    } finally {
      setMaintenanceRunning(false);
    }
  };

  const handleLifecycleDryRun = async () => {
    setCleanupRunning(true);
    try {
      const res = await api.runLifecycleCleanup(true);
      setCleanupPreview(res.data);
      if (res.data?.ok) {
        message.success('生命周期清理预检完成');
      } else {
        message.warning(res.data?.message || '生命周期清理预检完成但存在警告');
      }
      await fetchReport();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '生命周期清理预检失败');
    } finally {
      setCleanupRunning(false);
    }
  };

  const handleExecuteLifecycleCleanup = () => {
    const summary = cleanupPreview?.summary;
    Modal.confirm({
      title: '执行生命周期清理',
      icon: <ExclamationCircleOutlined />,
      okText: '执行清理',
      cancelText: '取消',
      okButtonProps: { danger: true },
      width: 560,
      content: (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Alert
            type="warning"
            showIcon
            message="清理会删除过期运维事件、审计日志和诊断包。"
            description="聊天、记忆、上传文档和工作区文件不会被删除。"
          />
          <Space wrap>
            <Tag>记录 {summary?.eligible_records ?? '-'}</Tag>
            <Tag>诊断包 {summary?.eligible_files ?? '-'}</Tag>
            <Tag>空间 {formatBytes(summary?.eligible_bytes)}</Tag>
          </Space>
        </Space>
      ),
      onOk: async () => {
        setCleanupExecuting(true);
        try {
          const res = await api.runLifecycleCleanup(false);
          setCleanupPreview(res.data);
          if (res.data?.ok) {
            message.success('生命周期清理已执行');
          } else {
            message.warning(res.data?.message || '生命周期清理完成但存在警告');
          }
          await fetchReport();
        } catch (err: any) {
          const detail = err.response?.data?.detail;
          message.error(typeof detail === 'string' ? detail : '生命周期清理失败');
          throw err;
        } finally {
          setCleanupExecuting(false);
        }
      },
    });
  };

  const handleOpsEventTypeFilterChange = (value?: string) => {
    setOpsEventTypeFilter(value);
    fetchOpsEvents(1, opsEventsPageSize, value, opsEventStatusFilter);
  };

  const handleOpsEventStatusFilterChange = (value?: string) => {
    setOpsEventStatusFilter(value);
    fetchOpsEvents(1, opsEventsPageSize, opsEventTypeFilter, value);
  };

  const handleOpsEventTableChange = (pagination: any) => {
    const nextPage = pagination.current || 1;
    const nextPageSize = pagination.pageSize || opsEventsPageSize;
    fetchOpsEvents(nextPage, nextPageSize, opsEventTypeFilter, opsEventStatusFilter);
  };

  const handleResetOpsEventFilters = () => {
    setOpsEventTypeFilter(undefined);
    setOpsEventStatusFilter(undefined);
    fetchOpsEvents(1, opsEventsPageSize, undefined, undefined);
  };

  const handleToolAuditStatusFilterChange = (value?: string) => {
    setToolAuditStatusFilter(value);
    fetchToolAudit(1, toolAuditPageSize, value, toolAuditToolFilter, toolAuditWorkerFilter, toolAuditTaskFilter);
  };

  const handleToolAuditToolFilterChange = (value?: string) => {
    setToolAuditToolFilter(value);
    fetchToolAudit(1, toolAuditPageSize, toolAuditStatusFilter, value, toolAuditWorkerFilter, toolAuditTaskFilter);
  };

  const handleToolAuditSearch = () => {
    fetchToolAudit(1, toolAuditPageSize, toolAuditStatusFilter, toolAuditToolFilter, toolAuditWorkerFilter, toolAuditTaskFilter);
  };

  const handleToolAuditTableChange = (pagination: any) => {
    const nextPage = pagination.current || 1;
    const nextPageSize = pagination.pageSize || toolAuditPageSize;
    fetchToolAudit(nextPage, nextPageSize, toolAuditStatusFilter, toolAuditToolFilter, toolAuditWorkerFilter, toolAuditTaskFilter);
  };

  const handleResetToolAuditFilters = () => {
    setToolAuditStatusFilter(undefined);
    setToolAuditToolFilter(undefined);
    setToolAuditWorkerFilter('');
    setToolAuditTaskFilter('');
    fetchToolAudit(1, toolAuditPageSize, undefined, undefined, '', '');
  };

  const handleRepairIndexes = () => {
    Modal.confirm({
      title: '修复检索索引',
      icon: <ExclamationCircleOutlined />,
      okText: '开始修复',
      cancelText: '取消',
      content: (
        <Space direction="vertical" size={8}>
          <Text>系统会重建 BM25，并清理指向缺失 Chroma 文档的 manifest 记录。</Text>
          <Text type="secondary">适合在真实恢复后、或系统状态提示索引不一致时执行。</Text>
        </Space>
      ),
      onOk: async () => {
        setRepairingIndexes(true);
        try {
          const res = await api.repairIndexes();
          if (res.data?.ok) {
            message.success(res.data?.repair_action_count ? '索引修复已完成' : '索引检查完成，无需修复');
          } else {
            message.error(res.data?.message || '索引修复失败');
          }
          await fetchReport();
        } catch (err: any) {
          const detail = err.response?.data?.detail;
          message.error(typeof detail === 'string' ? detail : '索引修复失败');
          throw err;
        } finally {
          setRepairingIndexes(false);
        }
      },
    });
  };

  const handleExportDiagnostics = async () => {
    setExportingDiagnostics(true);
    try {
      const res = await api.exportDiagnostics();
      const disposition = res.headers?.['content-disposition'] || '';
      const filenameMatch = disposition.match(/filename="?([^";]+)"?/);
      const filename = filenameMatch?.[1] || `memox-diagnostics-${Date.now()}.zip`;
      const url = URL.createObjectURL(res.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      message.success('诊断包已生成');
      await fetchReport();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '导出诊断包失败');
    } finally {
      setExportingDiagnostics(false);
    }
  };

  const handleVerifyBackup = async (archiveName: string) => {
    setVerifyingBackup(archiveName);
    try {
      const res = await api.verifyBackup(archiveName);
      if (res.data?.ok) {
        message.success('备份归档校验通过');
      } else {
        message.error(res.data?.message || '备份归档校验失败');
      }
      await fetchReport();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '备份归档校验失败');
    } finally {
      setVerifyingBackup('');
    }
  };

  const handleRunRestorePreflight = async (archiveName: string) => {
    setPreflightingBackup(archiveName);
    try {
      const res = await api.runRestorePreflight(archiveName);
      if (res.data?.ok && res.data?.status === 'ok') {
        message.success('恢复预检通过');
      } else if (res.data?.ok) {
        message.warning(res.data?.message || '恢复预检发现需要确认的覆盖项');
      } else {
        message.error(res.data?.message || '恢复预检失败');
      }
      await fetchReport();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '恢复预检失败');
    } finally {
      setPreflightingBackup('');
    }
  };

  const handleRunRestoreDrill = async (archiveName: string) => {
    setDrillingBackup(archiveName);
    try {
      const res = await api.runRestoreDrill(archiveName);
      if (res.data?.ok && res.data?.status === 'ok') {
        message.success('恢复演练通过');
      } else if (res.data?.ok) {
        message.warning(res.data?.message || '恢复演练完成但存在警告');
      } else {
        message.error(res.data?.message || '恢复演练失败');
      }
      await fetchReport();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '恢复演练失败');
    } finally {
      setDrillingBackup('');
    }
  };

  const handleRunRestoreBackup = (archiveName: string) => {
    let confirmation = '';
    Modal.confirm({
      title: '执行真实恢复',
      icon: <ExclamationCircleOutlined />,
      okText: '确认恢复',
      cancelText: '取消',
      okButtonProps: { danger: true },
      width: 560,
      content: (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Alert
            type="warning"
            showIcon
            message="恢复会覆盖当前部署中的同名配置、数据和工作区文件。"
            description="请确认服务已进入维护窗口。系统会在恢复前自动创建并校验一份当前状态的安全备份。"
          />
          <Text style={{ wordBreak: 'break-all' }}>
            输入完整归档名以确认：<Text code>{archiveName}</Text>
          </Text>
          <Input
            placeholder={archiveName}
            onChange={(event) => {
              confirmation = event.target.value.trim();
            }}
          />
        </Space>
      ),
      onOk: async () => {
        if (confirmation !== archiveName) {
          message.error('请输入完整归档名以确认恢复');
          return Promise.reject(new Error('confirmation mismatch'));
        }
        setRestoringBackup(archiveName);
        try {
          const res = await api.runRestoreBackup(archiveName, {
            confirm_archive_name: confirmation,
            acknowledge_overwrite: true,
            acknowledge_maintenance_mode: true,
          });
          if (res.data?.ok) {
            message.success('真实恢复已完成');
          } else if (res.data?.action === 'rejected') {
            message.warning(res.data?.message || '真实恢复已被安全闸门拒绝');
          } else {
            message.error(res.data?.message || '真实恢复失败');
          }
          await fetchReport();
        } catch (err: any) {
          const detail = err.response?.data?.detail;
          message.error(typeof detail === 'string' ? detail : '真实恢复失败');
          throw err;
        } finally {
          setRestoringBackup('');
        }
      },
    });
  };

  const handleRetryOpsTask = async (task: any) => {
    if (!task?.task_id || retryingOpsTaskId) return;
    setRetryingOpsTaskId(task.task_id);
    try {
      await api.retryTask(task.task_id);
      message.success('任务已重新入队');
      await fetchReport();
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '重试任务失败');
    } finally {
      setRetryingOpsTaskId('');
    }
  };

  if (user?.role !== 'admin') {
    return (
      <Alert
        type="warning"
        showIcon
        message="需要管理员权限"
        description="当前账号没有查看系统状态的权限。"
      />
    );
  }

  const checks = report?.checks || [];
  const getCheck = (name: string) => checks.find(check => check.name === name);
  const indexSummary = (getCheck('index_consistency')?.details?.summary || {}) as Record<string, number>;
  const issueCounts = (getCheck('index_consistency')?.details?.issue_counts || {}) as Record<string, number>;
  const disk = (getCheck('disk')?.details || {}) as Record<string, number | string>;
  const sqlite = (getCheck('sqlite')?.details?.databases || {}) as Record<string, any>;
  const persistentPaths = (getCheck('persistent_paths')?.details || {}) as { missing_directories?: string[] };
  const backupCheck = getCheck('latest_backup');
  const backupDetails = (backupCheck?.details || {}) as Record<string, any>;
  const backupWarnings = Array.isArray(backupDetails.warnings) ? backupDetails.warnings : [];
  const ops = report?.ops || {};
  const maintenanceEvent = (ops.last_backup_maintenance || {}) as Record<string, any>;
  const maintenanceDetails = (maintenanceEvent.details || {}) as Record<string, any>;
  const maintenanceMirror = (maintenanceDetails.mirror || {}) as Record<string, any>;
  const indexRepairEvent = (ops.last_index_repair || {}) as Record<string, any>;
  const indexRepairDetails = (indexRepairEvent.details || {}) as Record<string, any>;
  const indexRepairAfter = (indexRepairDetails.after || {}) as Record<string, any>;
  const diagnosticsEvent = (ops.last_diagnostics_export || {}) as Record<string, any>;
  const diagnosticsDetails = (diagnosticsEvent.details || {}) as Record<string, any>;
  const diagnosticsMirror = (diagnosticsDetails.mirror || {}) as Record<string, any>;
  const lifecycleEvent = (ops.last_lifecycle_cleanup || {}) as Record<string, any>;
  const lifecycleDetails = (lifecycleEvent.details || {}) as LifecycleCleanupResult;
  const lifecycleSummary = (cleanupPreview?.summary || lifecycleDetails.summary || {}) as Record<string, any>;
  const lifecycleTables = (cleanupPreview?.tables || lifecycleDetails.tables || []) as any[];
  const lifecycleDiagnostics = (cleanupPreview?.diagnostics || lifecycleDetails.diagnostics || {}) as Record<string, any>;
  const retention = (ops.retention || {}) as Record<string, number>;
  const restoreDrillEvent = (ops.last_restore_drill || {}) as Record<string, any>;
  const restoreDrillDetails = (restoreDrillEvent.details || {}) as Record<string, any>;
  const restoreDrillChecks = Array.isArray(restoreDrillDetails.checks) ? restoreDrillDetails.checks : [];
  const restoreExecuteEvent = (ops.last_restore_execute || {}) as Record<string, any>;
  const restoreExecuteDetails = (restoreExecuteEvent.details || {}) as Record<string, any>;
  const missingDirectories = persistentPaths.missing_directories || [];
  const statusCounts = checks.reduce<Record<string, number>>((acc, check) => {
    acc[check.status] = (acc[check.status] || 0) + 1;
    return acc;
  }, {});
  const diskTotalBytes = typeof disk.total_bytes === 'number' ? disk.total_bytes : 0;
  const diskUsedBytes = typeof disk.used_bytes === 'number' ? disk.used_bytes : 0;
  const diskFreeBytes = typeof disk.free_bytes === 'number' ? disk.free_bytes : undefined;
  const diskMinFreeBytes = typeof disk.min_free_bytes === 'number' ? disk.min_free_bytes : undefined;
  const diskUsedPercent = diskTotalBytes ? Math.round((diskUsedBytes / diskTotalBytes) * 100) : 0;
  const backupAgeSeconds = typeof backupDetails.age_seconds === 'number' ? backupDetails.age_seconds : undefined;
  const backupAgeText = backupAgeSeconds === undefined
    ? '-'
    : backupAgeSeconds < 3600
      ? `${Math.round(backupAgeSeconds / 60)} 分钟`
      : `${(backupAgeSeconds / 3600).toFixed(1)} 小时`;
  const taskJobs = (ops.task_jobs || {}) as Record<string, any>;
  const taskJobNumber = (name: string) => {
    const value = taskJobs[name];
    return typeof value === 'number' ? value : Number(value || 0);
  };
  const formatTaskJobAge = (seconds?: number | null) => {
    if (typeof seconds !== 'number') return '-';
    if (seconds < 60) return `${Math.round(seconds)} 秒`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
    if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`;
    return `${(seconds / 86400).toFixed(1)} 天`;
  };
  const taskJobTotal = taskJobNumber('total');
  const taskJobActive = taskJobNumber('active');
  const taskJobTerminal = taskJobNumber('terminal');
  const taskJobScheduledRetries = taskJobNumber('scheduled_retries');
  const taskJobManualRetryable = taskJobNumber('manual_retryable');
  const taskJobNeedsIntervention = taskJobNumber('needs_intervention');
  const taskJobExpiredLeases = taskJobNumber('expired_leases');
  const taskJobRecovered = taskJobNumber('recovered_jobs');
  const taskJobRecoveryTotal = taskJobNumber('recovery_count_total');
  const taskJobActivePercent = taskJobTotal ? Math.round((taskJobActive / taskJobTotal) * 100) : 0;
  const taskJobTerminalPercent = taskJobTotal ? Math.round((taskJobTerminal / taskJobTotal) * 100) : 0;
  const taskJobOpsStatus: ReadinessStatus = taskJobExpiredLeases > 0 || taskJobNeedsIntervention > 0
    ? 'error'
    : taskJobScheduledRetries > 0 || taskJobManualRetryable > 0
      ? 'warning'
      : 'ok';
  const taskJobOldestActiveAge = typeof taskJobs.oldest_active_age_seconds === 'number'
    ? taskJobs.oldest_active_age_seconds
    : undefined;
  const taskJobMetrics = [
    { label: '活跃任务', value: taskJobActive, color: '#1677ff', hint: `队列 ${taskJobNumber('queued')} / 执行 ${taskJobNumber('running')}` },
    { label: '等待自动重试', value: taskJobScheduledRetries, color: '#faad14', hint: '到点后后台自动入队' },
    { label: '可手动重试', value: taskJobManualRetryable, color: '#13c2c2', hint: '可由任务页人工触发' },
    { label: '需介入', value: taskJobNeedsIntervention, color: '#ff4d4f', hint: '不可重试或自动重试耗尽' },
  ];
  const isOpsTaskWaitingAutoRetry = (task: any) => Boolean(task?.job?.next_retry_at);
  const isOpsTaskAutoRetryExhausted = (task: any) => task?.last_failure?.event_type === 'auto_retry_exhausted';
  const canRetryOpsTask = (task: any) => (
    task?.status === 'timeout' || (task?.status === 'failed' && task?.last_failure?.retryable === true)
  );
  const needsOpsTaskIntervention = (task: any) => (
    task?.status === 'failed'
    && (isOpsTaskAutoRetryExhausted(task) || task?.last_failure?.retryable !== true)
  );
  const isOpsTaskManuallyRetryable = (task: any) => canRetryOpsTask(task) && !isOpsTaskWaitingAutoRetry(task);
  const recentActionableTasks = opsTasks
    .filter(task => needsOpsTaskIntervention(task) || isOpsTaskManuallyRetryable(task) || isOpsTaskWaitingAutoRetry(task))
    .slice(0, 8);
  const taskSignalTags = (task: any) => {
    const tags = [];
    if (isOpsTaskWaitingAutoRetry(task)) {
      tags.push(
        <Tooltip key="next-retry" title={task.job.next_retry_at}>
          <Tag color="warning">等待自动重试 {dayjs(task.job.next_retry_at).format('HH:mm:ss')}</Tag>
        </Tooltip>,
      );
    }
    if (isOpsTaskAutoRetryExhausted(task)) {
      tags.push(<Tag key="exhausted" color="red">自动重试耗尽</Tag>);
    } else if (needsOpsTaskIntervention(task)) {
      tags.push(<Tag key="attention" color="red">需介入</Tag>);
    }
    if (isOpsTaskManuallyRetryable(task)) {
      tags.push(<Tag key="manual-retry" color="blue">可手动重试</Tag>);
    }
    if (task.job?.recovery_count > 0) {
      tags.push(<Tag key="recovery" color="blue">恢复 {task.job.recovery_count} 次</Tag>);
    }
    return tags.length > 0 ? tags : <Text type="secondary">-</Text>;
  };
  const taskStatusTag = (status: string) => {
    const config: Record<string, { color: string; text: string }> = {
      queued: { color: 'default', text: '排队' },
      pending: { color: 'default', text: '等待' },
      running: { color: 'processing', text: '运行' },
      completed: { color: 'success', text: '完成' },
      failed: { color: 'error', text: '失败' },
      cancelled: { color: 'warning', text: '取消' },
      timeout: { color: 'warning', text: '超时' },
    };
    const item = config[status] || { color: 'default', text: status || '未知' };
    return <Tag color={item.color}>{item.text}</Tag>;
  };

  const columns = [
    {
      title: '检查项',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => <Text strong>{name}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: ReadinessStatus) => <Tag color={statusTagColor(status)}>{statusLabel(status)}</Tag>,
    },
    {
      title: '说明',
      dataIndex: 'message',
      key: 'message',
      render: (text: string) => <Text>{text}</Text>,
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 100,
      render: (ms: number) => <Text type="secondary">{formatDuration(ms)}</Text>,
    },
  ];
  const backupColumns = [
    {
      title: '归档',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, item: BackupArchiveSummary) => (
        <Space direction="vertical" size={0}>
          <Text strong style={{ wordBreak: 'break-all' }}>{name}</Text>
          <Text type="secondary" style={{ wordBreak: 'break-all' }}>{item.archive}</Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: ReadinessStatus) => <Tag color={statusTagColor(status)}>{statusLabel(status)}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (createdAt: string, item: BackupArchiveSummary) => <Text type="secondary">{createdAt || item.modified_at || '-'}</Text>,
    },
    {
      title: '大小',
      dataIndex: 'size_bytes',
      key: 'size_bytes',
      width: 100,
      render: (bytes: number) => <Text>{formatBytes(bytes)}</Text>,
    },
    {
      title: '条目',
      dataIndex: 'entry_count',
      key: 'entry_count',
      width: 80,
      render: (count: number) => <Text>{count ?? '-'}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 330,
      render: (_: any, item: BackupArchiveSummary) => (
        <Space>
          <Button
            size="small"
            icon={<CheckCircleOutlined />}
            onClick={() => handleVerifyBackup(item.name)}
            loading={verifyingBackup === item.name}
          >
            校验
          </Button>
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => handleRunRestorePreflight(item.name)}
            loading={preflightingBackup === item.name}
          >
            预检
          </Button>
          <Button
            size="small"
            icon={<SafetyCertificateOutlined />}
            onClick={() => handleRunRestoreDrill(item.name)}
            loading={drillingBackup === item.name}
          >
            演练
          </Button>
          <Button
            size="small"
            danger
            icon={<RollbackOutlined />}
            onClick={() => handleRunRestoreBackup(item.name)}
            loading={restoringBackup === item.name}
          >
            恢复
          </Button>
        </Space>
      ),
    },
  ];
  const eventColumns = [
    {
      title: '类型',
      dataIndex: 'event_type',
      key: 'event_type',
      width: 110,
      render: (eventType: string) => <Tag>{opsEventLabel(eventType)}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: ReadinessStatus) => <Tag color={statusTagColor(status)}>{statusLabel(status)}</Tag>,
    },
    {
      title: '动作',
      dataIndex: 'action',
      key: 'action',
      width: 120,
      render: (action: string, item: OpsEvent) => <Text>{action || item.details?.action || '-'}</Text>,
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (createdAt: string) => <Text type="secondary">{createdAt || '-'}</Text>,
    },
    {
      title: '操作者',
      key: 'actor',
      width: 140,
      render: (_: any, item: OpsEvent) => <Text>{opsEventActorLabel(item)}</Text>,
    },
    {
      title: '说明',
      dataIndex: 'message',
      key: 'message',
      render: (text: string, item: OpsEvent) => {
        const archive = item.details?.name || item.details?.archive;
        return (
          <Space direction="vertical" size={0}>
            <Text>{text || '-'}</Text>
            {archive && <Text type="secondary" style={{ wordBreak: 'break-all' }}>{archive}</Text>}
          </Space>
        );
      },
    },
    {
      title: '详情',
      key: 'details',
      width: 88,
      render: (_: any, item: OpsEvent) => (
        <Button size="small" icon={<EyeOutlined />} onClick={() => setSelectedOpsEvent(item)}>
          查看
        </Button>
      ),
    },
  ];
  const toolAuditColumns = [
    {
      title: '工具',
      dataIndex: 'resource_id',
      key: 'resource_id',
      width: 160,
      render: (toolName: string) => <Tag icon={<ToolOutlined />}>{toolName}</Tag>,
    },
    {
      title: '状态',
      key: 'status',
      width: 100,
      render: (_: any, item: ToolAuditEvent) => (
        <Tag color={toolAuditStatusColor(item.details?.status)}>
          {toolAuditStatusLabel(item.details?.status)}
        </Tag>
      ),
    },
    {
      title: 'Worker / Task',
      key: 'context',
      width: 220,
      render: (_: any, item: ToolAuditEvent) => (
        <Space direction="vertical" size={0}>
          <Text>{item.details?.worker_name || item.details?.worker_id || item.username || '-'}</Text>
          <Text type="secondary" style={{ fontSize: 12, wordBreak: 'break-all' }}>
            {item.details?.task_id || '-'}
          </Text>
        </Space>
      ),
    },
    {
      title: '耗时',
      key: 'duration',
      width: 90,
      render: (_: any, item: ToolAuditEvent) => <Text>{formatDuration(item.details?.duration_ms)}</Text>,
    },
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      width: 170,
      render: (timestamp: string) => <Text type="secondary">{timestamp || '-'}</Text>,
    },
    {
      title: '摘要',
      key: 'summary',
      render: (_: any, item: ToolAuditEvent) => {
        const args = item.details?.arguments || {};
        const result = item.details?.result;
        return (
          <Space direction="vertical" size={0}>
            <Text style={{ wordBreak: 'break-word' }}>
              {Object.keys(args).slice(0, 3).join(', ') || '无参数摘要'}
            </Text>
            <Text type={item.details?.error ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
              {item.details?.error || result?.preview || result?.type || '-'}
            </Text>
          </Space>
        );
      },
    },
    {
      title: '详情',
      key: 'details',
      width: 88,
      render: (_: any, item: ToolAuditEvent) => (
        <Button
          size="small"
          icon={<EyeOutlined />}
          data-testid={`tool-audit-detail-${item.id}`}
          onClick={() => setSelectedToolAudit(item)}
        >
          查看
        </Button>
      ),
    },
  ];
  const actionableTaskColumns = [
    {
      title: '任务',
      dataIndex: 'description',
      key: 'description',
      render: (description: string, task: any) => (
        <Space direction="vertical" size={0}>
          <Text strong>{description?.length > 54 ? `${description.substring(0, 54)}...` : description}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{task.task_id}</Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: string) => taskStatusTag(status),
    },
    {
      title: '处理信号',
      key: 'signals',
      width: 230,
      render: (_: any, task: any) => <Space wrap>{taskSignalTags(task)}</Space>,
    },
    {
      title: '最近失败',
      key: 'last_failure',
      render: (_: any, task: any) => (
        <Space direction="vertical" size={0}>
          <Text>{task.last_failure?.message ? (
            task.last_failure.message.length > 64
              ? `${task.last_failure.message.substring(0, 64)}...`
              : task.last_failure.message
          ) : '-'}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{task.last_failure?.created_at || task.job?.updated_at || task.created_at || '-'}</Text>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_: any, task: any) => (
        <Space>
          <Link to="/tasks" state={{ taskId: task.task_id }}>
            <Button size="small" icon={<EyeOutlined />}>详情</Button>
          </Link>
          <Button size="small" icon={<ClockCircleOutlined />} onClick={() => setSelectedOpsTask(task)}>
            事件
          </Button>
          {canRetryOpsTask(task) && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={retryingOpsTaskId === task.task_id}
              disabled={Boolean(retryingOpsTaskId) && retryingOpsTaskId !== task.task_id}
              onClick={() => handleRetryOpsTask(task)}
            >
              重试
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{
        display: 'flex',
        alignItems: isMobile ? 'flex-start' : 'center',
        justifyContent: 'space-between',
        gap: 12,
        flexDirection: isMobile ? 'column' : 'row',
        marginBottom: 16,
      }}>
        <div>
          <Title level={4} style={{ marginBottom: 4 }}>系统状态</Title>
          {lastUpdated && <Text type="secondary">更新于 {lastUpdated}</Text>}
        </div>
        <Space wrap>
          <Button icon={<SaveOutlined />} onClick={handleRunBackupMaintenance} loading={maintenanceRunning}>
            立即备份
          </Button>
          <Button icon={<DeleteOutlined />} onClick={handleLifecycleDryRun} loading={cleanupRunning}>
            清理预检
          </Button>
          <Button icon={<ToolOutlined />} onClick={handleRepairIndexes} loading={repairingIndexes}>
            修复索引
          </Button>
          <Button icon={<DownloadOutlined />} onClick={handleExportDiagnostics} loading={exportingDiagnostics}>
            导出诊断
          </Button>
          <Button icon={<ReloadOutlined />} onClick={fetchReport} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      {report && report.status !== 'ok' && (
        <Alert
          type={report.status === 'error' ? 'error' : 'warning'}
          showIcon
          message={`当前系统状态：${statusLabel(report.status)}`}
          description={checks.filter(check => check.status !== 'ok').map(check => `${check.name}: ${check.message}`).join(' / ')}
          style={{ marginBottom: 16 }}
        />
      )}

      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16, marginBottom: 16 }}>
        <Card size="small" loading={loading}>
          <Text type="secondary">总体状态</Text>
          <div style={{ marginTop: 8 }}>
            <Badge status={statusBadge(report?.status)} text={<Text strong>{statusLabel(report?.status)}</Text>} />
          </div>
        </Card>
        <Card size="small" loading={loading}>
          <Text type="secondary">检查数量</Text>
          <div style={{ marginTop: 8, fontSize: 24, fontWeight: 600 }}>{checks.length}</div>
          <Space size={4} wrap>
            <Tag color="green">{statusCounts.ok || 0} 正常</Tag>
            <Tag color="orange">{statusCounts.warning || 0} 警告</Tag>
            <Tag color="red">{statusCounts.error || 0} 错误</Tag>
          </Space>
        </Card>
        <Card size="small" loading={loading}>
          <Text type="secondary">索引块数</Text>
          <div style={{ marginTop: 8, fontSize: 24, fontWeight: 600 }}>{indexSummary.chroma_chunks ?? '-'}</div>
          <Text type="secondary">Chroma / BM25 {indexSummary.bm25_chunks ?? '-'}</Text>
        </Card>
        <Card size="small" loading={loading}>
          <Text type="secondary">可用磁盘</Text>
          <div style={{ marginTop: 8, fontSize: 24, fontWeight: 600 }}>{formatBytes(diskFreeBytes)}</div>
          <Text type="secondary">阈值 {formatBytes(diskMinFreeBytes)}</Text>
        </Card>
        <Card size="small" loading={loading}>
          <Text type="secondary">最近备份</Text>
          <div style={{ marginTop: 8 }}>
            <Tag color={statusTagColor(backupCheck?.status)}>{statusLabel(backupCheck?.status)}</Tag>
          </div>
          <Text type="secondary">距今 {backupAgeText}</Text>
        </Card>
        <Card size="small" loading={loading}>
          <Text type="secondary">后台任务</Text>
          <div style={{ marginTop: 8 }}>
            <Badge status={statusBadge(taskJobOpsStatus)} text={<Text strong>{statusLabel(taskJobOpsStatus)}</Text>} />
          </div>
          <Text type="secondary">活跃 {taskJobActive} / 总计 {taskJobTotal}</Text>
        </Card>
      </div>

      <Card
        title="后台任务运维"
        style={{ marginBottom: 16 }}
        loading={loading}
        extra={(
          <Link to="/tasks">
            <Button size="small" icon={<EyeOutlined />}>查看任务</Button>
          </Link>
        )}
      >
        <Space direction="vertical" style={{ width: '100%' }} size={14}>
          <Alert
            type={taskJobOpsStatus === 'error' ? 'error' : taskJobOpsStatus === 'warning' ? 'warning' : 'success'}
            showIcon
            message={
              taskJobOpsStatus === 'error'
                ? '任务后台需要人工处理'
                : taskJobOpsStatus === 'warning'
                  ? '任务后台有待重试项'
                  : '任务后台运行正常'
            }
            description={
              taskJobOpsStatus === 'error'
                ? '优先处理需介入任务和过期租约，再观察自动重试是否恢复。'
                : taskJobOpsStatus === 'warning'
                  ? '系统会继续自动重试；也可以进入任务页查看并手动重试。'
                  : '当前没有积压、过期租约或需要人工介入的后台任务。'
            }
          />
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr 1fr' : 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
            {taskJobMetrics.map(metric => (
              <div
                key={metric.label}
                style={{
                  border: '1px solid #f0f0f0',
                  borderRadius: 8,
                  padding: 12,
                  minHeight: 92,
                  background: '#fff',
                }}
              >
                <Text type="secondary">{metric.label}</Text>
                <div style={{ marginTop: 6, color: metric.color, fontSize: 24, fontWeight: 600 }}>
                  {metric.value}
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>{metric.hint}</Text>
              </div>
            ))}
          </div>
          <div>
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Text strong>任务吞吐</Text>
              <Text type="secondary">终态 {taskJobTerminalPercent}% · 活跃 {taskJobActivePercent}%</Text>
            </Space>
            <Progress
              percent={taskJobTerminalPercent}
              showInfo={false}
              strokeColor={taskJobOpsStatus === 'error' ? '#ff4d4f' : taskJobOpsStatus === 'warning' ? '#faad14' : '#52c41a'}
            />
          </div>
          <Space wrap>
            <Tag color="blue">排队 {taskJobNumber('queued')}</Tag>
            <Tag color="processing">运行 {taskJobNumber('running')}</Tag>
            <Tag color="success">完成 {taskJobNumber('completed')}</Tag>
            <Tag color="error">失败 {taskJobNumber('failed')}</Tag>
            <Tag color="warning">超时 {taskJobNumber('timeout')}</Tag>
            <Tag color={taskJobExpiredLeases > 0 ? 'red' : 'default'}>过期租约 {taskJobExpiredLeases}</Tag>
            <Tag color={taskJobRecovered > 0 ? 'blue' : 'default'}>
              恢复任务 {taskJobRecovered} / {taskJobRecoveryTotal} 次
            </Tag>
          </Space>
          <Divider style={{ margin: '2px 0' }} />
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text strong>最近待处理任务</Text>
            <Text type="secondary">显示需介入、可手动重试和等待自动重试的最近任务</Text>
          </Space>
          {recentActionableTasks.length > 0 ? (
            <Table
              size="small"
              rowKey="task_id"
              dataSource={recentActionableTasks}
              columns={actionableTaskColumns}
              pagination={false}
              scroll={{ x: 900 }}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无待处理任务" />
          )}
          <Space wrap>
            <Text type="secondary">最早活跃任务：{taskJobs.oldest_active_created_at || '-'}</Text>
            <Text type="secondary">活跃时长：{formatTaskJobAge(taskJobOldestActiveAge)}</Text>
            <Text type="secondary">最近更新：{taskJobs.last_job_updated_at || '-'}</Text>
          </Space>
        </Space>
      </Card>

      <Card title="运行检查" style={{ marginBottom: 16 }} loading={loading}>
        <Table
          size="small"
          rowKey="name"
          dataSource={checks}
          columns={columns}
          pagination={false}
          scroll={{ x: 720 }}
        />
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'minmax(0, 1fr) minmax(0, 1fr)', gap: 16 }}>
        <Card title="索引一致性" loading={loading}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Space wrap>
              <Tag>Chroma 文档 {indexSummary.chroma_documents ?? '-'}</Tag>
              <Tag>Chroma 块 {indexSummary.chroma_chunks ?? '-'}</Tag>
              <Tag>BM25 块 {indexSummary.bm25_chunks ?? '-'}</Tag>
              <Tag>Manifest {indexSummary.manifest_entries ?? '-'}</Tag>
            </Space>
            {Object.keys(issueCounts).length > 0 ? (
              <List
                size="small"
                dataSource={Object.entries(issueCounts)}
                renderItem={([name, count]) => (
                  <List.Item>
                    <Text>{name}</Text>
                    <Tag color="orange">{count}</Tag>
                  </List.Item>
                )}
              />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无索引问题" />
            )}
          </Space>
        </Card>

        <Card title="存储状态" loading={loading}>
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            <div>
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Text strong>{disk.path || '-'}</Text>
                <Text type="secondary">{diskUsedPercent}% 已用</Text>
              </Space>
              <Progress
                percent={diskUsedPercent}
                showInfo={false}
                strokeColor={(diskFreeBytes ?? 0) < (diskMinFreeBytes ?? 0) ? '#faad14' : '#52c41a'}
              />
              <Text type="secondary">已用 {formatBytes(diskUsedBytes)} / 总计 {formatBytes(diskTotalBytes)}</Text>
            </div>
            <Divider style={{ margin: '4px 0' }} />
            <Space direction="vertical" size={4}>
              {Object.entries(sqlite).map(([name, item]: [string, any]) => (
                <Space key={name} wrap>
                  <Tag color={statusTagColor(item.status === 'missing' ? 'warning' : item.status)}>{name}</Tag>
                  <Text type="secondary">{item.path}</Text>
                </Space>
              ))}
              {missingDirectories.length > 0 && (
                <Text type="warning">缺失目录：{missingDirectories.join(', ')}</Text>
              )}
            </Space>
          </Space>
        </Card>

        <Card title="备份状态" loading={loading}>
          <Space direction="vertical" style={{ width: '100%' }} size={8}>
            <Space wrap>
              <Tag color={statusTagColor(backupCheck?.status)}>{statusLabel(backupCheck?.status)}</Tag>
              <Text>{backupCheck?.message || '-'}</Text>
            </Space>
            <Text><Text strong>归档数:</Text> {backupDetails.archive_count ?? 0}</Text>
            <Text><Text strong>最新时间:</Text> {backupDetails.created_at || '-'}</Text>
            <Text><Text strong>距今:</Text> {backupAgeText}</Text>
            <Text><Text strong>条目数:</Text> {backupDetails.entries ?? '-'}</Text>
            <Text style={{ wordBreak: 'break-all' }}><Text strong>目录:</Text> {backupDetails.backup_dir || '-'}</Text>
            {backupDetails.archive && (
              <Text style={{ wordBreak: 'break-all' }}><Text strong>最新归档:</Text> {backupDetails.archive}</Text>
            )}
            {backupWarnings.length > 0 && (
              <Space wrap>
                {backupWarnings.map((warning: string) => (
                  <Tag key={warning} color="orange">{warning}</Tag>
                ))}
              </Space>
            )}
            <Divider style={{ margin: '4px 0' }} />
            <Text><Text strong>自动备份:</Text> {ops.auto_backup_enabled ? '已启用' : '未启用'}</Text>
            <Text><Text strong>后台维护:</Text> {ops.maintenance_runner_active ? '运行中' : '未运行'}</Text>
            <Text><Text strong>维护间隔:</Text> {ops.auto_backup_interval_hours ?? '-'} 小时</Text>
            <Text><Text strong>归档镜像:</Text> {ops.archive_mirror_enabled ? '已启用' : '未启用'}</Text>
            {ops.archive_mirror_enabled && (
              <Text style={{ wordBreak: 'break-all' }}><Text strong>镜像目录:</Text> {ops.archive_mirror_dir || '-'}</Text>
            )}
            {maintenanceEvent.id ? (
              <>
                <Space wrap>
                  <Tag color={statusTagColor(maintenanceEvent.status)}>{statusLabel(maintenanceEvent.status)}</Tag>
                  <Tag>{maintenanceEvent.action || '-'}</Tag>
                  <Text type="secondary">{maintenanceEvent.created_at}</Text>
                </Space>
                <Text>{maintenanceEvent.message || '-'}</Text>
                {maintenanceDetails.deleted?.length > 0 && (
                  <Text type="secondary">本次裁剪 {maintenanceDetails.deleted.length} 个旧归档</Text>
                )}
                {maintenanceMirror.enabled && (
                  <Text
                    type={maintenanceMirror.ok ? 'secondary' : 'danger'}
                    style={{ wordBreak: 'break-all' }}
                  >
                    镜像：{maintenanceMirror.destination || maintenanceMirror.message || '-'}
                  </Text>
                )}
              </>
            ) : (
              <Text type="secondary">暂无备份维护记录</Text>
            )}
            <Divider style={{ margin: '4px 0' }} />
            <Text strong>最近索引修复</Text>
            {indexRepairEvent.id ? (
              <>
                <Space wrap>
                  <Tag color={statusTagColor(indexRepairEvent.status)}>{statusLabel(indexRepairEvent.status)}</Tag>
                  <Text type="secondary">{indexRepairEvent.created_at}</Text>
                </Space>
                <Text>{indexRepairEvent.message || '-'}</Text>
                <Space wrap>
                  <Tag>动作 {indexRepairDetails.repair_action_count ?? 0}</Tag>
                  <Tag>Chroma {indexRepairAfter.summary?.chroma_chunks ?? '-'}</Tag>
                  <Tag>BM25 {indexRepairAfter.summary?.bm25_chunks ?? '-'}</Tag>
                </Space>
              </>
            ) : (
              <Text type="secondary">暂无索引修复记录</Text>
            )}
            <Divider style={{ margin: '4px 0' }} />
            <Text strong>最近诊断导出</Text>
            {diagnosticsEvent.id ? (
              <>
                <Space wrap>
                  <Tag color={statusTagColor(diagnosticsEvent.status)}>{statusLabel(diagnosticsEvent.status)}</Tag>
                  <Text type="secondary">{diagnosticsEvent.created_at}</Text>
                </Space>
                <Text style={{ wordBreak: 'break-all' }}>{diagnosticsDetails.filename || diagnosticsEvent.message || '-'}</Text>
                <Text type="secondary">大小 {formatBytes(diagnosticsDetails.size_bytes)}</Text>
                {diagnosticsMirror.enabled && (
                  <Text
                    type={diagnosticsMirror.ok ? 'secondary' : 'danger'}
                    style={{ wordBreak: 'break-all' }}
                  >
                    镜像：{diagnosticsMirror.destination || diagnosticsMirror.message || '-'}
                  </Text>
                )}
              </>
            ) : (
              <Text type="secondary">暂无诊断导出记录</Text>
            )}
            <Divider style={{ margin: '4px 0' }} />
            <Text strong>最近恢复演练</Text>
            {restoreDrillEvent.id ? (
              <>
                <Space wrap>
                  <Tag color={statusTagColor(restoreDrillEvent.status)}>{statusLabel(restoreDrillEvent.status)}</Tag>
                  <Text type="secondary">{restoreDrillEvent.created_at}</Text>
                </Space>
                <Text>{restoreDrillEvent.message || '-'}</Text>
                {restoreDrillChecks.length > 0 && (
                  <Space wrap>
                    {restoreDrillChecks.map((check: any) => (
                      <Tag key={check.name} color={statusTagColor(check.status)}>
                        {check.name}: {statusLabel(check.status)}
                      </Tag>
                    ))}
                  </Space>
                )}
              </>
            ) : (
              <Text type="secondary">暂无恢复演练记录</Text>
            )}
            <Divider style={{ margin: '4px 0' }} />
            <Text strong>最近真实恢复</Text>
            {restoreExecuteEvent.id ? (
              <>
                <Space wrap>
                  <Tag color={statusTagColor(restoreExecuteEvent.status)}>{statusLabel(restoreExecuteEvent.status)}</Tag>
                  <Tag>{restoreExecuteDetails.action || restoreExecuteEvent.action || '-'}</Tag>
                  <Text type="secondary">{restoreExecuteEvent.created_at}</Text>
                </Space>
                <Text>{restoreExecuteEvent.message || '-'}</Text>
                {restoreExecuteDetails.safety_backup?.archive && (
                  <Text type="secondary" style={{ wordBreak: 'break-all' }}>
                    安全备份：{restoreExecuteDetails.safety_backup.archive}
                  </Text>
                )}
              </>
            ) : (
              <Text type="secondary">暂无真实恢复记录</Text>
            )}
          </Space>
        </Card>

        <Card
          title="生命周期治理"
          loading={loading}
          extra={(
            <Space>
              <Button size="small" icon={<EyeOutlined />} onClick={handleLifecycleDryRun} loading={cleanupRunning}>
                预检
              </Button>
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={handleExecuteLifecycleCleanup}
                loading={cleanupExecuting}
                disabled={!cleanupPreview}
              >
                执行
              </Button>
            </Space>
          )}
        >
          <Space direction="vertical" style={{ width: '100%' }} size={10}>
            <Space wrap>
              <Tag>运维事件 {retention.ops_event_retention_days ?? '-'} 天</Tag>
              <Tag>审计日志 {retention.audit_log_retention_days ?? '-'} 天</Tag>
              <Tag>后台任务 {retention.task_job_retention_days ?? '-'} 天</Tag>
              <Tag>诊断包 {retention.diagnostic_retention_days ?? '-'} 天</Tag>
              <Tag>最多 {retention.max_diagnostic_bundles ?? '-'} 个</Tag>
            </Space>
            <Alert
              type="info"
              showIcon
              message="不会删除聊天、记忆、上传文档或工作区文件"
            />
            <Space wrap>
              <Tag color="blue">待清理记录 {lifecycleSummary.eligible_records ?? 0}</Tag>
              <Tag color="blue">待清理诊断包 {lifecycleSummary.eligible_files ?? 0}</Tag>
              <Tag color="blue">可释放 {formatBytes(lifecycleSummary.eligible_bytes)}</Tag>
              {cleanupPreview && (
                <Tag color={cleanupPreview.dry_run ? 'default' : 'green'}>
                  {cleanupPreview.dry_run ? '预检结果' : '执行结果'}
                </Tag>
              )}
            </Space>
            {lifecycleTables.length > 0 && (
              <List
                size="small"
                dataSource={lifecycleTables}
                renderItem={(item: any) => (
                  <List.Item>
                    <Space direction="vertical" size={0}>
                      <Text strong>{item.name}</Text>
                      <Text type="secondary">
                        过期 {item.eligible_count ?? 0} 条，已删除 {item.deleted_count ?? 0} 条
                      </Text>
                    </Space>
                  </List.Item>
                )}
              />
            )}
            {Array.isArray(lifecycleDiagnostics.eligible) && lifecycleDiagnostics.eligible.length > 0 && (
              <>
                <Divider style={{ margin: '4px 0' }} />
                <Text strong>待清理诊断包</Text>
                <List
                  size="small"
                  dataSource={lifecycleDiagnostics.eligible.slice(0, 5)}
                  renderItem={(item: any) => (
                    <List.Item>
                      <Space direction="vertical" size={0}>
                        <Text style={{ wordBreak: 'break-all' }}>{item.name}</Text>
                        <Text type="secondary">
                          {formatBytes(item.size_bytes)} · {item.reason === 'age' ? '超过保留期' : '超过数量上限'}
                        </Text>
                      </Space>
                    </List.Item>
                  )}
                />
              </>
            )}
            {lifecycleEvent.id ? (
              <>
                <Divider style={{ margin: '4px 0' }} />
                <Space wrap>
                  <Tag color={statusTagColor(lifecycleEvent.status)}>{statusLabel(lifecycleEvent.status)}</Tag>
                  <Tag>{lifecycleEvent.action || lifecycleDetails.action || '-'}</Tag>
                  <Text type="secondary">{lifecycleEvent.created_at}</Text>
                </Space>
                <Text>{lifecycleEvent.message || lifecycleDetails.message || '-'}</Text>
              </>
            ) : (
              <Text type="secondary">暂无清理执行记录</Text>
            )}
          </Space>
        </Card>

        <Card title="备份归档" loading={backupsLoading}>
          {backups.length > 0 ? (
            <Table
              size="small"
              rowKey="name"
              dataSource={backups}
              columns={backupColumns}
              pagination={{ pageSize: 5, size: 'small' }}
              scroll={{ x: 820 }}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无备份归档" />
          )}
        </Card>

        <Card
          title="运维事件"
          loading={eventsLoading}
          extra={(
            <Button size="small" icon={<ReloadOutlined />} onClick={() => fetchOpsEvents()}>
              刷新
            </Button>
          )}
        >
          <Space
            wrap
            direction={isMobile ? 'vertical' : 'horizontal'}
            style={{ width: '100%', marginBottom: 12 }}
          >
            <Select
              allowClear
              placeholder="事件类型"
              value={opsEventTypeFilter}
              onChange={handleOpsEventTypeFilterChange}
              style={{ width: isMobile ? '100%' : 180 }}
            >
              {opsEventTypeOptions.map(eventType => (
                <Select.Option key={eventType} value={eventType}>{opsEventLabel(eventType)}</Select.Option>
              ))}
            </Select>
            <Select
              allowClear
              placeholder="状态"
              value={opsEventStatusFilter}
              onChange={handleOpsEventStatusFilterChange}
              style={{ width: isMobile ? '100%' : 130 }}
            >
              {opsEventStatusOptions.map(status => (
                <Select.Option key={status} value={status}>{statusLabel(status)}</Select.Option>
              ))}
            </Select>
            <Button onClick={handleResetOpsEventFilters}>重置</Button>
          </Space>
          {opsEvents.length > 0 ? (
            <Table
              size="small"
              rowKey="id"
              dataSource={opsEvents}
              columns={eventColumns}
              pagination={{
                current: opsEventsPage,
                pageSize: opsEventsPageSize,
                total: opsEventsTotal,
                size: 'small',
                showSizeChanger: true,
                pageSizeOptions: ['8', '20', '50'],
                showTotal: total => `共 ${total} 条`,
              }}
              onChange={handleOpsEventTableChange}
              scroll={{ x: 1080 }}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无运维事件" />
          )}
        </Card>

        <Card
          title="工具调用审计"
          data-testid="tool-audit-card"
          loading={toolAuditLoading}
          extra={(
            <Button size="small" icon={<ReloadOutlined />} onClick={() => fetchToolAudit()}>
              刷新
            </Button>
          )}
        >
          <Space
            wrap
            direction={isMobile ? 'vertical' : 'horizontal'}
            style={{ width: '100%', marginBottom: 12 }}
          >
            <Select
              allowClear
              placeholder="状态"
              value={toolAuditStatusFilter}
              onChange={handleToolAuditStatusFilterChange}
              style={{ width: isMobile ? '100%' : 140 }}
            >
              {toolAuditStatusOptions.map(status => (
                <Select.Option key={status} value={status}>{toolAuditStatusLabel(status)}</Select.Option>
              ))}
            </Select>
            <Input
              allowClear
              placeholder="工具名"
              value={toolAuditToolFilter}
              onChange={event => setToolAuditToolFilter(event.target.value || undefined)}
              onPressEnter={handleToolAuditSearch}
              style={{ width: isMobile ? '100%' : 180 }}
            />
            <Input
              allowClear
              placeholder="Worker"
              value={toolAuditWorkerFilter}
              onChange={event => setToolAuditWorkerFilter(event.target.value)}
              onPressEnter={handleToolAuditSearch}
              style={{ width: isMobile ? '100%' : 160 }}
            />
            <Input
              allowClear
              placeholder="Task ID"
              value={toolAuditTaskFilter}
              onChange={event => setToolAuditTaskFilter(event.target.value)}
              onPressEnter={handleToolAuditSearch}
              style={{ width: isMobile ? '100%' : 180 }}
            />
            <Button icon={<FileSearchOutlined />} onClick={handleToolAuditSearch}>筛选</Button>
            <Button onClick={handleResetToolAuditFilters}>重置</Button>
          </Space>
          <Space wrap style={{ marginBottom: 12 }}>
            <Tag color="green">成功 {toolAuditSummary.success || 0}</Tag>
            <Tag color="orange">策略拒绝 {toolAuditSummary.rejected || 0}</Tag>
            <Tag color="red">错误 {toolAuditSummary.error || 0}</Tag>
          </Space>
          {toolAuditEvents.length > 0 ? (
            <Table
              size="small"
              rowKey="id"
              dataSource={toolAuditEvents}
              columns={toolAuditColumns}
              pagination={{
                current: toolAuditPage,
                pageSize: toolAuditPageSize,
                total: toolAuditTotal,
                size: 'small',
                showSizeChanger: true,
                pageSizeOptions: ['8', '20', '50'],
                showTotal: total => `共 ${total} 条`,
              }}
              onChange={handleToolAuditTableChange}
              scroll={{ x: 1180 }}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工具调用审计" />
          )}
        </Card>

        <Card title="运行时" loading={loading}>
          <Space direction="vertical">
            <Text><Text strong>应用:</Text> {report?.runtime?.app || '-'}</Text>
            <Text><Text strong>版本:</Text> {report?.runtime?.version || '-'}</Text>
            <Text><Text strong>配置:</Text> {report?.config || '-'}</Text>
            <Text><Text strong>根目录:</Text> {report?.root || '-'}</Text>
          </Space>
        </Card>
      </div>
      <Drawer
        title="任务事件日志"
        open={!!selectedOpsTask}
        onClose={() => setSelectedOpsTask(null)}
        width={isMobile ? '100%' : 720}
      >
        {selectedOpsTask && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap>
              {taskStatusTag(selectedOpsTask.status)}
              {taskSignalTags(selectedOpsTask)}
            </Space>
            <Text strong>{selectedOpsTask.description || '-'}</Text>
            <Text type="secondary" style={{ wordBreak: 'break-all' }}>{selectedOpsTask.task_id}</Text>
            {selectedOpsTask.last_failure?.message && (
              <Alert
                type={needsOpsTaskIntervention(selectedOpsTask) ? 'error' : 'warning'}
                showIcon
                message="最近失败"
                description={selectedOpsTask.last_failure.message}
              />
            )}
            <Divider style={{ margin: '4px 0' }} />
            <TaskEventsPanel taskId={selectedOpsTask.task_id} />
          </Space>
        )}
      </Drawer>
      <Drawer
        title="运维事件详情"
        open={!!selectedOpsEvent}
        onClose={() => setSelectedOpsEvent(null)}
        width={isMobile ? '100%' : 620}
      >
        {selectedOpsEvent && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap>
              <Tag>{opsEventLabel(selectedOpsEvent.event_type)}</Tag>
              <Tag color={statusTagColor(selectedOpsEvent.status)}>{statusLabel(selectedOpsEvent.status)}</Tag>
              <Tag>{selectedOpsEvent.action || selectedOpsEvent.details?.action || '-'}</Tag>
            </Space>
            <Text><Text strong>ID:</Text> {selectedOpsEvent.id}</Text>
            <Text><Text strong>时间:</Text> {selectedOpsEvent.created_at || '-'}</Text>
            <Text><Text strong>操作者:</Text> {opsEventActorLabel(selectedOpsEvent)}</Text>
            <Text><Text strong>说明:</Text> {selectedOpsEvent.message || '-'}</Text>
            <Divider style={{ margin: '4px 0' }} />
            <Text strong>原始详情</Text>
            <pre style={{
              margin: 0,
              padding: 12,
              background: '#f7f8fa',
              border: '1px solid #edf0f5',
              borderRadius: 6,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: '55vh',
              overflow: 'auto',
            }}>
              {JSON.stringify(selectedOpsEvent.details || {}, null, 2)}
            </pre>
          </Space>
        )}
      </Drawer>
      <Drawer
        title="工具调用详情"
        open={!!selectedToolAudit}
        onClose={() => setSelectedToolAudit(null)}
        width={isMobile ? '100%' : 680}
      >
        {selectedToolAudit && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap>
              <Tag icon={<ToolOutlined />}>{selectedToolAudit.resource_id}</Tag>
              <Tag color={toolAuditStatusColor(selectedToolAudit.details?.status)}>
                {toolAuditStatusLabel(selectedToolAudit.details?.status)}
              </Tag>
              <Text type="secondary">{selectedToolAudit.timestamp}</Text>
            </Space>
            <Text><Text strong>Worker:</Text> {selectedToolAudit.details?.worker_name || selectedToolAudit.details?.worker_id || selectedToolAudit.username || '-'}</Text>
            <Text style={{ wordBreak: 'break-all' }}><Text strong>Task:</Text> {selectedToolAudit.details?.task_id || '-'}</Text>
            <Text><Text strong>耗时:</Text> {formatDuration(selectedToolAudit.details?.duration_ms)}</Text>
            {selectedToolAudit.details?.error && (
              <Alert type="warning" showIcon message="执行未成功" description={selectedToolAudit.details.error} />
            )}
            <Divider style={{ margin: '4px 0' }} />
            <Text strong>脱敏参数与结果</Text>
            <pre style={{
              margin: 0,
              padding: 12,
              background: '#f7f8fa',
              border: '1px solid #edf0f5',
              borderRadius: 6,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: '55vh',
              overflow: 'auto',
            }}>
              {JSON.stringify({
                arguments: selectedToolAudit.details?.arguments || {},
                result: selectedToolAudit.details?.result || null,
              }, null, 2)}
            </pre>
          </Space>
        )}
      </Drawer>
    </div>
  );
};
