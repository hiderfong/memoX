import React, { useCallback, useEffect, useState } from 'react';
import { ReactFlow, MiniMap, Controls, Background, useNodesState, useEdgesState, addEdge, Handle, Position, Panel } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import { Typography, Tag, Space, Button, Menu, MenuProps, Modal, Input, message, Drawer, Form, Empty } from 'antd';
import { SyncOutlined, CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined } from '@ant-design/icons';
import axios from 'axios';
import yaml from 'js-yaml';

const { Text } = Typography;
const { TextArea } = Input;

// Custom Node for Workflow Step
const WorkflowStepNode = ({ data }: any) => {
  let borderStyle = '1px solid #d9d9d9';
  let StatusIcon = null;
  let bgStyle = '#fff';

  if (data.status === 'running') {
    borderStyle = '2px solid #1677ff';
    StatusIcon = <SyncOutlined spin style={{ color: '#1677ff' }} />;
  } else if (data.status === 'completed') {
    borderStyle = '2px solid #52c41a';
    StatusIcon = <CheckCircleOutlined style={{ color: '#52c41a' }} />;
  } else if (data.status === 'failed') {
    borderStyle = '2px solid #ff4d4f';
    bgStyle = '#fff1f0';
    StatusIcon = <CloseCircleOutlined style={{ color: '#ff4d4f' }} />;
  } else if (data.status === 'pending') {
    borderStyle = '1px dashed #d9d9d9';
    StatusIcon = <ClockCircleOutlined style={{ color: '#bfbfbf' }} />;
  }

  return (
    <div style={{
      padding: '10px 15px',
      borderRadius: '8px',
      background: bgStyle,
      border: borderStyle,
      boxShadow: data.status === 'running' ? '0 0 8px rgba(22, 119, 255, 0.5)' : '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
      minWidth: '200px',
      transition: 'all 0.3s ease'
    }}>
      <Handle type="target" position={Position.Top} className="w-16 !bg-blue-500" />
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={{ fontWeight: 'bold', fontSize: '14px', borderBottom: '1px solid #f0f0f0', paddingBottom: '4px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{data.label}</span>
          {StatusIcon && <span>{StatusIcon}</span>}
        </div>
        <div style={{ fontSize: '12px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <div><Text type="secondary">Worker: </Text> <Tag color="blue">{data.worker}</Tag></div>
          {data.condition && data.condition !== 'always' && (
            <div><Text type="secondary">Cond: </Text> <Tag color="warning">{data.condition}</Tag></div>
          )}
          {data.map_over && (
            <div><Text type="secondary">Map: </Text> <Tag color="purple">{data.map_over}</Tag></div>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="w-16 !bg-blue-500" />
    </div>
  );
};

const nodeTypes = {
  workflowStep: WorkflowStepNode,
};

const dagreGraph = new dagre.graphlib.Graph();
dagreGraph.setDefaultEdgeLabel(() => ({}));

const getLayoutedElements = (nodes: any[], edges: any[], direction = 'TB') => {
  const isHorizontal = direction === 'LR';
  dagreGraph.setGraph({ rankdir: direction, nodesep: 100, ranksep: 100 });

  nodes.forEach((node) => {
    // estimate node sizes
    dagreGraph.setNode(node.id, { width: 200, height: 100 });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  const newNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    const newNode = { ...node };

    // Shift positions
    newNode.targetPosition = isHorizontal ? 'left' : 'top';
    newNode.sourcePosition = isHorizontal ? 'right' : 'bottom';

    // We are shifting the dagre node position (anchor=center center) to the top left
    // so it matches the React Flow node anchor point (top left).
    newNode.position = {
      x: nodeWithPosition.x - 200 / 2,
      y: nodeWithPosition.y - 100 / 2,
    };

    return newNode;
  });

  return { nodes: newNodes, edges };
};

export const WorkflowCanvas = ({ yamlContent, activeRunData, onYamlChange }: { yamlContent: string; activeRunData?: any; onYamlChange?: (newYaml: string) => void }) => {
  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [menuVisible, setMenuVisible] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [contextModalVisible, setContextModalVisible] = useState(false);
  const [mockContextJson, setMockContextJson] = useState('{\n  "previous_step": {\n    "result": "mock output"\n  }\n}');
  const [executingStep, setExecutingStep] = useState(false);

  const [drawerVisible, setDrawerVisible] = useState(false);
  const [selectedNodeData, setSelectedNodeData] = useState<any>(null);
  const [editNodeForm] = Form.useForm();

  const fetchLayout = useCallback(async () => {
    if (!yamlContent) return;
    setLoading(true);
    setError(null);
    try {
      const res = await axios.post('/api/workflows/visualize', { yaml_content: yamlContent });
      const { nodes: initialNodes, edges: initialEdges } = res.data;

      const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
        initialNodes,
        initialEdges
      );

      setNodes(layoutedNodes);
      setEdges(layoutedEdges);
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [yamlContent, setNodes, setEdges]);

  useEffect(() => {
    fetchLayout();
  }, [fetchLayout]);

  useEffect(() => {
    if (!activeRunData || !activeRunData.steps) return;

    setNodes((nds) =>
      nds.map((node) => {
        const stepRecord = activeRunData.steps.find((s: any) => s.step_id === node.id);
        const newStatus = stepRecord ? stepRecord.status : 'pending';
        return {
          ...node,
          data: {
            ...node.data,
            status: newStatus,
          },
        };
      })
    );

    setEdges((eds) =>
      eds.map((edge) => {
        const targetStep = activeRunData.steps.find((s: any) => s.step_id === edge.target);
        // 如果目标节点正在运行，让连线变成动画状态
        const isRunning = targetStep && targetStep.status === 'running';
        const isCompleted = targetStep && targetStep.status === 'completed';
        return {
          ...edge,
          animated: isRunning || !isCompleted,
          style: { stroke: isRunning ? '#1677ff' : '#b1b1b7', strokeWidth: isRunning ? 2 : 1 }
        };
      })
    );
  }, [activeRunData, setNodes, setEdges]);

  const onConnect = useCallback(
    (params: any) => setEdges((eds) => addEdge(params, eds)),
    [setEdges],
  );

  const onLayout = useCallback(
    (direction: string) => {
      const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
        nodes,
        edges,
        direction
      );

      setNodes([...layoutedNodes]);
      setEdges([...layoutedEdges]);
    },
    [nodes, edges, setNodes, setEdges]
  );

  const onNodeClick = useCallback((event: React.MouseEvent, node: any) => {
    setSelectedNodeData(node.data);
    editNodeForm.setFieldsValue({
      description: node.data.description || '',
      condition: node.data.condition || 'always',
      condition_expr: node.data.condition_expr || '',
      map_over: node.data.map_over || '',
      input_str: JSON.stringify(node.data.input || {}, null, 2)
    });
    setDrawerVisible(true);
  }, [editNodeForm]);

  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: any) => {
      event.preventDefault();
      setSelectedNodeId(node.id);
      setMenuPosition({ top: event.clientY, left: event.clientX });
      setMenuVisible(true);
    },
    []
  );

  const handleMenuClick: MenuProps['onClick'] = (e) => {
    if (e.key === 'execute') {
      setContextModalVisible(true);
    }
    setMenuVisible(false);
  };

  const handleExecuteNode = async () => {
    if (!selectedNodeId) return;

    let parsedContext = {};
    try {
      parsedContext = JSON.parse(mockContextJson);
    } catch (err) {
      message.error('Mock Context 必须是合法的 JSON 格式');
      return;
    }

    setExecutingStep(true);
    try {
      const res = await axios.post('/api/workflows/run_step', {
        yaml_content: yamlContent,
        step_id: selectedNodeId,
        context: parsedContext
      });
      const data = res.data;
      if (data.status === 'completed') {
        Modal.success({
          title: `节点 ${selectedNodeId} 执行成功`,
          content: <pre style={{ maxHeight: 300, overflow: 'auto' }}>{JSON.stringify(data.output, null, 2)}</pre>,
          width: 600
        });
      } else {
        Modal.error({
          title: `节点 ${selectedNodeId} 执行失败`,
          content: data.error,
        });
      }
      setContextModalVisible(false);
    } catch (err: any) {
      message.error(err.response?.data?.detail || err.message);
    } finally {
      setExecutingStep(false);
    }
  };

  const handleSaveNode = async () => {
    if (!selectedNodeData || !yamlContent || !onYamlChange) return;
    try {
      const values = await editNodeForm.validateFields();

      let parsedInput = {};
      try {
        parsedInput = JSON.parse(values.input_str);
      } catch (err) {
        message.error("输入参数必须是合法的 JSON");
        return;
      }

      // Parse YAML
      const doc = yaml.load(yamlContent) as any;
      if (doc && doc.steps && Array.isArray(doc.steps)) {
        const stepIndex = doc.steps.findIndex((s: any) => s.id === selectedNodeData.label);
        if (stepIndex !== -1) {
          if (values.description) doc.steps[stepIndex].description = values.description;
          else delete doc.steps[stepIndex].description;

          if (values.condition && values.condition !== 'always') doc.steps[stepIndex].condition = values.condition;
          else delete doc.steps[stepIndex].condition;

          if (values.condition_expr) doc.steps[stepIndex].condition_expr = values.condition_expr;
          else delete doc.steps[stepIndex].condition_expr;

          if (values.map_over) doc.steps[stepIndex].map_over = values.map_over;
          else delete doc.steps[stepIndex].map_over;

          doc.steps[stepIndex].input = parsedInput;

          const newYaml = yaml.dump(doc);
          onYamlChange(newYaml);
          message.success("节点属性已更新");
          setDrawerVisible(false);
        }
      }
    } catch (err) {
      console.error(err);
    }
  };

  if (loading) {
    return <div>Loading workflow visualization...</div>;
  }

  if (error) {
    return <div style={{ color: 'red' }}>Error: {error}</div>;
  }

  return (
    <div
      style={{ width: '100%', height: '600px', border: '1px solid #eee', borderRadius: '8px', position: 'relative' }}
      onClick={() => setMenuVisible(false)}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onNodeContextMenu={onNodeContextMenu}
        nodeTypes={nodeTypes}
        fitView
      >
        <Panel position="top-right">
          <Space>
            <Button onClick={() => onLayout('TB')}>Vertical Layout</Button>
            <Button onClick={() => onLayout('LR')}>Horizontal Layout</Button>
          </Space>
        </Panel>
        <Controls />
        <MiniMap />
        <Background gap={12} size={1} />
      </ReactFlow>

      {menuVisible && (
        <div
          style={{
            position: 'fixed',
            top: menuPosition.top,
            left: menuPosition.left,
            zIndex: 1000,
            boxShadow: '0 2px 8px rgba(0, 0, 0, 0.15)',
          }}
        >
          <Menu
            onClick={handleMenuClick}
            items={[
              { key: 'execute', label: '执行节点 (Run Step)' }
            ]}
          />
        </div>
      )}

      <Modal
        title={`执行节点: ${selectedNodeId}`}
        open={contextModalVisible}
        onOk={handleExecuteNode}
        confirmLoading={executingStep}
        onCancel={() => setContextModalVisible(false)}
        okText="运行"
        cancelText="取消"
      >
        <div style={{ marginBottom: 16 }}>
          <Text type="secondary">
            该节点可能依赖前面步骤的输出。你可以在下方注入 Mock 的上下文数据 (JSON格式)：
          </Text>
        </div>
        <TextArea
          rows={6}
          value={mockContextJson}
          onChange={(e) => setMockContextJson(e.target.value)}
          style={{ fontFamily: 'monospace' }}
        />
      </Modal>

      <Drawer
        title="节点属性"
        placement="right"
        onClose={() => setDrawerVisible(false)}
        open={drawerVisible}
        width={400}
        extra={
          <Space>
            <Button onClick={() => setDrawerVisible(false)}>取消</Button>
            <Button type="primary" onClick={handleSaveNode} disabled={!onYamlChange}>
              保存
            </Button>
          </Space>
        }
      >
        {selectedNodeData ? (
          <Form layout="vertical" form={editNodeForm}>
            <Form.Item label="节点 ID">
              <Input value={selectedNodeData.label} readOnly variant="borderless" />
            </Form.Item>
            <Form.Item label="Worker">
              <Input value={selectedNodeData.worker} readOnly variant="borderless" />
            </Form.Item>
            <Form.Item label="描述 (Description)" name="description">
              <TextArea rows={2} placeholder="节点描述" />
            </Form.Item>
            <Form.Item label="条件模式 (Condition)" name="condition">
              <Input placeholder="always, if_result, custom" />
            </Form.Item>
            <Form.Item label="条件表达式 (Condition Expr)" name="condition_expr" tooltip="Python eval() 表达式">
              <Input placeholder="例如: ${step.result} > 0" />
            </Form.Item>
            <Form.Item label="循环 (Map Over)" name="map_over" tooltip="要遍历的列表变量引用">
              <Input placeholder="例如: ${step.items}" />
            </Form.Item>
            <Form.Item label="输入参数 (Input, JSON格式)" name="input_str">
              <TextArea rows={8} style={{ fontFamily: 'monospace' }} />
            </Form.Item>
          </Form>
        ) : (
          <Empty description="未选择节点" />
        )}
      </Drawer>
    </div>
  );
};
