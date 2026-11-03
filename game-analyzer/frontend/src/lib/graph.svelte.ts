import type { Edge, Node } from '@xyflow/svelte';
import * as api from './api';
import { computeVisible, layout } from './layout';
import type { GameSummary, Graph, GraphNode } from './types';

export interface FlowNodeData extends Record<string, unknown> {
  graphNode: GraphNode;
  expanded: boolean;
  highlight: boolean;
}

class GraphStore {
  games = $state<GameSummary[]>([]);
  selectedGameId = $state('');
  summary = $state<GameSummary | null>(null);
  graph = $state.raw<Graph | null>(null);
  expanded = $state<Record<string, boolean>>({});
  flowNodes = $state.raw<Node[]>([]);
  flowEdges = $state.raw<Edge[]>([]);
  showRefEdges = $state(false);
  openNodeId = $state<string | null>(null);
  focusRequest = $state<{ id: string; n: number } | null>(null);
  highlightId = $state<string | null>(null);
  fitRequest = $state(0);
  loading = $state(false);
  error = $state('');
  layoutTick = $state(0);
  private fetchSeq = 0;

  async loadGames() {
    try {
      const data = await api.fetchGames();
      this.games = data.games;
      this.error = '';
    } catch (err) {
      this.error = String(err);
    }
  }

  async selectGame(gameId: string) {
    this.selectedGameId = gameId;
    this.summary = this.games.find((g) => g.game_id === gameId) ?? null;
    this.openNodeId = null;
    this.expanded = {};
    await this.refreshGraph(true);
  }

  async refreshGraph(fit = false) {
    if (!this.selectedGameId) return;
    // Ignore stale responses when the user switches games while a graph is loading.
    const seq = ++this.fetchSeq;
    this.loading = true;
    try {
      const graph = await api.fetchGraph(this.selectedGameId);
      if (seq !== this.fetchSeq) return;
      this.graph = graph;
      this.summary = this.games.find((g) => g.game_id === this.selectedGameId) ?? null;
      // Drop expansion state for nodes that no longer exist.
      const ids = new Set(graph.nodes.map((n) => n.id));
      this.expanded = Object.fromEntries(
        Object.entries(this.expanded).filter(([id]) => ids.has(id)),
      );
      if (fit) {
        // Default view: the whole spine mission → game → players → turns →
        // finale, with roster/turn cards collapsed inside their groups.
        this.expanded[graph.root] = true;
        this.expanded[`game:${graph.game_id}`] = true;
      }
      this.relayout();
      if (fit) this.fitRequest++;
      this.error = '';
    } catch (err) {
      if (seq === this.fetchSeq) this.error = String(err);
    } finally {
      if (seq === this.fetchSeq) this.loading = false;
    }
  }

  toggleNode(id: string) {
    this.expanded[id] = !this.expanded[id];
    this.relayout();
    // Refit so the expanded content stays on screen.
    this.fitRequest++;
  }

  expandTo(id: string) {
    const graph = this.graph;
    if (!graph) return;
    const parents = new Map<string, string>();
    for (const node of graph.nodes) {
      if (node.parent) parents.set(node.id, node.parent);
    }
    let current: string | undefined = id;
    while (current) {
      this.expanded[current] = true;
      current = parents.get(current);
    }
    this.relayout();
  }

  focusNode(id: string) {
    this.expandTo(id);
    this.highlightId = id;
    this.focusRequest = { id, n: (this.focusRequest?.n ?? 0) + 1 };
  }

  clearHighlight() {
    this.highlightId = null;
    this.relayout();
  }

  openDetail(id: string) {
    this.openNodeId = id;
  }

  closeDetail() {
    this.openNodeId = null;
  }

  relayout() {
    const graph = this.graph;
    if (!graph) {
      this.flowNodes = [];
      this.flowEdges = [];
      return;
    }
    const expandedSet = new Set(
      Object.entries(this.expanded)
        .filter(([, value]) => value)
        .map(([id]) => id),
    );
    const visible = computeVisible(graph, expandedSet);
    const positions = layout(graph, visible);

    this.flowNodes = graph.nodes
      .filter((node) => visible.has(node.id))
      .map((node) => ({
        id: node.id,
        type: 'anode',
        position: positions.get(node.id) ?? { x: 0, y: 0 },
        data: {
          graphNode: node,
          expanded: expandedSet.has(node.id),
          highlight: this.highlightId === node.id,
        },
      }));

    this.flowEdges = graph.edges
      .filter(
        (edge) =>
          visible.has(edge.source) &&
          visible.has(edge.target) &&
          (edge.etype !== 'ref' || this.showRefEdges),
      )
      .map((edge) => {
        if (edge.etype === 'invite') {
          return {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            type: 'invite',
          };
        }
        if (edge.etype === 'ref') {
          return {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            type: 'smoothstep',
            style: 'stroke: #7a7a8c; stroke-dasharray: 2 5; stroke-width: 1;',
          };
        }
        if (edge.etype === 'flow' || edge.etype === 'chain') {
          return {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            type: 'smoothstep',
            animated: true,
            style: 'stroke: #4a9eff;',
          };
        }
        return {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          type: 'smoothstep',
        };
      });
    this.layoutTick++;
  }
}

export const store = new GraphStore();
