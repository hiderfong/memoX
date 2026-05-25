import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { Layout, Menu, Typography, Card, Button, Upload, List, Space, Avatar, Input, message, Spin, Tag, Progress, Badge, Drawer, Timeline, Alert, Empty, Tooltip, Form, Divider, Checkbox, Modal, Tabs, Table, Select, Slider, InputNumber, AutoComplete, Switch, Segmented } from 'antd';
import { UploadOutlined, FileTextOutlined, RobotOutlined, MessageOutlined, TeamOutlined, SettingOutlined, CloudUploadOutlined, DeleteOutlined, SendOutlined, LoadingOutlined, BulbOutlined, ThunderboltOutlined, ClockCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, InboxOutlined, UserOutlined, LockOutlined, LogoutOutlined, SafetyCertificateOutlined, LinkOutlined, FolderOpenOutlined, MailOutlined, LineChartOutlined, FileSearchOutlined, EyeOutlined, SaveOutlined, DownOutlined, UpOutlined, PlusOutlined, EditOutlined, DownloadOutlined, BgColorsOutlined, ReloadOutlined, RollbackOutlined, ExclamationCircleOutlined, ToolOutlined, DeploymentUnitOutlined } from '@ant-design/icons';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip as RTooltip, Legend, ResponsiveContainer } from 'recharts';
import { useNavigate, useLocation, Routes, Route, Link, Navigate } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';
import { I2VModal } from '../components/I2VModal';
import { WorkflowsPage } from '../pages/WorkflowsPage';
import { MOBILE_BREAKPOINT, useIsMobile, API_BASE, KnowledgeGroup, ReadinessStatus, SystemCheck, SystemHealthReport, BackupArchiveSummary, OpsEvent, OpsEventsResponse, LifecycleCleanupResult, statusTagColor, statusBadge, statusLabel, opsEventLabel, opsEventTypeOptions, opsEventStatusOptions, opsEventActorLabel, formatBytes, formatDuration, AuthUser, AuthContextType, AuthContext, TOKEN_KEY, USER_KEY, api } from '../shared';

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

  const handleRetryTask = async (task: any) => {
    if (!task?.task_id || executing || retryingTaskId) return;
    setExecuting(true);
    setRetryingTaskId(task.task_id);
    try {
      const res = await api.retryTask(task.task_id);
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
      case 'running': return <LoadingOutlined style={{ color: '#1890ff' }} />;
      default: return <ClockCircleOutlined style={{ color: '#999' }} />;
    }
  };

  const getStatusTag = (status: string) => {
    const config: Record<string, { color: string; text: string }> = {
      pending: { color: 'default', text: '等待中' },
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
              <Tag color={currentTask.final_score >= 0.8 ? 'success' : currentTask.final_score >= 0.6 ? 'warning' : 'error'}>
                评分 {(currentTask.final_score * 100).toFixed(0)}%
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
          <Tabs defaultActiveKey="result" items={[
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
