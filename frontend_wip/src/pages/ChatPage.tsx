import React, { useState, useEffect, useRef } from 'react';
import { Layout, Typography, Card, Button, Upload, List, Space, Avatar, Input, message, Spin, Tag, Drawer, Alert, Tooltip, Checkbox, Modal, Tabs } from 'antd';
import { UploadOutlined, RobotOutlined, MessageOutlined, DeleteOutlined, SendOutlined, LoadingOutlined, ThunderboltOutlined, ClockCircleOutlined, InboxOutlined, FolderOpenOutlined, EditOutlined, DownloadOutlined } from '@ant-design/icons';

import { useNavigate } from 'react-router-dom';

import dayjs from 'dayjs';
import { I2VModal } from '../components/I2VModal';

import { useIsMobile, API_BASE, KnowledgeGroup, api } from '../shared';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;
const { TextArea } = Input;
const { Dragger } = Upload;

// ==================== 聊天页面 ====================

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at?: string;
  sources?: any[];
  citations?: Citation[];
  worker_id?: string | null;
  images?: { url?: string; prompt?: string; error?: string }[];
  videos?: { url?: string; prompt?: string; error?: string }[];
  pendingHint?: string;
}

interface Citation {
  ref_id: string;
  doc_id: string;
  filename: string;
  chunk_index: number;
  content_preview: string;
  score: number;
}

export const ChatPage: React.FC = () => {
  const isMobile = useIsMobile();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>('');
  const [sources, setSources] = useState<any[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [groups, setGroups] = useState<KnowledgeGroup[]>([]);
  const [activeGroupIds, setActiveGroupIds] = useState<string[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [sessionView, setSessionView] = useState<'active' | 'archived'>('active');
  const [renameTarget, setRenameTarget] = useState<any | null>(null);
  const [renameInput, setRenameInput] = useState('');
  const [showSidebar, setShowSidebar] = useState(false);
  const [workers, setWorkers] = useState<any[]>([]);
  const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null);
  const [summarizing, setSummarizing] = useState(false);
  const [clarify, setClarify] = useState<{ question: string; options: string[] } | null>(null);
  const [i2vModalOpen, setI2vModalOpen] = useState(false);
  const [i2vSourceUrl, setI2vSourceUrl] = useState<string>('');
  const navigate = useNavigate();

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const fetchSessions = async (view: 'active' | 'archived' = sessionView) => {
    setSessionsLoading(true);
    try {
      const res = await api.listSessions(view === 'archived' ? 'archived' : undefined);
      setSessions(res.data);
    } catch (err) {
      console.error('获取会话列表失败', err);
    } finally {
      setSessionsLoading(false);
    }
  };

  const handleRenameSubmit = async () => {
    if (!renameTarget) return;
    const title = renameInput.trim();
    if (!title) {
      message.warning('名称不能为空');
      return;
    }
    try {
      await api.renameSession(renameTarget.id, title);
      message.success('已重命名');
      setRenameTarget(null);
      setRenameInput('');
      fetchSessions();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '重命名失败');
    }
  };

  const handleSummarizeAsTask = async (taskType?: string) => {
    if (!sessionId) {
      message.warning('请先进行对话再提炼任务');
      return;
    }
    setSummarizing(true);
    try {
      const res = await api.summarizeSessionAsTask(sessionId, taskType);
      const data = res.data;
      if (data.status === 'need_clarification') {
        setClarify({ question: data.question, options: data.options || [] });
      } else {
        setClarify(null);
        const isScheduled = (taskType || '').includes('定时');
        if (isScheduled) {
          navigate('/scheduled-tasks', {
            state: { prefill: data.summary || '', sourceSessionId: sessionId },
          });
        } else {
          navigate('/tasks', { state: { prefill: data.summary || '' } });
        }
      }
    } catch (err: any) {
      message.error(err.response?.data?.detail || '提炼任务失败');
    } finally {
      setSummarizing(false);
    }
  };

  const handleArchiveSession = async (sid: string, archived: boolean) => {
    try {
      await api.archiveSession(sid, archived);
      message.success(archived ? '会话已归档' : '已恢复为活跃会话');
      if (sessionId === sid && archived) handleNewSession();
      fetchSessions();
    } catch (err: any) {
      message.error(err.response?.data?.detail || (archived ? '归档失败' : '恢复失败'));
    }
  };

  useEffect(() => {
    fetchSessions(sessionView);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionView]);

  const groupsStorageKey = (sid: string) => `memox_chat_groups_${sid}`;
  const loadSessionGroups = (sid: string): string[] | null => {
    try {
      const raw = localStorage.getItem(groupsStorageKey(sid));
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : null;
    } catch { return null; }
  };
  const saveSessionGroups = (sid: string, ids: string[]) => {
    try { localStorage.setItem(groupsStorageKey(sid), JSON.stringify(ids)); } catch {}
  };

  const handleNewSession = () => {
    setSessionId('');
    setMessages([]);
    setSources([]);
    setCitations([]);
    setActiveGroupIds(groups.map(g => g.id));
  };

  const handleResumeSession = async (sid: string) => {
    try {
      const res = await api.getSessionMessages(sid);
      const imgRe = /!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g;
      const vidRe = /\[video:([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g;
      const msgs: Message[] = res.data.map((m: any, i: number) => {
        const images: { url: string; prompt: string }[] = [];
        const videos: { url: string; prompt: string }[] = [];
        let match;
        const raw: string = m.content || '';
        while ((match = imgRe.exec(raw)) !== null) {
          images.push({ prompt: match[1], url: match[2] });
        }
        while ((match = vidRe.exec(raw)) !== null) {
          videos.push({ prompt: match[1], url: match[2] });
        }
        const content = raw.replace(imgRe, '').replace(vidRe, '').replace(/\n{3,}/g, '\n\n').trim();
        return {
          id: `${sid}_${i}`,
          role: m.role,
          content,
          images: images.length ? images : undefined,
          videos: videos.length ? videos : undefined,
        };
      });
      setSessionId(sid);
      setMessages(msgs);
      setSources([]);
      setCitations([]);
      const stored = loadSessionGroups(sid);
      if (stored) {
        const valid = stored.filter(id => groups.some(g => g.id === id));
        setActiveGroupIds(valid.length ? valid : groups.map(g => g.id));
      } else {
        setActiveGroupIds(groups.map(g => g.id));
      }
    } catch (err) {
      message.error('恢复会话失败');
    }
  };

  const handleDeleteSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
      try { localStorage.removeItem(groupsStorageKey(sid)); } catch {}
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
  }, []);

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
    };
    const assistantId = (Date.now() + 1).toString();
    const assistantMessage: Message = {
      id: assistantId,
      role: 'assistant',
      content: '',
      worker_id: selectedWorkerId || null,
      images: [],
      videos: [],
    };

    setMessages(prev => [...prev, userMessage, assistantMessage]);
    const currentInput = input;
    setInput('');
    setLoading(true);
    setSources([]);
    setCitations([]);

    const updateAssistant = (patch: (m: Message) => Message) => {
      setMessages(prev => prev.map(m => m.id === assistantId ? patch(m) : m));
    };

    try {
      const allGroupIds = groups.map(g => g.id);
      const isAllSelected = activeGroupIds.length === allGroupIds.length;
      const token = localStorage.getItem('memox_token');
      const resp = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          message: currentInput,
          session_id: sessionId || undefined,
          use_rag: true,
          stream: true,
          active_group_ids: isAllSelected ? null : activeGroupIds,
          worker_id: selectedWorkerId || undefined,
        }),
      });
      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let finalSessionId = sessionId;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 2);
          if (!block.startsWith('data:')) continue;
          const payload = block.slice(5).trim();
          if (!payload) continue;
          let evt: any;
          try { evt = JSON.parse(payload); } catch { continue; }

          switch (evt.type) {
            case 'sources':
              updateAssistant(m => ({ ...m, sources: evt.data }));
              setSources(evt.data || []);
              break;
            case 'chunk':
              updateAssistant(m => ({ ...m, content: (m.content || '') + (evt.content || '') }));
              break;
            case 'image_pending':
              updateAssistant(m => ({ ...m, pendingHint: `正在生成图像：${evt.prompt || ''}` }));
              break;
            case 'image':
              updateAssistant(m => ({
                ...m,
                pendingHint: undefined,
                images: [...(m.images || []), { url: evt.url, prompt: evt.prompt }],
              }));
              break;
            case 'image_error':
              updateAssistant(m => ({
                ...m,
                pendingHint: undefined,
                images: [...(m.images || []), { error: evt.message, prompt: evt.prompt }],
              }));
              break;
            case 'video_pending':
              updateAssistant(m => ({ ...m, pendingHint: `正在生成视频（约 30s–数分钟）：${evt.prompt || ''}` }));
              break;
            case 'video':
              updateAssistant(m => ({
                ...m,
                pendingHint: undefined,
                videos: [...(m.videos || []), { url: evt.url, prompt: evt.prompt }],
              }));
              break;
            case 'video_error':
              updateAssistant(m => ({
                ...m,
                pendingHint: undefined,
                videos: [...(m.videos || []), { error: evt.message, prompt: evt.prompt }],
              }));
              break;
            case 'i2v_pending':
              // optional inline status — no-op for now
              break;
            case 'i2v':
              updateAssistant(m => ({
                ...m,
                pendingHint: undefined,
                videos: [...(m.videos || []), { url: evt.url, prompt: evt.prompt }],
              }));
              break;
            case 'i2v_error':
              updateAssistant(m => ({
                ...m,
                pendingHint: undefined,
                videos: [...(m.videos || []), { error: evt.message, prompt: evt.prompt }],
              }));
              break;
            case 'done':
              finalSessionId = evt.session_id || finalSessionId;
              if (evt.worker_id) {
                updateAssistant(m => ({ ...m, worker_id: evt.worker_id, pendingHint: undefined }));
              }
              if (evt.citations && evt.citations.length > 0) {
                updateAssistant(m => ({ ...m, citations: evt.citations }));
                setCitations(evt.citations || []);
              }
              break;
            case 'error':
              message.error(evt.message || '生成失败');
              break;
          }
        }
      }

      if (finalSessionId && !sessionId) {
        setSessionId(finalSessionId);
        saveSessionGroups(finalSessionId, activeGroupIds);
      }
      fetchSessions();
    } catch (err: any) {
      message.error(err?.message || '发送失败');
      setMessages(prev => prev.filter(m => m.id !== userMessage.id && m.id !== assistantId));
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
    <div style={{ display: 'flex', height: '100%', minHeight: 0, gap: isMobile ? 0 : 16 }}>
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
          width={300}
          extra={<Button size="small" type="primary" onClick={() => { handleNewSession(); setShowSidebar(false); }}>新对话</Button>}
        >
          <Tabs
            size="small"
            activeKey={sessionView}
            onChange={(k) => setSessionView(k as 'active' | 'archived')}
            items={[
              { key: 'active', label: '活跃' },
              { key: 'archived', label: '归档' },
            ]}
            style={{ marginBottom: 8 }}
          />
          <List
            loading={sessionsLoading}
            dataSource={sessions}
            locale={{ emptyText: sessionView === 'archived' ? '暂无归档会话' : '暂无历史会话' }}
            renderItem={(s: any) => (
              <List.Item
                style={{ cursor: 'pointer', background: sessionId === s.id ? '#e6f7ff' : undefined, padding: '8px 12px', display: 'block' }}
                onClick={() => { handleResumeSession(s.id); setShowSidebar(false); }}
              >
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, width: '100%' }}>
                  <Text ellipsis style={{ width: '100%' }}>{s.title || '未命名会话'}</Text>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                    <Text type="secondary" style={{ fontSize: 11 }}>{dayjs(s.updated_at).format('MM-DD HH:mm')}</Text>
                    <Space size={0}>
                      <Tooltip title="重命名">
                        <Button type="text" size="small" icon={<EditOutlined />}
                          onClick={(e: React.MouseEvent) => { e.stopPropagation(); setRenameTarget(s); setRenameInput(s.title || ''); }}
                        />
                      </Tooltip>
                      {sessionView === 'archived' ? (
                        <Tooltip title="恢复">
                          <Button type="text" size="small" icon={<FolderOpenOutlined />}
                            onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleArchiveSession(s.id, false); }}
                          />
                        </Tooltip>
                      ) : (
                        <Tooltip title="归档">
                          <Button type="text" size="small" icon={<InboxOutlined />}
                            onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleArchiveSession(s.id, true); }}
                          />
                        </Tooltip>
                      )}
                      <Tooltip title="删除">
                        <Button type="text" size="small" danger icon={<DeleteOutlined />}
                          onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleDeleteSession(s.id); }}
                        />
                      </Tooltip>
                    </Space>
                  </div>
                </div>
              </List.Item>
            )}
          />
        </Drawer>
      ) : (
      <Card
        title="会话历史"
        size="small"
        style={{ width: 260, flexShrink: 0, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, overflowY: 'auto', padding: '8px 0' } }}
        extra={<Button size="small" type="primary" onClick={handleNewSession}>新对话</Button>}
      >
        <Tabs
          size="small"
          activeKey={sessionView}
          onChange={(k) => setSessionView(k as 'active' | 'archived')}
          items={[
            { key: 'active', label: '活跃' },
            { key: 'archived', label: '归档' },
          ]}
          style={{ padding: '0 12px' }}
        />
        <List
          loading={sessionsLoading}
          dataSource={sessions}
          locale={{ emptyText: sessionView === 'archived' ? '暂无归档会话' : '暂无历史会话' }}
          renderItem={(s: any) => (
            <List.Item
              style={{
                cursor: 'pointer',
                background: sessionId === s.id ? '#e6f7ff' : undefined,
                padding: '8px 12px',
                display: 'block',
              }}
              onClick={() => handleResumeSession(s.id)}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, width: '100%' }}>
                <Text ellipsis style={{ width: '100%' }}>{s.title || '未命名会话'}</Text>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>{dayjs(s.updated_at).format('MM-DD HH:mm')}</Text>
                  <Space size={0}>
                    <Tooltip title="重命名">
                      <Button
                        type="text"
                        size="small"
                        icon={<EditOutlined />}
                        onClick={(e: React.MouseEvent) => { e.stopPropagation(); setRenameTarget(s); setRenameInput(s.title || ''); }}
                      />
                    </Tooltip>
                    {sessionView === 'archived' ? (
                      <Tooltip title="恢复">
                        <Button
                          type="text"
                          size="small"
                          icon={<FolderOpenOutlined />}
                          onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleArchiveSession(s.id, false); }}
                        />
                      </Tooltip>
                    ) : (
                      <Tooltip title="归档">
                        <Button
                          type="text"
                          size="small"
                          icon={<InboxOutlined />}
                          onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleArchiveSession(s.id, true); }}
                        />
                      </Tooltip>
                    )}
                    <Tooltip title="删除">
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={(e: React.MouseEvent) => { e.stopPropagation(); handleDeleteSession(s.id); }}
                      />
                    </Tooltip>
                  </Space>
                </div>
              </div>
            </List.Item>
          )}
        />
      </Card>
      )}

      {/* 原有聊天主区域 */}
      <Card
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
        styles={{ body: { flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' } }}
      >
        {sessionId && messages.length > 0 && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', paddingBottom: 8, borderBottom: '1px solid #f0f0f0', gap: 8 }}>
            <Tooltip title="导出为 Markdown 文件">
              <Button
                icon={<DownloadOutlined />}
                size="small"
                disabled={messages.length === 0}
                onClick={() => {
                  const lines: string[] = [`# 会话导出\n`, `> 导出时间: ${new Date().toLocaleString()}\n`];
                  messages.forEach(msg => {
                    const role = msg.role === 'user' ? '**你**' : '**AI 助手**';
                    lines.push(`\n---\n\n### ${role}\n\n${msg.content}\n`);
                    if (msg.citations && msg.citations.length > 0) {
                      lines.push('\n**引用来源：**\n');
                      msg.citations.forEach((c: Citation) => {
                        lines.push(`- [${c.ref_id}] ${c.filename} (#${c.chunk_index}) — ${c.content_preview.slice(0, 80)}...`);
                      });
                    }
                  });
                  const blob = new Blob([lines.join('')], { type: 'text/markdown' });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `memox-chat-${Date.now()}.md`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                导出对话
              </Button>
            </Tooltip>
            <Tooltip title="把本次会话汇总为任务描述并跳转到任务执行页">
              <Button
                icon={<ThunderboltOutlined />}
                size="small"
                loading={summarizing}
                onClick={() => handleSummarizeAsTask()}
              >
                提炼为任务
              </Button>
            </Tooltip>
          </div>
        )}
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 0' }}>
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
                      <div className="markdown-body" style={{ overflowX: 'auto', fontSize: '14px', lineHeight: '1.6' }}>
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          components={{
                            code({node, inline, className, children, ...props}: any) {
                              const match = /language-(\w+)/.exec(className || '')
                              return !inline && match ? (
                                <SyntaxHighlighter
                                  style={vscDarkPlus as any}
                                  language={match[1]}
                                  PreTag="div"
                                  {...props}
                                >
                                  {String(children).replace(/\n$/, '')}
                                </SyntaxHighlighter>
                              ) : (
                                <code className={className} style={{ background: 'rgba(0,0,0,0.05)', padding: '2px 4px', borderRadius: '4px' }} {...props}>
                                  {children}
                                </code>
                              )
                            }
                          }}
                        >
                          {msg.content}
                        </ReactMarkdown>
                      </div>
                      {msg.pendingHint && (
                        <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 6, color: '#888' }}>
                          <Spin indicator={<LoadingOutlined style={{ fontSize: 14 }} spin />} />
                          <Text type="secondary" style={{ fontSize: 12 }}>{msg.pendingHint}</Text>
                        </div>
                      )}
                    </Card>
                    {msg.images && msg.images.length > 0 && (
                      <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                        {msg.images.map((img, i) =>
                          img.url ? (
                            <div key={i} style={{ position: 'relative', display: 'inline-block' }}>
                              <a href={img.url} target="_blank" rel="noreferrer" title={img.prompt}>
                                <img src={img.url} alt={img.prompt || 'generated'}
                                  style={{ maxWidth: 320, maxHeight: 320, borderRadius: 6, border: '1px solid #eee' }} />
                              </a>
                              <Tooltip title="生成视频">
                                <Button
                                  size="small"
                                  shape="circle"
                                  icon={<span>🎬</span>}
                                  style={{ position: 'absolute', top: 4, right: 4 }}
                                  onClick={() => { setI2vSourceUrl(img.url!); setI2vModalOpen(true); }}
                                />
                              </Tooltip>
                            </div>
                          ) : (
                            <Tag key={i} color="error">图像生成失败: {img.error}</Tag>
                          )
                        )}
                      </div>
                    )}
                    {msg.videos && msg.videos.length > 0 && (
                      <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {msg.videos.map((vid, i) => (
                          vid.url ? (
                            <video key={i} src={vid.url} controls title={vid.prompt}
                              style={{ maxWidth: 480, borderRadius: 6, border: '1px solid #eee' }} />
                          ) : (
                            <Tag key={i} color="error">视频生成失败: {vid.error}</Tag>
                          )
                        ))}
                      </div>
                    )}
                    {msg.citations && msg.citations.length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>🔗 引用来源：</Text>
                        {msg.citations.map((c: Citation, i: number) => (
                          <Card key={i} size="small" style={{ marginTop: 4, background: '#fafafa' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                              <div style={{ flex: 1 }}>
                                <Space size={4}>
                                  <Tag color="blue">{c.ref_id}</Tag>
                                  <Text strong style={{ fontSize: 12 }}>{c.filename}</Text>
                                  <Tag>#{c.chunk_index}</Tag>
                                </Space>
                                <div style={{ fontSize: 11, color: '#666', marginTop: 2 }}>
                                  {c.content_preview.length > 120
                                    ? c.content_preview.slice(0, 120) + '...'
                                    : c.content_preview}
                                </div>
                              </div>
                              <Tag color="green" style={{ marginLeft: 8, flexShrink: 0 }}>
                                {Math.round(c.score * 100)}%
                              </Tag>
                            </div>
                          </Card>
                        ))}
                      </div>
                    )}
                    {msg.sources && msg.sources.length > 0 && !msg.citations && (
                      <div style={{ marginTop: 8 }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>📚 参考来源：</Text>
                        {msg.sources.map((s: any, i: number) => (
                          <Tag key={i} style={{ marginTop: 4 }}>{s.filename || s.doc_name || '未知'} ({Math.round((s.score || 0) * 100)}%)</Tag>
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

        {citations.length > 0 ? (
          <Alert
            message={`检索到 ${citations.length} 条引用来源`}
            description={
              <List
                size="small"
                dataSource={citations}
                renderItem={(c: Citation) => (
                  <List.Item style={{ padding: '4px 0' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'center' }}>
                      <div>
                        <Tag color="blue" style={{ marginRight: 4 }}>{c.ref_id}</Tag>
                        <Text>{c.filename}</Text>
                        <Tag style={{ marginLeft: 4 }}>#{c.chunk_index}</Tag>
                      </div>
                      <Tag color="green">{Math.round(c.score * 100)}%</Tag>
                    </div>
                  </List.Item>
                )}
              />
            }
            type="info"
            style={{ marginBottom: 16 }}
          />
        ) : sources.length > 0 && (
          <Alert
            message="检索到的相关文档"
            description={
              <List
                size="small"
                dataSource={sources}
                renderItem={(s: any) => (
                  <List.Item style={{ padding: '4px 0' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
                      <Text>{s.filename || s.doc_name || '未知文档'}</Text>
                      <Tag color="green">{Math.round((s.score || 0) * 100)}% 匹配</Tag>
                    </div>
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
              onChange={vals => {
                const ids = vals as string[];
                setActiveGroupIds(ids);
                if (sessionId) saveSessionGroups(sessionId, ids);
              }}
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
          <Space style={{ width: '100%', marginBottom: 8 }} wrap>
            <TextArea
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="输入问题，按 Enter 发送..."
              autoSize={{ minRows: 1, maxRows: 4 }}
              disabled={loading}
              style={{ flex: 1, minWidth: 200 }}
            />
            <Space>
              {messages.length > 0 && (
                <Tooltip title="导出对话（Markdown）">
                  <Button
                    icon={<DownloadOutlined />}
                    onClick={() => {
                      const md = messages.map(m => {
                        const role = m.role === 'user' ? '**用户**' : '**助手**';
                        const time = m.created_at ? dayjs(m.created_at).format('MM/DD HH:mm') : '';
                        let text = `${role} ${time}\n\n${m.content}`;
                        if (m.citations && m.citations.length > 0) {
                          text += '\n\n**引用来源：**\n' + m.citations.map((c: any) => `- [${c.filename} #${c.chunk_index}] ${c.content_preview.slice(0, 80)}...`).join('\n');
                        }
                        return text;
                      }).join('\n\n---\n\n');
                      const blob = new Blob([md], { type: 'text/markdown' });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = `memoX-chat-${dayjs().format('YYYYMMDD-HHmmss')}.md`;
                      a.click();
                      URL.revokeObjectURL(url);
                      message.success('对话已导出为 Markdown');
                    }}
                  />
                </Tooltip>
              )}
              <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={loading}>
                发送
              </Button>
            </Space>
          </Space>
        </div>
      </Card>

      <Modal
        title="重命名会话"
        open={!!renameTarget}
        onOk={handleRenameSubmit}
        onCancel={() => { setRenameTarget(null); setRenameInput(''); }}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
      >
        <Input
          value={renameInput}
          onChange={(e) => setRenameInput(e.target.value)}
          onPressEnter={handleRenameSubmit}
          placeholder="输入新名称"
          maxLength={100}
          autoFocus
        />
      </Modal>

      <Modal
        title="请选择任务类型"
        open={!!clarify}
        onCancel={() => setClarify(null)}
        footer={null}
        destroyOnHidden
      >
        {clarify && (
          <>
            <div style={{ marginBottom: 12 }}>{clarify.question}</div>
            <Space wrap>
              {clarify.options.map((opt) => (
                <Button
                  key={opt}
                  type="primary"
                  ghost
                  loading={summarizing}
                  onClick={() => handleSummarizeAsTask(opt)}
                >
                  {opt}
                </Button>
              ))}
            </Space>
          </>
        )}
      </Modal>

      <I2VModal
        open={i2vModalOpen}
        imageUrl={i2vSourceUrl}
        onClose={() => setI2vModalOpen(false)}
        onSuccess={(videoUrl, prompt, sourceImageUrl) => {
          setMessages((prev) => [
            ...prev,
            {
              id: `i2v_${Date.now()}`,
              role: 'assistant',
              content: `图生视频完成（源图: ${sourceImageUrl}）`,
              videos: [{ url: videoUrl, prompt }],
            },
          ]);
        }}
      />
    </div>
  );
};
