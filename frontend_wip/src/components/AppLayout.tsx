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

// ==================== 布局组件 ====================

export const AppLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useContext(AuthContext);
  const isMobile = useIsMobile();
  const selectedKey = location.pathname.split('/')[1] || 'documents';

  const handleLogout = async () => {
    await api.logout().catch(() => {});
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
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
            selectedKeys={[selectedKey]}
            onClick={({ key }) => { navigate(`/${key}`); if (isMobile) setCollapsed(true); }}
            style={{ height: '100%', borderRight: 0, fontSize: 16 }}
            items={[
              { key: 'documents', icon: <FileTextOutlined style={{ fontSize: 18 }} />, label: <span style={{ fontSize: 16, fontWeight: 500 }}>知识库</span> },
              { key: 'chat', icon: <MessageOutlined style={{ fontSize: 18 }} />, label: <span style={{ fontSize: 16, fontWeight: 500 }}>智能问答</span> },
              { key: 'tasks', icon: <RobotOutlined style={{ fontSize: 18 }} />, label: <span style={{ fontSize: 16, fontWeight: 500 }}>任务执行</span> },
              { key: 'scheduled-tasks', icon: <ClockCircleOutlined style={{ fontSize: 18 }} />, label: <span style={{ fontSize: 16, fontWeight: 500 }}>定时任务</span> },
              { key: 'workflows', icon: <DeploymentUnitOutlined style={{ fontSize: 18 }} />, label: <span style={{ fontSize: 16, fontWeight: 500 }}>工作流</span> },
              { key: 'workers', icon: <TeamOutlined style={{ fontSize: 18 }} />, label: <span style={{ fontSize: 16, fontWeight: 500 }}>Agent 监控</span> },
              ...(user?.role === 'admin' ? [
                { key: 'system', icon: <SafetyCertificateOutlined style={{ fontSize: 18 }} />, label: <span style={{ fontSize: 16, fontWeight: 500 }}>系统状态</span> },
              ] : []),
              { key: 'settings', icon: <SettingOutlined style={{ fontSize: 18 }} />, label: <span style={{ fontSize: 16, fontWeight: 500 }}>设置</span> },
            ]}
          />
        </Sider>
        <Layout style={{ padding: '0' }}>
          <Content style={{ padding: isMobile ? '12px' : '24px', background: '#f0f2f5', overflow: 'auto' }}>
            {children}
          </Content>
        </Layout>
      </Layout>
    </Layout>
  );
};

