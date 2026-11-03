import dagre from '@dagrejs/dagre';
import type { Graph, GraphEdge, GraphNode } from './types';

export const NODE_SIZES: Record<string, { width: number; height: number }> = {
  game: { width: 340, height: 160 },
  concept: { width: 300, height: 120 },
  players: { width: 300, height: 130 },
  turns: { width: 300, height: 130 },
  player: { width: 260, height: 150 },
  npc: { width: 260, height: 150 },
  turn: { width: 280, height: 115 },
  scene: { width: 290, height: 140 },
  dialogue: { width: 260, height: 90 },
  brief: { width: 300, height: 160 },
  choice: { width: 290, height: 130 },
  actionimg: { width: 230, height: 260 },
  outcome: { width: 310, height: 150 },
  death: { width: 240, height: 100 },
  msg: { width: 260, height: 110 },
  finale: { width: 310, height: 170 },
};

/** Layout edges: the tree plus the pipeline chain (mission → game → players
 * group → turns group → finale). Flow turn→turn arrows are sequence hints
 * only — feeding them to dagre cascades the turns diagonally. Ref/invite
 * edges would create cycles and are routed outside of dagre. */
function isLayoutEdge(edge: GraphEdge) {
  return edge.etype === 'tree' || edge.etype === 'chain';
}

export function computeVisible(graph: Graph, expanded: Set<string>): Set<string> {
  const children = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (edge.etype !== 'tree') continue;
    const list = children.get(edge.source) ?? [];
    list.push(edge.target);
    children.set(edge.source, list);
  }
  const visible = new Set<string>([graph.root]);
  const stack = [graph.root];
  while (stack.length) {
    const id = stack.pop() as string;
    if (!expanded.has(id)) continue;
    for (const child of children.get(id) ?? []) {
      if (!visible.has(child)) {
        visible.add(child);
        stack.push(child);
      }
    }
  }
  return visible;
}

export function layout(
  graph: Graph,
  visible: Set<string>,
): Map<string, { x: number; y: number }> {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setGraph({
    rankdir: 'TB',
    // Roomy vertical gap: invite edges route below the roster row inside it.
    ranksep: 110,
    nodesep: 24,
    edgesep: 10,
    marginx: 16,
    marginy: 16,
  });
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  for (const node of graph.nodes) {
    if (!visible.has(node.id)) continue;
    const size = NODE_SIZES[node.type] ?? { width: 260, height: 120 };
    // dagre mutates the object passed to setNode (writes x/y/rank into it):
    // sharing the NODE_SIZES entry between same-type nodes makes them all
    // read the last sibling's coordinates and stack on one spot.
    dagreGraph.setNode(node.id, { ...size });
  }
  for (const edge of graph.edges) {
    if (!isLayoutEdge(edge)) continue;
    if (visible.has(edge.source) && visible.has(edge.target)) {
      dagreGraph.setEdge(edge.source, edge.target);
    }
  }
  dagre.layout(dagreGraph);
  const positions = new Map<string, { x: number; y: number }>();
  for (const node of graph.nodes) {
    if (!visible.has(node.id)) continue;
    const placed = dagreGraph.node(node.id) as { x: number; y: number };
    const size = NODE_SIZES[node.type] ?? { width: 260, height: 120 };
    positions.set(node.id, {
      x: Math.round(placed.x - size.width / 2),
      y: Math.round(placed.y - size.height / 2),
    });
  }
  return positions;
}

export function nodeSize(node: GraphNode) {
  return NODE_SIZES[node.type] ?? { width: 260, height: 120 };
}
