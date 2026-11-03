export interface GameSummary {
  game_id: string;
  name: string;
  status: string;
  game_status: string;
  language: string;
  created_at: string;
  current_turn: number;
  max_turn: number;
  turns_generated: number;
  players: { total: number; dead: number; spectators: number };
  npcs: number;
  mission: { name: string; archetype: string; stage: string; completed: boolean };
  ship: { hull: number | null; shields: number | null; threat: number | null };
  finale_outcome_type: string;
  active_job: { turn: number; job_type: string; step: string } | null;
  last_event_at: string | null;
  event_count: number;
}

export interface GraphNode {
  id: string;
  type: string;
  parent: string | null;
  label: string;
  subtitle: string;
  summary: string;
  thumb: string | null;
  badges: string[];
  event_ids: number[];
  meta: Record<string, unknown>;
  children_count: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  etype: 'tree' | 'invite' | 'ref' | 'flow' | 'chain';
}

export interface Graph {
  game_id: string;
  root: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ChainSection {
  name: string;
  text: string;
  images?: string[];
}

export interface ChainStep {
  event_id: number;
  backend: 'llm' | 'comfyui';
  kind: string;
  stage: string;
  model: string;
  params: string;
  status: string;
  attempts: number;
  request_at: string | null;
  response_at: string | null;
  duration_ms: number | null;
  image_url: string | null;
  request_sections: ChainSection[];
  response_sections: ChainSection[];
  usage: string;
  finish_reason: string;
}

export interface DeliveryRow {
  push_type: string;
  status: string;
  retry_count: number;
  error: string | null;
  turn: number | null;
  created_at: string;
  sent_at: string | null;
  payload: Record<string, unknown>;
}

export interface UpstreamRef {
  node_id: string;
  label: string;
  type: string;
  relation: string;
}

export interface TurnActionVariant {
  id: string;
  text: string;
  consequence_kind: string;
}

export interface TurnAction {
  participant: string;
  is_npc: boolean;
  brief_node_id: string | null;
  choice_node_id: string | null;
  variants: TurnActionVariant[];
  chosen: TurnActionVariant | null;
  chosen_kind: 'selected' | 'auto_selected' | null;
  submitted_at: string | null;
  timing: 'on-time' | 'late' | 'auto' | 'pending' | 'npc' | 'chosen';
}

export interface TurnActions {
  deadline: string | null;
  outcome_node_id: string | null;
  actions: TurnAction[];
}

export interface NodeDetail {
  node: GraphNode;
  chain: ChainStep[];
  player_view: Record<string, unknown> | null;
  actions: TurnActions | null;
  delivery: DeliveryRow[];
  upstream: UpstreamRef[];
}

export interface SearchHit {
  id: number;
  game_id: string;
  player_id: string;
  turn: number;
  kind: string;
  stage: string;
  backend: string;
  status: string;
  request_at: string | null;
  response_at: string | null;
  snippet: string;
  node_id: string | null;
}
