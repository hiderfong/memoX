import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { Layout, Menu, Typography, Card, Button, Upload, List, Space, Avatar, Input, message, Spin, Tag, Progress, Badge, Drawer, Timeline, Alert, Empty, Tooltip, Form, Divider, Checkbox, Modal, Tabs, Table, Select, Slider, InputNumber, AutoComplete, Switch, Segmented } from 'antd';
import { UploadOutlined, FileTextOutlined, RobotOutlined, MessageOutlined, TeamOutlined, SettingOutlined, CloudUploadOutlined, DeleteOutlined, SendOutlined, LoadingOutlined, BulbOutlined, ThunderboltOutlined, ClockCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, InboxOutlined, UserOutlined, LockOutlined, LogoutOutlined, SafetyCertificateOutlined, LinkOutlined, FolderOpenOutlined, MailOutlined, LineChartOutlined, FileSearchOutlined, EyeOutlined, SaveOutlined, DownOutlined, UpOutlined, PlusOutlined, EditOutlined, DownloadOutlined, BgColorsOutlined, ReloadOutlined, RollbackOutlined, ExclamationCircleOutlined, ToolOutlined, DeploymentUnitOutlined } from '@ant-design/icons';
import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip as RTooltip, Legend, ResponsiveContainer } from 'recharts';
import { useNavigate, useLocation, Routes, Route, Link, Navigate } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';
import { I2VModal } from '../components/I2VModal';
import { WorkflowsPage } from '../pages/WorkflowsPage';
import { MOBILE_BREAKPOINT, useIsMobile, API_BASE, KnowledgeGroup, ReadinessStatus, SystemCheck, SystemHealthReport, BackupArchiveSummary, OpsEvent, OpsEventsResponse, LifecycleCleanupResult, ToolPolicyResponse, statusTagColor, statusBadge, statusLabel, opsEventLabel, opsEventTypeOptions, opsEventStatusOptions, opsEventActorLabel, formatBytes, formatDuration, AuthUser, AuthContextType, AuthContext, TOKEN_KEY, USER_KEY, api } from '../shared';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;
const { TextArea } = Input;
const { Dragger } = Upload;

// ==================== 设置页面 ====================

export const SettingsPage: React.FC = () => {
  const isMobile = useIsMobile();
  const [memoryConfig, setMemoryConfig] = useState<any>(null);
  const [providerStatuses, setProviderStatuses] = useState<any[]>([]);
  const [toolPolicy, setToolPolicy] = useState<ToolPolicyResponse | null>(null);
  const [toolPolicyHostsText, setToolPolicyHostsText] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toolPolicySaving, setToolPolicySaving] = useState(false);

  const fetchConfig = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/memory/config`);
      setMemoryConfig(res.data);
    } catch {
      message.error('获取配置失败');
    }
    try {
      const res = await api.listProviders();
      setProviderStatuses(res.data || []);
    } catch {
      message.error('获取 Provider 状态失败');
    }
    try {
      const res = await api.getToolPolicy();
      setToolPolicy(res.data);
      setToolPolicyHostsText((res.data.network.allow_internal_hosts || []).join('\n'));
    } catch {
      message.error('获取工具策略失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConfig();
  }, []);

  const handleSaveMemory = async (updates: any) => {
    setSaving(true);
    try {
      await axios.patch(`${API_BASE}/memory/config`, updates);
      message.success('配置已保存');
      fetchConfig();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const updateToolPolicyDraft = (updater: (policy: ToolPolicyResponse) => ToolPolicyResponse) => {
    setToolPolicy(current => current ? updater(current) : current);
  };

  const handleAddDataSource = () => {
    updateToolPolicyDraft(policy => ({
      ...policy,
      database: {
        ...policy.database,
        data_sources: [
          ...policy.database.data_sources,
          { name: `source_${policy.database.data_sources.length + 1}`, connection_string: '', redacted: false },
        ],
      },
    }));
  };

  const handleUpdateDataSource = (index: number, field: 'name' | 'connection_string', value: string) => {
    updateToolPolicyDraft(policy => ({
      ...policy,
      database: {
        ...policy.database,
        data_sources: policy.database.data_sources.map((source, itemIndex) => (
          itemIndex === index
            ? { ...source, [field]: value, redacted: field === 'connection_string' ? false : source.redacted }
            : source
        )),
      },
    }));
  };

  const handleRemoveDataSource = (index: number) => {
    updateToolPolicyDraft(policy => ({
      ...policy,
      database: {
        ...policy.database,
        data_sources: policy.database.data_sources.filter((_, itemIndex) => itemIndex !== index),
      },
    }));
  };

  const handleSaveToolPolicy = async () => {
    if (!toolPolicy) return;
    const allowInternalHosts = toolPolicyHostsText
      .split(/\r?\n/)
      .map(item => item.trim())
      .filter(Boolean);
    const payload: ToolPolicyResponse = {
      ...toolPolicy,
      network: { allow_internal_hosts: allowInternalHosts },
      database: {
        ...toolPolicy.database,
        data_sources: toolPolicy.database.data_sources
          .map(source => ({
            name: source.name.trim(),
            connection_string: source.connection_string.trim(),
            redacted: !!source.redacted,
          }))
          .filter(source => source.name && source.connection_string),
      },
    };

    setToolPolicySaving(true);
    try {
      const res = await api.updateToolPolicy(payload);
      setToolPolicy(res.data.tool_policy);
      setToolPolicyHostsText((res.data.tool_policy.network.allow_internal_hosts || []).join('\n'));
      message.success('工具策略已保存');
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '保存工具策略失败');
    } finally {
      setToolPolicySaving(false);
    }
  };

  return (
    <div>
      <Title level={4}>⚙️ 设置</Title>

      {/* 记忆管理 */}
      <Card title="🧠 记忆管理" style={{ marginBottom: 16, maxWidth: 720 }} loading={loading}>
        {memoryConfig && (
          <Form layout="vertical">
            <Form.Item label="启用记忆管理">
              <Switch
                checked={memoryConfig.enabled}
                onChange={v => handleSaveMemory({ enabled: v })}
                loading={saving}
              />
              <Text type="secondary" style={{ marginLeft: 12 }}>
                {memoryConfig.enabled ? '已开启 — 对话将在超过一定轮数后自动压缩摘要' : '已关闭'}
              </Text>
            </Form.Item>

            {memoryConfig.enabled && (
              <>
                <Form.Item label={`摘要触发轮数（当前: ${memoryConfig.max_turns_before_compress} 轮）`}>
                  <Slider
                    min={3}
                    max={30}
                    value={memoryConfig.max_turns_before_compress}
                    onAfterChange={v => handleSaveMemory({ max_turns_before_compress: v })}
                    marks={{ 5: '5', 10: '10', 20: '20', 30: '30' }}
                    disabled={saving}
                  />
                </Form.Item>

                <Form.Item label={`摘要最大字符数（当前: ${memoryConfig.summary_max_chars} 字）`}>
                  <Slider
                    min={100}
                    max={2000}
                    step={50}
                    value={memoryConfig.summary_max_chars}
                    onAfterChange={v => handleSaveMemory({ summary_max_chars: v })}
                    marks={{ 200: '200', 500: '500', 1000: '1000', 2000: '2000' }}
                    disabled={saving}
                  />
                </Form.Item>

                <Form.Item label={`摘要后保留最近消息数（当前: ${memoryConfig.recent_messages_to_keep} 条）`}>
                  <Slider
                    min={0}
                    max={20}
                    value={memoryConfig.recent_messages_to_keep}
                    onAfterChange={v => handleSaveMemory({ recent_messages_to_keep: v })}
                    marks={{ 0: '0', 4: '4', 10: '10', 20: '20' }}
                    disabled={saving}
                  />
                </Form.Item>
              </>
            )}
          </Form>
        )}
      </Card>

      {/* API Key 配置状态 */}
      <Card title="🔑 Provider API Key 状态" style={{ marginBottom: 16, maxWidth: 720 }}>
        <Alert
          message="API Key 安全说明"
          description="API Key 配置在 config.yaml 或环境变量中，服务端只返回是否已解析到密钥，不暴露实际密钥。"
          type="info"
          style={{ marginBottom: 16 }}
        />
        {providerStatuses.length === 0 ? (
          <Empty description="暂无 Provider 配置" />
        ) : (
          <List
            size="small"
            dataSource={providerStatuses}
            renderItem={(item: any) => {
              const configured = !!item.configured;
              const supported = item.supported !== false;
              const usedBy = item.used_by || [];
              return (
                <List.Item>
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Space wrap>
                      {configured ? (
                        <CheckCircleOutlined style={{ color: '#52c41a' }} />
                      ) : (
                        <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
                      )}
                      <Text strong>{item.name}</Text>
                      {item.env_var && <Text type="secondary" style={{ fontSize: 11 }}>环境变量: {item.env_var}</Text>}
                      <Tag color={configured ? 'green' : 'red'} style={{ fontSize: 11 }}>
                        {configured ? '已配置' : '未配置'}
                      </Tag>
                      {!supported && <Tag color="orange" style={{ fontSize: 11 }}>后端未支持</Tag>}
                    </Space>
                    <Space wrap size={[4, 4]}>
                      {usedBy.length > 0 ? usedBy.map((u: string) => (
                        <Tag key={u} style={{ fontSize: 11 }}>{u}</Tag>
                      )) : <Text type="secondary" style={{ fontSize: 11 }}>当前未被功能引用</Text>}
                    </Space>
                    {(item.warnings || []).map((w: string) => (
                      <Text key={w} type="danger" style={{ fontSize: 12 }}>{w}</Text>
                    ))}
                  </Space>
                </List.Item>
              );
            }}
          />
        )}
      </Card>

      <Card
        title="🛡️ 工具权限策略"
        data-testid="tool-policy-card"
        style={{ marginBottom: 16, maxWidth: 920 }}
        loading={loading}
        extra={(
          <Button
            data-testid="tool-policy-save"
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSaveToolPolicy}
            loading={toolPolicySaving}
            disabled={!toolPolicy}
          >
            保存
          </Button>
        )}
      >
        {toolPolicy && (
          <Form layout="vertical">
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message="工具权限策略"
              description="默认保持公网与只读数据库访问；需要内网访问、写入或 DDL 时在这里显式开启。数据源连接串如含凭证会脱敏显示，未修改时会保留原值。"
            />
            <Form.Item label="允许访问的内网主机">
              <TextArea
                rows={4}
                value={toolPolicyHostsText}
                onChange={event => setToolPolicyHostsText(event.target.value)}
                placeholder="每行一个 host 或 host:port，例如 127.0.0.1:3000"
              />
            </Form.Item>

            <Divider orientation="left">Web 搜索与抓取资源</Divider>
            <Space wrap>
              <Form.Item label="请求超时（秒）" style={{ marginBottom: 0 }}>
                <InputNumber
                  data-testid="web-request-timeout"
                  min={1}
                  max={300}
                  value={toolPolicy.web.request_timeout_seconds}
                  onChange={value => updateToolPolicyDraft(policy => ({
                    ...policy,
                    web: {
                      ...policy.web,
                      request_timeout_seconds: Number(value || 1),
                    },
                  }))}
                />
              </Form.Item>
              <Form.Item label="最大响应字节" style={{ marginBottom: 0 }}>
                <InputNumber
                  data-testid="web-max-response-bytes"
                  min={1024}
                  max={100000000}
                  step={1024}
                  value={toolPolicy.web.max_response_bytes}
                  onChange={value => updateToolPolicyDraft(policy => ({
                    ...policy,
                    web: {
                      ...policy.web,
                      max_response_bytes: Number(value || 1024),
                    },
                  }))}
                />
              </Form.Item>
              <Form.Item label="最大正文字符" style={{ marginBottom: 0 }}>
                <InputNumber
                  data-testid="web-max-fetch-chars"
                  min={100}
                  max={500000}
                  step={100}
                  value={toolPolicy.web.max_fetch_chars}
                  onChange={value => updateToolPolicyDraft(policy => ({
                    ...policy,
                    web: {
                      ...policy.web,
                      max_fetch_chars: Number(value || 100),
                    },
                  }))}
                />
              </Form.Item>
              <Form.Item label="最大搜索结果" style={{ marginBottom: 0 }}>
                <InputNumber
                  data-testid="web-max-search-results"
                  min={1}
                  max={50}
                  value={toolPolicy.web.max_search_results}
                  onChange={value => updateToolPolicyDraft(policy => ({
                    ...policy,
                    web: {
                      ...policy.web,
                      max_search_results: Number(value || 1),
                    },
                  }))}
                />
              </Form.Item>
            </Space>

            <Divider orientation="left">Playwright 爬虫资源</Divider>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Space wrap>
                <Form.Item label="最大并发" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={1}
                    max={20}
                    value={toolPolicy.playwright_crawler.max_concurrency}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      playwright_crawler: {
                        ...policy.playwright_crawler,
                        max_concurrency: Number(value || 1),
                      },
                    }))}
                  />
                </Form.Item>
                <Form.Item label="排队超时（秒）" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={0}
                    max={300}
                    value={toolPolicy.playwright_crawler.queue_timeout_seconds}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      playwright_crawler: {
                        ...policy.playwright_crawler,
                        queue_timeout_seconds: Number(value || 0),
                      },
                    }))}
                  />
                </Form.Item>
                <Form.Item label="总超时（秒）" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={1}
                    max={600}
                    value={toolPolicy.playwright_crawler.total_timeout_seconds}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      playwright_crawler: {
                        ...policy.playwright_crawler,
                        total_timeout_seconds: Number(value || 1),
                      },
                    }))}
                  />
                </Form.Item>
              </Space>
              <Space wrap>
                <Form.Item label="导航超时（毫秒）" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={1000}
                    max={300000}
                    step={1000}
                    value={toolPolicy.playwright_crawler.navigation_timeout_ms}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      playwright_crawler: {
                        ...policy.playwright_crawler,
                        navigation_timeout_ms: Number(value || 1000),
                      },
                    }))}
                  />
                </Form.Item>
                <Form.Item label="选择器超时（毫秒）" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={0}
                    max={300000}
                    step={1000}
                    value={toolPolicy.playwright_crawler.selector_timeout_ms}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      playwright_crawler: {
                        ...policy.playwright_crawler,
                        selector_timeout_ms: Number(value || 0),
                      },
                    }))}
                  />
                </Form.Item>
                <Form.Item label="空闲等待（毫秒）" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={0}
                    max={60000}
                    step={500}
                    value={toolPolicy.playwright_crawler.idle_wait_ms}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      playwright_crawler: {
                        ...policy.playwright_crawler,
                        idle_wait_ms: Number(value || 0),
                      },
                    }))}
                  />
                </Form.Item>
              </Space>
              <Space wrap>
                <Form.Item label="最大页面数" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={1}
                    max={10}
                    value={toolPolicy.playwright_crawler.max_pages}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      playwright_crawler: {
                        ...policy.playwright_crawler,
                        max_pages: Number(value || 1),
                      },
                    }))}
                  />
                </Form.Item>
                <Form.Item label="最大响应字节" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={1024}
                    max={100000000}
                    step={1024}
                    value={toolPolicy.playwright_crawler.max_response_bytes}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      playwright_crawler: {
                        ...policy.playwright_crawler,
                        max_response_bytes: Number(value || 1024),
                      },
                    }))}
                  />
                </Form.Item>
                <Form.Item label="最大输出字符" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={100}
                    max={200000}
                    step={100}
                    value={toolPolicy.playwright_crawler.max_output_chars}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      playwright_crawler: {
                        ...policy.playwright_crawler,
                        max_output_chars: Number(value || 100),
                      },
                    }))}
                  />
                </Form.Item>
              </Space>
            </Space>

            <Divider />
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Space wrap>
                <Form.Item label="数据库默认模式" style={{ marginBottom: 0 }}>
                  <Select
                    value={toolPolicy.database.default_access_mode}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      database: {
                        ...policy.database,
                        default_access_mode: value as ToolPolicyResponse['database']['default_access_mode'],
                      },
                    }))}
                    style={{ width: 160 }}
                  >
                    <Select.Option value="read_only">只读</Select.Option>
                    <Select.Option value="write">写入</Select.Option>
                    <Select.Option value="admin">管理</Select.Option>
                  </Select>
                </Form.Item>
                <Form.Item label="最大返回行数" style={{ marginBottom: 0 }}>
                  <InputNumber
                    min={1}
                    max={10000}
                    value={toolPolicy.database.max_result_rows}
                    onChange={value => updateToolPolicyDraft(policy => ({
                      ...policy,
                      database: { ...policy.database, max_result_rows: Number(value || 1) },
                    }))}
                  />
                </Form.Item>
              </Space>
              <Space wrap>
                <Checkbox
                  checked={toolPolicy.database.allow_raw_connection_strings}
                  onChange={event => updateToolPolicyDraft(policy => ({
                    ...policy,
                    database: { ...policy.database, allow_raw_connection_strings: event.target.checked },
                  }))}
                >
                  允许原始连接串
                </Checkbox>
                <Checkbox
                  checked={toolPolicy.database.allow_write}
                  onChange={event => updateToolPolicyDraft(policy => ({
                    ...policy,
                    database: { ...policy.database, allow_write: event.target.checked },
                  }))}
                >
                  允许显式写入
                </Checkbox>
                <Checkbox
                  checked={toolPolicy.database.allow_ddl}
                  onChange={event => updateToolPolicyDraft(policy => ({
                    ...policy,
                    database: { ...policy.database, allow_ddl: event.target.checked },
                  }))}
                >
                  允许 DDL/admin
                </Checkbox>
                <Checkbox
                  checked={toolPolicy.database.allow_multiple_statements}
                  onChange={event => updateToolPolicyDraft(policy => ({
                    ...policy,
                    database: { ...policy.database, allow_multiple_statements: event.target.checked },
                  }))}
                >
                  允许多语句
                </Checkbox>
              </Space>
            </Space>

            <Divider />
            <Space align="center" style={{ width: '100%', justifyContent: 'space-between', marginBottom: 8 }}>
              <Text strong>命名数据源</Text>
              <Button size="small" icon={<PlusOutlined />} onClick={handleAddDataSource}>添加数据源</Button>
            </Space>
            {toolPolicy.database.data_sources.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无命名数据源" />
            ) : (
              <List
                size="small"
                dataSource={toolPolicy.database.data_sources}
                renderItem={(source, index) => (
                  <List.Item
                    actions={[
                      <Button
                        key="delete"
                        danger
                        size="small"
                        icon={<DeleteOutlined />}
                        onClick={() => handleRemoveDataSource(index)}
                      />,
                    ]}
                  >
                    <Space direction={isMobile ? 'vertical' : 'horizontal'} style={{ width: '100%' }}>
                      <Input
                        value={source.name}
                        onChange={event => handleUpdateDataSource(index, 'name', event.target.value)}
                        placeholder="数据源名称"
                        style={{ width: isMobile ? '100%' : 180 }}
                      />
                      <Input
                        value={source.connection_string}
                        onChange={event => handleUpdateDataSource(index, 'connection_string', event.target.value)}
                        placeholder="SQLAlchemy 连接串或 ${ENV_VAR}"
                        style={{ flex: 1, minWidth: isMobile ? '100%' : 320 }}
                      />
                      {source.redacted && <Tag color="orange">已脱敏</Tag>}
                    </Space>
                  </List.Item>
                )}
              />
            )}
          </Form>
        )}
      </Card>

      {/* 关于 */}
      <Card title="ℹ️ 关于 MemoX" style={{ maxWidth: 720 }}>
        <Space direction="vertical">
          <Text><Text strong>版本:</Text> 0.1.0</Text>
          <Text><Text strong>架构:</Text> Multi-Agent RAG + 知识管理 + 任务调度</Text>
          <Text><Text strong>主要依赖:</Text> FastAPI · ChromaDB · DashScope · React</Text>
          <Text type="secondary">
            MemoX 是一个多 Agent 协作知识管理平台，支持混合检索、语义切片、知识图谱、对话摘要、跨会话记忆和用户偏好学习。
          </Text>
        </Space>
      </Card>
    </div>
  );
};
