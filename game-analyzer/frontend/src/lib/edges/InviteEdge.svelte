<script lang="ts">
  import { BaseEdge, EdgeLabel, type EdgeProps } from '@xyflow/svelte';
  import { store } from '../graph.svelte';
  import { nodeSize } from '../layout';

  let { id, source, target, sourceX, sourceY, targetX, targetY }: EdgeProps = $props();

  // Invite edges dock on the BOTTOM face of both cards and fan out below
  // the roster row at staggered depths, so each referral stays traceable
  // (arrow points referrer → invited) instead of merging into one
  // horizontal line.
  const route = $derived.by(() => {
    const graphNode = store.graph?.nodes.find((n) => n.id === target);
    const height = graphNode ? nodeSize(graphNode).height : 150;
    const tx = targetX;
    const ty = targetY + height; // bottom face of the target card
    const siblings = store.flowEdges.filter((e) => e.type === 'invite' && e.source === source);
    const index = Math.max(0, siblings.findIndex((e) => e.id === id));
    const depth = sourceY + 38 + index * 24;
    const d = `M ${sourceX} ${sourceY} L ${sourceX} ${depth} L ${tx} ${depth} L ${tx} ${ty}`;
    return { d, labelX: (sourceX + tx) / 2, labelY: depth };
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
