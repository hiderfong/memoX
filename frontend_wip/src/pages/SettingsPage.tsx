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

// ==================== 设置页面 ====================

export const SettingsPage: React.FC = () => {
  const isMobile = useIsMobile();
  const [memoryConfig, setMemoryConfig] = useState<any>(null);
  const [providerStatuses, setProviderStatuses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

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

