import React, { useEffect, useRef, useState } from 'react';
import ForceGraph2D, { ForceGraphMethods } from 'react-force-graph-2d';
import { Spin, Alert, Card } from 'antd';
import { api } from '../shared';
import axios from 'axios';

export const KnowledgeGraphView: React.FC = () => {
  const [graphData, setGraphData] = useState<{ nodes: any[]; links: any[] }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fgRef = useRef<ForceGraphMethods>();

  useEffect(() => {
    const fetchGraph = async () => {
      try {
        setLoading(true);
        // Note: adjust api base if necessary. Here we use axios to /api/knowledge/graph
        const res = await axios.get('/api/knowledge/graph');
        setGraphData(res.data);
      } catch (err: any) {
        setError(err.response?.data?.detail || err.message || '获取图谱失败');
      } finally {
        setLoading(false);
      }
    };
    fetchGraph();
  }, []);

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 50 }}><Spin tip="加载知识图谱..." /></div>;
  }

  if (error) {
    return <Alert message="加载失败" description={error} type="error" showIcon />;
  }

  if (graphData.nodes.length === 0) {
    return <Alert message="暂无图谱数据" description="请先上传文档并确保开启了图谱提取功能。" type="info" showIcon />;
  }

  return (
    <Card styles={{ body: { padding: 0, height: '600px' } }}>
      <ForceGraph2D
        ref={fgRef}
        graphData={graphData}
        nodeLabel="name"
        nodeColor={(node: any) => {
          // generate color based on degree (val)
          return node.val > 5 ? '#ff4d4f' : '#1890ff';
        }}
        nodeRelSize={4}
        linkLabel="label"
        linkColor={() => 'rgba(200, 200, 200, 0.5)'}
        linkDirectionalArrowLength={3.5}
        linkDirectionalArrowRelPos={1}
        linkCurvature={0.25}
        onNodeClick={(node) => {
          // Center the node
          fgRef.current?.centerAt(node.x, node.y, 1000);
          fgRef.current?.zoom(4, 2000);
        }}
        width={undefined} // auto fit container
        height={600}
      />
    </Card>
  );
};
