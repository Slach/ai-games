<script lang="ts">
  import { SvelteFlowProvider } from '@xyflow/svelte';
  import { store } from './lib/graph.svelte';
  import { parseUrlState, type UrlState } from './lib/url';
  import Canvas from './lib/components/Canvas.svelte';
  import GameBar from './lib/components/GameBar.svelte';
  import SearchBar from './lib/components/SearchBar.svelte';
  import DetailDrawer from './lib/components/DetailDrawer.svelte';

  function urlHasState(url: UrlState) {
    return url.expanded.length > 0 || url.node !== null;
  }

  store.loadGames().then(() => {
    const url = parseUrlState();
    const fromUrl = url.game && urlHasState(url) ? url : null;
    const target = (fromUrl && store.games.some((g) => g.game_id === url.game))
      ? url.game
      : store.games[0]?.game_id;
    if (target) store.selectGame(target, fromUrl ?? undefined);
  });

  // Manual hash edits / pasted links in the same tab (our own writes use
  // replaceState and never fire this).
  window.addEventListener('hashchange', () => {
    const url = parseUrlState();
    if (!url.game || !store.games.some((g) => g.game_id === url.game)) return;
    if (url.game !== store.selectedGameId) {
      store.selectGame(url.game, urlHasState(url) ? url : undefined);
    } else if (urlHasState(url)) {
      store.applyRestore(url.expanded, url.node);
    }
  });
</script>

<SvelteFlowProvider>
  <div class="app">
    <header class="top">
      <span class="brand">game-analyzer</span>
      <GameBar />
      <SearchBar />
    </header>
    <div class="canvas-wrap">
      <Canvas />
      <DetailDrawer />
    </div>
  </div>
</SvelteFlowProvider>

<style>
  :global(html, body, #app) {
    margin: 0;
    height: 100%;
    background: #101216;
    color: #d7dae0;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 14px;
  }
  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
  }
  .top {
    display: flex;
    align-items: center;
    gap: 14px;
    background: #14171d;
    border-bottom: 1px solid #2a2f3a;
    padding: 0 12px;
  }
  .brand {
    font-weight: 700;
    font-size: 14px;
    color: #7db4ff;
    letter-spacing: 0.5px;
    white-space: nowrap;
  }
  .canvas-wrap {
    position: relative;
    flex: 1;
    min-height: 0;
  }
</style>
