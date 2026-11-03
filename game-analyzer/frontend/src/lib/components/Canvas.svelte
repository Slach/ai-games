<script lang="ts">
  import {
    SvelteFlow,
    Background,
    BackgroundVariant,
    Controls,
    MiniMap,
    Panel,
    useSvelteFlow,
  } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import { tick } from 'svelte';
  import { store } from '../graph.svelte';
  import { nodeSize } from '../layout';
  import FlowNode from '../nodes/FlowNode.svelte';
  import InviteEdge from '../edges/InviteEdge.svelte';
  import Legend from './Legend.svelte';

  const nodeTypes = { anode: FlowNode };
  const edgeTypes = { invite: InviteEdge };
  const { fitView, setCenter } = useSvelteFlow();

  let highlightTimer: ReturnType<typeof setTimeout> | null = null;

  // Fit the view after a fresh graph (game switch / Refresh).
  // Deferred out of the reactive flush: calling fitView synchronously from an
  // $effect makes SvelteFlow's fitView bookkeeping (state written inside a
  // $derived) loop into effect_update_depth_exceeded. From a macrotask it
  // behaves like the event-handler calls in the library's own examples.
  $effect(() => {
    void store.fitRequest;
    if (store.fitRequest > 0) {
      setTimeout(() => {
        // No minZoom clamp: the whole graph must fit the screen, however
        // wide it gets. maxZoom keeps a single collapsed node from being
        // blown up to full screen.
        void fitView({ padding: 0.15, duration: 400, maxZoom: 1.1 });
      }, 0);
    }
  });

  // Scroll to and highlight the node picked by search / upstream jump.
  $effect(() => {
    const request = store.focusRequest;
    if (!request) return;
    void tick().then(() => {
      const node = store.flowNodes.find((n) => n.id === request.id);
      if (!node) return;
      const size = nodeSize(
        store.graph?.nodes.find((n) => n.id === request.id) ?? {
          id: request.id,
          type: 'brief',
        } as never,
      );
      void setCenter(node.position.x + size.width / 2, node.position.y + size.height / 2, {
        zoom: 1.1,
        duration: 500,
      });
      if (highlightTimer) clearTimeout(highlightTimer);
      highlightTimer = setTimeout(() => store.clearHighlight(), 4000);
    });
  });
</script>

<div class="canvas-root">
  <SvelteFlow
    bind:nodes={store.flowNodes}
    bind:edges={store.flowEdges}
    {nodeTypes}
    {edgeTypes}
    colorMode="dark"
    fitView
    minZoom={0.1}
    nodesDraggable={false}
    nodesConnectable={false}
  >
    <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
    <Controls position="bottom-right" />
    <MiniMap
      position="bottom-left"
      pannable
      zoomable
      nodeColor={(node) => {
        const data = node.data as { graphNode?: { badges: string[] } };
        const badges = data?.graphNode?.badges ?? [];
        if (badges.includes('delivery-failed')) return '#d64545';
        if (badges.includes('delivered')) return '#2e9e5b';
        if (badges.includes('not-delivered')) return '#4a6b8a';
        return '#3a3f4b';
      }}
    />
    <Panel position="bottom-left">
      <div class="legend-anchor"><Legend /></div>
    </Panel>
  </SvelteFlow>
  {#if store.loading}
    <div class="loading-overlay">
      <div class="spinner"></div>
      <span>Loading graph…</span>
    </div>
  {/if}
</div>

<style>
  .canvas-root {
    position: relative;
    width: 100%;
    height: 100%;
  }
  .loading-overlay {
    position: absolute;
    inset: 0;
    z-index: 25;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    background: rgba(16, 18, 22, 0.55);
    color: #9aa2b1;
    font-size: 14px;
  }
  .legend-anchor :global(.svelte-flow__minimap) {
    margin-bottom: 40px;
  }
</style>
