import React, { useState, useEffect } from 'react';
import { Layout, Typography, Card, Button, Upload, Space, Input, message, Tag, Alert, Tooltip, Checkbox, Modal, Table } from 'antd';
import { DeleteOutlined, PlusOutlined, EditOutlined } from '@ant-design/icons';

import { useNavigate, useLocation } from 'react-router-dom';

import dayjs from 'dayjs';

import { useProjectContext } from '../components/ProjectContext';
import { api, KnowledgeGroup } from '../shared';

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
  const [groups, setGroups] = useState<KnowledgeGroup[]>([]);
  const { selectedProjectId, selectedProject, projectGroupIds } = useProjectContext();
  const [editForm, setEditForm] = useState<{ description: string; cron: string; enabled: boolean; activeGroupIds: string[] }>({
    description: '',
    cron: '0 9 * * *',
    enabled: true,
    activeGroupIds: [],
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
    api.listGroups().then(res => setGroups(res.data)).catch(() => {});
    // 来自智能问答的"配置定时任务"预填
    const prefill = (location.state as any)?.prefill;
    const sourceSessionId = (location.state as any)?.sourceSessionId;
    if (prefill && typeof prefill === 'string') {
      setEditing({ __new: true, source_session_id: sourceSessionId || '' });
      setEditForm({ description: prefill, cron: '0 9 * * *', enabled: true, activeGroupIds: projectGroupIds ?? [] });
      navigate(location.pathname, { replace: true, state: {} });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openCreate = () => {
    setEditing({ __new: true });
    setEditForm({ description: '', cron: '0 9 * * *', enabled: true, activeGroupIds: projectGroupIds ?? [] });
  };

  const openEdit = (t: any) => {
    setEditing(t);
    setEditForm({ description: t.description, cron: t.cron, enabled: t.enabled, activeGroupIds: t.active_group_ids || [] });
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
      const scopedGroupIds = projectGroupIds ?? editForm.activeGroupIds;
      if (editing?.__new) {
        await api.createScheduledTask({
          description: desc,
          cron,
          enabled: editForm.enabled,
          active_group_ids: scopedGroupIds,
          project_id: selectedProjectId || undefined,
          source_session_id: editing.source_session_id || null,
        });
        message.success('定时任务已创建');
      } else {
        await api.updateScheduledTask(editing.id, {
          description: desc,
          cron,
          enabled: editForm.enabled,
          active_group_ids: scopedGroupIds,
          project_id: selectedProjectId || undefined,
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

  const groupById = new Map(groups.map(group => [group.id, group]));
  const visibleItems = selectedProjectId
    ? items.filter(item => (item.active_group_ids || []).includes(selectedProjectId))
    : items;
  const renderScopeTags = (ids: string[] = []) => (
    ids.length ? (
      <Space wrap size={[2, 2]}>
        {ids.map(id => {
          const group = groupById.get(id);
          return <Tag key={id} color={group?.color || 'default'}>{group?.name || id}</Tag>;
        })}
      </Space>
    ) : (
      <Tag>全局</Tag>
    )
  );

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
      title: '项目范围',
      dataIndex: 'active_group_ids',
      key: 'active_group_ids',
      render: (ids: string[]) => renderScopeTags(ids || []),
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
          description={<span>cron 格式：<code>分 时 日 月 周</code>（周：0=周日…6=周六）。{selectedProject ? <>当前仅显示 <Tag color={selectedProject.color}>{selectedProject.name}</Tag> 的定时任务。</> : '在智能问答中把会话类型选为"配置定时任务"可直接预填创建。'}</span>}
        />
        <Table
          rowKey="id"
          loading={loading}
          dataSource={visibleItems}
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
        destroyOnHidden
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
          {selectedProject ? (
            <div style={{ marginBottom: 12 }}>
              <Text strong>项目范围</Text>
              <div style={{ marginTop: 6 }}>
                <Tag color={selectedProject.color}>{selectedProject.name}</Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>触发时只检索该项目知识库</Text>
              </div>
            </div>
          ) : groups.length > 1 ? (
            <div style={{ marginBottom: 12 }}>
              <Text strong>项目范围</Text>
              <div style={{ marginTop: 6 }}>
                <Checkbox.Group
                  value={editForm.activeGroupIds}
                  onChange={(vals) => setEditForm(s => ({ ...s, activeGroupIds: vals as string[] }))}
                  options={groups.map(group => ({ label: <Tag color={group.color}>{group.name}</Tag>, value: group.id }))}
                />
              </div>
              <Text type="secondary" style={{ fontSize: 12 }}>不选择则作为全局定时任务保存</Text>
            </div>
          ) : null}
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
