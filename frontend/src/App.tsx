import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { Layout, Menu, Typography, Card, Button, Upload, List, Space, Avatar, Input, message, Spin, Tag, Progress, Badge, Drawer, Timeline, Alert, Empty, Tooltip, Form, Divider, Checkbox, Modal, Tabs, Table, Select, Slider, InputNumber, AutoComplete } from 'antd';
import { UploadOutlined, FileTextOutlined, RobotOutlined, MessageOutlined, TeamOutlined, SettingOutlined, CloudUploadOutlined, DeleteOutlined, SendOutlined, LoadingOutlined, BulbOutlined, ThunderboltOutlined, ClockCircleOutlined, CheckCircleOutlined, CloseCircleOutlined, InboxOutlined, UserOutlined, LockOutlined, LogoutOutlined, SafetyCertificateOutlined, LinkOutlined, FolderOpenOutlined, MailOutlined, LineChartOutlined, FileSearchOutlined, EyeOutlined, SaveOutlined, DownOutlined, UpOutlined, PlusOutlined, EditOutlined } from '@ant-design/icons';
import { useNavigate, Routes, Route, Link, Navigate } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;
const { TextArea } = Input;
const { Dragger } = Upload;

// ==================== 响应式 Hook ====================

const MOBILE_BREAKPOINT = 768;

function useIsMobile() {
  const [isMobile, setIsMobile] = useState(window.innerWidth < MOBILE_BREAKPOINT);
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, []);
  return isMobile;
}

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

  // Workers
  listWorkers: () => axios.get(`${API_BASE}/workers`),
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
  listSessions: () => axios.get(`${API_BASE}/chat/sessions`),
  getSessionMessages: (id: string) => axios.get(`${API_BASE}/chat/sessions/${id}/messages`),
  deleteSession: (id: string) => axios.delete(`${API_BASE}/chat/sessions/${id}`),

  // 任务取消
  cancelTask: (id: string) => axios.post(`${API_BASE}/tasks/${id}/cancel`),

  // 文档 chunks + 搜索
  getDocumentChunks: (docId: string) => axios.get(`${API_BASE}/documents/${docId}/chunks`),
  searchDocuments: (q: string, groupIds?: string) =>
    axios.get(`${API_BASE}/documents/search`, { params: { q, group_ids: groupIds } }),

  // 任务反馈
  submitTaskFeedback: (taskId: string, feedback: string) =>
    axios.post(`${API_BASE}/tasks/${taskId}/feedback`, { feedback }),
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
  const isMobile = useIsMobile();

  const handleLogout = async () => {
    await api.logout().catch(() => {});
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', background: '#001529', padding: isMobile ? '0 12px' : '0 24px' }}>
        <Title level={4} style={{ color: 'white', margin: 0, flexShrink: 0, fontSize: isMobile ? 16 : undefined }}>
          📚 MemoX
        </Title>
        <div style={{ flex: 1 }} />
        <Space size={isMobile ? 4 : 8}>
          {!isMobile && <Badge status="success" text={<Text style={{ color: 'white' }}>在线</Text>} />}
          {user && (
            <>
              <Avatar size="small" icon={<UserOutlined />} style={{ background: '#1890ff' }} />
              {!isMobile && <Text style={{ color: 'white' }}>{user.display_name}</Text>}
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
            onClick={({ key }) => { navigate(`/${key}`); if (isMobile) setCollapsed(true); }}
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
          <Content style={{ padding: isMobile ? '12px' : '24px', background: '#f0f2f5', minHeight: '100vh' }}>
            {children}
          </Content>
        </Layout>
      </Layout>
    </Layout>
  );
};

// ==================== 知识库页面 ====================

const DocumentsPage: React.FC = () => {
  const isMobile = useIsMobile();
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
  const [urlModalOpen, setUrlModalOpen] = useState(false);
  const [urlInput, setUrlInput] = useState('');
  const [importingUrl, setImportingUrl] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerDoc, setDrawerDoc] = useState<any>(null);
  const [drawerChunks, setDrawerChunks] = useState<any[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<any[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [previewDoc, setPreviewDoc] = useState<any>(null);
  const [previewChunks, setPreviewChunks] = useState<any[]>([]);
  const [previewLoading, setPreviewLoading] = useState(false);

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

  const handleImportUrl = async () => {
    if (!urlInput.trim()) return;
    setImportingUrl(true);
    try {
      await api.importUrl(urlInput.trim());
      message.success('网页导入成功');
      setUrlModalOpen(false);
      setUrlInput('');
      await fetchDocuments();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '网页导入失败');
    } finally {
      setImportingUrl(false);
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

  const handleViewChunks = async (doc: any) => {
    setDrawerDoc(doc);
    setDrawerOpen(true);
    setChunksLoading(true);
    try {
      const res = await api.getDocumentChunks(doc.id);
      setDrawerChunks(res.data.chunks || []);
    } catch (err) {
      message.error('获取文档内容失败');
      setDrawerChunks([]);
    } finally {
      setChunksLoading(false);
    }
  };

  const handlePreview = async (doc: any) => {
    setPreviewDoc(doc);
    setPreviewLoading(true);
    try {
      const res = await api.getDocumentChunks(doc.id);
      setPreviewChunks(res.data.chunks || []);
    } catch (err) {
      message.error('获取文档内容失败');
      setPreviewChunks([]);
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleSearch = async (value: string) => {
    if (!value.trim()) { setSearchResults(null); return; }
    setSearching(true);
    try {
      const res = await api.searchDocuments(value.trim());
      setSearchResults(res.data.results || []);
    } catch (err) {
      message.error('搜索失败');
    } finally {
      setSearching(false);
    }
  };

  return (
    <div>
      <Card style={{ marginBottom: 16 }} bodyStyle={{ padding: '12px 16px' }}>
        <Input.Search
          placeholder="搜索文档内容..."
          allowClear
          enterButton="搜索"
          loading={searching}
          onSearch={handleSearch}
          onChange={e => { if (!e.target.value) setSearchResults(null); }}
          style={{ maxWidth: 500 }}
        />
      </Card>

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
        title="���识库管理"
        extra={
          <Space>
            <Button icon={<LinkOutlined />} onClick={() => setUrlModalOpen(true)}>
              导入网页
            </Button>
            <Upload beforeUpload={handleUpload} showUploadList={false} disabled={uploading}>
              <Button type="primary" icon={<UploadOutlined />} loading={uploading}>
                上传文档
              </Button>
            </Upload>
          </Space>
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

      {searchResults !== null ? (
        <Card title={<Space>搜索结果 ({searchResults.length}) <Button size="small" onClick={() => setSearchResults(null)}>返回文档列表</Button></Space>} style={{ marginTop: 16 }}>
          <List
            dataSource={searchResults}
            locale={{ emptyText: '无匹配结果' }}
            renderItem={(r: any) => (
              <List.Item>
                <List.Item.Meta
                  avatar={<Avatar icon={<FileSearchOutlined />} style={{ background: '#1890ff' }} />}
                  title={<Space><Text>{r.filename}</Text><Tag color="green">{Math.round(r.score * 100)}%</Tag></Space>}
                  description={<Text type="secondary" style={{ fontSize: 12 }}>{r.content.slice(0, 200)}...</Text>}
                />
              </List.Item>
            )}
          />
        </Card>
      ) : (
        <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: 16, marginTop: 16 }}>
          <Card title="已上传文档" style={{ flex: isMobile ? undefined : 4, minWidth: 0 }}>
            {loading ? (
              <div style={{ textAlign: 'center', padding: 40 }}>
                <Spin />
              </div>
            ) : documents.length === 0 ? (
              <Empty description="暂无文档，请先上传" />
            ) : (
              <List
                dataSource={activeGroupFilter === 'all' ? documents : documents.filter(d => (d.group_id || 'ungrouped') === activeGroupFilter)}
                renderItem={(doc: any) => {
                  const g = groups.find(x => x.id === (doc.group_id || 'ungrouped'));
                  return (
                    <List.Item style={{ padding: '8px 0' }}>
                      <div style={{ display: 'flex', alignItems: 'center', width: '100%', gap: 8, minWidth: 0 }}>
                        <Avatar icon={<FileTextOutlined />} style={{ background: '#1890ff', flexShrink: 0 }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            <a onClick={() => handleViewChunks(doc)} style={{ fontWeight: 500 }}>{doc.filename}</a>
                          </div>
                          <div style={{ fontSize: 12, color: '#999', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {g && <Tag color={g.color} style={{ marginRight: 4, fontSize: 11 }}>{g.name}</Tag>}
                            <Tag style={{ fontSize: 11 }}>{doc.type}</Tag>
                            <span style={{ marginLeft: 4 }}>{doc.chunk_count}片段</span>
                            <span style={{ marginLeft: 6 }}>{formatSize(doc.size)}</span>
                          </div>
                          <div style={{ fontSize: 11, color: '#bbb', marginTop: 2 }}>
                            上传于 {dayjs(doc.created_at).format('YYYY-MM-DD HH:mm')}
                          </div>
                        </div>
                        <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 2 }}>
                          <select
                            value={doc.group_id || 'ungrouped'}
                            onChange={e => handleMoveGroup(doc.id, e.target.value)}
                            style={{ fontSize: 11, padding: '1px 2px', borderRadius: 4, border: '1px solid #d9d9d9', cursor: 'pointer' }}
                          >
                            {groups.map(g => (
                              <option key={g.id} value={g.id}>{g.name}</option>
                            ))}
                          </select>
                          <Button type="text" danger size="small" icon={<DeleteOutlined />} onClick={() => handleDelete(doc.id)} />
                          <Button type="text" size="small" icon={<EyeOutlined />} onClick={() => handlePreview(doc)} />
                        </div>
                      </div>
                    </List.Item>
                  );
                }}
              />
            )}
          </Card>
          <Card
            title={previewDoc ? `预览: ${previewDoc.filename}` : '文档预览'}
            style={{ flex: isMobile ? undefined : 6, minWidth: 0 }}
            extra={previewDoc && (
              <Button type="text" size="small" onClick={() => { setPreviewDoc(null); setPreviewChunks([]); }}>
                关闭
              </Button>
            )}
          >
            {!previewDoc ? (
              <Empty description="点击文件预览按钮查看全文" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : previewLoading ? (
              <div style={{ textAlign: 'center', padding: 40 }}><Spin tip="加载中..." /></div>
            ) : (
              <div style={{ maxHeight: 'calc(100vh - 360px)', overflow: 'auto' }}>
                <div style={{ marginBottom: 12 }}>
                  <Space>
                    <Tag>{previewDoc.type}</Tag>
                    <Text type="secondary">{previewDoc.chunk_count} 个片段</Text>
                    <Text type="secondary">{formatSize(previewDoc.size)}</Text>
                  </Space>
                </div>
                <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.8 }}>
                  {previewChunks.map((chunk: any, i: number) => (
                    <div key={i}>
                      {chunk.content}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>
        </div>
      )}

      {/* URL 导入弹窗 */}
      <Modal
        title="导入网页"
        open={urlModalOpen}
        onOk={handleImportUrl}
        onCancel={() => { setUrlModalOpen(false); setUrlInput(''); }}
        confirmLoading={importingUrl}
        okText="导入"
        cancelText="取消"
        okButtonProps={{ disabled: !urlInput.trim() }}
      >
        <Input
          placeholder="https://example.com/page"
          value={urlInput}
          onChange={e => setUrlInput(e.target.value)}
          onPressEnter={handleImportUrl}
          prefix={<LinkOutlined />}
          size="large"
          style={{ marginTop: 8 }}
        />
        <div style={{ marginTop: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            输入网页 URL，系统将自动抓取内容并导入知识库
          </Text>
        </div>
      </Modal>

      {/* 分组管理抽屉 */}
      <Drawer
        title="管理分组"
        placement="right"
        open={groupDrawerOpen}
        onClose={() => { setGroupDrawerOpen(false); setEditingGroup(null); setNewGroupName(''); }}
        width={360}
      >
        <div style={{ marginBottom: 16 }}>
          <Input
            placeholder="新分组名称"
            value={newGroupName}
            onChange={e => setNewGroupName(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <Space>
            <input
              type="color"
              value={newGroupColor}
              onChange={e => setNewGroupColor(e.target.value)}
              style={{ width: 40, height: 32, cursor: 'pointer', border: 'none' }}
            />
            <Button
              type="primary"
              disabled={!newGroupName.trim()}
              onClick={async () => {
                try {
                  await api.createGroup(newGroupName.trim(), newGroupColor);
                  message.success('分组已创建');
                  setNewGroupName('');
                  await fetchGroups();
                } catch (err: any) {
                  message.error(err.response?.data?.detail || '创建失败');
                }
              }}
            >
              创建分组
            </Button>
          </Space>
        </div>
        <Divider />
        <List
          dataSource={groups}
          renderItem={g => (
            <List.Item
              actions={g.id === 'ungrouped' ? [] : [
                <Button
                  key="del"
                  type="text"
                  danger
                  size="small"
                  icon={<DeleteOutlined />}
                  onClick={async () => {
                    try {
                      await api.deleteGroup(g.id);
                      message.success('分组已删除，文档已归回未分组');
                      await fetchGroups();
                      await fetchDocuments();
                    } catch (err: any) {
                      message.error(err.response?.data?.detail || '删除失败');
                    }
                  }}
                />,
              ]}
            >
              {editingGroup?.id === g.id ? (
                <Space>
                  <Input
                    size="small"
                    value={editGroupName}
                    onChange={e => setEditGroupName(e.target.value)}
                    style={{ width: 120 }}
                  />
                  <Button
                    size="small"
                    type="primary"
                    onClick={async () => {
                      try {
                        await api.updateGroup(g.id, { name: editGroupName });
                        setEditingGroup(null);
                        await fetchGroups();
                      } catch (err: any) {
                        message.error(err.response?.data?.detail || '更新失败');
                      }
                    }}
                  >
                    保存
                  </Button>
                  <Button size="small" onClick={() => setEditingGroup(null)}>取消</Button>
                </Space>
              ) : (
                <Space
                  style={{ cursor: g.id !== 'ungrouped' ? 'pointer' : 'default' }}
                  onClick={() => {
                    if (g.id !== 'ungrouped') {
                      setEditingGroup(g);
                      setEditGroupName(g.name);
                    }
                  }}
                >
                  <Tag color={g.color}>{g.name}</Tag>
                  <Text type="secondary">{g.doc_count} 篇文档</Text>
                  {g.id !== 'ungrouped' && <Text type="secondary" style={{ fontSize: 11 }}>（点击重命名）</Text>}
                </Space>
              )}
            </List.Item>
          )}
        />
      </Drawer>

      <Drawer
        title={drawerDoc?.filename || '文档详情'}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={600}
      >
        {drawerDoc && (
          <div style={{ marginBottom: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text><strong>类型:</strong> {drawerDoc.type}</Text>
              <Text><strong>大小:</strong> {formatSize(drawerDoc.size)}</Text>
              <Text><strong>分块数:</strong> {drawerDoc.chunk_count}</Text>
              <Text><strong>创建时间:</strong> {drawerDoc.created_at}</Text>
            </Space>
            <Divider />
          </div>
        )}
        {chunksLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : (
          <List
            dataSource={drawerChunks}
            renderItem={(chunk: any) => (
              <List.Item>
                <Card size="small" title={<Tag>#{chunk.index}</Tag>} style={{ width: '100%' }}>
                  <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0, maxHeight: 200, overflow: 'auto' }}>
                    {chunk.content}
                  </pre>
                </Card>
              </List.Item>
            )}
          />
        )}
      </Drawer>
    </div>
  );
};

// ==================== 聊天页面 ====================

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  sources?: any[];
  worker_id?: string | null;
}

const ChatPage: React.FC = () => {
  const isMobile = useIsMobile();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>('');
  const [sources, setSources] = useState<any[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [groups, setGroups] = useState<KnowledgeGroup[]>([]);
  const [activeGroupIds, setActiveGroupIds] = useState<string[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [showSidebar, setShowSidebar] = useState(false);
  const [workers, setWorkers] = useState<any[]>([]);
  const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const fetchSessions = async () => {
    setSessionsLoading(true);
    try {
      const res = await api.listSessions();
      setSessions(res.data);
    } catch (err) {
      console.error('获取会话列表失败', err);
    } finally {
      setSessionsLoading(false);
    }
  };

  const handleNewSession = () => {
    setSessionId('');
    setMessages([]);
    setSources([]);
  };

  const handleResumeSession = async (sid: string) => {
    try {
      const res = await api.getSessionMessages(sid);
      const msgs: Message[] = res.data.map((m: any, i: number) => ({
        id: `${sid}_${i}`,
        role: m.role,
        content: m.content,
      }));
      setSessionId(sid);
      setMessages(msgs);
      setSources([]);
    } catch (err) {
      message.error('恢复会话失败');
    }
  };

  const handleDeleteSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
      message.success('会话已删除');
      if (sessionId === sid) handleNewSession();
      fetchSessions();
    } catch (err) {
      message.error('删除失败');
    }
  };

  useEffect(() => {
    api.listGroups().then(res => {
      setGroups(res.data);
      setActiveGroupIds(res.data.map((g: KnowledgeGroup) => g.id));
    }).catch(() => {});
    api.listWorkers().then(res => setWorkers(res.data)).catch(() => {});
    fetchSessions();
  }, []);

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
      const allGroupIds = groups.map(g => g.id);
      const isAllSelected = activeGroupIds.length === allGroupIds.length;
      const res = await api.chat(input, sessionId || undefined, true, isAllSelected ? null : activeGroupIds, selectedWorkerId);
      const data = res.data;

      if (data.session_id && !sessionId) {
        setSessionId(data.session_id);
      }

      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: data.answer,
        sources: data.sources,
        worker_id: data.worker_id || null,
      };

      setMessages(prev => [...prev, assistantMessage]);
      setSources(data.sources || []);
      fetchSessions();
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
    <div style={{ display: 'flex', height: 'calc(100vh - 120px)', gap: isMobile ? 0 : 16 }}>
      {/* 会话列表 — 移动端用 Drawer */}
      {isMobile && (
        <Button
          icon={<ClockCircleOutlined />}
          onClick={() => setShowSidebar(true)}
          style={{ position: 'absolute', top: 68, left: 8, zIndex: 10 }}
          size="small"
        >
          历史
        </Button>
      )}
      {isMobile ? (
        <Drawer
          title="会话历史"
          placement="left"
          open={showSidebar}
          onClose={() => setShowSidebar(false)}
          width={260}
          extra={<Button size="small" type="primary" onClick={() => { handleNewSession(); setShowSidebar(false); }}>新对话</Button>}
        >
          <List
            loading={sessionsLoading}
            dataSource={sessions}
            locale={{ emptyText: '暂无历史会话' }}
            renderItem={(s: any) => (
              <List.Item
                style={{ cursor: 'pointer', background: sessionId === s.id ? '#e6f7ff' : undefined, padding: '8px 12px' }}
                onClick={() => { handleResumeSession(s.id); setShowSidebar(false); }}
                actions={[
                  <Tooltip title="删除" key="del">
                    <Button type="text" size="small" danger icon={<DeleteOutlined />}
                      onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleDeleteSession(s.id); }}
                    />
                  </Tooltip>,
                ]}
              >
                <List.Item.Meta
                  title={<Text ellipsis style={{ maxWidth: 140 }}>{s.title || '未命名会话'}</Text>}
                  description={<Text type="secondary" style={{ fontSize: 11 }}>{dayjs(s.updated_at).format('MM-DD HH:mm')}</Text>}
                />
              </List.Item>
            )}
          />
        </Drawer>
      ) : (
      <Card
        title="会话历史"
        size="small"
        style={{ width: 240, flexShrink: 0, display: 'flex', flexDirection: 'column' }}
        bodyStyle={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}
        extra={<Button size="small" type="primary" onClick={handleNewSession}>新对话</Button>}
      >
        <List
          loading={sessionsLoading}
          dataSource={sessions}
          locale={{ emptyText: '暂无历史会话' }}
          renderItem={(s: any) => (
            <List.Item
              style={{
                cursor: 'pointer',
                background: sessionId === s.id ? '#e6f7ff' : undefined,
                padding: '8px 12px',
              }}
              onClick={() => handleResumeSession(s.id)}
              actions={[
                <Tooltip title="删除" key="del">
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleDeleteSession(s.id); }}
                  />
                </Tooltip>,
              ]}
            >
              <List.Item.Meta
                title={<Text ellipsis style={{ maxWidth: 140 }}>{s.title || '未命名会话'}</Text>}
                description={<Text type="secondary" style={{ fontSize: 11 }}>{dayjs(s.updated_at).format('MM-DD HH:mm')}</Text>}
              />
            </List.Item>
          )}
        />
      </Card>
      )}

      {/* 原有聊天主区域 */}
      <Card style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
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
            messages.map(msg => {
              const msgWorker = msg.worker_id ? workers.find(w => w.id === msg.worker_id) : null;
              return (
              <div key={msg.id} style={{ marginBottom: 16 }}>
                <Space align="start">
                  {msg.role === 'user' ? (
                    <Avatar icon={<UploadOutlined />} style={{ background: '#1890ff' }} />
                  ) : msgWorker?.icon ? (
                    <Avatar style={{ background: '#52c41a', fontSize: 18 }}>{msgWorker.icon}</Avatar>
                  ) : (
                    <Avatar icon={<RobotOutlined />} style={{ background: '#52c41a' }} />
                  )}
                  <div style={{ flex: 1 }}>
                    <Text strong>{msg.role === 'user' ? '你' : (msgWorker?.display_name || msgWorker?.id || 'AI 助手')}</Text>
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
              );
            })
          )}
          {loading && (() => {
            const loadingWorker = selectedWorkerId ? workers.find(w => w.id === selectedWorkerId) : null;
            return (
            <div style={{ marginBottom: 16 }}>
              <Space align="start">
                {loadingWorker?.icon ? (
                  <Avatar style={{ background: '#52c41a', fontSize: 18 }}>{loadingWorker.icon}</Avatar>
                ) : (
                  <Avatar icon={<RobotOutlined />} style={{ background: '#52c41a' }} />
                )}
                <Card size="small" style={{ background: '#f6ffed' }}>
                  <Spin indicator={<LoadingOutlined style={{ fontSize: 16 }} spin />} />
                  <Text style={{ marginLeft: 8 }}>{loadingWorker?.display_name || '正在思考'}...</Text>
                </Card>
              </Space>
            </div>
            );
          })()}
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

        {groups.length > 1 && (
          <div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 12, marginBottom: 4 }}>
            <Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>激活分组：</Text>
            <Checkbox.Group
              value={activeGroupIds}
              onChange={vals => setActiveGroupIds(vals as string[])}
              options={groups.map(g => ({ label: <Tag color={g.color}>{g.name}</Tag>, value: g.id }))}
            />
          </div>
        )}
        {workers.length > 0 && (
          <div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 8, marginBottom: 4, display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
            <Text type="secondary" style={{ fontSize: 12, marginRight: 4 }}>回答模型：</Text>
            <span
              onClick={() => setSelectedWorkerId(null)}
              style={{
                cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '2px 10px', borderRadius: 12, fontSize: 12,
                background: selectedWorkerId === null ? '#e6f7ff' : '#fafafa',
                border: selectedWorkerId === null ? '1px solid #1890ff' : '1px solid #d9d9d9',
              }}
            >
              <RobotOutlined style={{ fontSize: 14 }} /> 默认助手
            </span>
            {workers.map((w: any) => (
              <span
                key={w.id}
                onClick={() => setSelectedWorkerId(w.id)}
                style={{
                  cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
                  padding: '2px 10px', borderRadius: 12, fontSize: 12,
                  background: selectedWorkerId === w.id ? '#e6f7ff' : '#fafafa',
                  border: selectedWorkerId === w.id ? '1px solid #1890ff' : '1px solid #d9d9d9',
                }}
              >
                {w.icon || <RobotOutlined style={{ fontSize: 14 }} />} {w.display_name || w.id}
              </span>
            ))}
          </div>
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
    </div>
  );
};

// ==================== 产物文件面板 ====================

const TaskFilesPanel: React.FC<{ taskId: string }> = ({ taskId }) => {
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

// ==================== 任务执行页面 ====================

const TasksPage: React.FC = () => {
  const isMobile = useIsMobile();
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [taskInput, setTaskInput] = useState('');
  const [executing, setExecuting] = useState(false);
  const [currentTask, setCurrentTask] = useState<any>(null);
  const [suggestions, setSuggestions] = useState<any[]>([]);
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
      cancelled: { color: 'warning', text: '已取消' },
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
              {getStatusTag(currentTask.result ? 'completed' : 'failed')}
            </Space>
          }
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

// ==================== Worker 监控页面 ====================

const WorkerCard: React.FC<{
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
                {providers.map((p: any) => <Select.Option key={p.name} value={p.name}>{p.name}</Select.Option>)}
              </Select>
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

const WorkersPage: React.FC = () => {
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

      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin />
        </div>
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
      )}

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
                <Select.Option key={p.name} value={p.name}>{p.name}</Select.Option>
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
