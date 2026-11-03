<script lang="ts">
  import { Handle, Position, type NodeProps } from '@xyflow/svelte';
  import { store, type FlowNodeData } from '../graph.svelte';
  import { nodeSize } from '../layout';

  let { id, data }: NodeProps = $props();

  const node = $derived((data as FlowNodeData).graphNode);
  const expanded = $derived((data as FlowNodeData).expanded);
  const highlight = $derived((data as FlowNodeData).highlight);
  // Explicit pixel sizing on the content element keeps the DOM size stable:
  // SvelteFlow's measured-dimension write-back then converges instead of
  // oscillating between adopt/measure cycles (effect_update_depth_exceeded).
  const size = $derived(nodeSize(node));
  const stateClass = $derived.by(() => {
    const badges = node.badges;
    if (badges.includes('delivery-failed')) return 'state-error';
    if (badges.includes('no-response') || badges.includes('fallback')) return 'state-warn';
    if (badges.includes('delivered')) return 'state-delivered';
    if (badges.includes('not-delivered')) return 'state-pending';
    return 'state-internal';
  });

  function onToggleClick(event: MouseEvent) {
    event.stopPropagation();
    store.toggleNode(id);
  }

  function onNodeClick() {
    store.openDetail(id);
  }
</script>

<Handle type="target" position={Position.Top} class="handle-dot" />
<div
  class="anode node-{node.type} {stateClass}"
  style="width: {size.width}px; height: {size.height}px;"
  class:highlight
  role="button"
  tabindex="0"
  onclick={onNodeClick}
  onkeydown={(e) => e.key === 'Enter' && onNodeClick()}
>
  <div class="anode-head">
    {#if node.thumb}
      <img class="anode-thumb" src={node.thumb} alt="" loading="lazy" />
    {:else}
      <div class="anode-icon anode-icon-{node.type}">{@render iconFor(node.type)}</div>
    {/if}
    <div class="anode-titles">
      <div class="anode-label" title={node.label}>{node.label}</div>
      <div class="anode-subtitle" title={node.subtitle}>{node.subtitle}</div>
    </div>
    {#if node.children_count > 0}
      <button class="anode-toggle" class:open={expanded} onclick={onToggleClick} title={expanded ? 'Collapse' : 'Expand'}>
        {node.children_count}
        <span class="chev">▸</span>
      </button>
    {/if}
  </div>
  {#if node.summary}
    <div class="anode-summary">{node.summary}</div>
  {/if}
  {#if node.badges.length}
    <div class="anode-badges">
      {#each node.badges as badge (badge)}
        <span class="badge badge-{badge}">{badge}</span>
      {/each}
    </div>
  {/if}
</div>
<Handle type="source" position={Position.Bottom} class="handle-dot" />

{#snippet iconFor(type: string)}{#if type === 'game'}🚀{:else if type === 'concept'}🧭{:else if type === 'player'}🧑{:else if type === 'npc'}🤖{:else if type === 'turn'}⏳{:else if type === 'scene'}🌌{:else if type === 'dialogue'}💬{:else if type === 'brief'}📜{:else if type === 'choice'}⚡{:else if type === 'actionimg'}🖼{:else if type === 'outcome'}⚔{:else if type === 'death'}💀{:else if type === 'finale'}🏁{:else}📄{/if}{/snippet}

<style>
  .anode {
    background: #1c1f26;
    border: 2px solid #3a3f4b;
    border-radius: 10px;
    padding: 8px 11px;
    font-size: 13px;
    color: #d7dae0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    cursor: pointer;
    overflow: hidden;
    box-sizing: border-box;
  }
  .anode:hover { border-color: #6ea8fe; }
  .state-delivered { border-color: #2e9e5b; }
  .state-error { border-color: #d64545; }
  .state-warn { border-color: #d6a445; }
  .state-pending { border-color: #4a6b8a; }
  .anode-head { display: flex; align-items: center; gap: 8px; min-height: 0; }
  .anode-thumb {
    width: 46px;
    height: 46px;
    border-radius: 6px;
    object-fit: cover;
    flex-shrink: 0;
    background: #101216;
  }
  .anode-icon {
    width: 34px;
    height: 34px;
    border-radius: 6px;
    background: #262b34;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 17px;
    flex-shrink: 0;
  }
  .anode-titles { min-width: 0; flex: 1; }
  .anode-label {
    font-weight: 600;
    font-size: 15px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .anode-subtitle {
    font-size: 12.5px;
    color: #8b93a1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .anode-toggle {
    background: #262b34;
    border: 1px solid #3a3f4b;
    border-radius: 12px;
    color: #9fc1ff;
    font-size: 12.5px;
    padding: 2px 9px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }
  .anode-toggle:hover { background: #303745; }
  .anode-toggle .chev { display: inline-block; transition: transform 0.15s; }
  .anode-toggle.open .chev { transform: rotate(90deg); }
  .anode-summary {
    font-size: 12.5px;
    color: #9aa2b1;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    line-height: 1.3;
  }
  .anode-badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: auto; }
  .badge {
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    padding: 1px 7px;
    border-radius: 8px;
    background: #2a2f3a;
    color: #a6adbc;
  }
  .badge-delivered { background: #14341f; color: #56d581; }
  .badge-not-delivered { background: #22303d; color: #7ab3d6; }
  .badge-delivery-failed { background: #3d1a1a; color: #ff8f8f; }
  .badge-npc { background: #2b1f3d; color: #c39bff; }
  .badge-player { background: #1d2f45; color: #7db4ff; }
  .badge-dead { background: #3d1a1a; color: #ff8f8f; }
  .badge-auto { background: #3d331a; color: #ffd66b; }
  .badge-fatal { background: #3d1a1a; color: #ff8f8f; }
  .badge-injury { background: #3d2a1a; color: #ffb26b; }
  .badge-progress { background: #14341f; color: #56d581; }
  .badge-delay { background: #2a2f3a; color: #a6adbc; }
  .badge-fallback { background: #3d331a; color: #ffd66b; }
  .badge-no-response { background: #3d1a1a; color: #ff8f8f; }
  .badge-active { background: #14341f; color: #56d581; }
  .badge-done { background: #1d2f45; color: #7db4ff; }
  .badge-ended { background: #2a2f3a; color: #a6adbc; }
  .anode.highlight {
    border-color: #ffcf4a;
    box-shadow: 0 0 0 4px rgba(255, 207, 74, 0.35);
    animation: pulse 0.8s ease-in-out 4;
  }
  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 0 2px rgba(255, 207, 74, 0.2); }
    50% { box-shadow: 0 0 0 8px rgba(255, 207, 74, 0.45); }
  }
  .node-game { background: linear-gradient(160deg, #1e2634, #171b22); }
  .node-turn { background: linear-gradient(160deg, #1f2430, #181c24); }
  .node-actionimg { align-items: center; }
  .node-actionimg :global(.anode-thumb) { width: 100%; height: 150px; }
  .handle-dot { background: #6ea8fe; border: none; width: 7px; height: 7px; }
</style>
