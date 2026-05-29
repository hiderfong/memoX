import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D, { ForceGraphMethods } from 'react-force-graph-2d';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Progress,
  Row,
  Select,
  Slider,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  AimOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeInvisibleOutlined,
  MergeCellsOutlined,
  ReloadOutlined,
  SplitCellsOutlined,
} from '@ant-design/icons';

import {
  api,
  KnowledgeGraphEntity,
  KnowledgeGraphLink,
  KnowledgeGraphPayload,
  KnowledgeGraphQualityCandidate,
  KnowledgeGraphQualityAlert,
  KnowledgeGraphQualityPayload,
  KnowledgeGraphQualitySnapshot,
  KnowledgeGraphReviewStatus,
  KnowledgeGraphTripleMutation,
} from '../shared';

const { Text } = Typography;

const emptyPayload: KnowledgeGraphPayload = {
  nodes: [],
  links: [],
  stats: {},
  entities: [],
  predicates: [],
  matched_entity: null,
  filters: {
    entity: '',
    query: '',
    depth: 1,
    limit: 1000,
    min_confidence: 0,
    predicate: '',
  },
};

const emptyQualityPayload: KnowledgeGraphQualityPayload = {
  summary: {
    total_candidates: 0,
    returned_candidates: 0,
    duplicate_entity_count: 0,
    low_confidence_relation_count: 0,
    isolated_relation_count: 0,
    conflicting_relation_count: 0,
    ambiguous_entity_count: 0,
    average_confidence: 0,
  },
  candidates: [],
  thresholds: {
    confidence_threshold: 0.6,
    limit: 20,
  },
};

function endpointId(value: KnowledgeGraphLink['source']) {
  if (typeof value === 'object' && value !== null) return value.id;
  return String(value || '');
}

type RelationRow = KnowledgeGraphLink & {
  key: string;
  sourceName: string;
  targetName: string;
};

type GraphFetchOverrides = Partial<{
  focusedEntity: string;
  searchValue: string;
  depth: number;
  minConfidence: number;
  predicate: string;
  qualityStatus: string;
}>;

type ActiveQualityCandidate = Pick<KnowledgeGraphQualityCandidate, 'id' | 'fingerprint' | 'type' | 'title'>;

type RelationFormValues = {
  subject: string;
  predicate: string;
  object: string;
  confidence: number;
};

type SplitFormValues = {
  source: string;
  new_entity: string;
};

type SplitContext = {
  source: string;
  newEntity?: string;
  triples: KnowledgeGraphTripleMutation[];
  candidate?: ActiveQualityCandidate | null;
};

type KnowledgeGraphViewProps = {
  autoFocusQualityQueue?: boolean;
  initialQualityStatus?: string;
};

function relationToMutation(row: RelationRow): KnowledgeGraphTripleMutation {
  return {
    subject: row.sourceName,
    predicate: row.predicate,
    object: row.targetName,
    source_chunk_id: row.source_chunk_id || '',
    confidence: row.confidence,
  };
}

function tripleToRelationRow(triple: KnowledgeGraphTripleMutation): RelationRow {
  return {
    key: `${triple.subject}-${triple.predicate}-${triple.object}-${triple.source_chunk_id || ''}`,
    source: triple.subject,
    target: triple.object,
    label: triple.predicate,
    predicate: triple.predicate,
    confidence: triple.confidence ?? 1,
    source_chunk_id: triple.source_chunk_id || '',
    sourceName: triple.subject,
    targetName: triple.object,
  };
}

function issueTypeLabel(type: string) {
  if (type === 'duplicate_entity') return '重复实体';
  if (type === 'ambiguous_entity') return '多义实体';
  if (type === 'low_confidence_relation') return '低置信度';
  if (type === 'isolated_relation') return '孤立关系';
  if (type === 'conflicting_relation') return '冲突关系';
  return type;
}

function severityColor(severity: string) {
  if (severity === 'high') return 'red';
  if (severity === 'medium') return 'orange';
  return 'blue';
}

function riskColor(level?: string) {
  if (level === 'high') return 'red';
  if (level === 'medium') return 'orange';
  return 'green';
}

function riskLabel(level?: string) {
  if (level === 'high') return '高风险';
  if (level === 'medium') return '需关注';
  return '健康';
}

function metricPercent(value?: number) {
  return Math.round((value || 0) * 100);
}

function candidateDecisionDetails(candidate: ActiveQualityCandidate) {
  return {
    candidate_fingerprint: candidate.fingerprint,
    candidate_type: candidate.type,
    candidate_title: candidate.title,
  };
}

function candidateStatusMessage(status: KnowledgeGraphReviewStatus, count: number = 1) {
  const prefix = count > 1 ? `${count} 项` : '';
  if (status === 'accepted') return `${prefix}已标记处理`;
  if (status === 'ignored') return `${prefix}已忽略`;
  if (status === 'snoozed') return `${prefix}已移到稍后处理`;
  return `${prefix}已重新打开`;
}

function renderTripleEvidence(triple: KnowledgeGraphTripleMutation, index: number) {
  return (
    <Space key={`${triple.subject}-${triple.predicate}-${triple.object}-${index}`} size={4} wrap>
      <Tag>{triple.subject}</Tag>
      <Text type="secondary">{triple.predicate}</Text>
      <Tag>{triple.object}</Tag>
      {typeof triple.confidence === 'number' ? (
        <Tag color={triple.confidence < 0.6 ? 'orange' : 'green'}>
          {Math.round(triple.confidence * 100)}%
        </Tag>
      ) : null}
      {triple.source_chunk_id ? <Text type="secondary">{triple.source_chunk_id}</Text> : null}
    </Space>
  );
}

function renderCandidateEvidence(candidate: KnowledgeGraphQualityCandidate) {
  const triples = [
    ...(candidate.triple ? [candidate.triple] : []),
    ...(candidate.related_triples || []),
  ];
  if (!triples.length && !candidate.reasons?.length) return null;
  return (
    <Collapse
      size="small"
      ghost
      items={[{
        key: 'evidence',
        label: '证据',
        children: (
          <Space direction="vertical" size={4}>
            {triples.slice(0, 5).map(renderTripleEvidence)}
            {candidate.reasons?.length ? (
              <Space size={4} wrap>
                {candidate.reasons.map(reason => <Tag key={reason}>{reason}</Tag>)}
              </Space>
            ) : null}
          </Space>
        ),
      }]}
    />
  );
}

function formatTrendTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function renderQualityTrend(snapshots: KnowledgeGraphQualitySnapshot[]) {
  if (!snapshots.length) {
    return <Text type="secondary">暂无历史快照</Text>;
  }
  const recent = snapshots.slice(-12);
  const first = recent[0];
  const latest = recent[recent.length - 1];
  const delta = latest.health_score - first.health_score;
  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, minHeight: 50 }}>
        {recent.map(snapshot => (
          <Tooltip
            key={`${snapshot.id}-${snapshot.created_at}`}
            title={`${formatTrendTime(snapshot.created_at)} 健康分 ${snapshot.health_score}`}
          >
            <div
              style={{
                width: 12,
                height: Math.max(8, Math.round(snapshot.health_score * 0.48)),
                borderRadius: 2,
                background: snapshot.health_score >= 75 ? '#52c41a' : snapshot.health_score >= 50 ? '#faad14' : '#ff4d4f',
              }}
            />
          </Tooltip>
        ))}
      </div>
      <Space size={6} wrap>
        <Tag color={delta >= 0 ? 'green' : 'red'}>健康 {delta >= 0 ? '+' : ''}{delta}</Tag>
        <Tag>待审 {metricPercent(latest.open_review_backlog_ratio ?? latest.review_backlog_ratio)}%</Tag>
        <Text type="secondary">{formatTrendTime(latest.created_at)}</Text>
      </Space>
    </Space>
  );
}

function renderQualityAlertDescription(alert: KnowledgeGraphQualityAlert) {
  return (
    <Space direction="vertical" size={2}>
      <Text>{alert.message}</Text>
      <Text type="secondary">{alert.action}</Text>
    </Space>
  );
}

function uniqueEntityOptions(values: string[]) {
  return Array.from(new Set(values.filter(Boolean))).map(value => ({ label: value, value }));
}

export const KnowledgeGraphView = ({
  autoFocusQualityQueue = false,
  initialQualityStatus = 'open',
}: KnowledgeGraphViewProps) => {
  const [graphData, setGraphData] = useState<KnowledgeGraphPayload>(emptyPayload);
  const [qualityData, setQualityData] = useState<KnowledgeGraphQualityPayload>(emptyQualityPayload);
  const [qualitySnapshots, setQualitySnapshots] = useState<KnowledgeGraphQualitySnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchValue, setSearchValue] = useState('');
  const [focusedEntity, setFocusedEntity] = useState('');
  const [predicate, setPredicate] = useState('');
  const [depth, setDepth] = useState(1);
  const [minConfidence, setMinConfidence] = useState(0);
  const [qualityStatus, setQualityStatus] = useState(initialQualityStatus);
  const [graphWidth, setGraphWidth] = useState(900);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeSaving, setMergeSaving] = useState(false);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([]);
  const [batchSaving, setBatchSaving] = useState(false);
  const [editingRelation, setEditingRelation] = useState<RelationRow | null>(null);
  const [splitContext, setSplitContext] = useState<SplitContext | null>(null);
  const [relationSaving, setRelationSaving] = useState(false);
  const [splitSaving, setSplitSaving] = useState(false);
  const [mergeForm] = Form.useForm<{ source: string; target: string }>();
  const [relationForm] = Form.useForm<RelationFormValues>();
  const [splitForm] = Form.useForm<SplitFormValues>();
  const [messageApi, contextHolder] = message.useMessage();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const qualityQueueRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<ForceGraphMethods>();
  const activeCandidate = useRef<ActiveQualityCandidate | null>(null);

  const fetchGraph = useCallback(async (overrides: GraphFetchOverrides = {}) => {
    const nextFocusedEntity = overrides.focusedEntity ?? focusedEntity;
    const nextSearchValue = overrides.searchValue ?? searchValue;
    const nextDepth = overrides.depth ?? depth;
    const nextMinConfidence = overrides.minConfidence ?? minConfidence;
    const nextPredicate = overrides.predicate ?? predicate;
    const nextQualityStatus = overrides.qualityStatus ?? qualityStatus;
    try {
      setLoading(true);
      setError(null);
      const [graphRes, qualityRes, historyRes] = await Promise.all([
        api.getKnowledgeGraph({
          entity: nextFocusedEntity || undefined,
          q: nextFocusedEntity ? undefined : nextSearchValue.trim() || undefined,
          depth: nextDepth,
          min_confidence: nextMinConfidence,
          predicate: nextPredicate || undefined,
          limit: 1200,
        }),
        api.getKnowledgeGraphQuality({
          confidence_threshold: Math.max(nextMinConfidence, 0.6),
          limit: 20,
          status: nextQualityStatus,
        }),
        api.getKnowledgeGraphQualityHistory({ limit: 30 }),
      ]);
      setGraphData(graphRes.data);
      setQualityData(qualityRes.data);
      setQualitySnapshots(historyRes.data.snapshots);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || '获取图谱失败');
    } finally {
      setLoading(false);
    }
  }, [depth, focusedEntity, minConfidence, predicate, qualityStatus, searchValue]);

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  useEffect(() => {
    setQualityStatus(initialQualityStatus);
  }, [initialQualityStatus]);

  useEffect(() => {
    if (!autoFocusQualityQueue || loading || !qualityQueueRef.current) return undefined;
    const timer = window.setTimeout(() => {
      qualityQueueRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 120);
    return () => window.clearTimeout(timer);
  }, [
    autoFocusQualityQueue,
    loading,
    qualityData.summary.returned_candidates,
    qualityData.summary.total_candidates,
  ]);

  useEffect(() => {
    if (!containerRef.current) return;
    const updateWidth = () => {
      const width = containerRef.current?.clientWidth;
      if (width && width > 320) setGraphWidth(width);
    };
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  const predicateOptions = useMemo(
    () => graphData.predicates.map(item => ({
      label: `${item.predicate} (${item.count})`,
      value: item.predicate,
    })),
    [graphData.predicates],
  );

  const relationRows = useMemo<RelationRow[]>(
    () => graphData.links.slice(0, 100).map((link, index) => ({
      ...link,
      key: `${endpointId(link.source)}-${link.predicate}-${endpointId(link.target)}-${index}`,
      sourceName: endpointId(link.source),
      targetName: endpointId(link.target),
    })),
    [graphData.links],
  );

  const candidateById = useMemo(
    () => new Map(qualityData.candidates.map(candidate => [candidate.id, candidate])),
    [qualityData.candidates],
  );

  const visibleCandidateIds = useMemo(
    () => qualityData.candidates.map(candidate => candidate.id),
    [qualityData.candidates],
  );

  const selectedCandidateIdSet = useMemo(
    () => new Set(selectedCandidateIds),
    [selectedCandidateIds],
  );

  const selectedCandidates = useMemo(
    () => selectedCandidateIds
      .map(candidateId => candidateById.get(candidateId))
      .filter((candidate): candidate is KnowledgeGraphQualityCandidate => Boolean(candidate)),
    [candidateById, selectedCandidateIds],
  );

  const allVisibleCandidatesSelected = (
    visibleCandidateIds.length > 0
    && visibleCandidateIds.every(candidateId => selectedCandidateIdSet.has(candidateId))
  );
  const someVisibleCandidatesSelected = visibleCandidateIds.some(candidateId => (
    selectedCandidateIdSet.has(candidateId)
  ));

  useEffect(() => {
    setSelectedCandidateIds(previous => previous.filter(candidateId => candidateById.has(candidateId)));
  }, [candidateById]);

  const focusEntity = (name: string) => {
    setFocusedEntity(name);
    setSearchValue(name);
  };

  const resetFilters = () => {
    setSearchValue('');
    setFocusedEntity('');
    setPredicate('');
    setDepth(1);
    setMinConfidence(0);
  };

  const markCandidateDecision = async (
    candidate: ActiveQualityCandidate | null,
    status: KnowledgeGraphReviewStatus,
    note: string = '',
  ) => {
    if (!candidate) return;
    await api.setKnowledgeGraphQualityDecision({
      candidate_id: candidate.id,
      status,
      note,
      details: candidateDecisionDetails(candidate),
    });
  };

  const toggleCandidateSelection = (candidateId: string, checked: boolean) => {
    setSelectedCandidateIds(previous => (
      checked
        ? Array.from(new Set([...previous, candidateId]))
        : previous.filter(item => item !== candidateId)
    ));
  };

  const toggleVisibleSelection = (checked: boolean) => {
    setSelectedCandidateIds(previous => {
      if (!checked) {
        const visibleIds = new Set(visibleCandidateIds);
        return previous.filter(candidateId => !visibleIds.has(candidateId));
      }
      return Array.from(new Set([...previous, ...visibleCandidateIds]));
    });
  };

  const setSelectedCandidatesStatus = async (status: KnowledgeGraphReviewStatus) => {
    if (!selectedCandidates.length) return;
    setBatchSaving(true);
    try {
      await api.setKnowledgeGraphQualityDecisions({
        decisions: selectedCandidates.map(candidate => ({
          candidate_id: candidate.id,
          status,
          note: `batch ${status} from quality queue`,
          details: candidateDecisionDetails(candidate),
        })),
      });
      messageApi.success(candidateStatusMessage(status, selectedCandidates.length));
      setSelectedCandidateIds([]);
      await fetchGraph();
    } catch (err: any) {
      messageApi.error(err.response?.data?.detail || err.message || '批量保存审核状态失败');
    } finally {
      setBatchSaving(false);
    }
  };

  const openMergeModal = (source?: string, target?: string, candidate?: ActiveQualityCandidate) => {
    activeCandidate.current = candidate || null;
    const suggestedSource = source || focusedEntity || graphData.matched_entity || searchValue.trim();
    mergeForm.setFieldsValue({ source: suggestedSource, target: target || '' });
    setMergeOpen(true);
  };

  const submitMerge = async () => {
    const values = await mergeForm.validateFields();
    const source = values.source.trim();
    const target = values.target.trim();
    setMergeSaving(true);
    try {
      await api.mergeKnowledgeGraphEntities({ source, target });
      await markCandidateDecision(activeCandidate.current, 'accepted', 'merged entity from quality queue');
      messageApi.success('实体已合并');
      activeCandidate.current = null;
      setMergeOpen(false);
      setFocusedEntity(target);
      setSearchValue(target);
      await fetchGraph({ focusedEntity: target, searchValue: target });
    } catch (err: any) {
      messageApi.error(err.response?.data?.detail || err.message || '合并实体失败');
    } finally {
      setMergeSaving(false);
    }
  };

  const openEditRelation = (row: RelationRow, candidate?: ActiveQualityCandidate) => {
    activeCandidate.current = candidate || null;
    setEditingRelation(row);
    relationForm.setFieldsValue({
      subject: row.sourceName,
      predicate: row.predicate,
      object: row.targetName,
      confidence: row.confidence,
    });
  };

  const submitRelationUpdate = async () => {
    if (!editingRelation) return;
    const values = await relationForm.validateFields();
    setRelationSaving(true);
    try {
      await api.updateKnowledgeGraphTriple({
        old: relationToMutation(editingRelation),
        new: {
          subject: values.subject.trim(),
          predicate: values.predicate.trim(),
          object: values.object.trim(),
          source_chunk_id: editingRelation.source_chunk_id || '',
          confidence: values.confidence,
        },
      });
      await markCandidateDecision(activeCandidate.current, 'accepted', 'updated relation from quality queue');
      messageApi.success('关系已更新');
      activeCandidate.current = null;
      setEditingRelation(null);
      await fetchGraph();
    } catch (err: any) {
      messageApi.error(err.response?.data?.detail || err.message || '更新关系失败');
    } finally {
      setRelationSaving(false);
    }
  };

  const deleteRelation = async (row: RelationRow, candidate?: ActiveQualityCandidate) => {
    try {
      await api.deleteKnowledgeGraphTriple(relationToMutation(row));
      await markCandidateDecision(candidate || null, 'accepted', 'deleted relation from quality queue');
      messageApi.success('关系已删除');
      await fetchGraph();
    } catch (err: any) {
      messageApi.error(err.response?.data?.detail || err.message || '删除关系失败');
    }
  };

  const openSplitRelation = (row: RelationRow) => {
    const defaultSource = [row.sourceName, row.targetName].includes(focusedEntity)
      ? focusedEntity
      : row.sourceName;
    setSplitContext({
      source: defaultSource,
      triples: [relationToMutation(row)],
    });
    splitForm.setFieldsValue({ source: defaultSource, new_entity: '' });
  };

  const openSplitSuggestion = (candidate: KnowledgeGraphQualityCandidate) => {
    const action = candidate.action;
    if (!action?.source || !action.triples?.length) return;
    setSplitContext({
      source: action.source,
      newEntity: action.new_entity || '',
      triples: action.triples,
      candidate,
    });
    splitForm.setFieldsValue({
      source: action.source,
      new_entity: action.new_entity || '',
    });
  };

  const submitEntitySplit = async () => {
    if (!splitContext) return;
    const values = await splitForm.validateFields();
    const newEntity = values.new_entity.trim();
    setSplitSaving(true);
    try {
      const response = await api.splitKnowledgeGraphEntity({
        source: values.source.trim(),
        new_entity: newEntity,
        triples: splitContext.triples,
      });
      await markCandidateDecision(splitContext.candidate || null, 'accepted', 'split entity from quality queue');
      messageApi.success(`已拆分 ${response.data.moved_edges || response.data.deduplicated_edges || 1} 条关系`);
      setSplitContext(null);
      setFocusedEntity(newEntity);
      setSearchValue(newEntity);
      await fetchGraph({ focusedEntity: newEntity, searchValue: newEntity });
    } catch (err: any) {
      messageApi.error(err.response?.data?.detail || err.message || '拆分实体失败');
    } finally {
      setSplitSaving(false);
    }
  };

  const setCandidateStatus = async (
    candidate: KnowledgeGraphQualityCandidate,
    status: KnowledgeGraphReviewStatus,
  ) => {
    try {
      await markCandidateDecision(candidate, status);
      messageApi.success(candidateStatusMessage(status));
      await fetchGraph();
    } catch (err: any) {
      messageApi.error(err.response?.data?.detail || err.message || '保存审核状态失败');
    }
  };

  const renderCandidateActions = (candidate: KnowledgeGraphQualityCandidate) => {
    const action = candidate.action;
    if (action?.type === 'merge_entities' && action.source && action.target) {
      return (
        <Button
          key="merge"
          size="small"
          aria-label={`合并实体 ${action.source} 到 ${action.target}`}
          icon={<MergeCellsOutlined />}
          onClick={() => openMergeModal(action.source, action.target, candidate)}
        />
      );
    }
    if (action?.type === 'split_entity' && action.source && action.triples?.length) {
      return (
        <Button
          key="split"
          size="small"
          aria-label="拆分实体"
          icon={<SplitCellsOutlined />}
          onClick={() => openSplitSuggestion(candidate)}
        />
      );
    }
    if (candidate.triple) {
      const row = tripleToRelationRow(candidate.triple);
      return (
        <Space key="triple" size={2}>
          <Button
            size="small"
            type="text"
            aria-label="定位关系"
            icon={<AimOutlined />}
            onClick={() => focusEntity(candidate.triple?.subject || '')}
          />
          <Button
            size="small"
            type="text"
            aria-label="编辑关系"
            icon={<EditOutlined />}
            onClick={() => openEditRelation(row, candidate)}
          />
          <Popconfirm
            title="删除这条关系？"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => deleteRelation(row, candidate)}
          >
            <Button type="text" danger size="small" aria-label="删除关系" icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      );
    }
    if (candidate.entities?.[0]) {
      return (
        <Button
          key="focus"
          size="small"
          type="text"
          aria-label="定位实体"
          icon={<AimOutlined />}
          onClick={() => focusEntity(candidate.entities?.[0] || '')}
        />
      );
    }
    return null;
  };

  const candidateActions = (candidate: KnowledgeGraphQualityCandidate) => ([
    renderCandidateActions(candidate),
    <Button
      key="accepted"
      size="small"
      type="text"
      aria-label="标记处理"
      icon={<CheckOutlined />}
      onClick={() => setCandidateStatus(candidate, 'accepted')}
    />,
    <Button
      key="snoozed"
      size="small"
      type="text"
      aria-label="稍后处理"
      icon={<ClockCircleOutlined />}
      onClick={() => setCandidateStatus(candidate, 'snoozed')}
    />,
    <Button
      key="ignored"
      size="small"
      type="text"
      aria-label="忽略候选"
      icon={<EyeInvisibleOutlined />}
      onClick={() => setCandidateStatus(candidate, 'ignored')}
    />,
  ].filter(Boolean));

  if (error) {
    return (
      <Alert
        message="加载失败"
        description={error}
        type="error"
        showIcon
        action={<Button size="small" onClick={() => fetchGraph()}>重试</Button>}
      />
    );
  }

  if (loading && graphData.nodes.length === 0) {
    return <div style={{ textAlign: 'center', padding: 50 }}><Spin tip="加载知识图谱..." /></div>;
  }

  const stats = graphData.stats || {};
  const matchedEntity = graphData.matched_entity || focusedEntity;
  const qualityMetrics = qualityData.summary.quality_metrics;
  const healthScore = qualityMetrics?.health_score ?? Math.round((qualityData.summary.average_confidence || 0) * 100);
  const openReviewRatio = qualityMetrics?.open_review_backlog_ratio ?? qualityMetrics?.review_backlog_ratio ?? 0;
  const qualityAlerts = qualityMetrics?.alerts || [];
  const qualityGate = qualityData.summary.quality_gate || qualityMetrics?.quality_gate;
  const qualityGateViolations = qualityGate?.violations || [];

  return (
    <>
      {contextHolder}
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card styles={{ body: { padding: '12px 16px' } }}>
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
              <Input.Search
                allowClear
                placeholder="搜索实体"
                value={searchValue}
                onChange={event => {
                  setSearchValue(event.target.value);
                  if (!event.target.value) setFocusedEntity('');
                }}
                onSearch={value => focusEntity(value.trim())}
                loading={loading}
                style={{ width: 320, maxWidth: '100%' }}
              />
              <Space wrap>
                <Select
                  allowClear
                  placeholder="关系"
                  value={predicate || undefined}
                  options={predicateOptions}
                  onChange={value => setPredicate(value || '')}
                  style={{ width: 180 }}
                />
                <Select
                  value={depth}
                  options={[
                    { label: '1 跳', value: 1 },
                    { label: '2 跳', value: 2 },
                    { label: '3 跳', value: 3 },
                  ]}
                  onChange={setDepth}
                  style={{ width: 100 }}
                />
                <Tooltip title="最低置信度">
                  <div style={{ width: 180 }}>
                    <Slider
                      min={0}
                      max={1}
                      step={0.1}
                      value={minConfidence}
                      onChange={setMinConfidence}
                    />
                  </div>
                </Tooltip>
                <Button icon={<MergeCellsOutlined />} onClick={() => openMergeModal()}>
                  合并实体
                </Button>
                <Button icon={<ReloadOutlined />} onClick={resetFilters}>
                  重置
                </Button>
              </Space>
            </Space>

            <Row gutter={[12, 12]}>
              <Col xs={12} sm={6}><Statistic title="实体" value={stats.visible_nodes ?? graphData.nodes.length} /></Col>
              <Col xs={12} sm={6}><Statistic title="关系" value={stats.visible_edges ?? graphData.links.length} /></Col>
              <Col xs={12} sm={6}><Statistic title="全部三元组" value={stats.total_triples ?? stats.edges ?? 0} /></Col>
              <Col xs={12} sm={6}>
                <Statistic title="聚焦实体" value={matchedEntity || '-'} valueStyle={{ fontSize: 18 }} />
              </Col>
            </Row>
          </Space>
        </Card>

        {qualityGate?.passed === false ? (
          <Alert
            showIcon
            type={qualityGate.status === 'error' ? 'error' : 'warning'}
            message="知识图谱质量门禁未通过"
            description={(
              <Space direction="vertical" size={2}>
                <Text>{qualityGate.message}</Text>
                {qualityGateViolations.slice(0, 3).map(violation => (
                  <Text key={violation.code} type="secondary">
                    {violation.title}: {violation.message}
                  </Text>
                ))}
              </Space>
            )}
          />
        ) : null}

        {qualityAlerts.length ? (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {qualityAlerts.slice(0, 3).map(alert => (
              <Alert
                key={alert.code}
                showIcon
                type={alert.level === 'error' ? 'error' : 'warning'}
                message={alert.title}
                description={renderQualityAlertDescription(alert)}
              />
            ))}
          </Space>
        ) : null}

        <Card title="抽取质量指标" styles={{ body: { padding: '12px 16px' } }}>
          <Row gutter={[16, 12]} align="middle">
            <Col xs={24} md={6}>
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <Progress
                  percent={healthScore}
                  size="small"
                  status={healthScore >= 75 ? 'success' : healthScore >= 50 ? 'normal' : 'exception'}
                />
                <Space size={6} wrap>
                  <Text strong>健康分</Text>
                  <Tag color={riskColor(qualityMetrics?.risk_level)}>
                    {riskLabel(qualityMetrics?.risk_level)}
                  </Tag>
                  {qualityGate ? (
                    <Tag color={!qualityGate.enabled ? 'default' : qualityGate.passed ? 'green' : 'orange'}>
                      {!qualityGate.enabled ? '门禁未启用' : qualityGate.passed ? '门禁通过' : '门禁未通过'}
                    </Tag>
                  ) : null}
                </Space>
              </Space>
            </Col>
            <Col xs={12} md={3}>
              <Statistic title="覆盖文档" value={qualityMetrics?.source_doc_count ?? 0} />
            </Col>
            <Col xs={12} md={3}>
              <Statistic title="覆盖分块" value={qualityMetrics?.source_chunk_count ?? 0} />
            </Col>
            <Col xs={12} md={3}>
              <Statistic title="分块密度" value={qualityMetrics?.triples_per_source_chunk ?? 0} precision={2} />
            </Col>
            <Col xs={12} md={3}>
              <Statistic title="低置信度" value={metricPercent(qualityMetrics?.low_confidence_ratio)} suffix="%" />
            </Col>
            <Col xs={12} md={3}>
              <Statistic title="孤立关系" value={metricPercent(qualityMetrics?.isolated_relation_ratio)} suffix="%" />
            </Col>
            <Col xs={12} md={3}>
              <Statistic title="待审积压" value={metricPercent(openReviewRatio)} suffix="%" />
            </Col>
            <Col xs={24} md={6}>
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <Text type="secondary">最近趋势</Text>
                {renderQualityTrend(qualitySnapshots)}
              </Space>
            </Col>
          </Row>
        </Card>

        <div id="graph-quality-queue" ref={qualityQueueRef} data-testid="graph-quality-queue">
          <Card
            title="质量审核队列"
            extra={(
              <Space wrap>
                <Tag color={qualityData.summary.total_candidates ? 'orange' : 'green'}>
                  {qualityData.summary.total_candidates} 项
                </Tag>
                {qualityData.summary.hidden_decided_count ? (
                  <Tag>{qualityData.summary.hidden_decided_count} 已隐藏</Tag>
                ) : null}
                {qualityData.summary.stale_decision_count ? (
                  <Tag color="blue">{qualityData.summary.stale_decision_count} 已重新激活</Tag>
                ) : null}
                <Select
                  value={qualityStatus}
                  size="small"
                  style={{ width: 110 }}
                  onChange={value => setQualityStatus(value)}
                  options={[
                    { label: '待处理', value: 'open' },
                    { label: '全部', value: 'all' },
                    { label: '已处理', value: 'accepted' },
                    { label: '已忽略', value: 'ignored' },
                    { label: '稍后', value: 'snoozed' },
                  ]}
                />
              </Space>
            )}
            styles={{ body: { padding: '8px 12px' } }}
          >
          {qualityData.candidates.length ? (
            <Space wrap style={{ marginBottom: 8 }}>
              <Checkbox
                checked={allVisibleCandidatesSelected}
                indeterminate={someVisibleCandidatesSelected && !allVisibleCandidatesSelected}
                onChange={event => toggleVisibleSelection(event.target.checked)}
              >
                本页
              </Checkbox>
              <Text type="secondary">{selectedCandidates.length} 已选</Text>
              <Button
                size="small"
                aria-label="批量处理候选"
                icon={<CheckOutlined />}
                disabled={!selectedCandidates.length}
                loading={batchSaving}
                onClick={() => setSelectedCandidatesStatus('accepted')}
              >
                批量处理
              </Button>
              <Button
                size="small"
                aria-label="批量稍后处理候选"
                icon={<ClockCircleOutlined />}
                disabled={!selectedCandidates.length}
                loading={batchSaving}
                onClick={() => setSelectedCandidatesStatus('snoozed')}
              >
                批量稍后
              </Button>
              <Button
                size="small"
                aria-label="批量忽略候选"
                icon={<EyeInvisibleOutlined />}
                disabled={!selectedCandidates.length}
                loading={batchSaving}
                onClick={() => setSelectedCandidatesStatus('ignored')}
              >
                批量忽略
              </Button>
              <Button
                size="small"
                aria-label="批量重新打开候选"
                icon={<ReloadOutlined />}
                disabled={!selectedCandidates.length}
                loading={batchSaving}
                onClick={() => setSelectedCandidatesStatus('open')}
              >
                重新打开
              </Button>
            </Space>
          ) : null}
          {qualityData.candidates.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <List<KnowledgeGraphQualityCandidate>
              size="small"
              dataSource={qualityData.candidates}
              renderItem={item => (
                <List.Item actions={candidateActions(item)}>
                  <List.Item.Meta
                    title={
                      <Space size={6} wrap>
                        <Checkbox
                          checked={selectedCandidateIdSet.has(item.id)}
                          onChange={event => toggleCandidateSelection(item.id, event.target.checked)}
                        />
                        <Tag color={severityColor(item.severity)}>{issueTypeLabel(item.type)}</Tag>
                        <Text>{item.title}</Text>
                        <Text type="secondary">{Math.round(item.score * 100)}%</Text>
                        {item.stale_decision ? <Tag color="blue">已重新激活</Tag> : null}
                      </Space>
                    }
                    description={
                      <Space direction="vertical" size={2}>
                        <Text type="secondary">{item.description}</Text>
                        {item.entities?.length ? (
                          <Space size={4} wrap>
                            {item.entities.slice(0, 4).map(entity => (
                              <Tag key={entity}>{entity}</Tag>
                            ))}
                          </Space>
                        ) : null}
                        {renderCandidateEvidence(item)}
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          )}
          </Card>
        </div>

      {graphData.nodes.length === 0 ? (
        <Alert message="暂无图谱数据" description="请先上传文档并开启图谱提取。" type="info" showIcon />
      ) : (
        <Row gutter={[16, 16]}>
          <Col xs={24} xl={17}>
            <Card
              title="关系网络"
              extra={loading ? <Spin size="small" /> : null}
              styles={{ body: { padding: 0 } }}
            >
              <div ref={containerRef} style={{ height: 560, width: '100%' }}>
                <ForceGraph2D
                  ref={fgRef}
                  graphData={{ nodes: graphData.nodes, links: graphData.links }}
                  nodeLabel={(node: any) => `${node.name} · ${node.degree || 0} 关系`}
                  nodeColor={(node: any) => {
                    if (node.matched) return '#fa8c16';
                    return node.degree > 5 ? '#f5222d' : '#1677ff';
                  }}
                  nodeRelSize={4}
                  linkLabel={(link: any) => `${link.label} · ${(link.confidence * 100).toFixed(0)}%`}
                  linkColor={() => 'rgba(120, 120, 120, 0.45)'}
                  linkDirectionalArrowLength={3.5}
                  linkDirectionalArrowRelPos={1}
                  linkCurvature={0.18}
                  onNodeClick={(node: any) => {
                    focusEntity(node.id);
                    fgRef.current?.centerAt(node.x, node.y, 800);
                    fgRef.current?.zoom(3.2, 800);
                  }}
                  width={graphWidth}
                  height={560}
                />
              </div>
            </Card>
          </Col>
          <Col xs={24} xl={7}>
            <Card title="核心实体" styles={{ body: { padding: '8px 12px' } }}>
              {graphData.entities.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <List<KnowledgeGraphEntity>
                  size="small"
                  dataSource={graphData.entities.slice(0, 12)}
                  renderItem={item => (
                    <List.Item
                      actions={[
                        <Button
                          key="focus"
                          size="small"
                          type={item.name === matchedEntity ? 'primary' : 'text'}
                          icon={<AimOutlined />}
                          onClick={() => focusEntity(item.name)}
                        />,
                      ]}
                    >
                      <List.Item.Meta
                        title={<Text ellipsis>{item.name}</Text>}
                        description={
                          <Space size={4} wrap>
                            <Tag>{item.degree} 关系</Tag>
                            <Tag>{item.source_doc_count} 文档</Tag>
                          </Space>
                        }
                      />
                    </List.Item>
                  )}
                />
              )}
            </Card>
          </Col>
        </Row>
      )}

      <Card title="关系证据" styles={{ body: { paddingTop: 8 } }}>
        <Table<RelationRow>
          size="small"
          pagination={{ pageSize: 8, showSizeChanger: false }}
          dataSource={relationRows}
          columns={[
            {
              title: '主体',
              dataIndex: 'sourceName',
              ellipsis: true,
              render: (value: string) => <Button type="link" size="small" onClick={() => focusEntity(value)}>{value}</Button>,
            },
            {
              title: '关系',
              dataIndex: 'predicate',
              width: 120,
              render: (value: string) => <Tag color="blue">{value}</Tag>,
            },
            {
              title: '客体',
              dataIndex: 'targetName',
              ellipsis: true,
              render: (value: string) => <Button type="link" size="small" onClick={() => focusEntity(value)}>{value}</Button>,
            },
            {
              title: '置信度',
              dataIndex: 'confidence',
              width: 100,
              render: (value: number) => `${Math.round(value * 100)}%`,
            },
            {
              title: '来源',
              dataIndex: 'source_chunk_id',
              ellipsis: true,
              render: (value: string) => value || '-',
            },
            {
              title: '操作',
              key: 'actions',
              width: 96,
              render: (_: unknown, record: RelationRow) => (
                <Space size={2}>
                  <Tooltip title="拆分到新实体">
                    <Button
                      type="text"
                      size="small"
                      icon={<SplitCellsOutlined />}
                      onClick={() => openSplitRelation(record)}
                    />
                  </Tooltip>
                  <Button
                    type="text"
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => openEditRelation(record)}
                  />
                  <Popconfirm
                    title="删除这条关系？"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => deleteRelation(record)}
                  >
                    <Button type="text" danger size="small" icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      </Space>

      <Modal
        title="合并实体"
        open={mergeOpen}
        okText="合并"
        cancelText="取消"
        confirmLoading={mergeSaving}
        onOk={submitMerge}
        onCancel={() => {
          activeCandidate.current = null;
          setMergeOpen(false);
        }}
      >
        <Form form={mergeForm} layout="vertical">
          <Form.Item
            name="source"
            label="源实体"
            rules={[{ required: true, message: '请输入要合并掉的实体' }]}
          >
            <Input placeholder="例如 Memo X" />
          </Form.Item>
          <Form.Item
            name="target"
            label="目标实体"
            rules={[{ required: true, message: '请输入保留后的实体' }]}
          >
            <Input placeholder="例如 MemoX" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="修正关系"
        open={Boolean(editingRelation)}
        okText="保存"
        cancelText="取消"
        confirmLoading={relationSaving}
        onOk={submitRelationUpdate}
        onCancel={() => {
          activeCandidate.current = null;
          setEditingRelation(null);
        }}
      >
        <Form form={relationForm} layout="vertical">
          <Form.Item
            name="subject"
            label="主体"
            rules={[{ required: true, message: '请输入主体' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="predicate"
            label="关系"
            rules={[{ required: true, message: '请输入关系' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="object"
            label="客体"
            rules={[{ required: true, message: '请输入客体' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="confidence"
            label="置信度"
            rules={[{ required: true, message: '请输入置信度' }]}
          >
            <InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} />
          </Form.Item>
          <Text type="secondary">来源：{editingRelation?.source_chunk_id || '-'}</Text>
        </Form>
      </Modal>

      <Modal
        title="拆分实体"
        open={Boolean(splitContext)}
        okText="拆分"
        cancelText="取消"
        confirmLoading={splitSaving}
        onOk={submitEntitySplit}
        onCancel={() => setSplitContext(null)}
      >
        <Form form={splitForm} layout="vertical">
          <Form.Item
            name="source"
            label="从哪个实体拆出"
            rules={[{ required: true, message: '请选择源实体' }]}
          >
            <Select
              options={uniqueEntityOptions([
                splitContext?.source || '',
                ...(splitContext?.triples || []).flatMap(triple => [triple.subject, triple.object]),
              ])}
            />
          </Form.Item>
          <Form.Item
            name="new_entity"
            label="新实体"
            rules={[{ required: true, message: '请输入新实体名' }]}
          >
            <Input placeholder="例如 Apple Inc" />
          </Form.Item>
          {splitContext?.triples.length ? (
            <Space direction="vertical" size={6}>
              <Text type="secondary">将迁移这些关系：</Text>
              {splitContext.triples.map(renderTripleEvidence)}
            </Space>
          ) : null}
        </Form>
      </Modal>
    </>
  );
};
