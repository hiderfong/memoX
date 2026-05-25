import React, { useState, useEffect, useRef } from 'react';
import { Card, Input, Button, Space, Typography, Row, Col, message } from 'antd';
import { PlayCircleOutlined, SyncOutlined } from '@ant-design/icons';
import { WorkflowCanvas } from '../components/WorkflowCanvas';
import axios from 'axios';

const { Title, Text } = Typography;
const { TextArea } = Input;

const DEFAULT_YAML = `name: sample_workflow
steps:
  - id: researcher
    worker: research_worker
    input: "请搜索关于 React Flow 的最新资料"
  
  - id: writer
    worker: code_worker
    input: "根据资料总结：\${researcher.result}"
    condition: if_result
`;

export const WorkflowsPage: React.FC = () => {
  const [yamlInput, setYamlInput] = useState(DEFAULT_YAML);
  const [visualizeYaml, setVisualizeYaml] = useState(DEFAULT_YAML);
  
  const [activeRunData, setActiveRunData] = useState<any>(null);
  const [isRunning, setIsRunning] = useState(false);
  const pollingRef = useRef<number | null>(null);

  const startPolling = (runId: string) => {
    if (pollingRef.current) {
      window.clearInterval(pollingRef.current);
    }
    pollingRef.current = window.setInterval(async () => {
      try {
        const res = await axios.get(`/api/workflows/runs/${runId}`);
        const runData = res.data;
        setActiveRunData(runData);
        if (runData.status === 'completed' || runData.status === 'failed') {
          if (pollingRef.current) window.clearInterval(pollingRef.current);
          setIsRunning(false);
          message.success(`工作流执行结束 (状态: ${runData.status})`);
        }
      } catch (err) {
        console.error("Polling error", err);
      }
    }, 1000);
  };

  useEffect(() => {
    return () => {
      if (pollingRef.current) window.clearInterval(pollingRef.current);
    };
  }, []);

  const handleVisualize = () => {
    if (!yamlInput.trim()) {
      message.warning('YAML 内容不能为空');
      return;
    }
    setVisualizeYaml(yamlInput);
    setActiveRunData(null); // Reset active run data when changing YAML
  };

  const handleRunWorkflow = async () => {
    if (!yamlInput.trim()) return;
    setVisualizeYaml(yamlInput); // Ensure canvas matches current YAML
    setIsRunning(true);
    setActiveRunData(null);
    try {
      const res = await axios.post('/api/workflows/run', {
        yaml_content: yamlInput,
        context: {}
      });
      const { run_id } = res.data;
      message.info(`已启动工作流执行: ${run_id}`);
      startPolling(run_id);
    } catch (err: any) {
      message.error(err.response?.data?.detail || err.message);
      setIsRunning(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Title level={4} style={{ margin: 0 }}>工作流可视化编排 (DAG)</Title>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <Card 
            title="YAML 定义" 
            size="small" 
            extra={
              <Space>
                <Button onClick={handleVisualize}>更新画布</Button>
                <Button 
                  type="primary" 
                  icon={isRunning ? <SyncOutlined spin /> : <PlayCircleOutlined />}
                  onClick={handleRunWorkflow}
                  loading={isRunning}
                >
                  {isRunning ? '执行中...' : '运行工作流'}
                </Button>
              </Space>
            }
            styles={{ body: { padding: 0 } }}
          >
            <TextArea
              value={yamlInput}
              onChange={(e) => setYamlInput(e.target.value)}
              style={{ 
                width: '100%', 
                height: '600px', 
                border: 'none', 
                borderRadius: '0 0 8px 8px',
                fontFamily: 'monospace',
                resize: 'none',
                padding: '12px'
              }}
              spellCheck={false}
            />
          </Card>
        </Col>
        <Col xs={24} lg={16}>
          <Card title="React Flow 画布" size="small" styles={{ body: { padding: 0 } }}>
            <WorkflowCanvas 
            yamlContent={visualizeYaml} 
            activeRunData={activeRunData} 
            onYamlChange={(newYaml) => {
              setYamlInput(newYaml);
              setVisualizeYaml(newYaml);
            }}
          />
          </Card>
        </Col>
      </Row>
    </div>
  );
};
