<script lang="ts">
  import { search } from '../api';
  import { store } from '../graph.svelte';
  import type { SearchHit } from '../types';

  let query = $state('');
  let scopeAllGames = $state(false);
  let results = $state<SearchHit[]>([]);
  let open = $state(false);
  let busy = $state(false);
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;

  function renderSnippet(snippet: string) {
    const escaped = snippet
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    return escaped.replaceAll('[', '<mark>').replaceAll(']', '</mark>');
  }

  function onInput() {
    if (debounceTimer) clearTimeout(debounceTimer);
    if (query.trim().length < 2) {
      results = [];
      open = false;
      return;
    }
    debounceTimer = setTimeout(runSearch, 300);
  }

  async function runSearch() {
    busy = true;
    try {
      const data = await search(query.trim(), scopeAllGames ? null : store.selectedGameId || null);
      results = data.results;
      open = true;
    } catch {
      results = [];
    } finally {
      busy = false;
    }
  }

  function onHit(hit: SearchHit) {
    open = false;
    if (!hit.node_id) return;
    if (hit.game_id !== store.selectedGameId) {
      store.selectGame(hit.game_id).then(() => store.focusNode(hit.node_id!));
    } else {
      store.focusNode(hit.node_id);
    }
  }
</script>

<div class="searchbar">
  <input
    type="text"
    placeholder="Search all prompts, responses, narratives…"
    bind:value={query}
    oninput={onInput}
    onfocus={() => results.length && (open = true)}
    onblur={() => setTimeout(() => (open = false), 150)}
  />
  <label class="scope">
    <input
      type="checkbox"
      bind:checked={scopeAllGames}
      onchange={() => query.trim().length >= 2 && runSearch()}
    />
    all games
  </label>
  {#if busy}<span class="busy"><span class="spinner sm"></span></span>{/if}
  {#if open && results.length}
    <div class="results">
      {#each results as hit (hit.id)}
        <button
          class="hit"
          class:disabled={!hit.node_id}
          disabled={!hit.node_id}
          onmousedown={() => onHit(hit)}
        >
          <div class="hit-meta">
            {hit.game_id} · turn {hit.turn} · {hit.backend}/{hit.kind}
            {#if !hit.node_id}<span class="no-node">· no node</span>{/if}
          </div>
          <div class="hit-snippet">{@html renderSnippet(hit.snippet)}</div>
        </button>
      {/each}
    </div>
  {:else if open && query.trim().length >= 2 && !busy}
    <div class="results"><div class="no-hits">No matches</div></div>
  {/if}
</div>

<style>
  .searchbar { position: relative; width: 460px; }
  input[type='text'] {
    width: 100%;
    box-sizing: border-box;
    background: #1c2028;
    border: 1px solid #3a3f4b;
    border-radius: 8px;
    color: #e4e7ec;
    font-size: 13px;
    padding: 6px 10px;
  }
  input[type='text']:focus { outline: none; border-color: #6ea8fe; }
  .scope {
    position: absolute;
    right: 8px;
    top: 6px;
    font-size: 11px;
    color: #8b93a1;
    display: flex;
    gap: 3px;
    align-items: center;
    background: #14171d;
    padding: 0 4px;
  }
  .busy { position: absolute; right: 76px; top: 9px; display: flex; }
  .results {
    position: absolute;
    top: 34px;
    left: 0;
    right: 0;
    max-height: 380px;
    overflow-y: auto;
    background: #171b22;
    border: 1px solid #3a3f4b;
    border-radius: 8px;
    z-index: 20;
    display: flex;
    flex-direction: column;
  }
  .hit {
    text-align: left;
    background: none;
    border: none;
    border-bottom: 1px solid #23272f;
    color: #c7ccd6;
    padding: 8px 10px;
    cursor: pointer;
    font-size: 12px;
  }
  .hit:hover:not(.disabled) { background: #1e2430; }
  .hit.disabled { cursor: default; opacity: 0.6; }
  .hit-meta { color: #7d8695; font-size: 11px; margin-bottom: 3px; }
  .no-node { color: #d6a445; }
  .hit-snippet {
    font-size: 11px;
    line-height: 1.35;
    max-height: 52px;
    overflow: hidden;
  }
  .hit-snippet :global(mark) { background: #665a24; color: #ffe9a8; border-radius: 2px; }
  .no-hits { padding: 10px; color: #8b93a1; font-size: 12px; }
</style>
