<script lang="ts">
  import { store } from '../graph.svelte';
  import { importLogs } from '../api';

  let autoRefresh = $state(false);
  let importing = $state(false);
  let importResult = $state('');
  let timer: ReturnType<typeof setInterval> | null = null;

  $effect(() => {
    if (timer) clearInterval(timer);
    timer = autoRefresh
      ? setInterval(() => {
          store.loadGames().then(() => store.refreshGraph(false));
        }, 15000)
      : null;
    return () => {
      if (timer) clearInterval(timer);
    };
  });

  async function onImport() {
    importing = true;
    importResult = '';
    try {
      const result = await importLogs();
      importResult = `imported=${result.imported} skipped=${result.skipped_existing}`;
      await store.loadGames();
      await store.refreshGraph(false);
    } catch (err) {
      importResult = String(err);
    } finally {
      importing = false;
    }
  }

  const summary = $derived(store.summary);
</script>

<div class="gamebar">
  <div class="row">
    <select
      class="game-select"
      value={store.selectedGameId}
      onchange={(e) => store.selectGame(e.currentTarget.value)}
    >
      <option value="" disabled>Select game…</option>
      {#each store.games as game (game.game_id)}
        <option value={game.game_id}>
          {game.name} — {game.game_id} ({game.status}, turn {game.current_turn})
        </option>
      {/each}
    </select>
    <button class="btn" onclick={() => store.refreshGraph(true)} disabled={store.loading || !store.selectedGameId}>
      {#if store.loading}<span class="spinner sm"></span>{:else}⟳{/if}
      Refresh
    </button>
    <label class="check">
      <input type="checkbox" bind:checked={autoRefresh} /> auto 15s
    </label>
    <label class="check">
      <input
        type="checkbox"
        checked={store.showRefEdges}
        onchange={(e) => {
          store.showRefEdges = e.currentTarget.checked;
          store.relayout();
        }}
      /> image refs
    </label>
    <button class="btn" onclick={onImport} disabled={importing} title="Import legacy file logs into generation_events via game-server">
      {#if importing}<span class="spinner sm"></span>{:else}⭳{/if}
      {importing ? 'Importing…' : 'Import legacy logs'}
    </button>
    {#if importResult}<span class="import-result">{importResult}</span>{/if}
  </div>
  {#if summary}
    <div class="chips">
      <span class="chip">status: {summary.game_status || summary.status}</span>
      <span class="chip">turn {summary.current_turn}/{summary.max_turn}</span>
      <span class="chip">players {summary.players.total} (dead {summary.players.dead})</span>
      <span class="chip">NPCs {summary.npcs}</span>
      <span class="chip">mission: {summary.mission.archetype} {summary.mission.stage}</span>
      <span class="chip">hull {summary.ship.hull ?? '—'}</span>
      <span class="chip">shields {summary.ship.shields ?? '—'}</span>
      <span class="chip">threat {summary.ship.threat ?? '—'}</span>
      {#if summary.active_job}
        <span class="chip chip-job">job: {summary.active_job.job_type} t{summary.active_job.turn} — {summary.active_job.step}</span>
      {/if}
      {#if summary.finale_outcome_type}
        <span class="chip">finale: {summary.finale_outcome_type}</span>
      {/if}
      <span class="chip chip-dim">{summary.event_count} events · {summary.last_event_at ?? ''}</span>
    </div>
  {/if}
  {#if store.error}<div class="error">{store.error}</div>{/if}
</div>

<style>
  .gamebar {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px 12px;
    background: #14171d;
    border-bottom: 1px solid #2a2f3a;
  }
  .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .game-select {
    background: #1c2028;
    color: #e4e7ec;
    border: 1px solid #3a3f4b;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 14px;
    min-width: 340px;
  }
  .btn {
    background: #262b34;
    color: #cfd4dd;
    border: 1px solid #3a3f4b;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 13.5px;
    cursor: pointer;
  }
  .btn:hover:not(:disabled) { background: #303745; }
  .btn:disabled { opacity: 0.5; cursor: default; }
  .btn .spinner { margin-right: 4px; vertical-align: -2px; }
  .check { font-size: 13.5px; color: #9aa2b1; display: flex; gap: 4px; align-items: center; }
  .import-result { font-size: 12.5px; color: #8b93a1; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip {
    font-size: 12.5px;
    background: #1c2028;
    border: 1px solid #2a2f3a;
    border-radius: 10px;
    padding: 2px 8px;
    color: #b9c0cc;
  }
  .chip-job { color: #ffd66b; border-color: #665a24; }
  .chip-dim { color: #77808f; }
  .error { color: #ff8f8f; font-size: 13.5px; }
</style>
