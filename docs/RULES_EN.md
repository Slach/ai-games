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
- Unfilled roles are filled by **NPCs**
- A live player who completes onboarding can **replace an NPC** in the same role
- NPC releases the role when a live player takes it
- Upon onboarding completion, all other players receive a **notification with avatar and profile**

### 3. Game Start and Mission

When enough players have joined (>= GAME_START_MIN_PLAYERS live players):

1. **NPC Generation** — NPCs are created for all unfilled roles with avatars:
   - No onboarding/interview
   - Species and gender are randomized
   - Avatar prompt is randomized for variety
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

> Internal DB and API use `day` numbering, but game mechanics and user interface
> use the term **turn**.

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
│     - 3-4 action choices                                       │
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
│     - Action choices                                           │
│                                                                 │
│  6. Collect responses from live players                        │
│                                                                 │
│  7. Once ALL live players have responded:                      │
│     - Feed intros to NPCs                                      │
│     - NPCs choose actions WITHOUT knowing consequences         │
│     - NPCs don't see other players' choices                    │
│                                                                 │
│  8. Analyze ALL chosen consequences:                           │
│     - Generate combined outcome                                │
│     - Update ship and crew state                               │
│     - Player actions have MORE WEIGHT than NPC actions         │
│     - Check mission objective progress                         │
│       → Stages progress NON-LINEARLY, effect accumulates      │
│     - If objectives met → notify live players                  │
│                                                                 │
│  9. Allowed outcomes:                                          │
│     - Crew member death                                        │
│     - Ship destruction (story is finite)                       │
│                                                                 │
│ 10. For dead crew members (non-NPC):                           │
│     - Consequences and intros generated each turn              │
│     - They remain spectators (see but don't influence)         │
│     - Dead player can press /start                             │
│       → join a new game                                        │
│       → or rejoin current game in a new role (replacing NPC)  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Key principles:**

- Player actions affect outcomes **more** than NPC actions
- Mission progress is non-linear — correct actions accumulate progress
- Ship and crew state tracked by numeric coefficients
- Endgame possible: ship destruction or successful mission completion

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
| Chief Engineer       | Ship's technical condition     | `chief_engineer`             |
| Science Officer      | Research and data analysis     | `science_officer`            |
| Communications Off.  | Negotiation and coordination   | `communications_officer`     |
| Security Chief       | Threat assessment/protection   | `security_chief`             |
| Navigator            | Course and navigation          | `navigator`                  |
| Medical Officer      | Crew health                    | `medical_officer`            |
| Tactical Officer     | Weapons and shields            | `tactical_officer`           |
| Quartermaster        | Resources and supply           | `quartermaster`              |
| Xenobiologist        | Alien life study               | `xenobiologist`              |
| Pilot                | Ship control                   | `pilot`                      |

## Game Mechanics

### Actions and Consequences

- Each action has visible text and **hidden consequence**
- Consequences have numeric influence coefficients
- Some improve state, some worsen it
- All coefficients tracked per action

### Mission Progress

- Mission divided into stages
- Stages progress non-linearly — correct actions accumulate progress
- Progress tracked by numeric counter
- Stage marked complete when threshold reached

### Team Play

- All player decisions affect the final outcome
- Live player actions have more weight
- NPCs act logically within their role

### Death and Spectating

- Crew members can die
- Dead non-NPC players become spectators
- Spectators see story development but don't influence it
- Can rejoin via /start

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
- Each turn linked to game_id and day (internal numbering)

## Conclusion

AI Game Master creates a unique cooperative experience with a living story,
generated content, and deep consequence mechanics.
Players don't just choose actions — they determine the fate of the crew and ship.
