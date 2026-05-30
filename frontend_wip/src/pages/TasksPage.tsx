import React, { useState, useEffect } from 'react';
import { Layout, Typography, Card, Button, Upload, List, Space, Input, message, Spin, Tag, Progress, Badge, Timeline, Alert, Empty, Tooltip, Divider, Checkbox, Modal, Tabs, Table, Select, Segmented } from 'antd';
import { FileTextOutlined, RobotOutlined, TeamOutlined, LoadingOutlined, BulbOutlined, ClockCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, FolderOpenOutlined, MailOutlined, LineChartOutlined, FileSearchOutlined, EyeOutlined, DownloadOutlined, ReloadOutlined, RollbackOutlined, ExclamationCircleOutlined, DeploymentUnitOutlined, CopyOutlined } from '@ant-design/icons';

import { useNavigate, useLocation } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';

import { useIsMobile, API_BASE, KnowledgeGroup, api } from '../shared';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;
const { TextArea } = Input;
const { Dragger } = Upload;

// ==================== 产物文件面板 ====================

export const TaskFilesPanel: React.FC<{ taskId: string }> = ({ taskId }) => {
  const isMobile = useIsMobile();
  const [files, setFiles] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedFile, setSelectedFile] = useState<any>(null);

  useEffect(() => {
    if (!taskId) return;
    setLoading(true);
    api.getTaskFiles(taskId)
      .then(res => setFiles(res.data.files || []))
      .catch(() => message.error('获取产物文件失败'))
      .finally(() => setLoading(false));
  }, [taskId]);

  if (loading) return <Spin />;
  if (files.length === 0) return <Empty description="无产物文件" />;

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };

  return (
    <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: 16 }}>
      <div style={{ width: isMobile ? '100%' : 280, flexShrink: 0 }}>
        <List
          size="small"
          bordered
          dataSource={files}
          renderItem={(f: any) => (
            <List.Item
              style={{
                cursor: 'pointer',
                background: selectedFile?.path === f.path ? '#e6f7ff' : undefined,
                padding: '8px 12px',
              }}
              onClick={() => setSelectedFile(f)}
            >
              <Space direction="vertical" size={0} style={{ width: '100%' }}>
                <Text strong style={{ fontSize: 13 }}>
                  <FileSearchOutlined style={{ marginRight: 4 }} />
                  {f.name}
                </Text>
                <Text type="secondary" style={{ fontSize: 11 }}>{f.path} · {formatSize(f.size)}</Text>
              </Space>
            </List.Item>
          )}
        />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {selectedFile ? (
          <Card
            size="small"
            title={<span><FileTextOutlined /> {selectedFile.path}</span>}
            extra={<Text type="secondary">{formatSize(selectedFile.size)}{selectedFile.truncated ? ' (已截断)' : ''}</Text>}
          >
            <pre style={{
              whiteSpace: 'pre-wrap',
              fontFamily: 'monospace',
              fontSize: 12,
              margin: 0,
              maxHeight: 500,
              overflow: 'auto',
              background: '#fafafa',
              padding: 12,
              borderRadius: 4,
            }}>
              {selectedFile.content}
            </pre>
          </Card>
        ) : (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Text type="secondary">点击左侧文件查看内容</Text>
          </div>
        )}
      </div>
    </div>
  );
};

export const TaskEventsPanel: React.FC<{ taskId: string }> = ({ taskId }) => {
  const [events, setEvents] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!taskId) return;
    setLoading(true);
    api.getTaskEvents(taskId)
      .then(res => setEvents(res.data.events || []))
      .catch(() => message.error('获取任务事件失败'))
      .finally(() => setLoading(false));
  }, [taskId]);

  if (loading) return <Spin />;
  if (events.length === 0) return <Empty description="暂无事件" />;

  const eventColor = (eventType: string) => {
    if (eventType === 'completed') return 'green';
    if (['failed', 'failed_non_retryable'].includes(eventType)) return 'red';
    if ([
      'failed_retryable',
      'timeout',
      'lease_lost',
      'lease_lost_stopped',
      'auto_retry_scheduled',
      'auto_retry_exhausted',
    ].includes(eventType)) return 'orange';
    if (['auto_retry_queued', 'retry_queued', 'recovery_queued'].includes(eventType)) return 'blue';
    if (['cancelled', 'cancel_requested'].includes(eventType)) return 'orange';
    if (eventType === 'running') return 'blue';
    return 'gray';
  };

  return (
    <Timeline
      items={events.map((event: any) => ({
        color: eventColor(event.event_type),
        children: (
          <Space direction="vertical" size={2}>
            <Space>
              <Tag color={eventColor(event.event_type)}>{event.event_type}</Tag>
              <Text type="secondary">{dayjs(event.created_at).format('YYYY-MM-DD HH:mm:ss')}</Text>
            </Space>
            {event.message && <Text>{event.message}</Text>}
            {event.details && Object.keys(event.details).length > 0 && (
              <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(event.details, null, 2)}
              </pre>
            )}
          </Space>
        ),
      }))}
    />
  );
};

type TaskTraceEvent = {
  id?: number | string;
  event_type: string;
  label: string;
  message?: string;
  stage: string;
  severity: string;
  subtask_id?: string;
  actor?: Record<string, string>;
  details?: Record<string, any>;
  created_at?: string;
};

type TaskTraceSubtask = {
  id: string;
  description?: string;
  status?: string;
  assigned_agent?: string;
  attempts?: number;
  dependencies?: string[];
  acceptance_criteria?: string[];
  result_preview?: string;
  error?: string;
  events?: TaskTraceEvent[];
};

type TaskDiagnosis = {
  level: 'ok' | 'warning' | 'critical';
  headline: string;
  root_causes?: string[];
  recommendations?: string[];
  evidence?: TaskTraceEvent[];
  metrics?: Record<string, any>;
  generated_at?: string;
};

type TaskRetrySuggestion = {
  mode: string;
  headline: string;
  retryable: boolean;
  force_required: boolean;
  confidence: string;
  blockers?: { type: string; message: string }[];
  steps?: string[];
  retry_request?: { enabled: boolean; body?: { force?: boolean } };
  latest_failure?: Record<string, any>;
};

const traceEventColor = (severity: string) => {
  if (severity === 'success') return 'green';
  if (severity === 'error') return 'red';
  if (severity === 'warning') return 'orange';
  if (severity === 'processing') return 'blue';
  return 'gray';
};

const traceStatusTag = (status?: string) => {
  const config: Record<string, { color: string; text: string }> = {
    pending: { color: 'default', text: '等待中' },
    queued: { color: 'processing', text: '排队中' },
    running: { color: 'processing', text: '执行中' },
    success: { color: 'success', text: '已完成' },
    completed: { color: 'success', text: '已完成' },
    failed: { color: 'error', text: '失败' },
    failed_non_retryable: { color: 'error', text: '不可重试失败' },
    cancelled: { color: 'warning', text: '已取消' },
    timeout: { color: 'warning', text: '超时' },
    unknown: { color: 'default', text: '未知' },
  };
  const c = config[status || 'unknown'] || { color: 'default', text: status || '未知' };
  return <Tag color={c.color}>{c.text}</Tag>;
};

const metricBoxStyle: React.CSSProperties = {
  border: '1px solid #f0f0f0',
  borderRadius: 8,
  padding: 12,
  minHeight: 96,
  background: '#fff',
};

const compactText = (value = '', maxLength = 96) => (
  value.length > maxLength ? `${value.substring(0, maxLength)}...` : value
);

const getScorePercent = (score: any) => {
  if (score === null || score === undefined || score === '') return null;
  const numericScore = Number(score);
  if (!Number.isFinite(numericScore)) return null;
  const percent = numericScore > 1 ? numericScore : numericScore * 100;
  return Math.max(0, Math.min(100, Math.round(percent)));
};

const getSubtaskStats = (subtasks: any[] = []) => {
  const stats = {
    total: subtasks.length,
    completed: 0,
    running: 0,
    failed: 0,
    pending: 0,
  };

  subtasks.forEach((subtask) => {
    const status = subtask.status || 'pending';
    if (['completed', 'success'].includes(status)) stats.completed += 1;
    else if (['running', 'processing'].includes(status)) stats.running += 1;
    else if (['failed', 'failed_non_retryable', 'timeout', 'cancelled'].includes(status)) stats.failed += 1;
    else stats.pending += 1;
  });

  return stats;
};

const getAgentWorkloads = (subtasks: any[] = []) => {
  const workloads = new Map<string, { name: string; total: number; completed: number; running: number; failed: number; pending: number }>();

  subtasks.forEach((subtask) => {
    const name = subtask.assigned_agent || '自动分配';
    const current = workloads.get(name) || { name, total: 0, completed: 0, running: 0, failed: 0, pending: 0 };
    const status = subtask.status || 'pending';
    current.total += 1;
    if (['completed', 'success'].includes(status)) current.completed += 1;
    else if (['running', 'processing'].includes(status)) current.running += 1;
    else if (['failed', 'failed_non_retryable', 'timeout', 'cancelled'].includes(status)) current.failed += 1;
    else current.pending += 1;
    workloads.set(name, current);
  });

  return Array.from(workloads.values()).sort((a, b) => b.running - a.running || b.failed - a.failed || b.total - a.total);
};

const renderTraceEventItems = (events: TaskTraceEvent[]) => (
  <Timeline
    items={events.map((event) => {
      const actor = event.actor || {};
      const showDetails = ['provider', 'tool', 'llm'].includes(event.stage) || event.severity === 'warning' || event.severity === 'error';
      return {
        color: traceEventColor(event.severity),
        children: (
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            <Space size={6} wrap>
              <Tag color={traceEventColor(event.severity)}>{event.label || event.event_type}</Tag>
              <Tag>{event.stage}</Tag>
              {actor.worker_id && <Tag color="purple">{actor.worker_id}</Tag>}
              {actor.worker && <Tag color="purple">{actor.worker}</Tag>}
              {actor.provider && <Tag color="geekblue">{actor.provider}</Tag>}
              {actor.model && <Tag color="cyan">{actor.model}</Tag>}
              {event.created_at && <Text type="secondary">{dayjs(event.created_at).format('YYYY-MM-DD HH:mm:ss')}</Text>}
            </Space>
            {event.message && <Text>{event.message}</Text>}
            {showDetails && event.details && Object.keys(event.details).length > 0 && (
              <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap', background: '#fafafa', padding: 8, borderRadius: 4 }}>
                {JSON.stringify(event.details, null, 2)}
              </pre>
            )}
          </Space>
        ),
      };
    })}
  />
);

export const TaskTracePanel: React.FC<{
  taskId: string;
  task?: any;
  onRetryTask?: (task: any, force?: boolean) => void;
  retrying?: boolean;
  retryDisabled?: boolean;
}> = ({ taskId, task, onRetryTask, retrying = false, retryDisabled = false }) => {
  const [trace, setTrace] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [diagnosis, setDiagnosis] = useState<TaskDiagnosis | null>(null);
  const [retrySuggestion, setRetrySuggestion] = useState<TaskRetrySuggestion | null>(null);
  const [diagnosisLoading, setDiagnosisLoading] = useState(false);
  const [reportLoading, setReportLoading] = useState<'download' | 'copy' | null>(null);

  useEffect(() => {
    if (!taskId) return;
    setLoading(true);
    const params = Object.fromEntries(Object.entries(filters).filter(([, value]) => value));
    api.getTaskTrace(taskId, params)
      .then(res => setTrace(res.data))
      .catch(() => message.error('获取执行树失败'))
      .finally(() => setLoading(false));
  }, [taskId, filters.subtask_id, filters.worker_id, filters.tool_name, filters.stage, filters.severity, filters.event_type]);

  if (loading) return <Spin />;
  if (!trace) return <Empty description="暂无执行树" />;

  const summary = trace.summary || {};
  const subtasks: TaskTraceSubtask[] = trace.subtasks || [];
  const taskEvents: TaskTraceEvent[] = trace.unassigned_events || [];
  const runDiagnosis = async () => {
    setDiagnosisLoading(true);
    try {
      const [diagnosisRes, retryRes] = await Promise.all([
        api.getTaskDiagnosis(taskId),
        api.getTaskRetrySuggestion(taskId),
      ]);
      setDiagnosis(diagnosisRes.data);
      setRetrySuggestion(retryRes.data);
    } catch {
      message.error('生成诊断摘要失败');
    } finally {
      setDiagnosisLoading(false);
    }
  };
  const handleSuggestedRetry = () => {
    if (!retrySuggestion?.retry_request?.enabled || !task || !onRetryTask) return;
    const force = Boolean(retrySuggestion.retry_request.body?.force);
    if (force) {
      Modal.confirm({
        title: '确认强制重试',
        content: '该任务需要先确认阻塞点已处理。强制重试不会绕过工具策略，只会重新入队执行。',
        okText: '强制重试',
        cancelText: '取消',
        onOk: () => onRetryTask(task, true),
      });
      return;
    }
    onRetryTask(task, false);
  };
  const fetchDiagnosisReport = async () => {
    const res = await api.getTaskDiagnosisReport(taskId);
    return res.data;
  };
  const downloadDiagnosisReport = async () => {
    setReportLoading('download');
    try {
      const report = await fetchDiagnosisReport();
      const blob = new Blob([report.markdown || ''], { type: report.content_type || 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = report.filename || `memox-diagnosis-${taskId}.md`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      message.success('诊断报告已下载');
    } catch {
      message.error('下载诊断报告失败');
    } finally {
      setReportLoading(null);
    }
  };
  const copyDiagnosisReport = async () => {
    setReportLoading('copy');
    try {
      const report = await fetchDiagnosisReport();
      await navigator.clipboard.writeText(report.markdown || report.share_text || '');
      message.success('诊断报告已复制');
    } catch {
      message.error('复制诊断报告失败');
    } finally {
      setReportLoading(null);
    }
  };
  const diagnosisType = diagnosis?.level === 'critical' ? 'error' : diagnosis?.level === 'warning' ? 'warning' : 'success';

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space wrap>
        <Select
          size="small"
          allowClear
          placeholder="阶段"
          style={{ width: 130 }}
          value={filters.stage || undefined}
          onChange={(value) => setFilters(prev => ({ ...prev, stage: value || '' }))}
          options={[
            { value: 'task', label: '任务' },
            { value: 'planning', label: '规划' },
            { value: 'iteration', label: '迭代' },
            { value: 'subtask', label: '子任务' },
            { value: 'provider', label: 'Provider' },
            { value: 'llm', label: 'LLM' },
            { value: 'tool', label: '工具' },
            { value: 'worker_log', label: '日志' },
            { value: 'recovery', label: '恢复' },
          ]}
        />
        <Select
          size="small"
          allowClear
          placeholder="级别"
          style={{ width: 120 }}
          value={filters.severity || undefined}
          onChange={(value) => setFilters(prev => ({ ...prev, severity: value || '' }))}
          options={[
            { value: 'processing', label: '执行中' },
            { value: 'success', label: '成功' },
            { value: 'warning', label: '警告' },
            { value: 'error', label: '错误' },
            { value: 'default', label: '普通' },
          ]}
        />
        <Input
          size="small"
          placeholder="子任务"
          style={{ width: 150 }}
          value={filters.subtask_id || ''}
          onChange={(event) => setFilters(prev => ({ ...prev, subtask_id: event.target.value.trim() }))}
        />
        <Input
          size="small"
          placeholder="Worker"
          style={{ width: 140 }}
          value={filters.worker_id || ''}
          onChange={(event) => setFilters(prev => ({ ...prev, worker_id: event.target.value.trim() }))}
        />
        <Input
          size="small"
          placeholder="工具"
          style={{ width: 140 }}
          value={filters.tool_name || ''}
          onChange={(event) => setFilters(prev => ({ ...prev, tool_name: event.target.value.trim() }))}
        />
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => setFilters({})}
        >
          清除
        </Button>
        <Button
          size="small"
          type="primary"
          icon={<FileSearchOutlined />}
          loading={diagnosisLoading}
          onClick={runDiagnosis}
        >
          诊断
        </Button>
      </Space>

      {diagnosis && (
        <Alert
          type={diagnosisType}
          showIcon
          message={
            <Space wrap>
              <Text strong>{diagnosis.headline}</Text>
              {diagnosis.generated_at && <Text type="secondary">{dayjs(diagnosis.generated_at).format('YYYY-MM-DD HH:mm:ss')}</Text>}
            </Space>
          }
          description={
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              {diagnosis.root_causes?.length ? (
                <div>
                  <Text type="secondary">可能原因</Text>
                  <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                    {diagnosis.root_causes.map((item, index) => <li key={index}>{item}</li>)}
                  </ul>
                </div>
              ) : null}
              {diagnosis.recommendations?.length ? (
                <div>
                  <Text type="secondary">建议动作</Text>
                  <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                    {diagnosis.recommendations.map((item, index) => <li key={index}>{item}</li>)}
                  </ul>
                </div>
              ) : null}
              <Space wrap>
                <Tag>失败 {diagnosis.metrics?.failure_count || 0}</Tag>
                <Tag>拦截 {diagnosis.metrics?.tool_rejected_count || 0}</Tag>
                <Tag>Fallback {diagnosis.metrics?.fallback_count || 0}</Tag>
                <Tag>Token 峰值 {diagnosis.metrics?.max_llm_call?.total_tokens || 0}</Tag>
              </Space>
              {diagnosis.evidence?.length ? (
                <div>
                  <Text type="secondary">关键证据</Text>
                  {renderTraceEventItems(diagnosis.evidence)}
                </div>
              ) : null}
              {retrySuggestion && (
                <div>
                  <Divider style={{ margin: '8px 0' }} />
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Space wrap>
                      <Text strong>{retrySuggestion.headline}</Text>
                      <Tag>{retrySuggestion.mode}</Tag>
                      <Tag color={retrySuggestion.force_required ? 'orange' : retrySuggestion.retryable ? 'green' : 'default'}>
                        {retrySuggestion.force_required ? '需确认后强制重试' : retrySuggestion.retryable ? '可重试' : '不建议直接重试'}
                      </Tag>
                      <Tag>置信度 {retrySuggestion.confidence}</Tag>
                    </Space>
                    {retrySuggestion.blockers?.length ? (
                      <div>
                        <Text type="secondary">重试前检查</Text>
                        <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                          {retrySuggestion.blockers.map((item, index) => <li key={index}>{item.message}</li>)}
                        </ul>
                      </div>
                    ) : null}
                    {retrySuggestion.steps?.length ? (
                      <div>
                        <Text type="secondary">重试步骤</Text>
                        <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                          {retrySuggestion.steps.map((item, index) => <li key={index}>{item}</li>)}
                        </ul>
                      </div>
                    ) : null}
                    {retrySuggestion.retry_request?.enabled && task && onRetryTask && (
                      <Button
                        size="small"
                        type={retrySuggestion.force_required ? 'default' : 'primary'}
                        icon={<ReloadOutlined />}
                        loading={retrying}
                        disabled={retryDisabled}
                        onClick={handleSuggestedRetry}
                      >
                        {retrySuggestion.force_required ? '确认后强制重试' : '按建议重试'}
                      </Button>
                    )}
                    <Space wrap>
                      <Button
                        size="small"
                        icon={<DownloadOutlined />}
                        loading={reportLoading === 'download'}
                        onClick={downloadDiagnosisReport}
                      >
                        下载报告
                      </Button>
                      <Button
                        size="small"
                        icon={<CopyOutlined />}
                        loading={reportLoading === 'copy'}
                        onClick={copyDiagnosisReport}
                      >
                        复制报告
                      </Button>
                    </Space>
                  </Space>
                </div>
              )}
            </Space>
          }
        />
      )}

      <Space wrap>
        <Tag color="blue">事件 {summary.event_count || 0}</Tag>
        <Tag color="purple">子任务 {summary.subtask_count || 0}</Tag>
        <Tag color="orange">重试 {summary.retry_count || 0}</Tag>
        <Tag color="geekblue">Fallback {summary.fallback_count || 0}</Tag>
        <Tag color="cyan">工具 {summary.tool_call_count || 0}</Tag>
        <Tag color={summary.tool_rejected_count ? 'orange' : 'green'}>拦截 {summary.tool_rejected_count || 0}</Tag>
        <Tag>LLM {summary.llm_usage?.total_tokens || 0} tokens</Tag>
        <Tag color={summary.failure_count ? 'red' : 'green'}>失败 {summary.failure_count || 0}</Tag>
        {summary.last_event_at && <Text type="secondary">最近更新 {dayjs(summary.last_event_at).format('YYYY-MM-DD HH:mm:ss')}</Text>}
      </Space>

      {taskEvents.length > 0 && (
        <div>
          <Title level={5} style={{ marginTop: 0 }}>任务级事件</Title>
          {renderTraceEventItems(taskEvents)}
        </div>
      )}

      {subtasks.length > 0 ? (
        <List
          dataSource={subtasks}
          renderItem={(subtask) => (
            <List.Item style={{ alignItems: 'stretch', display: 'block', padding: '14px 0' }}>
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <Space wrap>
                  {traceStatusTag(subtask.status)}
                  <Text strong>{subtask.id}</Text>
                  <Text>{subtask.description || '未命名子任务'}</Text>
                  {subtask.assigned_agent && <Tag color="purple">{subtask.assigned_agent}</Tag>}
                  <Tag>尝试 {subtask.attempts || 0}</Tag>
                  {subtask.dependencies?.length ? <Tag color="cyan">依赖 {subtask.dependencies.length}</Tag> : null}
                </Space>
                {subtask.error && <Alert type="error" showIcon message={subtask.error} />}
                {subtask.result_preview && (
                  <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12, background: '#fafafa', padding: 8, borderRadius: 4 }}>
                    {subtask.result_preview}
                  </pre>
                )}
                {subtask.events?.length ? renderTraceEventItems(subtask.events) : <Text type="secondary">暂无子任务事件</Text>}
              </Space>
            </List.Item>
          )}
        />
      ) : (
        <Empty description="暂无子任务执行信息" />
      )}
    </Space>
  );
};

const TaskExecutionOverview: React.FC<{
  task: any;
  suggestions: any[];
  canRetry: boolean;
  retrying: boolean;
  retryDisabled: boolean;
  onRetryTask: (task: any) => void;
}> = ({ task, suggestions, canRetry, retrying, retryDisabled, onRetryTask }) => {
  const subtasks = Array.isArray(task?.sub_tasks) ? task.sub_tasks : [];
  const iterations = Array.isArray(task?.iterations) ? task.iterations : [];
  const subtaskStats = getSubtaskStats(subtasks);
  const workloads = getAgentWorkloads(subtasks);
  const latestIteration = iterations.length ? iterations[iterations.length - 1] : null;
  const scorePercent = getScorePercent(task?.final_score);
  const latestScorePercent = getScorePercent(latestIteration?.score);
  const progressPercent = subtaskStats.total
    ? Math.round((subtaskStats.completed / subtaskStats.total) * 100)
    : task?.status === 'completed' ? 100 : 0;
  const failureMessage = task?.status !== 'completed'
    ? (task?.last_failure?.message || subtasks.find((subtask: any) => subtask.error)?.error)
    : '';
  const hasActiveLease = Boolean(task?.job?.lease_owner);
  const nextRetryAt = task?.job?.next_retry_at;

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
        <div style={metricBoxStyle}>
          <Text type="secondary" style={{ fontSize: 12 }}>当前状态</Text>
          <div style={{ marginTop: 8 }}>{traceStatusTag(task?.status)}</div>
          <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
            {hasActiveLease ? `Worker ${task.job.lease_owner}` : nextRetryAt ? `等待 ${dayjs(nextRetryAt).format('HH:mm:ss')}` : '无活跃租约'}
          </Text>
        </div>
        <div style={metricBoxStyle}>
          <Text type="secondary" style={{ fontSize: 12 }}>质量评分</Text>
          <div style={{ marginTop: 6 }}>
            <Text strong style={{ fontSize: 24 }}>{scorePercent === null ? '-' : `${scorePercent}%`}</Text>
          </div>
          <Progress
            percent={scorePercent || 0}
            size="small"
            status={scorePercent !== null && scorePercent < 60 ? 'exception' : 'active'}
            showInfo={false}
          />
        </div>
        <div style={metricBoxStyle}>
          <Text type="secondary" style={{ fontSize: 12 }}>子任务进度</Text>
          <div style={{ marginTop: 6 }}>
            <Text strong style={{ fontSize: 24 }}>{subtaskStats.completed}/{subtaskStats.total || 0}</Text>
          </div>
          <Progress percent={progressPercent} size="small" status={subtaskStats.failed ? 'exception' : 'active'} />
        </div>
        <div style={metricBoxStyle}>
          <Text type="secondary" style={{ fontSize: 12 }}>Agent 分配</Text>
          <div style={{ marginTop: 6 }}>
            <Text strong style={{ fontSize: 24 }}>{workloads.length || '-'}</Text>
          </div>
          <Space wrap size={4}>
            <Tag color="processing">执行 {subtaskStats.running}</Tag>
            <Tag color={subtaskStats.failed ? 'error' : 'success'}>异常 {subtaskStats.failed}</Tag>
          </Space>
        </div>
      </div>

      {nextRetryAt && (
        <Alert
          type="warning"
          showIcon
          message="等待自动重试"
          description={`下次重试 ${dayjs(nextRetryAt).format('YYYY-MM-DD HH:mm:ss')}，已自动重试 ${task?.job?.auto_retry_count || 0} 次。`}
        />
      )}

      {failureMessage && (
        <Alert
          type={canRetry ? 'warning' : 'error'}
          showIcon
          message={canRetry ? '任务失败，可重新入队' : '任务需要人工检查'}
          description={compactText(failureMessage, 180)}
          action={canRetry ? (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={retrying}
              disabled={retryDisabled}
              onClick={() => onRetryTask(task)}
            >
              重试
            </Button>
          ) : undefined}
        />
      )}

      <div>
        <Space wrap style={{ marginBottom: 8 }}>
          <Text strong>子任务编排</Text>
          <Tag>{subtaskStats.total || 0} 个子任务</Tag>
          <Tag color="success">完成 {subtaskStats.completed}</Tag>
          <Tag color="processing">执行 {subtaskStats.running}</Tag>
          <Tag color={subtaskStats.failed ? 'error' : 'default'}>异常 {subtaskStats.failed}</Tag>
        </Space>
        {subtasks.length ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 10 }}>
            {subtasks.map((subtask: any, index: number) => (
              <div key={subtask.id || index} style={{ ...metricBoxStyle, minHeight: 132 }}>
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap size={4}>
                    {traceStatusTag(subtask.status)}
                    <Tag color="purple">{subtask.assigned_agent || '自动分配'}</Tag>
                    <Tag>尝试 {subtask.attempts || 0}</Tag>
                  </Space>
                  <Text strong style={{ display: 'block', wordBreak: 'break-word' }}>
                    {compactText(subtask.description || subtask.id || '未命名子任务', 92)}
                  </Text>
                  {subtask.dependencies?.length ? (
                    <Space wrap size={4}>
                      {subtask.dependencies.slice(0, 4).map((dep: string) => <Tag key={dep} color="cyan">{dep}</Tag>)}
                      {subtask.dependencies.length > 4 && <Tag>+{subtask.dependencies.length - 4}</Tag>}
                    </Space>
                  ) : (
                    <Text type="secondary" style={{ fontSize: 12 }}>无依赖</Text>
                  )}
                  {(subtask.error || subtask.result) && (
                    <Text type={subtask.error ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
                      {compactText(subtask.error || subtask.result, 110)}
                    </Text>
                  )}
                </Space>
              </div>
            ))}
          </div>
        ) : (
          <Empty description="暂无子任务状态" />
        )}
      </div>

      {workloads.length > 0 && (
        <div>
          <Text strong>Agent 负载</Text>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10, marginTop: 8 }}>
            {workloads.map((agent) => {
              const donePercent = agent.total ? Math.round((agent.completed / agent.total) * 100) : 0;
              return (
                <div key={agent.name} style={metricBoxStyle}>
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Space wrap>
                      <TeamOutlined />
                      <Text strong>{agent.name}</Text>
                      <Tag>{agent.total} 项</Tag>
                    </Space>
                    <Progress percent={donePercent} size="small" status={agent.failed ? 'exception' : 'active'} />
                    <Space wrap size={4}>
                      <Tag color="success">完成 {agent.completed}</Tag>
                      <Tag color="processing">执行 {agent.running}</Tag>
                      <Tag color={agent.failed ? 'error' : 'default'}>异常 {agent.failed}</Tag>
                    </Space>
                  </Space>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {latestIteration && (
        <div style={metricBoxStyle}>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Space wrap>
              <LineChartOutlined />
              <Text strong>最近迭代</Text>
              <Tag>第 {(latestIteration.iteration ?? iterations.length - 1) + 1} 轮</Tag>
              {latestScorePercent !== null && (
                <Tag color={latestScorePercent >= 80 ? 'success' : latestScorePercent >= 60 ? 'warning' : 'error'}>
                  {latestScorePercent}%
                </Tag>
              )}
            </Space>
            {latestIteration.improvements?.length ? (
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                {latestIteration.improvements.slice(0, 3).map((item: string, index: number) => (
                  <Text key={index} type="secondary" style={{ fontSize: 13 }}>{item}</Text>
                ))}
              </Space>
            ) : (
              <Text type="secondary">暂无迭代改进项</Text>
            )}
          </Space>
        </div>
      )}

      {suggestions.length > 0 && (
        <Alert
          type="info"
          showIcon
          message={`发现 ${suggestions.length} 条优化建议`}
          description={suggestions.slice(0, 2).map((item: any) => item.title || item.description).filter(Boolean).join('；')}
        />
      )}
    </Space>
  );
};

// ==================== 任务执行页面 ====================

export const TasksPage: React.FC = () => {
  const isMobile = useIsMobile();
  const location = useLocation();
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [taskInput, setTaskInput] = useState('');

  useEffect(() => {
    const state = (location.state as any) || {};
    const prefill = state.prefill;
    const taskId = state.taskId;
    if (prefill && typeof prefill === 'string') {
      setTaskInput(prefill);
      message.info('已从会话提炼任务描述，请确认后点击"执行任务"');
      // 清除 state 防止刷新再次触发
      navigate(location.pathname, { replace: true, state: {} });
    } else if (taskId && typeof taskId === 'string') {
      api.getTask(taskId)
        .then(res => {
          setCurrentTask(res.data);
          setSuggestions(res.data.suggestions || []);
        })
        .catch(() => message.error('获取任务详情失败'))
        .finally(() => navigate(location.pathname, { replace: true, state: {} }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [executing, setExecuting] = useState(false);
  const [currentTask, setCurrentTask] = useState<any>(null);
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [retryingTaskId, setRetryingTaskId] = useState<string | null>(null);
  const [taskHistoryFilter, setTaskHistoryFilter] = useState('all');
  const [groups, setGroups] = useState<KnowledgeGroup[]>([]);
  const [activeGroupIds, setActiveGroupIds] = useState<string[]>([]);
  const [runningTaskIds, setRunningTaskIds] = useState<string[]>([]);
  const [feedbackModalOpen, setFeedbackModalOpen] = useState(false);
  const [feedbackTaskId, setFeedbackTaskId] = useState('');
  const [feedbackInfo, setFeedbackInfo] = useState<any>(null);
  const [feedbackText, setFeedbackText] = useState('');
  const [submittingFeedback, setSubmittingFeedback] = useState(false);

  // 执行中轮询运行任务列表
  useEffect(() => {
    if (!executing) { setRunningTaskIds([]); return; }
    const interval = setInterval(async () => {
      try {
        const res = await axios.get(`${API_BASE}/tasks/running`);
        setRunningTaskIds(res.data);
      } catch {}
    }, 2000);
    return () => clearInterval(interval);
  }, [executing]);

  useEffect(() => {
    const token = localStorage.getItem('memox_token');
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws?token=${token}`;
    let ws: WebSocket | null = null;

    if (executing) {
      ws = new WebSocket(wsUrl);
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'task_needs_input') {
            setFeedbackTaskId(data.task_id);
            setFeedbackInfo(data);
            setFeedbackText('');
            setFeedbackModalOpen(true);
          }
        } catch {}
      };
    }

    return () => { ws?.close(); };
  }, [executing]);

  const handleCancel = async () => {
    for (const tid of runningTaskIds) {
      try {
        await api.cancelTask(tid);
        message.info('已请求取消任务');
      } catch {}
    }
  };

  const handleSubmitFeedback = async () => {
    if (!feedbackText.trim()) return;
    setSubmittingFeedback(true);
    try {
      await api.submitTaskFeedback(feedbackTaskId, feedbackText.trim());
      message.success('反馈已提交');
      setFeedbackModalOpen(false);
    } catch (err) {
      message.error('提交失败');
    } finally {
      setSubmittingFeedback(false);
    }
  };

  const fetchTasks = async () => {
    try {
      const res = await api.listTasks();
      setTasks(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    fetchTasks();
    api.listGroups().then(res => {
      setGroups(res.data);
      setActiveGroupIds(res.data.map((g: KnowledgeGroup) => g.id));
    }).catch(() => {});
  }, []);

  const terminalStatuses = ['completed', 'failed', 'cancelled', 'timeout'];

  const showTerminalMessage = (task: any) => {
    if (task.status === 'completed') {
      message.success('任务执行完成');
    } else if (task.status === 'cancelled') {
      message.warning('任务已取消');
    } else if (task.status === 'timeout') {
      message.warning('任务执行超时');
    } else if (task.status === 'failed') {
      message.error('任务执行失败');
    }
  };

  const pollTaskUntilTerminal = async (taskId: string, initialTask: any) => {
    let latest = initialTask;
    for (let i = 0; i < 180 && !terminalStatuses.includes(latest.status); i += 1) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      const poll = await api.getTask(taskId);
      latest = poll.data;
      setCurrentTask(latest);
      setSuggestions(latest.suggestions || []);
      fetchTasks();
    }
    showTerminalMessage(latest);
    return latest;
  };

  const handleExecute = async () => {
    if (!taskInput.trim() || executing) return;

    setExecuting(true);
    setSuggestions([]);

    try {
      const allGroupIds = groups.map(g => g.id);
      const isAllSelected = activeGroupIds.length === allGroupIds.length;
      const res = await api.createTask(taskInput, undefined, isAllSelected ? null : activeGroupIds);
      const data = res.data;

      setCurrentTask(data);
      message.success('任务已提交，正在后台执行');
      fetchTasks();
      await pollTaskUntilTerminal(data.task_id, data);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '执行失败');
    } finally {
      setExecuting(false);
    }
  };

  const canRetryTask = (task: any) => {
    if (!task) return false;
    if (task.status === 'timeout') return true;
    return task.status === 'failed' && task.last_failure?.retryable === true;
  };

  const isTaskActive = (task: any) => (
    ['pending', 'queued', 'running'].includes(task?.status) || Boolean(task?.job?.lease_owner)
  );

  const isTaskWaitingAutoRetry = (task: any) => Boolean(task?.job?.next_retry_at);

  const isTaskAutoRetryExhausted = (task: any) => (
    task?.last_failure?.event_type === 'auto_retry_exhausted'
  );

  const needsHumanIntervention = (task: any) => (
    task?.status === 'failed'
    && (isTaskAutoRetryExhausted(task) || task?.last_failure?.retryable !== true)
  );

  const isTaskManuallyRetryable = (task: any) => (
    canRetryTask(task) && !isTaskWaitingAutoRetry(task)
  );

  const trimTaskText = (value: string = '', maxLength = 80) => (
    value.length > maxLength ? `${value.substring(0, maxLength)}...` : value
  );

  const getTaskHistoryStatusTags = (task: any) => {
    const tags = [getStatusTag(task.status)];

    if (isTaskWaitingAutoRetry(task)) {
      tags.push(
        <Tooltip key="next-retry" title={task.job.next_retry_at}>
          <Tag color="warning">等待自动重试 {dayjs(task.job.next_retry_at).format('HH:mm:ss')}</Tag>
        </Tooltip>,
      );
    }

    if (isTaskAutoRetryExhausted(task)) {
      tags.push(<Tag key="retry-exhausted" color="red">自动重试耗尽</Tag>);
    } else if (needsHumanIntervention(task)) {
      tags.push(<Tag key="needs-attention" color="red">需人工介入</Tag>);
    }

    if (isTaskManuallyRetryable(task)) {
      tags.push(<Tag key="retryable" color="blue">可手动重试</Tag>);
    }

    if (task.job?.auto_retry_count > 0) {
      tags.push(<Tag key="auto-retry-count" color="orange">自动重试 {task.job.auto_retry_count} 次</Tag>);
    }

    if (task.job?.lease_owner) {
      tags.push(
        <Tooltip key="lease" title={`租约到期：${task.job.lease_expires_at || '-'}`}>
          <Tag color="processing">执行租约</Tag>
        </Tooltip>,
      );
    }

    return tags;
  };

  const taskHistoryCounts = {
    active: tasks.filter(isTaskActive).length,
    retrying: tasks.filter(isTaskWaitingAutoRetry).length,
    retryable: tasks.filter(isTaskManuallyRetryable).length,
    attention: tasks.filter(needsHumanIntervention).length,
  };

  const taskHistoryFilterOptions = [
    { label: `全部 ${tasks.length}`, value: 'all' },
    { label: `执行中 ${taskHistoryCounts.active}`, value: 'active' },
    { label: `等待自动重试 ${taskHistoryCounts.retrying}`, value: 'retrying' },
    { label: `可手动重试 ${taskHistoryCounts.retryable}`, value: 'retryable' },
    { label: `需介入 ${taskHistoryCounts.attention}`, value: 'attention' },
  ];

  const filteredTasks = tasks.filter((task: any) => {
    if (taskHistoryFilter === 'active') return isTaskActive(task);
    if (taskHistoryFilter === 'retrying') return isTaskWaitingAutoRetry(task);
    if (taskHistoryFilter === 'retryable') return isTaskManuallyRetryable(task);
    if (taskHistoryFilter === 'attention') return needsHumanIntervention(task);
    return true;
  });

  const handleSelectHistoryTask = async (task: any) => {
    if (!task?.task_id) return;
    try {
      const res = await api.getTask(task.task_id);
      setCurrentTask(res.data);
      setSuggestions(res.data.suggestions || []);
    } catch (err: any) {
      setCurrentTask(task);
      setSuggestions(task.suggestions || []);
      message.warning('未能刷新任务详情，已显示历史快照');
    }
  };

  const handleRetryTask = async (task: any, force: boolean = false) => {
    if (!task?.task_id || executing || retryingTaskId) return;
    setExecuting(true);
    setRetryingTaskId(task.task_id);
    try {
      const res = await api.retryTask(task.task_id, force);
      const data = res.data;
      setCurrentTask(data);
      setSuggestions([]);
      message.success('任务已重新入队');
      fetchTasks();
      await pollTaskUntilTerminal(data.task_id, data);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '重试失败');
    } finally {
      setRetryingTaskId(null);
      setExecuting(false);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
      case 'failed': return <CloseCircleOutlined style={{ color: '#ff4d4f' }} />;
      case 'queued':
      case 'running': return <LoadingOutlined style={{ color: '#1890ff' }} />;
      default: return <ClockCircleOutlined style={{ color: '#999' }} />;
    }
  };

  const getStatusTag = (status: string) => {
    const config: Record<string, { color: string; text: string }> = {
      pending: { color: 'default', text: '等待中' },
      queued: { color: 'processing', text: '排队中' },
      running: { color: 'processing', text: '执行中' },
      completed: { color: 'success', text: '已完成' },
      failed: { color: 'error', text: '失败' },
      cancelled: { color: 'warning', text: '已取消' },
      timeout: { color: 'warning', text: '超时' },
    };
    const c = config[status] || config.pending;
    return <Tag color={c.color}>{c.text}</Tag>;
  };

  const getComplexityTag = (complexity: string) => {
    const config: Record<string, string> = {
      simple: 'blue',
      parallel: 'purple',
      sequential: 'orange',
      mixed: 'magenta',
    };
    return <Tag color={config[complexity] || 'default'}>{complexity}</Tag>;
  };

  const currentTaskScorePercent = getScorePercent(currentTask?.final_score);
  const historyStatusItems = [
    { key: 'active', label: '执行中', count: taskHistoryCounts.active, color: '#1677ff', icon: <LoadingOutlined /> },
    { key: 'retrying', label: '自动重试', count: taskHistoryCounts.retrying, color: '#faad14', icon: <RollbackOutlined /> },
    { key: 'retryable', label: '可手动重试', count: taskHistoryCounts.retryable, color: '#722ed1', icon: <ReloadOutlined /> },
    { key: 'attention', label: '需介入', count: taskHistoryCounts.attention, color: '#ff4d4f', icon: <ExclamationCircleOutlined /> },
  ];

  return (
    <div>
      <Card title="任务执行">
        {groups.length > 1 && (
          <div style={{ marginBottom: 12 }}>
            <Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>激活知识库分组：</Text>
            <Checkbox.Group
              value={activeGroupIds}
              onChange={vals => setActiveGroupIds(vals as string[])}
              options={groups.map(g => ({ label: <Tag color={g.color}>{g.name}</Tag>, value: g.id }))}
            />
          </div>
        )}
        <TextArea
          value={taskInput}
          onChange={e => setTaskInput(e.target.value)}
          placeholder="输入任务描述，我会自动拆分为子任务并行执行..."
          autoSize={{ minRows: 3, maxRows: 6 }}
          style={{ marginBottom: 16 }}
        />
        <Button
          type="primary"
          icon={<RobotOutlined />}
          onClick={handleExecute}
          loading={executing}
          disabled={!taskInput.trim()}
          size="large"
        >
          执行任务
        </Button>
        {executing && runningTaskIds.length > 0 && (
          <Button
            danger
            icon={<CloseCircleOutlined />}
            onClick={handleCancel}
            style={{ marginLeft: 8 }}
          >
            取消任务
          </Button>
        )}
      </Card>

      {currentTask && (
        <Card
          title={
            <Space>
              执行结果
              <Tag color={(currentTaskScorePercent || 0) >= 80 ? 'success' : (currentTaskScorePercent || 0) >= 60 ? 'warning' : 'error'}>
                评分 {currentTaskScorePercent === null ? '-' : `${currentTaskScorePercent}%`}
              </Tag>
              {getStatusTag(currentTask.status)}
              {currentTask.job?.recovery_count > 0 && (
                <Tag color="blue">恢复 {currentTask.job.recovery_count} 次</Tag>
              )}
              {currentTask.job?.auto_retry_count > 0 && (
                <Tag color="orange">自动重试 {currentTask.job.auto_retry_count} 次</Tag>
              )}
              {currentTask.job?.next_retry_at && (
                <Tooltip title={currentTask.job.next_retry_at}>
                  <Tag color="warning">下次重试 {dayjs(currentTask.job.next_retry_at).format('HH:mm:ss')}</Tag>
                </Tooltip>
              )}
              {currentTask.job?.lease_owner && (
                <Tooltip title={`租约到期：${currentTask.job.lease_expires_at || '-'}`}>
                  <Tag color="processing">租约 {currentTask.job.lease_owner}</Tag>
                </Tooltip>
              )}
            </Space>
          }
          extra={canRetryTask(currentTask) ? (
            <Button
              icon={<ReloadOutlined />}
              onClick={() => handleRetryTask(currentTask)}
              loading={retryingTaskId === currentTask.task_id}
              disabled={executing && retryingTaskId !== currentTask.task_id}
            >
              重试任务
            </Button>
          ) : null}
          style={{ marginTop: 16 }}
        >
          <Tabs defaultActiveKey="overview" items={[
            {
              key: 'overview',
              label: <span><DeploymentUnitOutlined /> 执行概览</span>,
              children: (
                <TaskExecutionOverview
                  task={currentTask}
                  suggestions={suggestions}
                  canRetry={canRetryTask(currentTask)}
                  retrying={retryingTaskId === currentTask.task_id}
                  retryDisabled={executing && retryingTaskId !== currentTask.task_id}
                  onRetryTask={handleRetryTask}
                />
              ),
            },
            {
              key: 'result',
              label: <span><FileTextOutlined /> 任务结果</span>,
              children: (
                <Card size="small" style={{ background: '#fafafa' }}>
                  <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 13, margin: 0 }}>
                    {currentTask.result}
                  </pre>
                </Card>
              ),
            },
            {
              key: 'iterations',
              label: <span><LineChartOutlined /> 迭代记录 ({currentTask.iterations?.length || 0})</span>,
              children: currentTask.iterations?.length > 0 ? (
                <Timeline
                  items={currentTask.iterations.map((iter: any, i: number) => ({
                    color: iter.score >= 0.8 ? 'green' : iter.score >= 0.6 ? 'blue' : 'red',
                    children: (
                      <Card key={i} size="small" style={{ marginBottom: 8 }}>
                        <Space style={{ marginBottom: 8 }}>
                          <Tag color={iter.score >= 0.8 ? 'success' : iter.score >= 0.6 ? 'warning' : 'error'}>
                            第 {iter.iteration + 1} 轮
                          </Tag>
                          <Progress
                            percent={Math.round(iter.score * 100)}
                            size="small"
                            style={{ width: 120 }}
                            status={iter.score >= 0.8 ? 'success' : 'active'}
                          />
                        </Space>
                        {iter.improvements?.length > 0 && (
                          <div>
                            <Text type="secondary" style={{ fontSize: 12 }}>改进建议：</Text>
                            <ul style={{ margin: '4px 0', paddingLeft: 20, fontSize: 13 }}>
                              {iter.improvements.map((imp: string, j: number) => (
                                <li key={j}>{imp}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </Card>
                    ),
                  }))}
                />
              ) : <Empty description="无迭代记录" />,
            },
            {
              key: 'subtasks',
              label: <span><TeamOutlined /> 子任务 ({currentTask.sub_tasks?.length || 0})</span>,
              children: currentTask.sub_tasks?.length > 0 ? (
                <Table
                  size="small"
                  pagination={false}
                  dataSource={currentTask.sub_tasks}
                  rowKey="id"
                  columns={[
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 100,
                      render: (status: string) => getStatusTag(status),
                    },
                    {
                      title: '任务',
                      dataIndex: 'description',
                      render: (text: string, record: any) => (
                        <Space direction="vertical" size={2}>
                          <Text>{text}</Text>
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {record.assigned_agent || '自动分配'} · 尝试 {record.attempts || 0} 次
                          </Text>
                        </Space>
                      ),
                    },
                    {
                      title: '依赖',
                      dataIndex: 'dependencies',
                      width: 160,
                      render: (deps: string[]) => deps?.length ? deps.map(dep => <Tag key={dep}>{dep}</Tag>) : <Text type="secondary">无</Text>,
                    },
                  ]}
                  expandable={{
                    expandedRowRender: (record: any) => (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        {record.acceptance_criteria?.length > 0 && (
                          <div>
                            <Text type="secondary">验收标准</Text>
                            <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                              {record.acceptance_criteria.map((item: string, i: number) => <li key={i}>{item}</li>)}
                            </ul>
                          </div>
                        )}
                        {record.error && <Alert type="error" showIcon message={record.error} />}
                        {record.result && (
                          <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12 }}>
                            {record.result}
                          </pre>
                        )}
                      </Space>
                    ),
                  }}
                />
              ) : <Empty description="暂无子任务状态" />,
            },
            {
              key: 'mail',
              label: <span><MailOutlined /> Agent 通信</span>,
              children: currentTask.mail_log ? (
                <Card size="small" style={{ background: '#fafafa' }}>
                  <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 13, margin: 0, maxHeight: 500, overflow: 'auto' }}>
                    {currentTask.mail_log}
                  </pre>
                </Card>
              ) : <Empty description="无通信记录" />,
            },
            {
              key: 'files',
              label: <span><FolderOpenOutlined /> 产物文件</span>,
              children: <TaskFilesPanel taskId={currentTask.task_id} />,
            },
            {
              key: 'trace',
              label: <span><DeploymentUnitOutlined /> 执行树</span>,
              children: (
                <TaskTracePanel
                  taskId={currentTask.task_id}
                  task={currentTask}
                  onRetryTask={handleRetryTask}
                  retrying={retryingTaskId === currentTask.task_id}
                  retryDisabled={executing && retryingTaskId !== currentTask.task_id}
                />
              ),
            },
            {
              key: 'events',
              label: <span><ClockCircleOutlined /> 事件日志</span>,
              children: <TaskEventsPanel taskId={currentTask.task_id} />,
            },
            ...(suggestions.length > 0 ? [{
              key: 'suggestions',
              label: <span><BulbOutlined /> 优化建议 ({suggestions.length})</span>,
              children: (
                <Timeline
                  items={suggestions.map((s: any, i: number) => ({
                    color: s.priority === 2 ? 'red' : s.priority === 1 ? 'blue' : 'green',
                    children: (
                      <Card key={i} size="small" style={{ marginBottom: 8 }}>
                        <Space>
                          <Tag color={
                            s.type === 'performance' ? 'red' :
                            s.type === 'security' ? 'orange' :
                            s.type === 'code_quality' ? 'blue' :
                            s.type === 'architecture' ? 'purple' : 'green'
                          }>
                            {s.type}
                          </Tag>
                          <Text strong>{s.title}</Text>
                          <Tooltip title={`置信度: ${Math.round(s.confidence * 100)}%`}>
                            <Progress percent={Math.round(s.confidence * 100)} size="small" style={{ width: 80 }} />
                          </Tooltip>
                        </Space>
                        <div style={{ marginTop: 8 }}>{s.description}</div>
                        {s.code_snippet && (
                          <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, fontSize: 12 }}>
                            {s.code_snippet}
                          </pre>
                        )}
                      </Card>
                    ),
                  }))}
                />
              ),
            }] : []),
          ]} />
        </Card>
      )}

      <Card
        title={
          <Space>
            历史任务
            {taskHistoryCounts.attention > 0 && (
              <Badge count={taskHistoryCounts.attention} title="需要人工介入的任务" />
            )}
          </Space>
        }
        style={{ marginTop: 16 }}
      >
        {tasks.length === 0 ? (
          <Empty description="暂无执行记录" />
        ) : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(138px, 1fr))', gap: 8, marginBottom: 16 }}>
              {historyStatusItems.map(item => (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={taskHistoryFilter === item.key}
                  onClick={() => setTaskHistoryFilter(item.key)}
                  style={{
                    border: taskHistoryFilter === item.key ? `1px solid ${item.color}` : '1px solid #f0f0f0',
                    borderRadius: 8,
                    background: taskHistoryFilter === item.key ? `${item.color}10` : '#fff',
                    padding: '10px 12px',
                    cursor: 'pointer',
                    textAlign: 'left',
                    minHeight: 72,
                  }}
                >
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Space style={{ color: item.color }}>
                      {item.icon}
                      <Text style={{ color: item.color }}>{item.label}</Text>
                    </Space>
                    <Text strong style={{ fontSize: 22, lineHeight: '24px' }}>{item.count}</Text>
                  </Space>
                </button>
              ))}
            </div>
            {isMobile ? (
              <Select
                value={taskHistoryFilter}
                onChange={setTaskHistoryFilter}
                options={taskHistoryFilterOptions}
                style={{ width: '100%', marginBottom: 16 }}
              />
            ) : (
              <Segmented
                value={taskHistoryFilter}
                onChange={value => setTaskHistoryFilter(value as string)}
                options={taskHistoryFilterOptions}
                style={{ marginBottom: 16 }}
              />
            )}
            {filteredTasks.length === 0 ? (
              <Empty description="暂无匹配任务" />
            ) : (
              <List
                dataSource={filteredTasks}
                renderItem={(task: any) => (
                  <List.Item
                    actions={[
                      <Button
                        key="view"
                        size="small"
                        icon={<EyeOutlined />}
                        onClick={() => handleSelectHistoryTask(task)}
                      >
                        查看
                      </Button>,
                      canRetryTask(task) ? (
                        <Button
                          key="retry"
                          size="small"
                          icon={<ReloadOutlined />}
                          loading={retryingTaskId === task.task_id}
                          disabled={executing && retryingTaskId !== task.task_id}
                          onClick={() => handleRetryTask(task)}
                        >
                          重试
                        </Button>
                      ) : null,
                    ].filter(Boolean)}
                  >
                    <List.Item.Meta
                      title={
                        <Space wrap>
                          {getStatusIcon(task.status)}
                          <Text>{trimTaskText(task.description, 56)}</Text>
                          {getTaskHistoryStatusTags(task)}
                        </Space>
                      }
                      description={
                        <Space direction="vertical" size={2} style={{ width: '100%' }}>
                          <Space wrap>
                            <Text type="secondary">
                              {task.sub_tasks_count ?? '-'} 个子任务
                            </Text>
                            <Text type="secondary">
                              {dayjs(task.created_at).format('YYYY-MM-DD HH:mm')}
                            </Text>
                            {task.updated_at && (
                              <Text type="secondary">
                                更新 {dayjs(task.updated_at).format('HH:mm')}
                              </Text>
                            )}
                          </Space>
                          {task.last_failure?.message && task.status !== 'completed' && (
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              最近失败：{trimTaskText(task.last_failure.message, 120)}
                            </Text>
                          )}
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </>
        )}
      </Card>

      <Modal
        title="任务需要你的指导"
        open={feedbackModalOpen}
        onCancel={() => setFeedbackModalOpen(false)}
        onOk={handleSubmitFeedback}
        okText="提交反馈"
        cancelText="跳过（自动继续）"
        confirmLoading={submittingFeedback}
      >
        {feedbackInfo && (
          <div style={{ marginBottom: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text>
                第 {(feedbackInfo.iteration || 0) + 1} 轮迭代评分：
                <Tag color={feedbackInfo.score >= 0.6 ? 'warning' : 'error'} style={{ marginLeft: 8 }}>
                  {(feedbackInfo.score * 100).toFixed(0)}%
                </Tag>
              </Text>
              {feedbackInfo.improvements?.length > 0 && (
                <div>
                  <Text type="secondary">AI 建议的改进方向：</Text>
                  <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                    {feedbackInfo.improvements.map((imp: string, i: number) => (
                      <li key={i}><Text style={{ fontSize: 13 }}>{imp}</Text></li>
                    ))}
                  </ul>
                </div>
              )}
            </Space>
            <Divider style={{ margin: '12px 0' }} />
            <Text>请输入你的指导意见（将注入下一轮迭代）：</Text>
            <TextArea
              value={feedbackText}
              onChange={e => setFeedbackText(e.target.value)}
              placeholder="例如：请重点关注代码的错误处理..."
              autoSize={{ minRows: 3, maxRows: 6 }}
              style={{ marginTop: 8 }}
            />
          </div>
        )}
      </Modal>
    </div>
  );
};
