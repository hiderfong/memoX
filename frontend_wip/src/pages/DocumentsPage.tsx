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

import { KnowledgeGraphView } from '../components/KnowledgeGraphView';

// ==================== 知识库页面 ====================

export const DocumentsPage: React.FC = () => {
  const isMobile = useIsMobile();
  const [viewMode, setViewMode] = useState<'list' | 'graph'>('list');
  const [documents, setDocuments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadFileName, setUploadFileName] = useState('');
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
    setUploadFileName(file.name);
    setUploadProgress(0);
    // 模拟进度条（后端单 POST 请求不支持实时进度推送）
    const ticker = setInterval(() => {
      setUploadProgress(p => Math.min(p + Math.random() * 15, 90));
    }, 300);
    try {
      await api.uploadDocument(file);
      setUploadProgress(100);
      message.success(
        <span>文档 <b>{file.name}</b> 上传成功</span>
      );
      await fetchDocuments();
    } catch (err: any) {
      clearInterval(ticker);
      message.error(err.response?.data?.detail || '上传失败');
    } finally {
      clearInterval(ticker);
      setTimeout(() => {
        setUploading(false);
        setUploadProgress(0);
        setUploadFileName('');
      }, 600);
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
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 16 }}>
          <Input.Search
            placeholder="搜索文档内容..."
            allowClear
            enterButton="搜索"
            loading={searching}
            onSearch={handleSearch}
            onChange={e => { if (!e.target.value) setSearchResults(null); }}
            style={{ maxWidth: 500, flex: 1, minWidth: 200 }}
          />
          <Segmented
            options={[
              { label: '文档列表', value: 'list', icon: <FileTextOutlined /> },
              { label: '知识图谱', value: 'graph', icon: <DeploymentUnitOutlined /> },
            ]}
            value={viewMode}
            onChange={(val) => setViewMode(val as 'list' | 'graph')}
          />
        </div>
      </Card>

      {viewMode === 'graph' ? (
        <KnowledgeGraphView />
      ) : (
        <>
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
          style={{ background: uploading ? '#f5f5f5' : '#fafafa' }}
        >
          {uploading ? (
            <div style={{ padding: '24px 0' }}>
              <Progress
                percent={Math.round(uploadProgress)}
                status={uploadProgress >= 100 ? 'success' : 'active'}
                strokeColor="#1890ff"
                format={p => <span style={{ color: '#595959' }}>{uploadFileName} · {p}%</span>}
              />
              <p style={{ color: '#8c8c8c', marginTop: 8, fontSize: 13 }}>正在上传并解析文档…</p>
            </div>
          ) : (
            <>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽上传文档</p>
              <p className="ant-upload-hint">
                支持 PDF、Markdown、TXT、DOCX 格式
              </p>
            </>
          )}
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
        </>
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

