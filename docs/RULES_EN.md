<!-- markdownlint-disable MD013 MD060 -->

# AI Game Master Rules

## Game Overview

AI Game Master is a cooperative game with AI-generated narrative, delivered through a Telegram bot.
Each turn generates a unique story in a space setting (starship, crew, adventures), where players
make decisions that influence the plot development.

## Core Mechanics

### 1. Registration and Onboarding

- After `/start`, the player goes through an interview (5+ questions with options)
- Questions are LLM-generated; each option contains role_scores for role assignment
- Additional questions determine the character's **species** and **gender**:
  - 10 species questions (human, humanoid, non-humanoid, energy, cybernetic, symbiotic)
  - 4 gender questions (male, female, neutral, fluid, multiple, etc.)
- Avatar is generated via ComfyUI with species/gender info
- Role is assigned deterministically by maximum role_scores points
- After onboarding: role, description, species, gender, avatar are sent to the player

### 2. Crew Assembly

- **GAME_START_MIN_PLAYERS** (default: 3) — minimum live players to start
- Unfilled seats at game start are taken by **NPCs** — up to exactly
  **NPC_COUNT = 4** seats, preferring the key roles
  `chief_engineer` / `medical_officer` / `pilot` / `science_officer`
- A live player who completes onboarding can **take an NPC's seat** in the same role
- NPC releases the role when a live player takes it
- A kicked or dead player's seat is **NOT refilled with an NPC** — it stays
  empty for the rest of the game; the crew is never replenished automatically
- Upon onboarding completion, all other players receive a **notification with avatar and profile**

### 3. Game Start and Mission

When enough players have joined (>= GAME_START_MIN_PLAYERS live players):

1. **NPC Generation** — NPCs are created for unfilled key roles (up to
   NPC_COUNT = 4 seats) with avatars:
   - No onboarding/interview
   - Species and gender are randomized
   - Avatar prompt is randomized for variety
   - Each NPC starts with **loyalty** 70/100 (see "NPC Loyalty and Mutiny")
2. **Mission Generation** — LLM creates:
   - Mission name and description
   - Mission objectives divided into **stages**
   - Each stage has completion requirements
   - Mission data stored separately (`game_missions` table)
   - Used in every subsequent turn's generation algorithm
3. **Bridge Image Generation** — complex image pipeline:
   - LLM generates a prompt incorporating all roles and avatars
   - Team avatars used as **reference images** (ControlNet/IP-Adapter)
   - Single scene: starship bridge with the crew at their stations
4. **Mission Briefing** — sent to all players:
   - Mission description and objectives
   - Generated bridge image with the crew

## Turn Algorithm


```text
┌─────────────────────────────────────────────────────────────────┐
│  TURN START                                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Generate global intro based on previous turn:              │
│     - If prior turn exists — factor in its consequences        │
│     - Factor in current mission state and stage progress       │
│     - Create situation description for the ship surroundings   │
│                                                                 │
│  2. Generate image prompt from global intro                    │
│     → Generate image via ComfyUI                               │
│                                                                 │
│  3. Generate briefing for EACH player:                         │
│     - Personal intro (what this character sees/hears/feels)    │
│     - 5 action choices (2/2/1 kind quota)                      │
│     - Each action has a HIDDEN consequence                     │
│     - Some consequences improve state, some worsen             │
│     - Consequences have influence coefficients (tracked)       │
│                                                                 │
│  4. Generate individual briefing image prompt                  │
│     → Generate image via ComfyUI                               │
│                                                                 │
│  5. Send to live players:                                      │
│     - Global intro + global image                              │
│     - Individual briefing + individual image                   │
│     - Action choices + the turn deadline                       │
│       ("Turn closes at: ..." UTC)                              │
│                                                                 │
│  6. Collect responses from live players:                       │
│     - T-2h and T-30m deadline reminders                        │
│     - Anyone who hasn't chosen gets an LLM auto-pick;          │
│       on LLM failure a HESITATION (delay) is recorded          │
│                                                                 │
│  7. Once ALL live players have responded:                      │
│     - Feed intros to NPCs                                      │
│     - NPCs choose actions WITHOUT knowing consequences         │
│     - NPCs don't see other players' choices                    │
│                                                                 │
│  8. Analyze ALL chosen consequences:                           │
│     - Generate combined outcome                                │
│     - The LLM returns ship DELTAS and entity_id events;        │
│       absolute state is computed by code                       │
│     - Player actions have MORE WEIGHT than NPC actions         │
│     - Check mission objective progress                         │
│       → Stages progress NON-LINEARLY, effect accumulates      │
│     - Threat level grows every turn (+8 plus accelerators)    │
│     - If objectives met → notify live players                  │
│                                                                 │
│  9. Allowed outcomes:                                          │
│     - Wounds (escalate: minor → moderate → critical)           │
│     - Crew deaths (critical wound + any new wound = death)    │
│     - Ship destruction: hull 0 = the end (decided by code)    │
│     - Total crew wipe (crew_wiped) is reachable —             │
│       the crew is never replenished                            │
│                                                                 │
│ 10. For dead crew members (non-NPC):                           │
│     - Consequences and intros generated each turn              │
│     - They remain spectators (see but don't influence)         │
│     - Dead player can press /start                             │
│       → join a new game                                        │
│       → or rejoin current game in a new role (taking an NPC seat)
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Key principles:**

- Player actions affect outcomes **more** than NPC actions
- Mission progress is non-linear — correct actions accumulate progress
- Ship status, wounds, threat and loyalty are persistent game state
- There are five ways to lose: threat 100, hull 0, crew wiped,
  NPC mutiny, failed mission (see "Outcome Matrix")

## Game Master Behavior

### At each turn start

1. **State check** — game not ended (ship/crew alive), all live players responded
2. **Story generation** — factors in previous turn consequences, mission progress
3. **Content generation** — global image and individual briefing images via ComfyUI
4. **Action processing** — live players choose first, NPCs choose after (no consequence knowledge)

## Player Communication

### Text messages

- Players can communicate with Game Master at any time
- GM responds in character as the ship computer

### Voice messages

- Voice message support (no transcription yet)
- Recording and storage

## Crew Roles

| Role                 | Description                    | Key                          |
|----------------------|--------------------------------|------------------------------|
| Captain              | Crew command                   | `captain`                    |
| Chief Engineer       | Ship's technical condition     | `chief_engineer`             |
| Science Officer      | Research and data analysis     | `science_officer`            |
| Communications Off.  | Negotiation and coordination   | `communications_officer`     |
| Security Chief       | Threat assessment/protection   | `security_chief`             |
| Navigator            | Course and navigation          | `navigator`                  |
| Medical Officer      | Crew health                    | `medical_officer`            |
| Tactical Officer     | Weapons and shields            | `tactical_officer`           |
| Xenobiologist        | Alien life study               | `xenobiologist`              |
| Pilot                | Ship control                   | `pilot`                      |

## Game Mechanics

### Actions and Consequences

- Each action has visible text and a **hidden consequence** with a kind
  (`consequence_kind`): `progress` / `injury` / `fatal`
- Briefing choice quota: **2 progress / 2 injury / 1 fatal** (defaults,
  configurable via `GAME_TURN_PROGRESS_ACTIONS` / `GAME_TURN_INJURY_ACTIONS` /
  `GAME_TURN_FATAL_ACTIONS`)
- The kind is hidden from the player — the choice is blind; action text must
  not reveal the outcome
- The consequence tag is a binding commitment for the LLM: `[fatal]` means a
  participant of that decision dies, `[injury]` — a wound, `[progress]` — the
  mission advances
- The probabilities of the 5 outcome options are reweighted by code
  (`option_badness` / `reweight_probabilities` / `compute_risk_factor`):
  catastrophes become more likely with a damaged hull, high threat and a high
  share of reckless decisions

### Mission Progress

- Mission divided into stages (2 to 4)
- Stage thresholds are clamped to **3–5** by the engine
- Stages progress non-linearly — correct actions accumulate progress
- **Completed stages are frozen** — regression cannot touch them
- **Regression is capped** at −1 per turn, incomplete stages only
- **An empty turn = 0 progress**: hesitation and passive turns do not move
  the mission
- Progress tracked by numeric counter; stage marked complete when threshold reached

### Team Play

- All player decisions affect the final outcome
- Live player actions have more weight
- NPCs act logically within their role

### The Ship (persistent state)

- Hull (`hull_integrity`), shields (`shields`) and the offline-systems list
  (`systems_offline`) are persistent game state in `game_state`, accumulated
  turn over turn
- The LLM returns only **per-turn deltas**: `ship_hull_change` /
  `ship_shields_change` / `systems_taken_offline` / `systems_restored`;
  code applies them to the current values (clamped 0–100)
- Repairs (`systems_restored`, positive hull delta) require an explicit
  repair action among the decisions
- **Hull 0 = ship destroyed** — decided by code (`end_game("ship_destroyed")`);
  the LLM cannot declare destruction itself
- Ship status is telegraphed in briefings: below 40/100 — grave damage,
  below 20/100 — the ship is at death's door

### Doom Clock (threat level)

- `threat_level` 0–100 — code-owned state in `game_state`; the tick every
  turn is computed by **code**, never by the LLM
- Tick formula (`compute_threat_tick`): **+8** base, **+5 × the share of
  auto-picked (hesitation) actions** among the turn's decisions, **+3** when
  hull < 40%, **+2** when the mission stagnated (progress did not grow)
- **Threat 100 → game over** (`end_game("overwhelmed")`) — even when the
  narrative is optimistic
- The scale is visible to players in briefings and the outcome push; above
  70/100 briefings must offer acute, desperate situations

### Wounds and Healing

- Three severity steps: **minor → moderate → critical** (`wound_severity` is
  stored persistently per player and NPC)
- A new wound is resolved via the `resolve_injury` ladder: the result is the
  max of the stored and incoming severity — wounds never heal on their own
- **Critical + any new wound = DEATH** — mechanically, by code
- Healing (`crew_healed`) happens only when the Medical Officer explicitly
  picks a treatment action: the severity improves one step, `healthy` = fully
  healed
- Wounds are telegraphed in the briefing: wounded characters see their
  condition; a critically wounded one knows the next wound is probably fatal

### NPC Loyalty and Mutiny

- Every NPC has **loyalty** 0–100 (`npc_profiles.loyalty`, starts at 70) —
  code-owned state the LLM never touches
- Every turn `compute_loyalty_change` applies: −8 per death (cap −16), −5 when
  hull damage ≥ 15, −4 on mission regression, +3 on progress, +2 per healed
  NPC (cap +4); the result is clamped
- Loyalty is visible in `/game/team` (`loyalty_band` / `loyalty_status`:
  steadfast / uneasy / on edge / mutinous) and feeds the NPC's action choice
- **2+ active NPCs at loyalty ≤ 15 → open mutiny**, game over
  (`end_game("mutiny")`)

### Turn Window and Hesitation

- Every turn has a **deadline** (`game_turns.deadline`, UTC) — the moment the
  scheduler generates the next turn; the briefing push shows it as
  "Turn closes at: ..."
- The scheduler sends **T-2h (level 1) and T-30m (level 2) reminders** to
  players who haven't chosen (`GET /game/turn-deadline/{game_id}`,
  `POST /game/remind-turn/{game_id}/{turn}`)
- Anyone who hasn't chosen by the deadline gets an LLM **auto-pick**
  (in character)
- On LLM failure the auto-pick is honestly recorded as **HESITATION**
  (`consequence_kind = "delay"`): it does not move the mission, and the share
  of hesitations accelerates the doom clock
- Nothing is disguised: a timeout is never presented as the player's own
  meaningful choice

### Death and Spectating

- Crew members can die — the LLM addresses victims by **entity_id**
  (`p<player_id>` for players, `n<npc_key>` for NPCs) in `dead_crew_members`;
  matching is by id, with an exact-name fallback from the roster
- No caps or cooldowns: every death assigned by tag is applied
- A second death channel is mechanical: critical wound + any new wound
- If everyone dies (players and NPCs) → `end_game("crew_wiped")` — reachable,
  because the crew is never replenished
- Dead non-NPC players become spectators
- Spectators see story development but don't influence it
- Can rejoin via /start

### Outcome Matrix

The final verdict is computed by **code** (`compute_outcome_type`) from the
end-state — the LLM only writes the finale narrative in the required tone,
without a predetermined verdict:

- **triumph** — mission completed, hull ≥ 60%, alive crew ≥ 70%, threat < 70
- **victory** — mission completed, hull ≥ 30%, alive crew ≥ 40%
- **pyrrhic** — mission completed at any price (including a destroyed
  ship/wiped crew)
- **stalemate** — mission not completed, threat reached 100, but progress
  ≥ 60% of thresholds: the crew survived and withdrew with part of the objective
- **defeat** — everything else (ship destroyed, crew wiped, plain failure)

The end reason (5 statuses: `mission_complete` / `ship_destroyed` /
`crew_wiped` / `overwhelmed` / `mutiny`) is passed into the finale prompt as a
fact; the verdict token is stored in `game_state.finale_outcome_type`.

### Post-game Summary

- Right after the finale, players receive a **summary** as a separate message
  (`push_game_summary`): outcome, end reason, turns played, hull/shields/threat,
  casualties, and per-player action stats including hesitation counts
  (`get_game_action_stats` over `player_action_stats`)
- Telemetry over all ended games: `GET /admin/win-rate` (per-token outcome
  counts, win_share, avg_turns, avg_auto_ratio)

### Rules Layer (game_rules.py)

Between the LLM response (`combined_outcome`) and the database write runs a
deterministic **rules layer**:

- Normalizes mission stages (thresholds 3–5, strict 1-based indexing)
- Applies the regression cap (−1) and freezes completed stages
- Applies ship deltas and decides destruction (hull 0)
- Resolves wounds on the ladder (critical + new = death)
- Ticks the doom clock and applies NPC loyalty changes
- Computes the finale verdict (outcome matrix)
- Guarantees fairness: the LLM stays creative, the engine keeps it honest

Pure functions in `game-server/game_rules.py`, no DB, no LLM — easy to unit test.

## Technical Details

### System Architecture

```text
┌──────────────────────────────────────────────────────────┐
│  Telegram Bot (aiogram)                                 │
│  - Commands: /start, /profile, /turn, /help            │
│  - Onboarding with FSM                                 │
│  - Message handling                                    │
│  - ✅ Push-server (port 9090) — receiving briefings    │
└──────────┬───────────────────────────────────────────┬───┘
           │                                           │
           │ POST /push/briefings                       │ /gm* commands
           ▼                                           ▼
┌─────────────────────────────────────────────────────────┐
│  Game Master API (FastAPI)                             │
│  - Story generation via LLM                            │
│  - Image generation via ComfyUI                        │
│  - State management                                    │
│  - ✅ Push-client with exponential retry — sending briefings
└────────────────────────┬────────────────────────────────┘
                       │
               ┌───────┴───────┐
               ▼               ▼
        ┌───────────┐   ┌──────────┐
        │Scheduler  │   │ComfyUI   │
        │(cron)     │   │(GPU gen) │
        └───────────┘   └──────────┘
```

### Database

- SQLite for profiles, sessions, turns, actions, messages, missions
- Missions stored separately (game_missions, mission_stages)
- Each turn linked to game_id 

## Conclusion

AI Game Master creates a unique cooperative experience with a living story,
generated content, and deep consequence mechanics.
Players don't just choose actions — they determine the fate of the crew and ship.
