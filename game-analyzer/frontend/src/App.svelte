<script lang="ts">
  import { SvelteFlowProvider } from '@xyflow/svelte';
  import { store } from './lib/graph.svelte';
  import Canvas from './lib/components/Canvas.svelte';
  import GameBar from './lib/components/GameBar.svelte';
  import SearchBar from './lib/components/SearchBar.svelte';
  import DetailDrawer from './lib/components/DetailDrawer.svelte';

  store.loadGames().then(() => {
    const active = store.games[0];
    if (active) store.selectGame(active.game_id);
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
