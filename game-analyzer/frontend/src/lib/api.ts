import type { GameSummary, Graph, NodeDetail, SearchHit } from './types';

async function get<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail.slice(0, 200)}`);
  }
  return response.json() as Promise<T>;
}

export function fetchGames() {
  return get<{ games: GameSummary[] }>('/api/games');
}

export function fetchGraph(gameId: string) {
  return get<Graph>(`/api/games/${encodeURIComponent(gameId)}/graph`);
}

export function fetchNodeDetail(gameId: string, nodeId: string) {
  return get<NodeDetail>(`/api/games/${encodeURIComponent(gameId)}/nodes/${encodeURIComponent(nodeId)}`);
}

export function search(query: string, gameId: string | null) {
  const params = new URLSearchParams({ q: query });
  if (gameId) params.set('game_id', gameId);
  return get<{ results: SearchHit[] }>(`/api/search?${params.toString()}`);
}

export async function importLogs() {
  const response = await fetch('/api/import-logs', { method: 'POST' });
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.json();
}
