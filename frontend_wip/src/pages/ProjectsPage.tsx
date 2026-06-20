import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Progress, Space, Spin, Tag, Typography } from 'antd';
import { CalendarOutlined, CheckCircleOutlined, ClockCircleOutlined, FileTextOutlined, FolderOpenOutlined, LineChartOutlined, ProjectOutlined, RobotOutlined, WarningOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';

import { useProjectContext } from '../components/ProjectContext';
import { api, useIsMobile } from '../shared';

const { Title, Text } = Typography;

type ProjectHealth = 'active' | 'attention' | 'empty' | 'healthy' | string;

type ProjectSummary = {
  id: string;
  name: string;
  color: string;
  created_at: string;
  health: ProjectHealth;
  metrics: {
    doc_count: number;
    chunk_count: number;
    task_count: number;
    active_task_count: number;
    failed_task_count: number;
    waiting_retry_count: number;
    scheduled_task_count: number;
    enabled_scheduled_task_count: number;
    average_score: number | null;
    task_status_counts: Record<string, number>;
  };
  recent_tasks: Array<{
    task_id: string;
    description: string;
    status: string;
    final_score: number | null;
    created_at: string;
    next_retry_at?: string;
  }>;
  recent_documents: Array<{
    id: string;
    filename: string;
    type: string;
    chunk_count: number;
    created_at: string;
  }>;
  next_scheduled_tasks: Array<{
    id: string;
    description: string;
    next_run_at: string;
    enabled: boolean;
  }>;
};

const metricStyle: React.CSSProperties = {
  border: '1px solid #f0f0f0',
  borderRadius: 8,
  padding: 12,
  background: '#fff',
  minHeight: 90,
};

const healthConfig = (health: ProjectHealth) => {
  if (health === 'active') return { color: 'processing', text: '执行中', icon: <ClockCircleOutlined /> };
  if (health === 'attention') return { color: 'error', text: '需关注', icon: <WarningOutlined /> };
  if (health === 'empty') return { color: 'default', text: '待填充', icon: <FolderOpenOutlined /> };
  return { color: 'success', text: '正常', icon: <CheckCircleOutlined /> };
};

const scorePercent = (score: number | null | undefined) => {
  if (score === null || score === undefined) return null;
  return Math.round(Math.max(0, Math.min(1, score)) * 100);
};

export const ProjectsPage: React.FC = () => {
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const { selectedProjectId, setSelectedProjectId } = useProjectContext();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);

  const fetchProjects = async () => {
    setLoading(true);
    try {
      const res = await api.listProjects();
      setProjects(res.data.projects || []);
      setSummary(res.data.summary || {});
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProjects();
  }, []);

  if (loading && projects.length === 0) {
    return <Spin />;
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>项目</Title>
          <Text type="secondary">文档、任务、定时执行与运行风险</Text>
        </div>
        <Space wrap>
          <Button icon={<FileTextOutlined />} onClick={() => navigate('/documents')}>
            知识库
          </Button>
          <Button icon={<RobotOutlined />} onClick={() => navigate('/tasks')}>
            任务执行
          </Button>
        </Space>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
        <div style={metricStyle}>
          <Text type="secondary">项目</Text>
          <div><Text strong style={{ fontSize: 24 }}>{summary.project_count || 0}</Text></div>
        </div>
        <div style={metricStyle}>
          <Text type="secondary">文档</Text>
          <div><Text strong style={{ fontSize: 24 }}>{summary.doc_count || 0}</Text></div>
        </div>
        <div style={metricStyle}>
          <Text type="secondary">任务</Text>
          <div><Text strong style={{ fontSize: 24 }}>{summary.task_count || 0}</Text></div>
        </div>
        <div style={metricStyle}>
          <Text type="secondary">运行/待处理</Text>
          <div>
            <Text strong style={{ fontSize: 24 }}>{summary.active_task_count || 0}</Text>
            <Text type="secondary"> / {summary.failed_task_count || 0}</Text>
          </div>
        </div>
      </div>

      {summary.failed_task_count > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`${summary.failed_task_count} 个项目任务需要关注`}
          action={<Button size="small" onClick={() => navigate('/tasks')}>查看任务</Button>}
        />
      )}

      {projects.length === 0 ? (
        <Empty description="暂无项目" />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
          {projects.map((project) => {
            const health = healthConfig(project.health);
            const score = scorePercent(project.metrics.average_score);
            const taskProgress = project.metrics.task_count
              ? Math.round(((project.metrics.task_status_counts.completed || 0) / project.metrics.task_count) * 100)
              : 0;
            return (
              <Card
                key={project.id}
                title={
                  <Space wrap>
                    <ProjectOutlined style={{ color: project.color }} />
                    <Text strong>{project.name}</Text>
                    <Tag color={health.color} icon={health.icon}>{health.text}</Tag>
                  </Space>
                }
                extra={<Tag>{project.id}</Tag>}
                styles={{ body: { padding: 16 } }}
              >
                <Space direction="vertical" size={14} style={{ width: '100%' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
                    <div style={metricStyle}>
                      <FileTextOutlined />
                      <div><Text strong>{project.metrics.doc_count}</Text></div>
                      <Text type="secondary" style={{ fontSize: 12 }}>文档</Text>
                    </div>
                    <div style={metricStyle}>
                      <RobotOutlined />
                      <div><Text strong>{project.metrics.task_count}</Text></div>
                      <Text type="secondary" style={{ fontSize: 12 }}>任务</Text>
                    </div>
                    <div style={metricStyle}>
                      <CalendarOutlined />
                      <div><Text strong>{project.metrics.scheduled_task_count}</Text></div>
                      <Text type="secondary" style={{ fontSize: 12 }}>定时</Text>
                    </div>
                  </div>

                  <div>
                    <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                      <Text type="secondary">任务完成度</Text>
                      <Text>{project.metrics.task_status_counts.completed || 0}/{project.metrics.task_count}</Text>
                    </Space>
                    <Progress percent={taskProgress} size="small" status={project.metrics.failed_task_count ? 'exception' : 'active'} />
                  </div>

                  {score !== null && (
                    <Space wrap>
                      <LineChartOutlined />
                      <Text type="secondary">平均评分</Text>
                      <Tag color={score >= 80 ? 'success' : score >= 60 ? 'warning' : 'error'}>{score}%</Tag>
                    </Space>
                  )}

                  <div>
                    <Text strong>最近任务</Text>
                    {project.recent_tasks.length ? (
                      <Space direction="vertical" size={4} style={{ width: '100%', marginTop: 6 }}>
                        {project.recent_tasks.slice(0, 3).map(task => (
                          <div key={task.task_id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                            <Text style={{ minWidth: 0 }} ellipsis>{task.description || task.task_id}</Text>
                            <Tag>{task.status}</Tag>
                          </div>
                        ))}
                      </Space>
                    ) : (
                      <Text type="secondary" style={{ display: 'block', marginTop: 6 }}>暂无任务</Text>
                    )}
                  </div>

                  <div>
                    <Text strong>最近文档</Text>
                    {project.recent_documents.length ? (
                      <Space direction="vertical" size={4} style={{ width: '100%', marginTop: 6 }}>
                        {project.recent_documents.slice(0, 3).map(doc => (
                          <div key={doc.id} style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                            <Text style={{ minWidth: 0 }} ellipsis>{doc.filename}</Text>
                            <Text type="secondary">{doc.chunk_count} 块</Text>
                          </div>
                        ))}
                      </Space>
                    ) : (
                      <Text type="secondary" style={{ display: 'block', marginTop: 6 }}>暂无文档</Text>
                    )}
                  </div>

                  {project.next_scheduled_tasks.length > 0 && (
                    <Alert
                      type="info"
                      showIcon
                      message={`下次定时任务 ${dayjs(project.next_scheduled_tasks[0].next_run_at).format('MM-DD HH:mm')}`}
                    />
                  )}
                  <Space wrap>
                    <Button
                      type={selectedProjectId === project.id ? 'default' : 'primary'}
                      icon={<FolderOpenOutlined />}
                      onClick={() => {
                        setSelectedProjectId(project.id);
                        navigate('/documents');
                      }}
                    >
                      {selectedProjectId === project.id ? '进入项目' : '设为当前'}
                    </Button>
                    {selectedProjectId === project.id && (
                      <Button onClick={() => setSelectedProjectId('')}>退出项目范围</Button>
                    )}
                  </Space>
                </Space>
              </Card>
            );
          })}
        </div>
      )}
    </Space>
  );
};
