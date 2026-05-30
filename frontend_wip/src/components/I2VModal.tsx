import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Collapse, Form, Input, InputNumber, Modal, Progress, Select, Segmented, Space, Switch, Tag, Typography, message } from 'antd';
import { CheckCircleOutlined, CopyOutlined, DownloadOutlined, LinkOutlined, ReloadOutlined, VideoCameraOutlined } from '@ant-design/icons';
import { api } from '../shared';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

interface I2VModalProps {
  open: boolean;
  imageUrl: string;
  onClose: () => void;
  onSuccess: (videoUrl: string, prompt: string, sourceImageUrl: string) => void;
}

type I2VResult = {
  videoUrl: string;
  prompt: string;
  sourceImageUrl: string;
  inputMode?: string;
};

type MediaAsset = {
  id: string;
  status: 'queued' | 'running' | 'success' | 'failed' | string;
  url?: string;
  prompt?: string;
  source_url?: string;
  input_mode?: string;
  error?: string;
};

const promptPresets = [
  {
    label: '轻微动态',
    value: 'subtle',
    prompt: '镜头缓慢推进，主体保持清晰，背景自然微动，光影柔和变化',
  },
  {
    label: '产品展示',
    value: 'product',
    prompt: '镜头围绕主体平滑移动，突出材质细节和轮廓，背景保持干净',
  },
  {
    label: '电影感',
    value: 'cinematic',
    prompt: '电影感运镜，轻微景深变化，主体自然运动，画面稳定',
  },
  {
    label: '社媒短片',
    value: 'social',
    prompt: '节奏明快的短视频镜头，主体轻微运动，画面明亮清晰',
  },
];

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (!minutes) return `${rest}s`;
  return `${minutes}m ${rest.toString().padStart(2, '0')}s`;
}

function mediaStatusLabel(status?: string) {
  if (status === 'queued') return '排队中';
  if (status === 'running') return '生成中';
  if (status === 'success') return '已完成';
  if (status === 'failed') return '失败';
  return status || '提交中';
}

function mediaStatusColor(status?: string) {
  if (status === 'queued') return 'blue';
  if (status === 'running') return 'processing';
  if (status === 'success') return 'success';
  if (status === 'failed') return 'error';
  return 'default';
}

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

export const I2VModal: React.FC<I2VModalProps> = ({ open, imageUrl, onClose, onSuccess }) => {
  const [prompt, setPrompt] = useState('');
  const [duration, setDuration] = useState(5);
  const [resolution, setResolution] = useState('720P');
  const [negativePrompt, setNegativePrompt] = useState('');
  const [lastFrameUrl, setLastFrameUrl] = useState('');
  const [firstClipUrl, setFirstClipUrl] = useState('');
  const [drivingAudioUrl, setDrivingAudioUrl] = useState('');
  const [promptExtend, setPromptExtend] = useState(true);
  const [watermark, setWatermark] = useState(false);
  const [seed, setSeed] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState('');
  const [result, setResult] = useState<I2VResult | null>(null);
  const [queuedAsset, setQueuedAsset] = useState<MediaAsset | null>(null);

  const progressPercent = useMemo(() => {
    if (!loading) return result ? 100 : 0;
    return Math.min(92, 18 + elapsed * 3);
  }, [elapsed, loading, result]);

  const canSubmit = prompt.trim().length > 0 && Boolean(imageUrl) && !loading;

  useEffect(() => {
    if (!open) return;
    setError('');
    setResult(null);
    setQueuedAsset(null);
    setElapsed(0);
  }, [imageUrl, open]);

  useEffect(() => {
    if (!loading) return undefined;
    const timer = window.setInterval(() => setElapsed(value => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [loading]);

  const handleClose = () => {
    onClose();
  };

  const pollVideoAsset = async (assetId: string) => {
    for (let attempt = 0; attempt < 160; attempt += 1) {
      await new Promise(resolve => window.setTimeout(resolve, 3000));
      const res = await api.getVideoAsset(assetId);
      const asset = res.data as MediaAsset;
      setQueuedAsset(asset);

      if (asset.status === 'success') {
        if (!asset.url) throw new Error('后台任务已完成，但没有返回视频链接');
        return asset;
      }
      if (asset.status === 'failed') {
        throw new Error(asset.error || '后台视频生成失败');
      }
    }
    throw new Error('后台生成仍在排队或执行中，请稍后在媒体创作作品库查看');
  };

  const applyPreset = (value: string | number) => {
    const preset = promptPresets.find(item => item.value === value);
    if (preset) setPrompt(preset.prompt);
  };

  const handleCopy = async (value: string, label: string) => {
    try {
      await copyText(value);
      message.success(`${label}已复制`);
    } catch (err: any) {
      message.error(err?.message || '复制失败');
    }
  };

  const handleSubmit = async () => {
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt) {
      message.warning('请填写 prompt');
      return;
    }
    setLoading(true);
    setError('');
    setResult(null);
    setQueuedAsset(null);
    setElapsed(0);
    try {
      const resp = await api.enqueueI2VJob({
        image_url: imageUrl,
        prompt: cleanPrompt,
        duration,
        resolution,
        negative_prompt: negativePrompt.trim() || undefined,
        last_frame_url: lastFrameUrl.trim() || undefined,
        first_clip_url: firstClipUrl.trim() || undefined,
        driving_audio_url: drivingAudioUrl.trim() || undefined,
        prompt_extend: promptExtend,
        watermark,
        seed: seed ?? undefined,
      });
      const asset = resp.data?.asset as MediaAsset | undefined;
      if (!asset?.id) {
        throw new Error('后台任务提交失败：未返回媒体资产 ID');
      }
      setQueuedAsset(asset);
      message.success('已提交后台生成任务，正在等待结果');

      const completedAsset = await pollVideoAsset(asset.id);
      const nextResult = {
        videoUrl: completedAsset.url || '',
        prompt: completedAsset.prompt || cleanPrompt,
        sourceImageUrl: completedAsset.source_url || imageUrl,
        inputMode: completedAsset.input_mode,
      };
      setResult(nextResult);
      onSuccess(nextResult.videoUrl, nextResult.prompt, nextResult.sourceImageUrl);
      message.success('视频生成完成');
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || '生成失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title={(
        <Space size={8}>
          <VideoCameraOutlined />
          <span>图生视频</span>
          {result ? <Tag color="success">已完成</Tag> : null}
        </Space>
      )}
      open={open}
      onCancel={handleClose}
      footer={[
        <Button key="cancel" onClick={handleClose}>
          {loading ? '关闭' : result ? '完成' : '取消'}
        </Button>,
        <Button
          key="ok"
          type="primary"
          icon={result ? <ReloadOutlined /> : <VideoCameraOutlined />}
          loading={loading}
          disabled={!canSubmit}
          onClick={handleSubmit}
        >
          {result ? '重新生成' : '提交后台任务'}
        </Button>,
      ]}
      width={760}
      destroyOnHidden={false}
      data-testid="i2v-modal"
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '180px minmax(0, 1fr)', gap: 16 }}>
          <Card size="small" styles={{ body: { padding: 8 } }}>
            <img
              src={imageUrl}
              alt="源图"
              style={{
                width: '100%',
                aspectRatio: '1 / 1',
                objectFit: 'cover',
                borderRadius: 6,
                border: '1px solid #f0f0f0',
                display: 'block',
              }}
            />
            <Space size={4} wrap style={{ marginTop: 8 }}>
              <Tag>{resolution}</Tag>
              <Tag>{duration}s</Tag>
              {promptExtend ? <Tag color="blue">扩写</Tag> : null}
              {watermark ? <Tag color="orange">水印</Tag> : null}
            </Space>
          </Card>

          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Segmented
              block
              disabled={loading}
              options={promptPresets.map(item => ({ label: item.label, value: item.value }))}
              onChange={applyPreset}
            />
            <Form layout="vertical" requiredMark={false}>
              <Form.Item label="Prompt" style={{ marginBottom: 12 }}>
                <TextArea
                  rows={4}
                  showCount
                  maxLength={800}
                  placeholder="描述画面中的运动、镜头、速度和氛围"
                  value={prompt}
                  onChange={event => setPrompt(event.target.value)}
                  disabled={loading}
                />
              </Form.Item>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <Form.Item label="时长" style={{ marginBottom: 0 }}>
                  <Select
                    value={duration}
                    onChange={setDuration}
                    disabled={loading}
                    options={[
                      { value: 3, label: '3 秒' },
                      { value: 5, label: '5 秒' },
                      { value: 8, label: '8 秒' },
                    ]}
                  />
                </Form.Item>
                <Form.Item label="分辨率" style={{ marginBottom: 0 }}>
                  <Select
                    value={resolution}
                    onChange={setResolution}
                    disabled={loading}
                    options={[
                      { value: '480P', label: '480P' },
                      { value: '720P', label: '720P' },
                      { value: '1080P', label: '1080P' },
                    ]}
                  />
                </Form.Item>
              </div>
            </Form>
          </Space>
        </div>

        <Collapse
          size="small"
          items={[{
            key: 'advanced',
            label: '高级参数',
            children: (
              <Form layout="vertical" requiredMark={false}>
                <Form.Item label="Negative prompt" style={{ marginBottom: 12 }}>
                  <TextArea
                    rows={2}
                    placeholder="模糊、抖动、畸变、额外肢体、文字、水印"
                    value={negativePrompt}
                    onChange={event => setNegativePrompt(event.target.value)}
                    disabled={loading}
                  />
                </Form.Item>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <Form.Item label="尾帧 URL" style={{ marginBottom: 12 }}>
                    <Input
                      allowClear
                      placeholder="https://..."
                      value={lastFrameUrl}
                      onChange={event => setLastFrameUrl(event.target.value)}
                      disabled={loading}
                    />
                  </Form.Item>
                  <Form.Item label="续接视频 URL" style={{ marginBottom: 12 }}>
                    <Input
                      allowClear
                      placeholder="https://..."
                      value={firstClipUrl}
                      onChange={event => setFirstClipUrl(event.target.value)}
                      disabled={loading}
                    />
                  </Form.Item>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 160px', gap: 12 }}>
                  <Form.Item label="驱动音频 URL" style={{ marginBottom: 0 }}>
                    <Input
                      allowClear
                      placeholder="https://..."
                      value={drivingAudioUrl}
                      onChange={event => setDrivingAudioUrl(event.target.value)}
                      disabled={loading}
                    />
                  </Form.Item>
                  <Form.Item label="Seed" style={{ marginBottom: 0 }}>
                    <InputNumber
                      min={0}
                      max={2147483647}
                      value={seed}
                      onChange={value => setSeed(typeof value === 'number' ? value : null)}
                      disabled={loading}
                      style={{ width: '100%' }}
                    />
                  </Form.Item>
                </div>
                <Space size={24} wrap style={{ marginTop: 12 }}>
                  <Space>
                    <Switch checked={promptExtend} onChange={setPromptExtend} disabled={loading} />
                    <Text>Prompt 扩写</Text>
                  </Space>
                  <Space>
                    <Switch checked={watermark} onChange={setWatermark} disabled={loading} />
                    <Text>添加水印</Text>
                  </Space>
                </Space>
              </Form>
            ),
          }]}
        />

        {loading ? (
          <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Space>
                  <Text strong>{queuedAsset ? '后台任务' : '提交中'}</Text>
                  <Tag color={mediaStatusColor(queuedAsset?.status)}>
                    {mediaStatusLabel(queuedAsset?.status)}
                  </Tag>
                </Space>
                <Space size={8}>
                  {queuedAsset?.id ? <Text type="secondary">{queuedAsset.id.slice(0, 8)}</Text> : null}
                  <Text type="secondary">{formatElapsed(elapsed)}</Text>
                </Space>
              </Space>
              <Progress percent={progressPercent} status="active" />
              <Text type="secondary" style={{ fontSize: 12 }}>
                任务已进入媒体后台队列，耗时较长时也可以稍后在媒体创作作品库查看。
              </Text>
            </Space>
          </Card>
        ) : null}

        {error ? (
          <Alert
            showIcon
            type="error"
            message="生成失败"
            description={error}
            action={(
              <Button size="small" icon={<ReloadOutlined />} onClick={handleSubmit} disabled={!prompt.trim()}>
                重试
              </Button>
            )}
          />
        ) : null}

        {result ? (
          <Card
            size="small"
            title={(
              <Space>
                <CheckCircleOutlined style={{ color: '#52c41a' }} />
                <span>生成结果</span>
                {result.inputMode ? <Tag>{result.inputMode === 'dashscope_upload' ? '本地素材' : 'URL 素材'}</Tag> : null}
              </Space>
            )}
            extra={(
              <Space>
                <Button size="small" icon={<CopyOutlined />} onClick={() => handleCopy(result.videoUrl, '视频链接')}>
                  复制
                </Button>
                <Button size="small" icon={<LinkOutlined />} href={result.videoUrl} target="_blank">
                  打开
                </Button>
                <Button size="small" icon={<DownloadOutlined />} href={result.videoUrl} target="_blank">
                  下载
                </Button>
              </Space>
            )}
            styles={{ body: { padding: 12 } }}
          >
            <video
              controls
              src={result.videoUrl}
              style={{ width: '100%', maxHeight: 360, borderRadius: 6, background: '#000' }}
            />
            <Paragraph style={{ marginTop: 10, marginBottom: 0 }} copyable={{ text: result.prompt }}>
              {result.prompt}
            </Paragraph>
          </Card>
        ) : null}
      </Space>
    </Modal>
  );
};
