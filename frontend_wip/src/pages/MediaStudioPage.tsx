import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Empty, Form, Input, InputNumber, List, Progress, Select, Space, Statistic, Switch, Tabs, Tag, Typography, message } from 'antd';
import { CopyOutlined, DeleteOutlined, DownloadOutlined, LinkOutlined, PlusOutlined, ReloadOutlined, VideoCameraOutlined } from '@ant-design/icons';

import { api, useIsMobile } from '../shared';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

type BatchItem = {
  id: string;
  image_url: string;
  prompt: string;
  duration: number;
  resolution: string;
  negative_prompt: string;
};

type BatchResult = {
  index: number;
  ok: boolean;
  asset_id?: string;
  url?: string;
  prompt?: string;
  image_url?: string;
  input_mode?: string;
  error?: string;
  status_code?: number;
};

type EditResult = {
  asset_id?: string;
  url: string;
  prompt: string;
  video_url: string;
  input_mode?: string;
};

type MediaAsset = {
  id: string;
  kind: string;
  status: 'queued' | 'running' | 'success' | 'failed';
  operation: string;
  url: string;
  source_url: string;
  prompt: string;
  input_mode?: string;
  error?: string;
  parameters?: Record<string, any>;
  created_at: string;
  updated_at: string;
};

type QueueStatus = {
  max_concurrent: number;
  runtime_pending: number;
  runtime_running: number;
  runtime_tracked: number;
  persisted_queued: number;
  persisted_running: number;
};

const defaultBatchItem = (): BatchItem => ({
  id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
  image_url: '',
  prompt: '',
  duration: 5,
  resolution: '720P',
  negative_prompt: '',
});

const durationOptions = [
  { value: 3, label: '3 秒' },
  { value: 5, label: '5 秒' },
  { value: 8, label: '8 秒' },
];

const resolutionOptions = [
  { value: '480P', label: '480P' },
  { value: '720P', label: '720P' },
  { value: '1080P', label: '1080P' },
];

const ratioOptions = [
  { value: '16:9', label: '16:9' },
  { value: '9:16', label: '9:16' },
  { value: '1:1', label: '1:1' },
  { value: '4:3', label: '4:3' },
];

const emptyQueueStatus: QueueStatus = {
  max_concurrent: 0,
  runtime_pending: 0,
  runtime_running: 0,
  runtime_tracked: 0,
  persisted_queued: 0,
  persisted_running: 0,
};

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
}

function splitLines(value: string) {
  return value
    .split(/\r?\n/)
    .map(item => item.trim())
    .filter(Boolean);
}

function resultInputModeLabel(value?: string) {
  if (value === 'dashscope_upload') return '本地素材';
  if (value === 'url') return 'URL 素材';
  return value || '素材';
}

function operationLabel(value?: string) {
  if (value === 'i2v') return '图生视频';
  if (value === 'video_edit') return '视频编辑';
  return value || '媒体任务';
}

function assetStatusLabel(value: MediaAsset['status']) {
  if (value === 'queued') return '排队中';
  if (value === 'running') return '生成中';
  if (value === 'success') return '成功';
  return '失败';
}

function assetStatusColor(value: MediaAsset['status']) {
  if (value === 'queued') return 'default';
  if (value === 'running') return 'processing';
  if (value === 'success') return 'success';
  return 'error';
}

function formatAssetTime(value?: string) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export const MediaStudioPage: React.FC = () => {
  const isMobile = useIsMobile();
  const [batchItems, setBatchItems] = useState<BatchItem[]>([defaultBatchItem()]);
  const [submittedBatchAssets, setSubmittedBatchAssets] = useState<MediaAsset[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [editVideoUrl, setEditVideoUrl] = useState('');
  const [editPrompt, setEditPrompt] = useState('');
  const [referenceImageUrls, setReferenceImageUrls] = useState('');
  const [editResolution, setEditResolution] = useState('720P');
  const [editRatio, setEditRatio] = useState('16:9');
  const [editDuration, setEditDuration] = useState<number | null>(null);
  const [editNegativePrompt, setEditNegativePrompt] = useState('');
  const [editAudioSetting, setEditAudioSetting] = useState('');
  const [editPromptExtend, setEditPromptExtend] = useState(true);
  const [editWatermark, setEditWatermark] = useState(false);
  const [editSeed, setEditSeed] = useState<number | null>(null);
  const [editLoading, setEditLoading] = useState(false);
  const [editError, setEditError] = useState('');
  const [editResult, setEditResult] = useState<EditResult | null>(null);
  const [editQueuedAsset, setEditQueuedAsset] = useState<MediaAsset | null>(null);
  const [assetLoading, setAssetLoading] = useState(false);
  const [mediaAssets, setMediaAssets] = useState<MediaAsset[]>([]);
  const [assetStatusFilter, setAssetStatusFilter] = useState('all');
  const [assetOperationFilter, setAssetOperationFilter] = useState('all');
  const [retryingAssetIds, setRetryingAssetIds] = useState<Set<string>>(() => new Set());
  const [queueStatus, setQueueStatus] = useState<QueueStatus>(emptyQueueStatus);

  const validBatchItems = useMemo(
    () => batchItems.filter(item => item.image_url.trim() && item.prompt.trim()),
    [batchItems],
  );

  const submittedBatchAssetsLive = useMemo(
    () => submittedBatchAssets.map(asset => (
      mediaAssets.find(item => item.id === asset.id) || asset
    )),
    [mediaAssets, submittedBatchAssets],
  );

  const editLiveAsset = useMemo(() => (
    editQueuedAsset ? mediaAssets.find(item => item.id === editQueuedAsset.id) || editQueuedAsset : null
  ), [editQueuedAsset, mediaAssets]);

  const batchCompleted = submittedBatchAssetsLive.filter(item => (
    item.status === 'success' || item.status === 'failed'
  )).length;
  const batchSucceeded = submittedBatchAssetsLive.filter(item => item.status === 'success').length;
  const batchFailed = submittedBatchAssetsLive.filter(item => item.status === 'failed').length;
  const batchProgress = submittedBatchAssetsLive.length ? Math.round((batchCompleted / submittedBatchAssetsLive.length) * 100) : 0;
  const hasActiveAssets = (
    mediaAssets.some(item => item.status === 'queued' || item.status === 'running')
    || submittedBatchAssetsLive.some(item => item.status === 'queued' || item.status === 'running')
    || editLiveAsset?.status === 'queued'
    || editLiveAsset?.status === 'running'
    || queueStatus.runtime_pending > 0
    || queueStatus.runtime_running > 0
  );

  const loadMediaAssets = useCallback(async () => {
    setAssetLoading(true);
    try {
      const params: Record<string, any> = { limit: 50, kind: 'video' };
      if (assetStatusFilter !== 'all') params.status = assetStatusFilter;
      if (assetOperationFilter !== 'all') params.operation = assetOperationFilter;
      const [assetsResponse, queueResponse] = await Promise.all([
        api.listVideoAssets(params),
        api.getVideoJobsStatus(),
      ]);
      setMediaAssets(assetsResponse.data.assets || []);
      setQueueStatus({ ...emptyQueueStatus, ...(queueResponse.data || {}) });
    } catch (err: any) {
      message.error(err.response?.data?.detail || err.message || '加载作品库失败');
    } finally {
      setAssetLoading(false);
    }
  }, [assetOperationFilter, assetStatusFilter]);

  useEffect(() => {
    void loadMediaAssets();
  }, [loadMediaAssets]);

  useEffect(() => {
    if (!hasActiveAssets) return undefined;
    const timer = window.setInterval(() => {
      void loadMediaAssets();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [hasActiveAssets, loadMediaAssets]);

  const updateBatchItem = (id: string, patch: Partial<BatchItem>) => {
    setBatchItems(items => items.map(item => (
      item.id === id ? { ...item, ...patch } : item
    )));
  };

  const addBatchItem = () => {
    setBatchItems(items => {
      if (items.length >= 8) {
        message.warning('批量任务最多支持 8 个素材');
        return items;
      }
      return [...items, defaultBatchItem()];
    });
  };

  const removeBatchItem = (id: string) => {
    setBatchItems(items => items.length <= 1 ? items : items.filter(item => item.id !== id));
  };

  const duplicateBatchItem = (item: BatchItem) => {
    setBatchItems(items => {
      if (items.length >= 8) {
        message.warning('批量任务最多支持 8 个素材');
        return items;
      }
      return [...items, { ...item, id: `${Date.now()}-${Math.random().toString(36).slice(2)}` }];
    });
  };

  const handleCopy = async (value: string, label: string) => {
    try {
      await copyText(value);
      message.success(`${label}已复制`);
    } catch (err: any) {
      message.error(err?.message || '复制失败');
    }
  };

  const handleDeleteAsset = async (assetId: string) => {
    try {
      await api.deleteVideoAsset(assetId);
      setMediaAssets(items => items.filter(item => item.id !== assetId));
      message.success('作品记录已删除');
    } catch (err: any) {
      message.error(err.response?.data?.detail || err.message || '删除失败');
    }
  };

  const handleRetryAsset = async (asset: MediaAsset) => {
    setRetryingAssetIds(ids => new Set(ids).add(asset.id));
    try {
      const response = await api.retryVideoAsset(asset.id);
      const queued = response.data.asset as MediaAsset;
      setMediaAssets(items => items.map(item => item.id === queued.id ? queued : item));
      setSubmittedBatchAssets(items => items.map(item => item.id === queued.id ? queued : item));
      if (editQueuedAsset?.id === queued.id) {
        setEditQueuedAsset(queued);
      }
      message.success('已重新提交后台任务');
      void loadMediaAssets();
    } catch (err: any) {
      message.error(err.response?.data?.detail || err.message || '重试失败');
    } finally {
      setRetryingAssetIds(ids => {
        const next = new Set(ids);
        next.delete(asset.id);
        return next;
      });
    }
  };

  const runBatch = async () => {
    if (!validBatchItems.length) {
      message.warning('请至少填写 1 个素材 URL 和 Prompt');
      return;
    }
    setBatchLoading(true);
    setSubmittedBatchAssets([]);
    try {
      const payload = validBatchItems.map(item => ({
        image_url: item.image_url.trim(),
        prompt: item.prompt.trim(),
        duration: item.duration,
        resolution: item.resolution,
        negative_prompt: item.negative_prompt.trim() || undefined,
      }));
      const response = await api.enqueueI2VBatchJobs(payload);
      setSubmittedBatchAssets(response.data.assets || []);
      setAssetStatusFilter('all');
      setAssetOperationFilter('all');
      message.success(`已提交 ${response.data.count || payload.length} 个后台生成任务`);
      void loadMediaAssets();
    } catch (err: any) {
      message.error(err.response?.data?.detail || err.message || '批量任务提交失败');
    } finally {
      setBatchLoading(false);
    }
  };

  const runEdit = async () => {
    if (!editVideoUrl.trim() || !editPrompt.trim()) {
      message.warning('请填写视频 URL 和编辑指令');
      return;
    }
    setEditLoading(true);
    setEditError('');
    setEditResult(null);
    setEditQueuedAsset(null);
    try {
      const response = await api.enqueueVideoEditJob({
        video_url: editVideoUrl.trim(),
        prompt: editPrompt.trim(),
        reference_image_urls: splitLines(referenceImageUrls),
        resolution: editResolution || undefined,
        ratio: editRatio || undefined,
        duration: editDuration ?? undefined,
        negative_prompt: editNegativePrompt.trim() || undefined,
        audio_setting: editAudioSetting.trim() || undefined,
        prompt_extend: editPromptExtend,
        watermark: editWatermark,
        seed: editSeed ?? undefined,
      });
      setEditQueuedAsset(response.data.asset);
      setAssetStatusFilter('all');
      setAssetOperationFilter('all');
      message.success('视频编辑任务已提交');
      void loadMediaAssets();
    } catch (err: any) {
      setEditError(err.response?.data?.detail || err.message || '视频编辑任务提交失败');
    } finally {
      setEditLoading(false);
    }
  };

  const renderVideoResult = (result: { url?: string; prompt?: string; input_mode?: string }, key: string) => {
    if (!result.url) return null;
    return (
      <Card key={key} size="small" styles={{ body: { padding: 12 } }}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <video
            controls
            src={result.url}
            style={{ width: '100%', maxHeight: 280, borderRadius: 6, background: '#000' }}
          />
          <Space size={6} wrap>
            <Tag color="success">成功</Tag>
            <Tag>{resultInputModeLabel(result.input_mode)}</Tag>
          </Space>
          {result.prompt ? (
            <Paragraph style={{ marginBottom: 0 }} copyable={{ text: result.prompt }}>
              {result.prompt}
            </Paragraph>
          ) : null}
          <Space wrap>
            <Button size="small" icon={<CopyOutlined />} onClick={() => handleCopy(result.url || '', '视频链接')}>
              复制链接
            </Button>
            <Button size="small" icon={<LinkOutlined />} href={result.url} target="_blank">
              打开
            </Button>
            <Button size="small" icon={<DownloadOutlined />} href={result.url} target="_blank">
              下载
            </Button>
          </Space>
        </Space>
      </Card>
    );
  };

  const renderAssetCard = (asset: MediaAsset, options?: { showDelete?: boolean }) => (
    <Card key={asset.id} size="small" style={{ width: '100%' }} styles={{ body: { padding: 12 } }}>
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        <Space size={6} wrap>
          <Tag color={assetStatusColor(asset.status)}>{assetStatusLabel(asset.status)}</Tag>
          <Tag>{operationLabel(asset.operation)}</Tag>
          <Tag>{resultInputModeLabel(asset.input_mode)}</Tag>
          <Text type="secondary">{formatAssetTime(asset.created_at)}</Text>
        </Space>
        {asset.status === 'success' && asset.url ? (
          <video
            controls
            src={asset.url}
            style={{ width: '100%', maxHeight: 240, borderRadius: 6, background: '#000' }}
          />
        ) : asset.status === 'failed' ? (
          <Alert showIcon type="error" message="生成失败" description={asset.error || '任务未返回可用结果'} />
        ) : (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Progress percent={asset.status === 'queued' ? 12 : 45} status="active" />
            <Text type="secondary">{asset.status === 'queued' ? '任务已入队，等待后台执行。' : '后台正在执行，作品库会自动刷新。'}</Text>
          </Space>
        )}
        <Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}>
          {asset.prompt || '无 Prompt'}
        </Paragraph>
        {asset.source_url ? (
          <Text type="secondary" ellipsis={{ tooltip: asset.source_url }}>
            来源：{asset.source_url}
          </Text>
        ) : null}
        <Space wrap>
          {asset.status === 'failed' && ['i2v', 'video_edit'].includes(asset.operation) ? (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={retryingAssetIds.has(asset.id)}
              onClick={() => void handleRetryAsset(asset)}
            >
              重试
            </Button>
          ) : null}
          {asset.url ? (
            <>
              <Button size="small" icon={<CopyOutlined />} onClick={() => handleCopy(asset.url, '视频链接')}>
                复制链接
              </Button>
              <Button size="small" icon={<LinkOutlined />} href={asset.url} target="_blank">
                打开
              </Button>
            </>
          ) : null}
          {options?.showDelete !== false ? (
            <Button size="small" danger icon={<DeleteOutlined />} onClick={() => void handleDeleteAsset(asset.id)}>
              删除记录
            </Button>
          ) : null}
        </Space>
      </Space>
    </Card>
  );

  const renderMediaLibrary = () => (
    <Card
      title="作品库"
      extra={(
        <Button size="small" icon={<ReloadOutlined />} loading={assetLoading} onClick={() => void loadMediaAssets()}>
          刷新
        </Button>
      )}
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: 10 }}>
          <Select
            value={assetOperationFilter}
            onChange={setAssetOperationFilter}
            options={[
              { value: 'all', label: '全部任务' },
              { value: 'i2v', label: '图生视频' },
              { value: 'video_edit', label: '视频编辑' },
            ]}
          />
          <Select
            value={assetStatusFilter}
            onChange={setAssetStatusFilter}
            options={[
              { value: 'all', label: '全部状态' },
              { value: 'queued', label: '排队中' },
              { value: 'running', label: '生成中' },
              { value: 'success', label: '成功' },
              { value: 'failed', label: '失败' },
            ]}
          />
        </div>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: isMobile ? '1fr 1fr' : 'repeat(4, 1fr)',
            gap: 10,
            padding: 12,
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            background: '#fafafa',
          }}
        >
          <Statistic title="运行槽位" value={`${queueStatus.runtime_running} / ${queueStatus.max_concurrent || 0}`} />
          <Statistic title="等待执行" value={queueStatus.runtime_pending} />
          <Statistic title="排队记录" value={queueStatus.persisted_queued} />
          <Statistic title="运行记录" value={queueStatus.persisted_running} />
        </div>

        {assetLoading ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Progress percent={35} status="active" />
            <Text type="secondary">正在加载历史作品。</Text>
          </Space>
        ) : mediaAssets.length ? (
          <List
            dataSource={mediaAssets}
            renderItem={asset => (
              <List.Item>
                {renderAssetCard(asset)}
              </List.Item>
            )}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史作品" />
        )}
      </Space>
    </Card>
  );

  return (
    <div>
      <Title level={4}>媒体创作</Title>

      <Tabs
        defaultActiveKey="batch"
        items={[
          {
            key: 'batch',
            label: '批量图生视频',
            children: (
              <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'minmax(0, 1.1fr) minmax(320px, 0.9fr)', gap: 16 }}>
                <Card
                  title="素材队列"
                  extra={(
                    <Space>
                      <Tag>{validBatchItems.length} / {batchItems.length} 可提交</Tag>
                      <Button size="small" icon={<PlusOutlined />} onClick={addBatchItem} disabled={batchItems.length >= 8}>
                        添加素材
                      </Button>
                    </Space>
                  )}
                >
                  <Space direction="vertical" size={12} style={{ width: '100%' }}>
                    {batchItems.map((item, index) => (
                      <Card
                        key={item.id}
                        size="small"
                        title={`素材 ${index + 1}`}
                        extra={(
                          <Space>
                            <Button size="small" onClick={() => duplicateBatchItem(item)}>复制</Button>
                            <Button
                              size="small"
                              danger
                              icon={<DeleteOutlined />}
                              disabled={batchItems.length <= 1}
                              onClick={() => removeBatchItem(item.id)}
                            />
                          </Space>
                        )}
                        styles={{ body: { padding: 12 } }}
                      >
                        <Form layout="vertical" requiredMark={false}>
                          <Form.Item label="图片 URL 或 /api/files 文件名" style={{ marginBottom: 10 }}>
                            <Input
                              allowClear
                              placeholder="https://... 或 /api/files/example.png"
                              value={item.image_url}
                              onChange={event => updateBatchItem(item.id, { image_url: event.target.value })}
                              disabled={batchLoading}
                            />
                          </Form.Item>
                          <Form.Item label="Prompt" style={{ marginBottom: 10 }}>
                            <TextArea
                              rows={3}
                              showCount
                              maxLength={800}
                              placeholder="描述运动、镜头和氛围"
                              value={item.prompt}
                              onChange={event => updateBatchItem(item.id, { prompt: event.target.value })}
                              disabled={batchLoading}
                            />
                          </Form.Item>
                          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: 10 }}>
                            <Form.Item label="时长" style={{ marginBottom: 0 }}>
                              <Select
                                value={item.duration}
                                options={durationOptions}
                                onChange={value => updateBatchItem(item.id, { duration: value })}
                                disabled={batchLoading}
                              />
                            </Form.Item>
                            <Form.Item label="分辨率" style={{ marginBottom: 0 }}>
                              <Select
                                value={item.resolution}
                                options={resolutionOptions}
                                onChange={value => updateBatchItem(item.id, { resolution: value })}
                                disabled={batchLoading}
                              />
                            </Form.Item>
                          </div>
                          <Form.Item label="Negative prompt" style={{ marginTop: 10, marginBottom: 0 }}>
                            <Input
                              allowClear
                              placeholder="模糊、抖动、畸变、水印"
                              value={item.negative_prompt}
                              onChange={event => updateBatchItem(item.id, { negative_prompt: event.target.value })}
                              disabled={batchLoading}
                            />
                          </Form.Item>
                        </Form>
                      </Card>
                    ))}
                    <Button
                      type="primary"
                      icon={<VideoCameraOutlined />}
                      loading={batchLoading}
                      disabled={!validBatchItems.length}
                      onClick={runBatch}
                    >
                      提交后台生成
                    </Button>
                  </Space>
                </Card>

                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <Card title="批量状态">
                    <Space direction="vertical" size={12} style={{ width: '100%' }}>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
                        <Statistic title="完成" value={batchCompleted} />
                        <Statistic title="成功" value={batchSucceeded} valueStyle={{ color: '#3f8600' }} />
                        <Statistic title="失败" value={batchFailed} valueStyle={{ color: batchFailed ? '#cf1322' : undefined }} />
                      </div>
                      <Progress percent={batchLoading ? 18 : batchProgress} status={batchFailed ? 'exception' : batchSucceeded ? 'success' : 'normal'} />
                      <Text type="secondary">提交后可离开页面，后台完成后会写入作品库。</Text>
                    </Space>
                  </Card>

                  <Card title="本次提交">
                    {submittedBatchAssetsLive.length ? (
                      <List
                        dataSource={submittedBatchAssetsLive}
                        renderItem={asset => (
                          <List.Item>
                            {renderAssetCard(asset, { showDelete: false })}
                          </List.Item>
                        )}
                      />
                    ) : (
                      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无本次提交任务" />
                    )}
                  </Card>
                  {renderMediaLibrary()}
                </Space>
              </div>
            ),
          },
          {
            key: 'edit',
            label: '视频编辑',
            children: (
              <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'minmax(0, 1fr) minmax(320px, 0.9fr)', gap: 16 }}>
                <Card title="编辑参数">
                  <Form layout="vertical" requiredMark={false}>
                    <Form.Item label="视频 URL 或 /api/files 文件名">
                      <Input
                        allowClear
                        placeholder="https://... 或 /api/files/source.mp4"
                        value={editVideoUrl}
                        onChange={event => setEditVideoUrl(event.target.value)}
                        disabled={editLoading}
                      />
                    </Form.Item>
                    <Form.Item label="编辑指令">
                      <TextArea
                        rows={4}
                        showCount
                        maxLength={1000}
                        placeholder="例如：保持主体一致，改成傍晚光线，镜头更稳定"
                        value={editPrompt}
                        onChange={event => setEditPrompt(event.target.value)}
                        disabled={editLoading}
                      />
                    </Form.Item>
                    <Form.Item label="参考图片 URL，每行一个">
                      <TextArea
                        rows={3}
                        placeholder="https://..."
                        value={referenceImageUrls}
                        onChange={event => setReferenceImageUrls(event.target.value)}
                        disabled={editLoading}
                      />
                    </Form.Item>
                    <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr 1fr', gap: 12 }}>
                      <Form.Item label="分辨率">
                        <Select value={editResolution} options={resolutionOptions} onChange={setEditResolution} disabled={editLoading} />
                      </Form.Item>
                      <Form.Item label="画幅">
                        <Select value={editRatio} options={ratioOptions} onChange={setEditRatio} disabled={editLoading} />
                      </Form.Item>
                      <Form.Item label="时长">
                        <InputNumber
                          min={1}
                          max={30}
                          value={editDuration}
                          placeholder="默认"
                          onChange={value => setEditDuration(typeof value === 'number' ? value : null)}
                          disabled={editLoading}
                          style={{ width: '100%' }}
                        />
                      </Form.Item>
                    </div>
                    <Form.Item label="Negative prompt">
                      <Input
                        allowClear
                        placeholder="模糊、抖动、畸变、水印"
                        value={editNegativePrompt}
                        onChange={event => setEditNegativePrompt(event.target.value)}
                        disabled={editLoading}
                      />
                    </Form.Item>
                    <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 180px', gap: 12 }}>
                      <Form.Item label="音频设置">
                        <Input
                          allowClear
                          placeholder="可选"
                          value={editAudioSetting}
                          onChange={event => setEditAudioSetting(event.target.value)}
                          disabled={editLoading}
                        />
                      </Form.Item>
                      <Form.Item label="Seed">
                        <InputNumber
                          min={0}
                          max={2147483647}
                          value={editSeed}
                          onChange={value => setEditSeed(typeof value === 'number' ? value : null)}
                          disabled={editLoading}
                          style={{ width: '100%' }}
                        />
                      </Form.Item>
                    </div>
                    <Space size={24} wrap>
                      <Space>
                        <Switch checked={editPromptExtend} onChange={setEditPromptExtend} disabled={editLoading} />
                        <Text>Prompt 扩写</Text>
                      </Space>
                      <Space>
                        <Switch checked={editWatermark} onChange={setEditWatermark} disabled={editLoading} />
                        <Text>添加水印</Text>
                      </Space>
                    </Space>
                    <div style={{ marginTop: 16 }}>
                      <Button
                        type="primary"
                        icon={editLiveAsset ? <ReloadOutlined /> : <VideoCameraOutlined />}
                        loading={editLoading}
                        disabled={!editVideoUrl.trim() || !editPrompt.trim()}
                        onClick={runEdit}
                      >
                        {editLiveAsset ? '重新提交' : '提交后台编辑'}
                      </Button>
                    </div>
                  </Form>
                </Card>

                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  {editError ? (
                    <Alert
                      showIcon
                      type="error"
                      message="视频编辑失败"
                      description={editError}
                      action={(
                        <Button size="small" icon={<ReloadOutlined />} onClick={runEdit}>
                          重试
                        </Button>
                      )}
                    />
                  ) : null}
                  <Card title="编辑结果">
                    {editLoading ? (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        <Progress percent={35} status="active" />
                        <Text type="secondary">正在提交后台视频编辑任务。</Text>
                      </Space>
                    ) : editLiveAsset ? (
                      renderAssetCard(editLiveAsset, { showDelete: false })
                    ) : editResult ? (
                      renderVideoResult(editResult, 'edit-result')
                    ) : (
                      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无编辑结果" />
                    )}
                  </Card>
                  <Alert
                    showIcon
                    type="info"
                    message="素材引用"
                    description="本地上传文件可使用 /api/files/{name} 形式；公网 URL 会直接提交给模型服务。"
                  />
                  {renderMediaLibrary()}
                </Space>
              </div>
            ),
          },
        ]}
      />
    </div>
  );
};
