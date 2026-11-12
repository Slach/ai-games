<script lang="ts">
  import { fetchNodeDetail } from '../api';
  import { store } from '../graph.svelte';
  import type { ChainSection, ChainStep, EditInputs, NodeDetail } from '../types';

  let detail = $state<NodeDetail | null>(null);
  let error = $state('');
  let tab = $state<'player' | 'actions' | 'chain' | 'delivery' | 'upstream'>('player');

  $effect(() => {
    const nodeId = store.openNodeId;
    if (!nodeId || !store.selectedGameId) {
      detail = null;
      return;
    }
    detail = null;
    error = '';
    tab = 'player';
    fetchNodeDetail(store.selectedGameId, nodeId)
      .then((data) => {
        detail = data;
        tab = data.actions ? 'actions' : 'player';
      })
      .catch((err) => (error = String(err)));
  });

  function copyText(text: string) {
    navigator.clipboard.writeText(text);
  }

  /** Pretty-print the section body when it is pure JSON (LLM responses). */
  function prettyJson(text: string): string | null {
    const trimmed = text.trim();
    if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return null;
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      return null;
    }
  }

  function highlightJson(json: string): string {
    const escaped = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return escaped.replace(
      /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
      (match: string, str?: string, colon?: string, kw?: string) => {
        if (str) {
          return colon ? `<span class="j-key">${str}</span>${colon}` : `<span class="j-str">${str}</span>`;
        }
        if (kw) return `<span class="j-kw">${match}</span>`;
        return `<span class="j-num">${match}</span>`;
      },
    );
  }

  function gotoNode(nodeId: string | null) {
    if (!nodeId) return;
    store.focusNode(nodeId);
    store.openDetail(nodeId);
  }

  /** Pose id from a pose_lib_*.png skeleton filename (e.g. 'console_typing'). */
  function poseId(filename: string): string {
    const match = filename.match(/^pose_lib_(\w+)\.png$/);
    return match ? match[1] : filename;
  }

  const playerView = $derived((detail?.player_view ?? null) as Record<string, unknown> | null);
  const pvText = $derived(playerView ? String(playerView['text'] ?? '') : '');
  const pvTitle = $derived(playerView ? String(playerView['title'] ?? '') : '');
  const pvImage = $derived(playerView ? (playerView['image'] as string | null) ?? null : null);
  const pvChoices = $derived(
    playerView && Array.isArray(playerView['choices'])
      ? (playerView['choices'] as { id: string; text: string; consequence?: string; consequence_kind?: string }[])
      : [],
  );
</script>

{#if store.openNodeId}
  <div class="drawer" class:maximized={store.drawerMaximized}>
    <div class="drawer-head">
      {#if detail}
        <div class="head-titles">
          <div class="head-label">{detail.node.label}</div>
          <div class="head-sub">
            {detail.node.type} · {detail.node.subtitle}
            {#each detail.node.badges as badge (badge)}<span class="badge">{badge}</span>{/each}
          </div>
        </div>
      {:else}
        <div class="head-label loading-row"><span class="spinner"></span>Loading…</div>
      {/if}
      <div class="head-actions">
        <button
          class="icon-btn"
          title={store.drawerMaximized ? 'Restore panel' : 'Maximize panel'}
          onclick={() => (store.drawerMaximized = !store.drawerMaximized)}
        >
          {#if store.drawerMaximized}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 4v5H4" /><path d="M15 20v-5h5" />
            </svg>
          {:else}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 9V4h5" /><path d="M20 9V4h-5" /><path d="M4 15v5h5" /><path d="M20 15v5h-5" />
            </svg>
          {/if}
        </button>
        <button class="close" onclick={() => store.closeDetail()}>✕</button>
      </div>
    </div>
    {#if error}<div class="error">{error}</div>{/if}
    {#if detail}
      <div class="tabs">
        <button class:active={tab === 'player'} onclick={() => (tab = 'player')}>Player view</button>
        {#if detail.actions}
          <button class:active={tab === 'actions'} onclick={() => (tab = 'actions')}>
            Actions ({detail.actions.actions.length})
          </button>
        {/if}
        <button class:active={tab === 'chain'} onclick={() => (tab = 'chain')}>
          Chain ({detail.chain.length})
        </button>
        <button class:active={tab === 'delivery'} onclick={() => (tab = 'delivery')}>
          Delivery ({detail.delivery.length})
        </button>
        <button class:active={tab === 'upstream'} onclick={() => (tab = 'upstream')}>
          Upstream ({detail.upstream.length})
        </button>
      </div>
      <div class="drawer-body">
        {#if tab === 'player'}
          {#if playerView}
            {#if pvImage}<img class="pv-image" src={pvImage} alt="" loading="lazy" />{/if}
            {#if pvTitle}<div class="pv-title">{pvTitle}</div>{/if}
            {#if pvText}<pre class="pv-text">{pvText}</pre>{/if}
            {#if pvChoices.length}
              <div class="pv-choices">
                {#each pvChoices as choice (choice.id)}
                  <div class="pv-choice" class:chosen={detail.node.type === 'choice' && detail.node.meta['action_id'] === choice.id}>
                    <span class="pv-choice-id">{choice.id}</span> {choice.text}
                    {#if choice.consequence}
                      <div class="pv-consequence">
                        {#if choice.consequence_kind}
                          <span class="pv-ck ck-{choice.consequence_kind}">{choice.consequence_kind}</span>
                        {/if}
                        {choice.consequence}
                      </div>
                    {/if}
                  </div>
                {/each}
              </div>
            {/if}
            {#if !pvImage && !pvText && !pvChoices.length && playerView['dialogues'] === undefined}
              <div class="empty">No player-facing view for this node</div>
            {/if}
            {#if Array.isArray(playerView['dialogues'])}
              <pre class="pv-text">{JSON.stringify(playerView['dialogues'], null, 2)}</pre>
            {/if}
            {#if Array.isArray(playerView['key_events']) && playerView['key_events'].length}
              <ul class="pv-events">
                {#each playerView['key_events'] as ev}{@const event = String(ev)}<li>{event}</li>{/each}
              </ul>
            {/if}
          {:else}
            <div class="empty">Internal node — nothing was sent to players directly</div>
          {/if}
        {:else if tab === 'actions'}
          {#if detail.actions}
            {@const acts = detail.actions}
            <div class="ta-deadline">
              <span>deadline: {acts.deadline ?? '—'}</span>
              {#if acts.outcome_node_id}
                <button class="ta-link" onclick={() => gotoNode(acts.outcome_node_id)}>turn outcome →</button>
              {/if}
            </div>
            {#each acts.actions as action (action.participant)}
              <div class="ta-card">
                <div class="ta-head">
                  <span class="ta-name">{action.participant}</span>
                  <span class="ta-timing timing-{action.timing}">{action.timing}</span>
                </div>
                <div class="ta-times">
                  submitted: {action.submitted_at ?? '—'}
                  {#if action.chosen_kind}· {action.chosen_kind}{/if}
                </div>
                {#if action.variants.length}
                  <div class="pv-choices">
                    {#each action.variants as variant (variant.id)}
                      <div class="pv-choice" class:chosen={action.chosen?.id === variant.id}>
                        <span class="pv-choice-id">{variant.id}</span>{variant.text}
                        {#if variant.consequence}
                          <div class="pv-consequence">
                            {#if variant.consequence_kind}
                              <span class="pv-ck ck-{variant.consequence_kind}">{variant.consequence_kind}</span>
                            {/if}
                            {variant.consequence}
                          </div>
                        {/if}
                      </div>
                    {/each}
                  </div>
                {/if}
                <div class="ta-links">
                  {#if action.brief_node_id}
                    <button class="ta-link" onclick={() => gotoNode(action.brief_node_id)}>briefing →</button>
                  {/if}
                  {#if action.choice_node_id}
                    <button class="ta-link" onclick={() => gotoNode(action.choice_node_id)}>choice →</button>
                  {/if}
                </div>
              </div>
            {/each}
          {:else}
            <div class="empty">No actions data</div>
          {/if}
        {:else if tab === 'chain'}
          {#if detail.chain.length === 0}
            <div class="empty">No generation events attached (legacy data or a pure DB action)</div>
          {/if}
          {#each detail.chain as step (step.event_id)}
            <div class="step">
              <div class="step-head">
                <span class="step-backend backend-{step.backend}">{step.backend}</span>
                <span class="step-kind">{step.kind}</span>
                {#if step.model}<span class="step-model">{step.model}</span>{/if}
                <span class="step-status status-{step.status}">{step.status}</span>
                {#if step.attempts > 1}<span class="step-meta">attempts: {step.attempts}</span>{/if}
                {#if step.duration_ms != null}<span class="step-meta">{(step.duration_ms / 1000).toFixed(1)}s</span>{/if}
                {#if step.usage}<span class="step-meta">{step.usage}</span>{/if}
                {#if step.finish_reason}<span class="step-meta">finish: {step.finish_reason}</span>{/if}
              </div>
              <div class="step-times">
                {step.request_at ?? ''} → {step.response_at ?? '…'}
              </div>
              {#if step.image_url}
                <a href={step.image_url} target="_blank">
                  <img class="step-image" src={step.image_url} alt="generated" loading="lazy" />
                </a>
              {/if}
              {#if step.edit_inputs}
                {@render editInputsBlock(step.edit_inputs)}
              {/if}
              {#each step.request_sections as section, i (step.event_id + ':' + i + ':' + section.name)}
                {@render sectionBlock(section, 'request')}
              {/each}
              {#each step.response_sections as section, i (step.event_id + ':r:' + i + ':' + section.name)}
                {@render sectionBlock(section, 'response')}
              {/each}
            </div>
          {/each}
        {:else if tab === 'delivery'}
          {#if detail.delivery.length === 0}
            <div class="empty">Nothing was pushed to Telegram for this node</div>
          {/if}
          {#each detail.delivery as row, index (index)}
            <div class="delivery">
              <div class="delivery-head">
                <span class="badge">{row.push_type}</span>
                <span class="step-status status-{row.status}">{row.status}</span>
                {#if row.retry_count}<span class="step-meta">retries: {row.retry_count}</span>{/if}
                <span class="step-meta">turn {row.turn ?? '—'}</span>
                <span class="step-meta">{row.created_at} → {row.sent_at ?? '…'}</span>
              </div>
              {#if row.error}<div class="delivery-error">{row.error}</div>{/if}
              <details>
                <summary>push payload</summary>
                <pre class="sec-text json">{@html highlightJson(JSON.stringify(row.payload, null, 2))}</pre>
              </details>
            </div>
          {/each}
        {:else if tab === 'upstream'}
          {#if detail.upstream.length === 0}
            <div class="empty">No upstream relations</div>
          {/if}
          {#each detail.upstream as ref (ref.node_id)}
            <button class="upstream-btn" onclick={() => store.focusNode(ref.node_id)}>
              <span class="upstream-type">{ref.type}</span>
              <span class="upstream-label">{ref.label}</span>
              <span class="upstream-relation">{ref.relation}</span>
            </button>
          {/each}
        {/if}
      </div>
    {/if}
  </div>
{/if}

{#snippet editRefThumb(label: string, imageUrl: string | null | undefined, filename: string | null, pendingLabel = 'none')}
  <div class="edit-thumb">
    <span class="edit-label">
      {label}
      {#if label === 'Pose' && filename}
        <span class="edit-pose-id">{poseId(filename)}</span>
      {/if}
    </span>
    {#if imageUrl}
      <a href={imageUrl} target="_blank">
        <img src={imageUrl} alt={label} title={filename ?? label} loading="lazy" />
      </a>
    {:else}
      <span class="edit-missing">{pendingLabel}</span>
    {/if}
    {#if filename}<span class="edit-file">{filename}</span>{/if}
  </div>
{/snippet}

{#snippet editInputsBlock(edit: EditInputs)}
  <div class="edit-inputs">
    <div class="edit-strip">
      {@render editRefThumb('Avatar', edit.avatar?.image_url, edit.avatar?.filename ?? null)}
      {@render editRefThumb('Background', edit.background?.image_url, edit.background?.filename ?? null)}
      {@render editRefThumb('Pose', edit.pose?.image_url, edit.pose?.filename ?? null)}
      <span class="edit-arrow">→</span>
      {@render editRefThumb('Output', edit.output_image_url, null, 'pending')}
    </div>
    {#if edit.instruction}
      <details>
        <summary class="sec-request">
          edit instruction
          <button
            class="copy"
            title="Copy"
            onclick={(e) => {
              e.preventDefault();
              copyText(edit.instruction ?? '');
            }}>⧉</button>
        </summary>
        <pre class="sec-text">{edit.instruction}</pre>
      </details>
    {/if}
  </div>
{/snippet}

{#snippet sectionBlock(section: ChainSection, direction: string)}
  <details open={direction === 'response' || section.name === 'meta' ? false : undefined}>
    <summary class="sec-{direction}">
      {section.name}
      {#if section.images?.length}<span class="sec-imgs">+{section.images.length} img</span>{/if}
      <button
        class="copy"
        title="Copy"
        onclick={(e) => {
          e.preventDefault();
          copyText(section.text);
        }}>⧉</button>
    </summary>
    {#if section.images?.length}
      <div class="sec-image-row">
        {#each section.images as img (img)}<img src={img} alt="reference" loading="lazy" />{/each}
      </div>
    {/if}
    {#if section.text && section.name !== 'WORKFLOW JSON' && section.text.length < 6000}
      {@const json = prettyJson(section.text)}
      {#if json}
        <pre class="sec-text json">{@html highlightJson(json)}</pre>
      {:else}
        <pre class="sec-text">{section.text}</pre>
      {/if}
    {:else if section.text}
      {@const bigJson = prettyJson(section.text)}
      {#if bigJson}
        {@const shown = bigJson.length > 50000 ? bigJson.slice(0, 50000) + '\n…' : bigJson}
        <pre class="sec-text json">{@html highlightJson(shown)}</pre>
        {#if bigJson.length > 50000}
          <button class="copy" onclick={() => copyText(bigJson)}>copy full JSON</button>
        {/if}
      {:else}
        <pre class="sec-text">{section.text.slice(0, 6000)}…
          <button class="copy" onclick={() => copyText(section.text)}>copy full</button>
        </pre>
      {/if}
    {/if}
  </details>
{/snippet}

<style>
  .drawer {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    width: 520px;
    max-width: 90vw;
    background: #14171d;
    border-left: 1px solid #2a2f3a;
    z-index: 30;
    display: flex;
    flex-direction: column;
    transition: width 0.18s ease;
  }
  .drawer.maximized {
    width: 100%;
    max-width: none;
  }
  .head-actions { display: flex; align-items: center; gap: 6px; }
  .icon-btn {
    background: none;
    border: none;
    color: #8b93a1;
    cursor: pointer;
    padding: 3px;
    display: flex;
  }
  .icon-btn:hover { color: #fff; }
  .drawer-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    padding: 12px 14px 8px;
    border-bottom: 1px solid #2a2f3a;
  }
  .head-label { font-weight: 600; font-size: 16px; color: #e8ebf0; }
  .loading-row { display: flex; align-items: center; gap: 8px; color: #9aa2b1; }
  .head-sub { font-size: 13px; color: #8b93a1; margin-top: 3px; }
  .badge {
    font-size: 11px;
    background: #2a2f3a;
    border-radius: 8px;
    padding: 1px 6px;
    margin-left: 4px;
    color: #a6adbc;
    text-transform: uppercase;
  }
  .close {
    background: none;
    border: none;
    color: #8b93a1;
    font-size: 16px;
    cursor: pointer;
  }
  .close:hover { color: #fff; }
  .tabs { display: flex; border-bottom: 1px solid #2a2f3a; }
  .tabs button {
    flex: 1;
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: #9aa2b1;
    font-size: 13.5px;
    padding: 8px 4px;
    cursor: pointer;
  }
  .tabs button.active { color: #7db4ff; border-bottom-color: #7db4ff; }
  .drawer-body {
    overflow-y: auto;
    padding: 12px 14px;
    flex: 1;
    display: flex;
    flex-direction: column;
  }
  .error { color: #ff8f8f; padding: 10px 14px; font-size: 13.5px; }
  .empty { color: #77808f; font-size: 13.5px; padding: 16px 0; }
  .pv-image { max-width: 100%; border-radius: 8px; margin-bottom: 10px; }
  .pv-title { font-weight: 600; color: #ffd66b; font-size: 14.5px; margin-bottom: 8px; }
  .pv-text {
    white-space: pre-wrap;
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    color: #cdd2dc;
    background: none;
    margin: 0 0 10px;
  }
  .pv-choices { display: flex; flex-direction: column; gap: 6px; }
  .pv-choice {
    font-size: 13.5px;
    color: #b9c0cc;
    background: #1c2028;
    border: 1px solid #2a2f3a;
    border-radius: 8px;
    padding: 6px 10px;
  }
  .pv-choice.chosen { border-color: #2e9e5b; }
  .pv-choice-id { color: #7d8695; margin-right: 6px; font-size: 12px; }
  .pv-consequence {
    margin-top: 5px;
    padding: 4px 8px;
    border-left: 2px solid #3a3f4b;
    color: #8f97a6;
    font-size: 12.5px;
    line-height: 1.45;
  }
  .pv-ck {
    border-radius: 6px;
    padding: 0 6px;
    font-size: 10.5px;
    text-transform: uppercase;
    margin-right: 6px;
  }
  .ck-progress { background: #14341f; color: #56d581; }
  .ck-fatal { background: #3d1a1a; color: #ff8f8f; }
  .ck-injury { background: #3d2a1a; color: #ffb26b; }
  .ck-delay { background: #2a2f3a; color: #a6adbc; }
  .pv-events { padding-left: 18px; font-size: 13px; color: #9aa2b1; }
  .step {
    border: 1px solid #2a2f3a;
    border-radius: 8px;
    padding: 8px 10px;
    margin-bottom: 12px;
    background: #171b22;
  }
  .step-head { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 12.5px; }
  .step-backend {
    border-radius: 6px;
    padding: 1px 7px;
    text-transform: uppercase;
    font-weight: 600;
    font-size: 11px;
  }
  .backend-llm { background: #1d2f45; color: #7db4ff; }
  .backend-comfyui { background: #2b1f3d; color: #c39bff; }
  .step-kind { color: #e4e7ec; font-weight: 600; }
  .step-model { color: #7d8695; }
  .step-status { text-transform: uppercase; font-size: 11px; }
  .status-ok { color: #56d581; }
  .status-fallback { color: #ffd66b; }
  .status-pending { color: #7ab3d6; }
  .status-error { color: #ff8f8f; }
  .step-meta { color: #77808f; font-size: 11px; }
  .step-times { color: #5d6673; font-size: 11px; margin: 4px 0; }
  .step-image { max-width: 100%; border-radius: 6px; margin: 6px 0; }
  details { margin: 6px 0; }
  summary {
    cursor: pointer;
    font-size: 12.5px;
    color: #9fb6d8;
    display: flex;
    align-items: center;
    gap: 6px;
    user-select: none;
  }
  .copy {
    margin-left: auto;
    background: #262b34;
    border: 1px solid #3a3f4b;
    border-radius: 5px;
    color: #9aa2b1;
    font-size: 11px;
    padding: 0 6px;
    cursor: pointer;
  }
  .sec-imgs { color: #c39bff; font-size: 11px; }
  .sec-image-row { display: flex; gap: 8px; margin: 6px 0; flex-wrap: wrap; }
  .sec-image-row img { width: 120px; border-radius: 6px; }
  /* Edit-model inputs: three reference thumbs → output thumb, plus the
   * collapsible edit instruction. */
  .edit-inputs {
    border: 1px solid #2a2f3a;
    border-radius: 8px;
    background: #10131a;
    padding: 8px;
    margin: 6px 0;
  }
  .edit-strip { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .edit-thumb { display: flex; flex-direction: column; gap: 3px; width: 116px; }
  .edit-thumb a { display: block; }
  .edit-thumb img { width: 116px; border-radius: 6px; border: 1px solid #2a2f3a; display: block; }
  .edit-label {
    font-size: 10.5px;
    color: #9aa2b1;
    text-transform: uppercase;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .edit-pose-id {
    background: #2b1f3d;
    color: #c39bff;
    border-radius: 8px;
    padding: 0 6px;
    font-size: 10px;
    text-transform: none;
  }
  .edit-arrow { color: #77808f; font-size: 18px; }
  .edit-missing {
    color: #5d6673;
    font-size: 12px;
    border: 1px dashed #2a2f3a;
    border-radius: 6px;
    padding: 34px 0;
    text-align: center;
  }
  .edit-file { font-size: 10px; color: #77808f; word-break: break-all; }
  /* No per-section max-height/scroll: expanded sections grow fully and the
   * drawer body is the single scroll context. */
  .sec-text {
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 13px;
    line-height: 1.45;
    color: #b9c0cc;
    background: #10131a;
    border-radius: 6px;
    padding: 8px;
  }
  /* Same wrapping as plain sections: highlighted JSON must not scroll
   * horizontally inside the narrow side panel. */
  .sec-text.json {
    font-family: 'Cascadia Code', Consolas, Menlo, monospace;
    font-size: 12.5px;
  }
  /* An open payload fills all remaining drawer height (no fixed cap, no
   * dead space below) and scrolls inside. Newer Chromium renders details'
   * children inside an internal ::details-content box, so the flex chain
   * has to reach through it; without support the plain details rule
   * applies instead. */
  .delivery:has(details[open]) {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .delivery details[open] {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .delivery details[open]::details-content {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  .delivery .sec-text {
    flex: 1;
    min-height: 160px;
    overflow-y: auto;
  }
  /* {@html} content carries no Svelte scoping class — the highlight spans
   * need :global() to be reachable by these rules. */
  .sec-text.json :global(.j-key) { color: #7db4ff; }
  .sec-text.json :global(.j-str) { color: #9ad19a; }
  .sec-text.json :global(.j-num) { color: #ffd66b; }
  .sec-text.json :global(.j-kw) { color: #c39bff; }
  .delivery { border: 1px solid #2a2f3a; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
  .delivery-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12.5px; color: #9aa2b1; }
  .delivery-error { color: #ff8f8f; font-size: 12.5px; margin-top: 4px; }
  .ta-deadline {
    color: #9aa2b1;
    font-size: 13px;
    margin-bottom: 10px;
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
  }
  .ta-card {
    border: 1px solid #2a2f3a;
    border-radius: 8px;
    padding: 10px;
    margin-bottom: 10px;
    background: #171b22;
  }
  .ta-head { display: flex; gap: 8px; align-items: center; }
  .ta-name { font-weight: 600; color: #e4e7ec; font-size: 14px; }
  .ta-timing {
    text-transform: uppercase;
    font-size: 11px;
    border-radius: 8px;
    padding: 1px 8px;
  }
  .timing-on-time { background: #14341f; color: #56d581; }
  .timing-late { background: #3d2a1a; color: #ffb26b; }
  .timing-auto { background: #3d331a; color: #ffd66b; }
  .timing-pending { background: #22303d; color: #7ab3d6; }
  .timing-npc { background: #2b1f3d; color: #c39bff; }
  .timing-chosen { background: #2a2f3a; color: #a6adbc; }
  .ta-times { color: #77808f; font-size: 12px; margin: 4px 0 8px; }
  .ta-links { display: flex; gap: 8px; margin-top: 8px; }
  .ta-link {
    background: #262b34;
    border: 1px solid #3a3f4b;
    border-radius: 6px;
    color: #9fc1ff;
    font-size: 12.5px;
    padding: 2px 10px;
    cursor: pointer;
  }
  .ta-link:hover { background: #303745; }
  .upstream-btn {
    display: flex;
    gap: 8px;
    align-items: baseline;
    width: 100%;
    text-align: left;
    background: #1c2028;
    border: 1px solid #2a2f3a;
    border-radius: 8px;
    color: #cdd2dc;
    font-size: 13.5px;
    padding: 8px 10px;
    margin-bottom: 6px;
    cursor: pointer;
  }
  .upstream-btn:hover { border-color: #6ea8fe; }
  .upstream-type { color: #7db4ff; font-size: 12.5px; }
  .upstream-relation { color: #77808f; font-size: 12.5px; margin-left: auto; }
</style>
