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

// ==================== Worker Token 图表 ====================

const CHART_COLORS = ['#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1', '#13c2c2'];

export const WorkerTokenCharts: React.FC<{ workers: any[] }> = ({ workers }) => {
  if (workers.length === 0) {
    return <Empty description="暂无 Worker 数据" style={{ marginTop: 40 }} />;
  }

  // 总计数据
  const totalInput = workers.reduce((s: number, w: any) => s + (w.token_usage?.input_tokens || 0), 0);
  const totalOutput = workers.reduce((s: number, w: any) => s + (w.token_usage?.output_tokens || 0), 0);
  const totalTokens = totalInput + totalOutput;
  const totalCalls = workers.reduce((s: number, w: any) => s + (w.token_usage?.call_count || 0), 0);

  // 每个 Worker 的输入/输出分布
  const barData = workers.map((w: any) => ({
    name: w.display_name || w.id,
    输入: w.token_usage?.input_tokens || 0,
    输出: w.token_usage?.output_tokens || 0,
    总计: (w.token_usage?.total_tokens || 0),
  }));

  const pieData = [
    { name: '输入 Token', value: totalInput },
    { name: '输出 Token', value: totalOutput },
  ].filter(d => d.value > 0);

  return (
    <div style={{ marginTop: 16 }}>
      {/* 总体统计 */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 24, flexWrap: 'wrap' }}>
        {[
          { label: '总调用次数', value: totalCalls, color: '#1890ff' },
          { label: '总 Token 消耗', value: totalTokens.toLocaleString(), color: '#52c41a' },
          { label: '输入 Token', value: totalInput.toLocaleString(), color: '#1890ff' },
          { label: '输出 Token', value: totalOutput.toLocaleString(), color: '#faad14' },
        ].map(s => (
          <Card key={s.label} size="small" style={{ minWidth: 140, flex: 1 }}>
            <div style={{ color: '#999', fontSize: 12 }}>{s.label}</div>
            <div style={{ color: s.color, fontSize: 20, fontWeight: 600 }}>{s.value}</div>
          </Card>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 24 }}>
        {/* Token 消耗柱状图 */}
        <Card title="各 Worker Token 消耗对比" size="small">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={barData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <XAxis dataKey="name" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <RTooltip formatter={(v: number) => v.toLocaleString()} />
              <Legend />
              <Bar dataKey="输入" stackId="a" fill="#1890ff" name="输入" />
              <Bar dataKey="输出" stackId="a" fill="#52c41a" name="输出" />
            </BarChart>
          </ResponsiveContainer>
        </Card>

        {/* 输入/输出占比饼图 */}
        <Card title="总体 Token 输入/输出占比" size="small">
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={90}
                  paddingAngle={3}
                  dataKey="value"
                  label={({ name, percent }) => `${name} ${(percent * 100).toFixed(1)}%`}
                  labelLine={false}
                >
                  {pieData.map((_, i) => (
                    <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <RTooltip formatter={(v: number) => v.toLocaleString()} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <Empty description="暂无数据" />
          )}
        </Card>
      </div>
    </div>
  );
};


// ==================== Worker 日志查看器 ====================

export const WorkerLogViewer: React.FC<{ workers: any[]; onRefresh: () => void }> = ({ workers, onRefresh }) => {
  const [selectedWorker, setSelectedWorker] = useState<string | null>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  const fetchLogs = async (workerId: string) => {
    setLoading(true);
    try {
      const res = await api.getWorkerLogs(workerId, 100);
      setLogs(res.data.logs || []);
    } catch (err: any) {
      message.error('获取日志失败: ' + (err.response?.data?.detail || err.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (selectedWorker) {
      fetchLogs(selectedWorker);
      if (autoRefresh) {
        const interval = setInterval(() => fetchLogs(selectedWorker), 3000);
        return () => clearInterval(interval);
      }
    }
  }, [selectedWorker, autoRefresh]);

  useEffect(() => {
    if (autoRefresh) {
      logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoRefresh]);

  const handleClearLogs = async () => {
    if (!selectedWorker) return;
    try {
      await api.clearWorkerLogs(selectedWorker);
      setLogs([]);
      message.success('日志已清空');
      onRefresh();
    } catch (err: any) {
      message.error('清空失败: ' + (err.response?.data?.detail || err.message));
    }
  };

  const levelColor = (level: string) => {
    switch (level) {
      case 'error': return '#f5222d';
      case 'warn': return '#faad14';
      case 'debug': return '#999';
      default: return '#52c41a';
    }
  };

  return (
    <div style={{ marginTop: 8 }}>
      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          placeholder="选择 Worker"
          style={{ width: 200 }}
          value={selectedWorker}
          onChange={v => { setSelectedWorker(v); setLogs([]); }}
          allowClear
          options={workers.map((w: any) => ({ value: w.id, label: w.display_name || w.id }))}
        />
        <Button icon={<ReloadOutlined />} onClick={() => selectedWorker && fetchLogs(selectedWorker)} disabled={!selectedWorker}>
          刷新
        </Button>
        <Button icon={<DeleteOutlined />} danger onClick={handleClearLogs} disabled={!selectedWorker}>
          清空日志
        </Button>
        <Checkbox checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)}>
          自动刷新（3秒）
        </Checkbox>
        {selectedWorker && logs.length > 0 && (
          <Tag>{logs.length} 条日志</Tag>
        )}
      </Space>

      {selectedWorker ? (
        <Card
          styles={{ body: { padding: 0, background: '#1e1e1e', maxHeight: 480, overflow: 'auto' } }}
          size="small"
        >
          {loading && logs.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}><Spin /></div>
          ) : logs.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无日志</div>
          ) : (
            <div style={{ fontFamily: 'monospace', fontSize: 12, padding: 8 }}>
              {logs.map((log: any, i: number) => (
                <div key={i} style={{ color: levelColor(log.level), padding: '2px 0', borderBottom: '1px solid #2a2a2a' }}>
                  <span style={{ color: '#666', marginRight: 8 }}>
                    {dayjs(log.timestamp).format('HH:mm:ss')}
                  </span>
                  <span style={{
                    background: levelColor(log.level),
                    color: '#fff',
                    borderRadius: 3,
                    padding: '0 4px',
                    fontSize: 10,
                    marginRight: 8,
                  }}>
                    {log.level.toUpperCase()}
                  </span>
                  <span style={{ color: '#d4d4d4' }}>{log.message}</span>
                  {log.meta && Object.keys(log.meta).length > 0 && (
                    <span style={{ color: '#888', marginLeft: 8 }}>
                      {Object.entries(log.meta).map(([k, v]) => `${k}=${v}`).join(', ')}
                    </span>
                  )}
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          )}
        </Card>
      ) : (
        <Empty description="请选择 Worker 查看日志" />
      )}
    </div>
  );
};


// ==================== Worker 监控页面 ====================

export const WorkerCard: React.FC<{
  worker: any;
  providers: any[];
  onSaved: () => void;
  onDelete: (id: string) => void;
  workerCount: number;
}> = ({ worker, providers, onSaved, onDelete, workerCount }) => {
  const [editing, setEditing] = useState(false);
  const [provider, setProvider] = useState(worker.provider);
  const [model, setModel] = useState(worker.model);
  const [skills, setSkills] = useState<string[]>(worker.skills || []);
  const [tools, setTools] = useState<string[]>(worker.tools || []);
  const [temperature, setTemperature] = useState(worker.temperature ?? 0.7);
  const [maxTokens, setMaxTokens] = useState(worker.max_tokens ?? 4096);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [skillInput, setSkillInput] = useState('');
  const [toolInput, setToolInput] = useState('');
  const [skillOptions, setSkillOptions] = useState<{ value: string; label: any; data: any }[]>([]);
  const [installModal, setInstallModal] = useState<null | {
    name: string;
    sourceUrl: string;
    stages: { stage: string; message: string; ts: number }[];
    status: 'running' | 'success' | 'error';
    error?: string;
  }>(null);
  const [icon, setIcon] = useState(worker.icon || '');
  const [displayName, setDisplayName] = useState(worker.display_name || '');
  const [iconPickerOpen, setIconPickerOpen] = useState(false);

  // Sync local state from props when not editing (e.g. after polling refresh)
  useEffect(() => {
    if (!editing) {
      setProvider(worker.provider);
      setModel(worker.model);
      setSkills(worker.skills || []);
      setTools(worker.tools || []);
      setTemperature(worker.temperature ?? 0.7);
      setMaxTokens(worker.max_tokens ?? 4096);
      setIcon(worker.icon || '');
      setDisplayName(worker.display_name || '');
    }
  }, [worker, editing]);

  const ICON_OPTIONS = [
    '🤖', '🧠', '💻', '🔬', '📝', '🎨', '🔧', '📊',
    '🚀', '🛡️', '🔍', '📚', '⚡', '🌐', '🎯', '🏗️',
  ];

  const SKILL_DESC: Record<string, string> = {
    'code-review': '代码审查与质量分析',
    'frontend-design-3': '前端界面设计与开发',
    'data-analysis': '数据分析与可视化',
    'docx': 'Word 文档处理',
    'pdf': 'PDF 文档解析',
    'writing': '文本写作与编辑',
    'translation': '多语言翻译',
    'summarization': '文本摘要与总结',
    'coding': '编程与代码生成',
    'reasoning': '逻辑推理与问题分析',
  };
  // 真实可用工具 — 与 src/coordinator/iterative_orchestrator.py:_prepare_workers 里的候选对应
  const TOOL_DESC: Record<string, string> = {
    'read_file': '读取自身沙箱、shared/ 与其他 Agent 沙箱(同任务内)的文件',
    'write_file': '写入自身沙箱的文件',
    'list_files': '列出自身沙箱目录',
    'run_shell': '在自身沙箱内执行 shell 命令',
    'send_mail': '向其他 Agent 发邮件',
    'read_mail': '读取自己的未读邮件',
  };

  const currentProvider = providers.find((p: any) => p.name === provider);
  const modelOptions = currentProvider?.models || [];

  const handleProviderChange = (val: string) => {
    setProvider(val);
    const newP = providers.find((p: any) => p.name === val);
    const newModels = newP?.models || [];
    if (newModels.length > 0 && !newModels.includes(model)) {
      setModel(newModels[0]);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateWorkerConfig(worker.id, {
        provider, model, skills, tools, temperature,
        max_tokens: maxTokens, icon, display_name: displayName,
      });
      message.success(`${displayName || worker.id} 配置已保存`);
      setEditing(false);
      onSaved();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setProvider(worker.provider);
    setModel(worker.model);
    setSkills(worker.skills || []);
    setTools(worker.tools || []);
    setTemperature(worker.temperature ?? 0.7);
    setMaxTokens(worker.max_tokens ?? 4096);
    setIcon(worker.icon || '');
    setDisplayName(worker.display_name || '');
    setShowAdvanced(false);
    setIconPickerOpen(false);
    setEditing(false);
  };

  const addSkill = () => {
    const v = skillInput.trim();
    if (v && !skills.includes(v)) setSkills([...skills, v]);
    setSkillInput('');
    setSkillOptions([]);
  };

  // 识别 GitHub 技能 URL:github.com/owner/repo[/tree/branch/subpath]
  const GITHUB_URL_RE = /^(?:https?:\/\/)?(?:www\.)?github\.com\/[^/\s]+\/[^/\s]+(?:\/(?:tree|blob)\/[^/\s]+)?(?:\/[^\s]*)?$/;

  // 搜索 registry(节流)
  const skillSearchTimer = useRef<number | null>(null);
  const handleSkillSearch = (val: string) => {
    setSkillInput(val);
    if (skillSearchTimer.current) window.clearTimeout(skillSearchTimer.current);

    const trimmed = val.trim();
    const isGitHubUrl = GITHUB_URL_RE.test(trimmed);

    skillSearchTimer.current = window.setTimeout(async () => {
      const fmtStars = (n?: number) => {
        if (typeof n !== 'number') return null;
        if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k';
        return String(n);
      };
      const fmtPushed = (iso?: string) => {
        if (!iso) return null;
        const d = new Date(iso);
        const days = Math.floor((Date.now() - d.getTime()) / 86400000);
        if (days < 1) return '今日';
        if (days < 30) return `${days}d ago`;
        if (days < 365) return `${Math.floor(days / 30)}mo ago`;
        return `${Math.floor(days / 365)}y ago`;
      };

      const buildOptions = (results: any[]) => {
        const items = results.map((r: any) => {
          const stars = fmtStars(r.stars);
          const pushed = fmtPushed(r.pushed_at);
          return {
            value: r.name,
            data: { kind: 'registry', ...r },
            label: (
              <div style={{ display: 'flex', flexDirection: 'column', padding: '2px 0' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                  <span style={{ fontWeight: 500 }}>{r.name}</span>
                  {(stars || pushed) && (
                    <span style={{ fontSize: 11, color: '#aaa', whiteSpace: 'nowrap' }}>
                      {stars && <span>⭐ {stars}</span>}
                      {stars && pushed && <span style={{ margin: '0 4px' }}>·</span>}
                      {pushed && <span>{pushed}</span>}
                    </span>
                  )}
                </div>
                <span style={{ fontSize: 11, color: '#888', whiteSpace: 'normal' }}>{r.description}</span>
              </div>
            ),
          };
        });
        if (isGitHubUrl) {
          items.unshift({
            value: `__url__:${trimmed}`,
            data: { kind: 'url', source_url: trimmed },
            label: (
              <div style={{ display: 'flex', flexDirection: 'column', padding: '2px 0' }}>
                <span style={{ fontWeight: 500 }}>📦 安装此 GitHub URL</span>
                <span style={{ fontSize: 11, color: '#888', whiteSpace: 'normal', wordBreak: 'break-all' }}>{trimmed}</span>
              </div>
            ),
          });
        }
        return items;
      };

      try {
        const res = await api.searchSkills(isGitHubUrl ? '' : val, 10);
        setSkillOptions(buildOptions(res.data?.results || []));
      } catch (err: any) {
        console.error('[skills/search] failed:', err);
        // 搜索挂了也不阻断 URL 安装入口
        setSkillOptions(isGitHubUrl ? buildOptions([]) : []);
        if (err?.response?.status === 404) {
          message.error('搜索接口 404 — 后端需要重启以加载新路由');
        }
      }
    }, 250);
  };

  // 选中推荐项 → 触发 SSE 安装
  const handleSkillSelect = async (_value: string, option: any) => {
    const r = option?.data;
    if (!r) return;
    setSkillInput('');
    setSkillOptions([]);
    const isUrl = r.kind === 'url';
    if (!isUrl && skills.includes(r.name)) {
      message.info(`${r.name} 已在列表中`);
      return;
    }
    setInstallModal({
      name: isUrl ? '(待从 SKILL.md 读取)' : r.name,
      sourceUrl: r.source_url,
      stages: [],
      status: 'running',
    });

    try {
      const token = localStorage.getItem(TOKEN_KEY) || '';
      const resp = await fetch(`${API_BASE}/skills/install`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ source_url: r.source_url, force: false }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop() || '';
        for (const part of parts) {
          const line = part.split('\n').find(l => l.startsWith('data: '));
          if (!line) continue;
          const evt = JSON.parse(line.slice(6));
          setInstallModal(m => m && {
            ...m,
            name: evt.stage === 'success' && evt.name ? evt.name : m.name,
            stages: [...m.stages, { stage: evt.stage, message: evt.message || evt.name || '', ts: Date.now() }],
            status: evt.stage === 'success' ? 'success' : evt.stage === 'error' ? 'error' : m.status,
            error: evt.stage === 'error' ? evt.message : m.error,
          });
          if (evt.stage === 'success') {
            // 安装成功 → 把 skill 名加入当前 worker 的 skills 列表(尚未持久化,需点保存)
            setSkills(prev => prev.includes(evt.name) ? prev : [...prev, evt.name]);
          }
        }
      }
    } catch (err: any) {
      setInstallModal(m => m && { ...m, status: 'error', error: err?.message || String(err) });
    }
  };
  const addTool = () => {
    const v = toolInput.trim();
    if (v && !tools.includes(v)) setTools([...tools, v]);
    setToolInput('');
  };

  const fmtToken = (n: number) => n >= 10000 ? (n / 1000).toFixed(1) + 'k' : String(n);
  const u = worker.token_usage || {};

  return (
    <Card size="small">
      <Space direction="vertical" style={{ width: '100%' }} size={10}>
        {/* 标题行 — 始终显示 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Space>
            <Avatar
              style={{ background: worker.busy ? '#ff4d4f' : '#52c41a', fontSize: (worker.icon || icon) ? 20 : 14 }}
            >
              {(editing ? icon : worker.icon) || <RobotOutlined />}
            </Avatar>
            <div style={{ lineHeight: 1.3 }}>
              <Text strong>{(editing ? displayName : worker.display_name) || worker.id}</Text>
              {(editing ? displayName : worker.display_name) && (
                <div><Text type="secondary" style={{ fontSize: 11 }}>{worker.id}</Text></div>
              )}
            </div>
            <Badge status={worker.busy ? 'error' : 'success'} text={worker.busy ? '忙碌' : '空闲'} />
          </Space>
          <Space size={0}>
            {!editing && (
              <Tooltip title="修改配置">
                <Button type="text" size="small" icon={<EditOutlined />} onClick={() => setEditing(true)} disabled={worker.busy} />
              </Tooltip>
            )}
            <Tooltip title={workerCount <= 1 ? '至少保留一个 Worker' : '删除此 Worker'}>
              <Button type="text" danger size="small" icon={<DeleteOutlined />} disabled={worker.busy || workerCount <= 1} onClick={() => onDelete(worker.id)} />
            </Tooltip>
          </Space>
        </div>

        {/* 状态进度 — 始终显示 */}
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>状态: </Text>
          <Progress
            percent={worker.busy ? 100 : 0}
            status={worker.busy ? 'active' : 'normal'}
            size="small"
            style={{ width: 100, display: 'inline-block', marginLeft: 8 }}
          />
        </div>

        {/* 基本信息 — 非编辑模式显示 */}
        {!editing && (
          <>
            <div style={{ fontSize: 12, color: '#666' }}>
              <Space split={<Divider type="vertical" style={{ margin: '0 4px' }} />}>
                <span>{worker.provider}</span>
                <span>{worker.model}</span>
              </Space>
            </div>
            {worker.skills?.length > 0 && (
              <div>
                <Text type="secondary" style={{ fontSize: 11, marginRight: 4 }}>技能</Text>
                {worker.skills.map((s: string) => (
                  <Tooltip key={s} title={SKILL_DESC[s] || `技能: ${s}`}>
                    <Tag color="blue" style={{ fontSize: 11, cursor: 'default' }}>{s}</Tag>
                  </Tooltip>
                ))}
              </div>
            )}
            {worker.tools?.length > 0 && (
              <div>
                <Text type="secondary" style={{ fontSize: 11, marginRight: 4 }}>工具</Text>
                {worker.tools.map((t: string) => (
                  <Tooltip key={t} title={TOOL_DESC[t] || `工具: ${t}`}>
                    <Tag style={{ fontSize: 11, borderRadius: 2, cursor: 'default' }}>{t}</Tag>
                  </Tooltip>
                ))}
              </div>
            )}
          </>
        )}

        {/* Token 用量 — 始终显示 */}
        <div style={{ background: '#f6f8fa', padding: '6px 10px', borderRadius: 6, fontSize: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', color: '#666' }}>
            <span>调用 <Text strong style={{ fontSize: 12 }}>{u.call_count || 0}</Text> 次</span>
            <span>总计 <Text strong style={{ fontSize: 12 }}>{fmtToken(u.total_tokens || 0)}</Text> tokens</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', color: '#999', marginTop: 2 }}>
            <span>输入: {fmtToken(u.input_tokens || 0)}</span>
            <span>输出: {fmtToken(u.output_tokens || 0)}</span>
          </div>
          {/* Token 消耗可视化条 — CSS bar */}
          {u.total_tokens ? (
            <div style={{ marginTop: 6 }}>
              <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', background: '#e8e8e8' }}>
                <div style={{
                  width: `${u.total_tokens ? Math.min(((u.input_tokens || 0) / (u.total_tokens || 1)) * 100, 100) : 0}%`,
                  background: '#1890ff',
                  borderRadius: '3px 0 0 3px',
                }} />
                <div style={{
                  width: `${u.total_tokens ? Math.min(((u.output_tokens || 0) / (u.total_tokens || 1)) * 100, 100) : 0}%`,
                  background: '#52c41a',
                }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 2, fontSize: 10, color: '#aaa' }}>
                <span><span style={{ color: '#1890ff' }}>■</span> 输入 {Math.round((u.input_tokens || 0) / (u.total_tokens || 1) * 100)}%</span>
                <span><span style={{ color: '#52c41a' }}>■</span> 输出 {Math.round((u.output_tokens || 0) / (u.total_tokens || 1) * 100)}%</span>
              </div>
            </div>
          ) : null}
        </div>

        {/* ========== 编辑模式 ========== */}
        {editing && (
          <>
            <Divider style={{ margin: '4px 0' }} />

            {/* 图标选择 */}
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>头像图标</Text>
              <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {ICON_OPTIONS.map(e => (
                  <span
                    key={e}
                    onClick={() => setIcon(e)}
                    style={{
                      cursor: 'pointer', fontSize: 20, padding: '3px 5px', borderRadius: 6,
                      background: icon === e ? '#e6f7ff' : undefined,
                      border: icon === e ? '1px solid #1890ff' : '1px solid transparent',
                    }}
                  >{e}</span>
                ))}
                <span
                  onClick={() => setIcon('')}
                  style={{ cursor: 'pointer', fontSize: 11, padding: '5px 8px', borderRadius: 6, color: '#999', alignSelf: 'center' }}
                >默认</span>
              </div>
            </div>

            {/* 显示名称 */}
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>显示名称</Text>
              <Input size="small" placeholder={worker.id} value={displayName} onChange={e => setDisplayName(e.target.value)} style={{ marginTop: 4 }} />
            </div>

            {/* Provider */}
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>Provider</Text>
              <Select value={provider} onChange={handleProviderChange} style={{ width: '100%', marginTop: 4 }} size="small">
                {providers.map((p: any) => (
                  <Select.Option key={p.name} value={p.name}>
                    {p.name}{p.configured === false ? '（未配置）' : ''}{p.supported === false ? '（未支持）' : ''}
                  </Select.Option>
                ))}
              </Select>
              {currentProvider?.warnings?.length > 0 && (
                <Text type="danger" style={{ display: 'block', fontSize: 12, marginTop: 4 }}>
                  {currentProvider.warnings.join('；')}
                </Text>
              )}
            </div>

            {/* Model */}
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>Model</Text>
              <Select value={model} onChange={setModel} style={{ width: '100%', marginTop: 4 }} size="small" showSearch>
                {modelOptions.map((m: string) => <Select.Option key={m} value={m}>{m}</Select.Option>)}
              </Select>
            </div>

            {/* Skills */}
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>Skills</Text>
              <div style={{ marginTop: 4 }}>
                {skills.map(s => <Tag key={s} closable onClose={() => setSkills(skills.filter(x => x !== s))} style={{ marginBottom: 4 }}>{s}</Tag>)}
              </div>
              <AutoComplete
                size="small"
                style={{ width: '100%', marginTop: 4 }}
                value={skillInput}
                options={skillOptions}
                onSearch={handleSkillSearch}
                onSelect={handleSkillSelect}
                onChange={(v) => setSkillInput(typeof v === 'string' ? v : '')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && skillOptions.length === 0) {
                    addSkill();
                  }
                }}
                placeholder="输入关键字搜索 → 下拉选择自动安装;无结果时回车按名添加"
                popupMatchSelectWidth={360}
                notFoundContent={skillInput ? <span style={{ color: '#999', fontSize: 12 }}>未在 registry 中匹配到,回车将直接添加名称</span> : null}
              />
            </div>

            {/* 高级选项 */}
            <Button type="link" size="small" icon={showAdvanced ? <UpOutlined /> : <DownOutlined />} onClick={() => setShowAdvanced(!showAdvanced)} style={{ padding: 0 }}>
              高级选项
            </Button>

            {showAdvanced && (
              <div style={{ background: '#fafafa', padding: 12, borderRadius: 6 }}>
                <Space direction="vertical" style={{ width: '100%' }} size={10}>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      Tools <span style={{ color: '#aaa', fontSize: 11 }}>(留空 = 全部可用;勾选 = 仅白名单内可调用)</span>
                    </Text>
                    <Select
                      mode="multiple"
                      size="small"
                      style={{ width: '100%', marginTop: 4 }}
                      value={tools}
                      onChange={setTools}
                      placeholder="选择允许的工具;留空表示不限制"
                      options={Object.entries(TOOL_DESC).map(([k, v]) => ({
                        value: k,
                        label: <span><b>{k}</b> <span style={{ color: '#999', fontSize: 11 }}>— {v}</span></span>,
                      }))}
                    />
                  </div>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>Temperature: {temperature}</Text>
                    <Slider min={0} max={2} step={0.1} value={temperature} onChange={setTemperature} />
                  </div>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>Max Tokens</Text>
                    <InputNumber size="small" min={256} max={128000} step={256} value={maxTokens} onChange={v => setMaxTokens(v || 4096)} style={{ width: '100%', marginTop: 4 }} />
                  </div>
                </Space>
              </div>
            )}

            {/* 保存/取消 */}
            <Space style={{ width: '100%' }}>
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave} size="small">
                保存
              </Button>
              <Button size="small" onClick={handleCancel}>取消</Button>
            </Space>
          </>
        )}
      </Space>

      {/* 安装进度 Modal */}
      <Modal
        open={!!installModal}
        title={installModal ? `安装技能: ${installModal.name}` : ''}
        onCancel={() => installModal?.status !== 'running' && setInstallModal(null)}
        maskClosable={installModal?.status !== 'running'}
        closable={installModal?.status !== 'running'}
        footer={installModal?.status === 'running' ? null : (
          <Button type="primary" onClick={() => setInstallModal(null)}>关闭</Button>
        )}
      >
        {installModal && (
          <div>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 8, wordBreak: 'break-all' }}>
              来源: {installModal.sourceUrl}
            </div>
            <Timeline
              items={[
                ...installModal.stages.map(s => ({
                  color: s.stage === 'error' ? 'red' : s.stage === 'success' ? 'green' : 'blue',
                  children: (<span style={{ fontSize: 12 }}><b>{s.stage}</b> — {s.message}</span>) as React.ReactNode,
                })),
                ...(installModal.status === 'running'
                  ? [{ color: 'gray', children: (<Spin size="small" />) as React.ReactNode }]
                  : []),
              ]}
            />
            {installModal.status === 'success' && (
              <Alert
                type="success"
                showIcon
                message="安装完成"
                description={`已添加到当前 Worker 的技能列表,点击"保存"按钮提交配置。`}
                style={{ marginTop: 8 }}
              />
            )}
            {installModal.status === 'error' && (
              <Alert type="error" showIcon message="安装失败" description={installModal.error} style={{ marginTop: 8 }} />
            )}
          </div>
        )}
      </Modal>
    </Card>
  );
};

export const WorkersPage: React.FC = () => {
  const [workers, setWorkers] = useState<any[]>([]);
  const [providers, setProviders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newProvider, setNewProvider] = useState('');
  const [newModel, setNewModel] = useState('');

  const fetchWorkers = async () => {
    try {
      const res = await api.listWorkers();
      setWorkers(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const fetchProviders = async () => {
    try {
      const res = await api.listProviders();
      setProviders(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    fetchWorkers();
    fetchProviders();
    const interval = setInterval(fetchWorkers, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleCreate = async () => {
    if (!newName.trim() || !newProvider || !newModel) return;
    setCreating(true);
    try {
      await api.createWorker({ name: newName.trim(), provider: newProvider, model: newModel });
      message.success(`Worker '${newName.trim()}' 已创建`);
      setCreateOpen(false);
      setNewName('');
      setNewProvider('');
      setNewModel('');
      await fetchWorkers();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    Modal.confirm({
      title: `确认删除 Worker "${id}"？`,
      content: '删除后将从配置中移除，不可恢复。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await api.deleteWorker(id);
          message.success(`Worker '${id}' 已删除`);
          await fetchWorkers();
        } catch (err: any) {
          message.error(err.response?.data?.detail || '删除失败');
        }
      },
    });
  };

  // 新建弹窗中 provider 变化时联动 model
  const createProviderModels = providers.find(p => p.name === newProvider)?.models || [];
  const providerWarnings = providers.filter((p: any) => (p.used_by || []).length > 0 && (p.warnings || []).length > 0);

  return (
    <Card
      title="Agent Worker 状态"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新增 Worker
        </Button>
      }
    >
      <Alert
        message="Worker Agent 池"
        description="每个 Worker Agent 可以独立配置不同的大模型、技能和工具。任务会自动分配给空闲的 Worker 执行。"
        type="info"
        style={{ marginBottom: 16 }}
      />
      {providerWarnings.length > 0 && (
        <Alert
          message="部分 Provider 需要处理"
          description={providerWarnings.map((p: any) => `${p.name}: ${(p.warnings || []).join('；')}`).join(' / ')}
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      <Tabs
        defaultActiveKey="cards"
        style={{ marginBottom: 0 }}
        items={[
          {
            key: 'cards',
            label: 'Worker 名片',
            children: loading ? (
              <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 16 }}>
                {workers.map((worker: any) => (
                  <WorkerCard
                    key={worker.id}
                    worker={worker}
                    providers={providers}
                    onSaved={fetchWorkers}
                    onDelete={handleDelete}
                    workerCount={workers.length}
                  />
                ))}
              </div>
            ),
          },
          {
            key: 'tokens',
            label: 'Token 统计',
            children: <WorkerTokenCharts workers={workers} />,
          },
          {
            key: 'logs',
            label: '运行日志',
            children: <WorkerLogViewer workers={workers} onRefresh={fetchWorkers} />,
          },
        ]}
      />

      {/* 新增 Worker 弹窗 */}
      <Modal
        title="新增 Worker"
        open={createOpen}
        onCancel={() => { setCreateOpen(false); setNewName(''); setNewProvider(''); setNewModel(''); }}
        onOk={handleCreate}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
        okButtonProps={{ disabled: !newName.trim() || !newProvider || !newModel }}
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>名称（字母、数字、下划线）</Text>
            <Input
              placeholder="如 my_worker"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>Provider</Text>
            <Select
              value={newProvider || undefined}
              placeholder="选择 Provider"
              onChange={(v: string) => { setNewProvider(v); setNewModel(''); }}
              style={{ width: '100%', marginTop: 4 }}
            >
              {providers.map((p: any) => (
                <Select.Option
                  key={p.name}
                  value={p.name}
                  disabled={p.configured === false || p.supported === false}
                >
                  {p.name}{p.configured === false ? '（未配置）' : ''}{p.supported === false ? '（未支持）' : ''}
                </Select.Option>
              ))}
            </Select>
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>Model</Text>
            <Select
              value={newModel || undefined}
              placeholder="选择模型"
              onChange={setNewModel}
              style={{ width: '100%', marginTop: 4 }}
              disabled={!newProvider}
              showSearch
            >
              {createProviderModels.map((m: string) => (
                <Select.Option key={m} value={m}>{m}</Select.Option>
              ))}
            </Select>
          </div>
          <Text type="secondary" style={{ fontSize: 11 }}>
            创建后可在 Worker 名片中配置技能、工具等高级选项
          </Text>
        </Space>
      </Modal>
    </Card>
  );
};
