<script lang="ts">
  let open = $state(false);
</script>

<div class="legend" class:open>
  <button class="legend-toggle" onclick={() => (open = !open)}>{open ? '×' : 'ℹ'}</button>
  {#if open}
    <div class="legend-body">
      <div class="row"><span class="swatch sw-delivered"></span> delivered to player</div>
      <div class="row"><span class="swatch sw-internal"></span> internal only</div>
      <div class="row"><span class="swatch sw-pending"></span> not delivered yet</div>
      <div class="row"><span class="swatch sw-error"></span> failed / no response</div>
      <div class="row"><span class="swatch sw-warn"></span> fallback / retry</div>
      <div class="row"><span class="line line-flow"></span> turn flow</div>
      <div class="row"><span class="line line-invite"></span> invite link</div>
      <div class="row"><span class="line line-ref"></span> image reference (toggle "image refs")</div>
      <div class="hint">click node → details · number button → expand/collapse</div>
    </div>
  {/if}
</div>

<style>
  .legend { position: relative; }
  .legend-toggle {
    background: #1c2028;
    border: 1px solid #3a3f4b;
    color: #9aa2b1;
    border-radius: 8px;
    width: 28px;
    height: 28px;
    cursor: pointer;
    font-size: 14px;
  }
  .legend-body {
    position: absolute;
    bottom: 34px;
    left: 0;
    background: #171b22;
    border: 1px solid #3a3f4b;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 11px;
    color: #b9c0cc;
    display: flex;
    flex-direction: column;
    gap: 5px;
    width: 250px;
  }
  .row { display: flex; align-items: center; gap: 8px; }
  .swatch { width: 14px; height: 14px; border-radius: 4px; display: inline-block; }
  .sw-delivered { background: #1c1f26; border: 2px solid #2e9e5b; }
  .sw-internal { background: #1c1f26; border: 2px solid #3a3f4b; }
  .sw-pending { background: #1c1f26; border: 2px solid #4a6b8a; }
  .sw-error { background: #1c1f26; border: 2px solid #d64545; }
  .sw-warn { background: #1c1f26; border: 2px solid #d6a445; }
  .line { width: 18px; height: 0; border-top: 2px solid #3a3f4b; display: inline-block; }
  .line-flow { border-top-color: #4a9eff; }
  .line-invite { border-top-style: dashed; border-top-color: #e0b040; }
  .line-ref { border-top-style: dotted; border-top-color: #7a7a8c; }
  .hint { color: #77808f; margin-top: 4px; }
</style>
