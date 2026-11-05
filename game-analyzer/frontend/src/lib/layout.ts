import type { Graph, GraphNode } from './types';

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

const DEFAULT_SIZE = { width: 260, height: 120 };
/** Vertical gap between spine blocks (mission → game → players → turns → finale). */
const SPINE_GAP = 90;
/** Gap between vertically stacked siblings in a column / subtree. */
const SIB_GAP = 24;
/** Horizontal gap between a parent card and its child column. */
const H_GAP = 80;

export function nodeSize(node: GraphNode) {
  return NODE_SIZES[node.type] ?? DEFAULT_SIZE;
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

/** Deterministic pipeline layout (no dagre):
 * - the spine (mission → game → players group → turns group → finale) runs
 *   top-down, centered on x = 0;
 * - the roster (players + NPCs) expands into a vertical column LEFT of the
 *   players group;
 * - turns expand into a vertical column RIGHT of the turns group;
 * - a turn's own subtree grows further right (children in a vertical stack
 *   beside their parent, parents centered on their stack) and each turn's
 *   slot height is its subtree height — expanding a turn pushes the turns
 *   below it down. */
export function layout(
  graph: Graph,
  visible: Set<string>,
): Map<string, { x: number; y: number }> {
  const nodesById = new Map(graph.nodes.map((n) => [n.id, n]));
  const childrenOf = new Map<string, GraphNode[]>();
  for (const node of graph.nodes) {
    if (!visible.has(node.id) || !node.parent || !visible.has(node.parent)) continue;
    const list = childrenOf.get(node.parent) ?? [];
    list.push(node);
    childrenOf.set(node.parent, list);
  }

  const positions = new Map<string, { x: number; y: number }>();

  // Place a subtree whose children sit RIGHT of the parent, stacked
  // vertically. Returns the subtree height (≥ the parent's own height).
  const placeLR = (node: GraphNode, x: number, top: number): number => {
    const size = nodeSize(node);
    const kids = childrenOf.get(node.id) ?? [];
    if (!kids.length) {
      positions.set(node.id, { x, y: top });
      return size.height;
    }
    let y = top;
    for (const kid of kids) {
      y += placeLR(kid, x + size.width + H_GAP, y) + SIB_GAP;
    }
    const total = Math.max(size.height, y - SIB_GAP - top);
    positions.set(node.id, { x, y: top + (total - size.height) / 2 });
    return total;
  };

  // Mirror of placeLR: children sit LEFT of the parent.
  const placeRL = (node: GraphNode, right: number, top: number): number => {
    const size = nodeSize(node);
    const kids = childrenOf.get(node.id) ?? [];
    const left = right - size.width;
    if (!kids.length) {
      positions.set(node.id, { x: left, y: top });
      return size.height;
    }
    let y = top;
    for (const kid of kids) {
      y += placeRL(kid, left - H_GAP, y) + SIB_GAP;
    }
    const total = Math.max(size.height, y - SIB_GAP - top);
    positions.set(node.id, { x: left, y: top + (total - size.height) / 2 });
    return total;
  };

  const gid = graph.game_id;
  let y = 0;
  for (const kind of ['concept', 'game', 'players', 'turns', 'finale'] as const) {
    const id = `${kind}:${gid}`;
    if (!visible.has(id)) continue;
    const node = nodesById.get(id);
    if (!node) continue;
    const size = nodeSize(node);
    const x = -size.width / 2;
    positions.set(id, { x, y });
    let blockHeight = size.height;
    if (kind === 'players') {
      let cy = y;
      for (const kid of childrenOf.get(id) ?? []) {
        cy += placeRL(kid, x - H_GAP, cy) + SIB_GAP;
      }
      blockHeight = Math.max(blockHeight, cy - SIB_GAP - y);
    }
    if (kind === 'turns') {
      let cy = y;
      for (const kid of childrenOf.get(id) ?? []) {
        cy += placeLR(kid, x + size.width + H_GAP, cy) + SIB_GAP;
      }
      blockHeight = Math.max(blockHeight, cy - SIB_GAP - y);
    }
    y += blockHeight + SPINE_GAP;
  }
  return positions;
}
