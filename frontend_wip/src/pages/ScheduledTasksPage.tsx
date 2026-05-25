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

// ==================== 定时任务页面 ====================

const CRON_PRESETS: { label: string; value: string }[] = [
  { label: '每分钟', value: '* * * * *' },
  { label: '每 5 分钟', value: '*/5 * * * *' },
  { label: '每小时整点', value: '0 * * * *' },
  { label: '每天 09:00', value: '0 9 * * *' },
  { label: '每天 18:00', value: '0 18 * * *' },
  { label: '工作日 09:00', value: '0 9 * * 1-5' },
  { label: '每周一 09:00', value: '0 9 * * 1' },
  { label: '每月 1 号 09:00', value: '0 9 1 * *' },
];

export const ScheduledTasksPage: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<any | null>(null);
  const [editForm, setEditForm] = useState<{ description: string; cron: string; enabled: boolean }>({
    description: '',
    cron: '0 9 * * *',
    enabled: true,
  });
  const [saving, setSaving] = useState(false);

  const fetchList = async () => {
    setLoading(true);
    try {
      const res = await api.listScheduledTasks();
      setItems(res.data);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '加载定时任务失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchList();
    // 来自智能问答的"配置定时任务"预填
    const prefill = (location.state as any)?.prefill;
    const sourceSessionId = (location.state as any)?.sourceSessionId;
    if (prefill && typeof prefill === 'string') {
      setEditing({ __new: true, source_session_id: sourceSessionId || '' });
      setEditForm({ description: prefill, cron: '0 9 * * *', enabled: true });
      navigate(location.pathname, { replace: true, state: {} });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openCreate = () => {
    setEditing({ __new: true });
    setEditForm({ description: '', cron: '0 9 * * *', enabled: true });
  };

  const openEdit = (t: any) => {
    setEditing(t);
    setEditForm({ description: t.description, cron: t.cron, enabled: t.enabled });
  };

  const handleToggle = async (t: any, enabled: boolean) => {
    try {
      await api.updateScheduledTask(t.id, { enabled });
      message.success(enabled ? '已启用' : '已停用');
      fetchList();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '更新失败');
    }
  };

  const handleDelete = (t: any) => {
    Modal.confirm({
      title: '删除定时任务',
      content: `确认删除 "${t.description.slice(0, 30)}..." ？`,
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api.deleteScheduledTask(t.id);
          message.success('已删除');
          fetchList();
        } catch (err: any) {
          message.error(err.response?.data?.detail || '删除失败');
        }
      },
    });
  };

  const handleSave = async () => {
    const desc = editForm.description.trim();
    const cron = editForm.cron.trim();
    if (!desc) { message.warning('任务描述不能为空'); return; }
    if (!cron || cron.split(/\s+/).length !== 5) {
      message.warning('cron 表达式需为 5 段（分 时 日 月 周）');
      return;
    }
    setSaving(true);
    try {
      if (editing?.__new) {
        await api.createScheduledTask({
          description: desc,
          cron,
          enabled: editForm.enabled,
          source_session_id: editing.source_session_id || null,
        });
        message.success('定时任务已创建');
      } else {
        await api.updateScheduledTask(editing.id, {
          description: desc,
          cron,
          enabled: editForm.enabled,
        });
        message.success('已保存');
      }
      setEditing(null);
      fetchList();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const columns = [
    {
      title: '任务描述',
      dataIndex: 'description',
      key: 'description',
      render: (v: string) => (
        <Tooltip title={v}>
          <div style={{ maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</div>
        </Tooltip>
      ),
    },
    {
      title: '执行时间/频率',
      dataIndex: 'cron',
      key: 'cron',
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    {
      title: '下次执行',
      dataIndex: 'next_run_at',
      key: 'next_run_at',
      render: (v: string) => v ? <Text type="secondary" style={{ fontSize: 12 }}>{dayjs(v).format('MM-DD HH:mm')}</Text> : <Text type="secondary">—</Text>,
    },
    {
      title: '上次执行',
      dataIndex: 'last_run_at',
      key: 'last_run_at',
      render: (v: string) => v ? <Text type="secondary" style={{ fontSize: 12 }}>{dayjs(v).format('MM-DD HH:mm')}</Text> : <Text type="secondary">—</Text>,
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      key: 'enabled',
      render: (v: boolean, t: any) => (
        <Checkbox checked={v} onChange={(e) => handleToggle(t, e.target.checked)} />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: any, t: any) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(t)}>编辑</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(t)}>删除</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Card
        title="定时任务"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建定时任务</Button>}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="说明"
          description={<span>cron 格式：<code>分 时 日 月 周</code>（周：0=周日…6=周六）。在智能问答中把会话类型选为"配置定时任务"可直接预填创建。</span>}
        />
        <Table
          rowKey="id"
          loading={loading}
          dataSource={items}
          columns={columns as any}
          pagination={{ pageSize: 10 }}
          locale={{ emptyText: '尚未创建定时任务' }}
        />
      </Card>

      <Modal
        title={editing?.__new ? '新建定时任务' : '编辑定时任务'}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={handleSave}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        destroyOnClose
        width={640}
      >
        <div style={{ marginBottom: 12 }}>
          <Text strong>任务描述</Text>
          <Input.TextArea
            value={editForm.description}
            onChange={(e) => setEditForm(s => ({ ...s, description: e.target.value }))}
            autoSize={{ minRows: 3, maxRows: 8 }}
            placeholder="将作为任务被下发给 worker，请尽量写清楚目标与产物"
          />
        </div>
        <div style={{ marginBottom: 12 }}>
          <Text strong>Cron 表达式</Text>
          <Input
            value={editForm.cron}
            onChange={(e) => setEditForm(s => ({ ...s, cron: e.target.value }))}
            placeholder="分 时 日 月 周，例如 0 9 * * 1-5"
            style={{ fontFamily: 'monospace' }}
          />
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>预设：</Text>
            <Space wrap size={[4, 4]} style={{ marginTop: 4 }}>
              {CRON_PRESETS.map(p => (
                <Tag
                  key={p.value}
                  style={{ cursor: 'pointer' }}
                  color={editForm.cron === p.value ? 'blue' : undefined}
                  onClick={() => setEditForm(s => ({ ...s, cron: p.value }))}
                >
                  {p.label}
                </Tag>
              ))}
            </Space>
          </div>
        </div>
        <div>
          <Checkbox
            checked={editForm.enabled}
            onChange={(e) => setEditForm(s => ({ ...s, enabled: e.target.checked }))}
          >
            创建后立即启用
          </Checkbox>
        </div>
      </Modal>
    </div>
  );
};
