import React, { useState } from 'react';
import { Modal, Input, Select, Button, Collapse, message } from 'antd';

const { TextArea } = Input;

interface I2VModalProps {
  open: boolean;
  imageUrl: string;
  authToken: string;
  onClose: () => void;
  onSuccess: (videoUrl: string, prompt: string, sourceImageUrl: string) => void;
}

export const I2VModal: React.FC<I2VModalProps> = ({ open, imageUrl, authToken, onClose, onSuccess }) => {
  const [prompt, setPrompt] = useState('');
  const [duration, setDuration] = useState(5);
  const [resolution, setResolution] = useState('720P');
  const [negativePrompt, setNegativePrompt] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!prompt.trim()) {
      message.warning('请填写 prompt');
      return;
    }
    setLoading(true);
    try {
      const resp = await fetch('/api/videos/i2v', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authToken}`,
        },
        body: JSON.stringify({
          image_url: imageUrl,
          prompt: prompt.trim(),
          duration,
          resolution,
          negative_prompt: negativePrompt.trim() || undefined,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      onSuccess(data.url, prompt.trim(), imageUrl);
      onClose();
      setPrompt('');
      setNegativePrompt('');
    } catch (e: any) {
      message.error(`生成失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="图生视频"
      open={open}
      onCancel={loading ? undefined : onClose}
      footer={[
        <Button key="cancel" onClick={onClose} disabled={loading}>取消</Button>,
        <Button key="ok" type="primary" loading={loading} onClick={handleSubmit}>生成</Button>,
      ]}
      width={520}
    >
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        <img src={imageUrl} alt="source"
             style={{ width: 120, height: 120, objectFit: 'cover', borderRadius: 4 }} />
        <div style={{ flex: 1 }}>
          <TextArea
            rows={3}
            placeholder="描述画面中的运动/变化…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={loading}
          />
        </div>
      </div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ marginBottom: 4 }}>时长</div>
          <Select value={duration} onChange={setDuration} style={{ width: '100%' }}
                  disabled={loading}
                  options={[{value:3,label:'3 秒'},{value:5,label:'5 秒'},{value:8,label:'8 秒'}]} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ marginBottom: 4 }}>分辨率</div>
          <Select value={resolution} onChange={setResolution} style={{ width: '100%' }}
                  disabled={loading}
                  options={[{value:'480P',label:'480P'},{value:'720P',label:'720P'},{value:'1080P',label:'1080P'}]} />
        </div>
      </div>
      <Collapse
        items={[{
          key: 'adv', label: '高级',
          children: (
            <TextArea rows={2} placeholder="negative prompt (可选)"
                      value={negativePrompt}
                      onChange={(e) => setNegativePrompt(e.target.value)}
                      disabled={loading} />
          )
        }]}
      />
      {loading && (
        <div style={{ marginTop: 12, color: '#888', fontSize: 12 }}>
          正在生成，可能需要 30–120 秒…
        </div>
      )}
    </Modal>
  );
};
