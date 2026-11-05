<script lang="ts">
  import { BaseEdge, EdgeLabel, type EdgeProps } from '@xyflow/svelte';
  import { store } from '../graph.svelte';
  import { nodeSize } from '../layout';

  let { id, source, target }: EdgeProps = $props();

  // Roster cards sit in a vertical column left of the players group, so
  // invites route around that column: out of the referrer's left face, down
  // a dedicated lane per invite, into the invited card's left face. Lanes
  // are staggered so each referral stays individually traceable (arrow
  // points referrer → invited) instead of merging into one vertical line.
  const route = $derived.by(() => {
    const sourceNode = store.graph?.nodes.find((n) => n.id === source);
    const targetNode = store.graph?.nodes.find((n) => n.id === target);
    const sourcePos = store.flowNodes.find((n) => n.id === source)?.position;
    const targetPos = store.flowNodes.find((n) => n.id === target)?.position;
    if (!sourceNode || !targetNode || !sourcePos || !targetPos) {
      return { d: '', labelX: 0, labelY: 0 };
    }
    const sy = sourcePos.y + nodeSize(sourceNode).height / 2;
    const ty = targetPos.y + nodeSize(targetNode).height / 2;
    const sx = sourcePos.x; // left face of the referrer
    const tx = targetPos.x; // left face of the invited card
    const invites = store.flowEdges.filter((e) => e.type === 'invite');
    const index = Math.max(0, invites.findIndex((e) => e.id === id));
    const lane = Math.min(sx, tx) - 28 - index * 16;
    const d = `M ${sx} ${sy} L ${lane} ${sy} L ${lane} ${ty} L ${tx} ${ty}`;
    return { d, labelX: lane, labelY: (sy + ty) / 2 };
  });

  const markerId = $derived(`ia-${id.replace(/[^a-zA-Z0-9_-]/g, '_')}`);
</script>

<defs>
  <marker
    id={markerId}
    viewBox="0 0 10 10"
    refX="8"
    refY="5"
    markerWidth="7"
    markerHeight="7"
    orient="auto-start-reverse"
  >
    <path d="M 0 0 L 10 5 L 0 10 z" fill="#e0b040" />
  </marker>
</defs>
<BaseEdge
  {id}
  path={route.d}
  markerEnd={`url(#${markerId})`}
  style="stroke: #e0b040; stroke-width: 2; stroke-dasharray: 8 4;"
/>
<EdgeLabel
  x={route.labelX}
  y={route.labelY}
  style="font-size: 12.5px; color: #e0b040; background: #14171d; border: 1px solid #665a24; border-radius: 8px; padding: 0 7px;"
>invite</EdgeLabel>
