/** URL hash state: #/<game_id>?e=<expanded ids CSV>&n=<open node id>.
 * Node ids contain colons but never commas/&, so a comma is a safe separator.
 * The hash is used (not the path) so the static SPA needs no history fallback,
 * and replaceState keeps every toggle from flooding browser history. */

export interface UrlState {
  game: string | null;
  expanded: string[];
  node: string | null;
}

export function parseUrlState(): UrlState {
  const raw = window.location.hash.replace(/^#\/?/, '');
  if (!raw) return { game: null, expanded: [], node: null };
  const [path, query = ''] = raw.split('?');
  const game = path.split('/')[0] || null;
  const params = new URLSearchParams(query);
  const expanded = (params.get('e') || '').split(',').filter(Boolean);
  return { game, expanded, node: params.get('n') };
}

export function writeUrlState(gameId: string, expandedIds: string[], nodeId: string | null) {
  const parts: string[] = [];
  if (expandedIds.length) parts.push(`e=${expandedIds.join(',')}`);
  if (nodeId) parts.push(`n=${nodeId}`);
  const hash = `#/${gameId}${parts.length ? `?${parts.join('&')}` : ''}`;
  if (window.location.hash !== hash) {
    history.replaceState(null, '', hash);
  }
}
