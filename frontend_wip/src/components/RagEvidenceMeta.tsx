import React from 'react';
import { Space, Tag } from 'antd';

export interface GraphRelation {
  subject: string;
  predicate: string;
  object: string;
  source_chunk_id?: string;
  confidence?: number;
}

export interface RagEvidenceLike {
  evidence_reason?: string;
  evidence_quality?: string;
  graph_boosted?: boolean;
  matched_entities?: string[];
  graph_entity?: string;
  graph_degree?: number;
  graph_relations?: GraphRelation[];
}

const evidenceQualityLabel = (quality?: string) => {
  if (quality === 'strong') return '强证据';
  if (quality === 'medium') return '中等证据';
  return '基础证据';
};

const evidenceQualityColor = (quality?: string) => {
  if (quality === 'strong') return 'green';
  if (quality === 'medium') return 'blue';
  return 'default';
};

export const RagEvidenceMeta: React.FC<{ item: RagEvidenceLike; compact?: boolean }> = ({
  item,
  compact = false,
}) => {
  const relations = item.graph_relations || [];
  const entities = item.matched_entities || [];

  return (
    <div style={{ marginTop: compact ? 4 : 6 }}>
      <Space size={4} wrap>
        <Tag color={evidenceQualityColor(item.evidence_quality)}>
          {evidenceQualityLabel(item.evidence_quality)}
        </Tag>
        {item.graph_boosted && <Tag color="geekblue">图谱增强</Tag>}
        {item.graph_degree ? <Tag>实体度 {item.graph_degree}</Tag> : null}
        {entities.slice(0, 3).map(entity => (
          <Tag key={entity} color="cyan">{entity}</Tag>
        ))}
      </Space>
      {item.evidence_reason && (
        <div style={{ fontSize: compact ? 11 : 12, color: '#666', marginTop: 4 }}>
          {item.evidence_reason}
        </div>
      )}
      {!compact && relations.length > 0 && (
        <div style={{ fontSize: 12, color: '#595959', marginTop: 4 }}>
          {relations.slice(0, 2).map((rel, idx) => (
            <div key={`${rel.subject}-${rel.predicate}-${rel.object}-${idx}`}>
              {rel.subject} / {rel.predicate} / {rel.object}
              {typeof rel.confidence === 'number' ? ` · ${Math.round(rel.confidence * 100)}%` : ''}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
