import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { Layout, Menu, Typography, Card, Button, Upload, List, Space, Avatar, Input, message, Spin, Tag, Progress, Badge, Drawer, Timeline, Alert, Empty, Tooltip, Form, Divider } from 'antd';
import { UploadOutlined, FileTextOutlined, RobotOutlined, MessageOutlined, TeamOutlined, SettingOutlined, CloudUploadOutlined, DeleteOutlined, SendOutlined, LoadingOutlined, BulbOutlined, ThunderboltOutlined, ClockCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, InboxOutlined, UserOutlined, LockOutlined, LogoutOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useNavigate, Routes, Route, Link, Navigate } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;
const { TextArea } = Input;
const { Dragger } = Upload;

// ==================== API 配置 ====================

const API_BASE = '/api';

// ==================== 分组类型 ====================

interface KnowledgeGroup {
  id: string;
  name: string;
  color: string;
  created_at: string;
  doc_count: number;
}

// ==================== 认证状态 ====================

interface AuthUser {
  username: string;
  role: string;
  display_name: string;
}

interface AuthContextType {
  user: AuthUser | null;
  token: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  token: null,
  login: async () => {},
  logout: () => {},
});

const TOKEN_KEY = 'memox_token';
const USER_KEY  = 'memox_user';

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

const api = {
  // 文档
  listDocuments: () => axios.get(`${API_BASE}/documents`),
  uploadDocument: (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return axios.post(`${API_BASE}/documents`, formData);
  },
  deleteDocument: (id: string) => axios.delete(`${API_BASE}/documents/${id}`),
  
  // 聊天
  chat: (message: string, sessionId?: string, useRag: boolean = true) =>
    axios.post(`${API_BASE}/chat`, { message, session_id: sessionId, use_rag: useRag, stream: false }),
  chatStream: (message: string, sessionId?: string, useRag: boolean = true) =>
    axios.post(`${API_BASE}/chat/stream`, { message, session_id: sessionId, use_rag: useRag, stream: true }),
  
  // 任务
  createTask: (description: string, context?: object) =>
    axios.post(`${API_BASE}/tasks`, { description, context, generate_suggestions: true }),
  listTasks: () => axios.get(`${API_BASE}/tasks`),
  getTask: (id: string) => axios.get(`${API_BASE}/tasks/${id}`),
  
  // Workers
  listWorkers: () => axios.get(`${API_BASE}/workers`),
  
  // 系统
  health: () => axios.get(`${API_BASE}/health`),

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
};

// ==================== 登录页 ====================

const LoginPage: React.FC = () => {
  const { login } = useContext(AuthContext);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      await login(values.username, values.password);
      navigate('/documents', { replace: true });
    } catch (err: any) {
      message.error(err.response?.data?.detail || '登录失败，请检查用户名和密码');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(135deg, #001529 0%, #003a70 100%)',
    }}>
      <Card
        style={{ width: 400, borderRadius: 12, boxShadow: '0 8px 32px rgba(0,0,0,0.3)' }}
        bordered={false}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Avatar
            size={64}
            icon={<SafetyCertificateOutlined />}
            style={{ background: '#1890ff', marginBottom: 12 }}
          />
          <Title level={3} style={{ margin: 0 }}>MemoX</Title>
          <Text type="secondary">开发测试环境 · 请先登录</Text>
        </div>

        <Form layout="vertical" onFinish={onFinish} size="large">
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" autoFocus />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" block loading={loading}>
              登录
            </Button>
          </Form.Item>
        </Form>

        <Divider />
        <div style={{ textAlign: 'center' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Token 有效期 24 小时 · 仅限开发测试使用
          </Text>
        </div>
      </Card>
    </div>
  );
};


// ==================== 布局组件 ====================

const AppLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const { user, logout } = useContext(AuthContext);

  const handleLogout = async () => {
    await api.logout().catch(() => {});
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', background: '#001529', padding: '0 24px' }}>
        <Title level={4} style={{ color: 'white', margin: 0, flexShrink: 0 }}>
          📚 MemoX
        </Title>
        <div style={{ flex: 1 }} />
        <Space>
          <Badge status="success" text={<Text style={{ color: 'white' }}>在线</Text>} />
          {user && (
            <>
              <Avatar size="small" icon={<UserOutlined />} style={{ background: '#1890ff' }} />
              <Text style={{ color: 'white' }}>{user.display_name}</Text>
              <Tooltip title="退出登录">
                <Button
                  type="text"
                  icon={<LogoutOutlined />}
                  style={{ color: 'rgba(255,255,255,0.65)' }}
                  onClick={handleLogout}
                />
              </Tooltip>
            </>
          )}
        </Space>
      </Header>
      <Layout>
        <Sider 
          collapsible 
          collapsed={collapsed} 
          onCollapse={setCollapsed}
          breakpoint="lg"
          collapsedWidth="0"
          style={{ background: '#fff' }}
        >
          <Menu 
            mode="inline" 
            defaultSelectedKeys={['documents']}
            onClick={({ key }) => navigate(`/${key}`)}
            style={{ height: '100%', borderRight: 0 }}
            items={[
              { key: 'documents', icon: <FileTextOutlined />, label: '知识库' },
              { key: 'chat', icon: <MessageOutlined />, label: '智能问答' },
              { key: 'tasks', icon: <RobotOutlined />, label: '任务执行' },
              { key: 'workers', icon: <TeamOutlined />, label: 'Agent 监控' },
            ]}
          />
        </Sider>
        <Layout style={{ padding: '0' }}>
          <Content style={{ padding: '24px', background: '#f0f2f5', minHeight: '100vh' }}>
            {children}
          </Content>
        </Layout>
      </Layout>
    </Layout>
  );
};

// ==================== 知识库页面 ====================

const DocumentsPage: React.FC = () => {
  const [documents, setDocuments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [groups, setGroups] = useState<KnowledgeGroup[]>([]);
  const [activeGroupFilter, setActiveGroupFilter] = useState<string>('all');
  const [groupDrawerOpen, setGroupDrawerOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupColor, setNewGroupColor] = useState('#1890ff');
  const [editingGroup, setEditingGroup] = useState<KnowledgeGroup | null>(null);
  const [editGroupName, setEditGroupName] = useState('');

  const fetchDocuments = async () => {
    setLoading(true);
    try {
      const res = await api.listDocuments();
      setDocuments(res.data);
    } catch (err) {
      message.error('获取文档列表失败');
    } finally {
      setLoading(false);
    }
  };

  const fetchGroups = async () => {
    try {
      const res = await api.listGroups();
      setGroups(res.data);
    } catch (err) {
      console.error('获取分组失败', err);
    }
  };

  useEffect(() => {
    fetchDocuments();
    fetchGroups();
  }, []);

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      await api.uploadDocument(file);
      message.success(`文档 ${file.name} 上传成功`);
      await fetchDocuments();  // await 确保列表刷新完成后再解除 uploading 状态
    } catch (err: any) {
      message.error(err.response?.data?.detail || '上传失败');
    } finally {
      setUploading(false);
    }
    return false;
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteDocument(id);
      message.success('删除成功');
      fetchDocuments();
    } catch (err) {
      message.error('删除失败');
    }
  };

  const handleMoveGroup = async (docId: string, groupId: string) => {
    try {
      await api.moveDocumentGroup(docId, groupId);
      message.success('已移动到新分组');
      await fetchDocuments();
      await fetchGroups();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '移动失败');
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };

  return (
    <div>
      {/* 分组标签栏 */}
      <Card style={{ marginBottom: 16 }} bodyStyle={{ padding: '12px 16px' }}>
        <Space wrap>
          <Tag
            color={activeGroupFilter === 'all' ? '#1890ff' : 'default'}
            style={{ cursor: 'pointer', fontSize: 13 }}
            onClick={() => setActiveGroupFilter('all')}
          >
            全部 ({documents.length})
          </Tag>
          {groups.map(g => (
            <Tag
              key={g.id}
              color={activeGroupFilter === g.id ? g.color : 'default'}
              style={{ cursor: 'pointer', fontSize: 13 }}
              onClick={() => setActiveGroupFilter(g.id)}
            >
              {g.name} ({g.doc_count})
            </Tag>
          ))}
          <Button
            size="small"
            icon={<SettingOutlined />}
            onClick={() => setGroupDrawerOpen(true)}
          >
            管理分组
          </Button>
        </Space>
      </Card>

      <Card
        title="知识库管理"
        extra={
          <Upload beforeUpload={handleUpload} showUploadList={false} disabled={uploading}>
            <Button type="primary" icon={<UploadOutlined />} loading={uploading}>
              上传文档
            </Button>
          </Upload>
        }
      >
        <Dragger 
          beforeUpload={handleUpload}
          showUploadList={false}
          disabled={uploading}
          style={{ background: '#fafafa' }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽上传文档</p>
          <p className="ant-upload-hint">
            支持 PDF、Markdown、TXT、DOCX 格式
          </p>
        </Dragger>
      </Card>

      <Card title="已上传文档" style={{ marginTop: 16 }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : documents.length === 0 ? (
          <Empty description="暂无文档，请先上传" />
        ) : (
          <List
            dataSource={activeGroupFilter === 'all' ? documents : documents.filter(d => (d.group_id || 'ungrouped') === activeGroupFilter)}
            renderItem={(doc: any) => (
              <List.Item
                actions={[
                  <select
                    key="move"
                    value={doc.group_id || 'ungrouped'}
                    onChange={e => handleMoveGroup(doc.id, e.target.value)}
                    style={{ fontSize: 12, padding: '2px 4px', borderRadius: 4, border: '1px solid #d9d9d9', cursor: 'pointer' }}
                  >
                    {groups.map(g => (
                      <option key={g.id} value={g.id}>{g.name}</option>
                    ))}
                  </select>,
                  <Button
                    key="delete"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => handleDelete(doc.id)}
                  >
                    删除
                  </Button>
                ]}
              >
                <List.Item.Meta
                  avatar={<Avatar icon={<FileTextOutlined />} style={{ background: '#1890ff' }} />}
                  title={doc.filename}
                  description={
                    <Space>
                      {(() => {
                        const g = groups.find(x => x.id === (doc.group_id || 'ungrouped'));
                        return g ? <Tag color={g.color}>{g.name}</Tag> : null;
                      })()}
                      <Tag>{doc.type}</Tag>
                      <Text type="secondary">{doc.chunk_count} 个片段</Text>
                      <Text type="secondary">{formatSize(doc.size)}</Text>
                      <Text type="secondary">
                        {dayjs(doc.created_at).format('YYYY-MM-DD HH:mm')}
                      </Text>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  );
};

// ==================== 聊天页面 ====================

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: any[];
}

const ChatPage: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>('');
  const [sources, setSources] = useState<any[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
    };

    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);
    setSources([]);

    try {
      const res = await api.chat(input, sessionId || undefined);
      const data = res.data;
      
      if (data.session_id && !sessionId) {
        setSessionId(data.session_id);
      }

      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: data.answer,
        sources: data.sources,
      };

      setMessages(prev => [...prev, assistantMessage]);
      setSources(data.sources || []);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '发送失败');
      setMessages(prev => prev.filter(m => m.id !== userMessage.id));
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <Card style={{ height: 'calc(100vh - 120px)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 0' }}>
        {messages.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 60 }}>
            <Avatar size={64} icon={<MessageOutlined />} style={{ marginBottom: 16 }} />
            <Title level={4}>开始对话</Title>
            <Text type="secondary">
              问我任何关于知识库中的问题，我会基于已上传的文档为你解答
            </Text>
          </div>
        ) : (
          messages.map(msg => (
            <div key={msg.id} style={{ marginBottom: 16 }}>
              <Space align="start">
                <Avatar 
                  icon={msg.role === 'user' ? <UploadOutlined /> : <RobotOutlined />} 
                  style={{ background: msg.role === 'user' ? '#1890ff' : '#52c41a' }}
                />
                <div style={{ flex: 1 }}>
                  <Text strong>{msg.role === 'user' ? '你' : 'AI 助手'}</Text>
                  <Card size="small" style={{ marginTop: 8, background: msg.role === 'user' ? '#e6f7ff' : '#f6ffed' }}>
                    <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
                  </Card>
                  {msg.sources && msg.sources.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>📚 参考来源：</Text>
                      {msg.sources.map((s: any, i: number) => (
                        <Tag key={i} style={{ marginTop: 4 }}>{s.filename} ({Math.round(s.score * 100)}%)</Tag>
                      ))}
                    </div>
                  )}
                </div>
              </Space>
            </div>
          ))
        )}
        {loading && (
          <div style={{ marginBottom: 16 }}>
            <Space align="start">
              <Avatar icon={<RobotOutlined />} style={{ background: '#52c41a' }} />
              <Card size="small" style={{ background: '#f6ffed' }}>
                <Spin indicator={<LoadingOutlined style={{ fontSize: 16 }} spin />} />
                <Text style={{ marginLeft: 8 }}>正在思考...</Text>
              </Card>
            </Space>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {sources.length > 0 && (
        <Alert
          message="检索到的相关文档"
          description={
            <List
              size="small"
              dataSource={sources}
              renderItem={(s: any) => (
                <List.Item style={{ padding: '4px 0' }}>
                  <Text>{s.filename}</Text>
                  <Tag color="green">{Math.round(s.score * 100)}% 匹配</Tag>
                </List.Item>
              )}
            />
          }
          type="info"
          style={{ marginBottom: 16 }}
        />
      )}

      <div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 16 }}>
        <Space.Compact style={{ width: '100%' }}>
          <TextArea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="输入问题，按 Enter 发送..."
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={loading}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={loading}>
            发送
          </Button>
        </Space.Compact>
      </div>
    </Card>
  );
};

// ==================== 任务执行页面 ====================

const TasksPage: React.FC = () => {
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [taskInput, setTaskInput] = useState('');
  const [executing, setExecuting] = useState(false);
  const [currentTask, setCurrentTask] = useState<any>(null);
  const [suggestions, setSuggestions] = useState<any[]>([]);

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
  }, []);

  const handleExecute = async () => {
    if (!taskInput.trim() || executing) return;

    setExecuting(true);
    setSuggestions([]);
    
    try {
      const res = await api.createTask(taskInput);
      const data = res.data;
      
      setCurrentTask(data);
      setSuggestions(data.suggestions || []);
      message.success('任务执行完成');
      fetchTasks();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '执行失败');
    } finally {
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
      </Card>

      {currentTask && (
        <Card 
          title="执行结果" 
          style={{ marginTop: 16 }}
          extra={
            <Space>
              {getComplexityTag(currentTask.complexity)}
              {getStatusTag(currentTask.result ? 'completed' : 'failed')}
            </Space>
          }
        >
          <Title level={5}>任务结果</Title>
          <Card size="small" style={{ background: '#fafafa', marginBottom: 16 }}>
            <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 13 }}>
              {currentTask.result}
            </pre>
          </Card>

          {suggestions.length > 0 && (
            <>
              <Title level={5}>
                <BulbOutlined style={{ color: '#faad14', marginRight: 8 }} />
                优化建议
              </Title>
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
            </>
          )}
        </Card>
      )}

      <Card title="历史任务" style={{ marginTop: 16 }}>
        {tasks.length === 0 ? (
          <Empty description="暂无执行记录" />
        ) : (
          <List
            dataSource={tasks}
            renderItem={(task: any) => (
              <List.Item>
                <List.Item.Meta
                  title={
                    <Space>
                      {getStatusIcon(task.status)}
                      <Text>{task.description.substring(0, 50)}{task.description.length > 50 ? '...' : ''}</Text>
                    </Space>
                  }
                  description={
                    <Space>
                      <Text type="secondary">
                        {task.sub_tasks_count} 个子任务
                      </Text>
                      <Text type="secondary">
                        {dayjs(task.created_at).format('YYYY-MM-DD HH:mm')}
                      </Text>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  );
};

// ==================== Worker 监控页面 ====================

const WorkersPage: React.FC = () => {
  const [workers, setWorkers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

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

  useEffect(() => {
    fetchWorkers();
    const interval = setInterval(fetchWorkers, 5000); // 每5秒刷新
    return () => clearInterval(interval);
  }, []);

  return (
    <Card title="Agent Worker 状态">
      <Alert
        message="Worker Agent 池"
        description="每个 Worker Agent 可以独立配置不同的大模型、技能和工具。任务会自动分配给空闲的 Worker 执行。"
        type="info"
        style={{ marginBottom: 16 }}
      />
      
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin />
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
          {workers.map((worker: any) => (
            <Card key={worker.id} size="small">
              <Space direction="vertical" style={{ width: '100%' }}>
                <div>
                  <Space>
                    <Avatar 
                      icon={<RobotOutlined />} 
                      style={{ background: worker.busy ? '#ff4d4f' : '#52c41a' }} 
                    />
                    <Text strong>{worker.id}</Text>
                    <Badge status={worker.busy ? 'error' : 'success'} text={worker.busy ? '忙碌' : '空闲'} />
                  </Space>
                </div>
                <div>
                  <Text type="secondary">模型: </Text>
                  <Tag>{worker.model}</Tag>
                </div>
                <div>
                  <Text type="secondary">状态: </Text>
                  <Progress 
                    percent={worker.busy ? 100 : 0} 
                    status={worker.busy ? 'active' : 'normal'}
                    size="small"
                    style={{ width: 100, display: 'inline-block', marginLeft: 8 }}
                  />
                </div>
              </Space>
            </Card>
          ))}
        </div>
      )}
    </Card>
  );
};

// ==================== 受保护路由 ====================

const RequireAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user } = useContext(AuthContext);
  const navigate = useNavigate();

  useEffect(() => {
    if (!user) navigate('/login', { replace: true });
  }, [user, navigate]);

  if (!user) return null;
  return <>{children}</>;
};


// ==================== 主应用 ====================

const App: React.FC = () => {
  const [user, setUser] = useState<AuthUser | null>(() => {
    try {
      const stored = localStorage.getItem(USER_KEY);
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  });
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(TOKEN_KEY)
  );

  const login = async (username: string, password: string) => {
    const res = await api.login(username, password);
    const { token: t, user: u } = res.data;
    localStorage.setItem(TOKEN_KEY, t);
    localStorage.setItem(USER_KEY, JSON.stringify(u));
    setToken(t);
    setUser(u);
  };

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  };

  // 启动时验证 token 是否仍然有效
  useEffect(() => {
    if (token) {
      api.me().catch(() => logout());
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, login, logout }}>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/documents" replace /> : <LoginPage />} />
        <Route path="/*" element={
          <RequireAuth>
            <AppLayout>
              <Routes>
                <Route path="/" element={<Navigate to="/documents" replace />} />
                <Route path="/documents" element={<DocumentsPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/tasks" element={<TasksPage />} />
                <Route path="/workers" element={<WorkersPage />} />
              </Routes>
            </AppLayout>
          </RequireAuth>
        } />
      </Routes>
    </AuthContext.Provider>
  );
};

export default App;
