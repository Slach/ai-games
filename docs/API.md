# Game Master API Documentation

## Base URL
```
http://game-master-api:8000
```

## Endpoints

### Onboarding

#### Start Onboarding Session
**POST** `/onboarding/start`

Start a new onboarding session for a player.

**Request Body:**
```json
{
  "player_id": 281412419,
  "game_id": "default_game",
  "language": "en"
}
```

**Response:**
```json
{
  "session_id": "abc123",
  "game_id": "default_game",
  "question": {
    "id": 1,
    "text": "Корабль обнаружил неизвестный сигнал. Ваши действия?",
    "options": [
      {"value": "cautious", "label": "Изучить сигнал с осторожностью"},
      {"value": "bold", "label": "Немедленно подойти ближе"}
    ]
  }
}
```

#### Submit Onboarding Answer
**POST** `/onboarding/{session_id}/answer`

Submit an answer to an onboarding question.

**Path Parameters:**
- `session_id` - The onboarding session ID

**Request Body:**
```json
{
  "question_id": 1,
  "answer": "cautious"
}
```

**Query Parameters:**
- `language` - Optional, defaults to "en"

**Response:**
```json
{
  "completed": false,
  "next_question": {
    "id": 2,
    "text": "...",
    "options": [...]
  }
}
```

If completed:
```json
{
  "completed": true,
  "next_question": null,
  "profile": {
    "player_id": 281412419,
    "role": "Chief Engineer",
    "role_description": "...",
    "personality_traits": ["технический", "практичный"],
    "game_id": "default_game"
  }
}
```

#### Complete Onboarding
**POST** `/onboarding/{session_id}/complete`

Complete onboarding and trigger avatar generation.

**Path Parameters:**
- `session_id` - The onboarding session ID

**Response:**
```json
{
  "status": "completed",
  "profile": {...},
  "avatar_url": "http://..."
}
```

#### Get Onboarding Status
**GET** `/onboarding/{session_id}`

Get the current status of an onboarding session.

**Path Parameters:**
- `session_id` - The onboarding session ID

**Query Parameters:**
- `language` - Optional, defaults to "en"

**Response:**
```json
{
  "session_id": "abc123",
  "game_id": "default_game",
  "current_question": 1,
  "completed": false,
  "next_question": {...}
}
```

#### Get Onboarding Questions (Static)
**GET** `/onboarding/questions`

Get all static onboarding questions.

**Response:**
```json
{
  "questions": [
    {
      "id": 1,
      "text": "...",
      "options": [...]
    }
  ]
}
```

### Player Profile

#### Get Player Profile
**GET** `/players/{player_id}/profile`

Get the player's profile.

**Path Parameters:**
- `player_id` - The player ID

**Response:**
```json
{
  "player_id": 281412419,
  "role": "Chief Engineer",
  "role_description": "...",
  "personality_traits": ["технический", "практичный"],
  "avatar_url": "http://...",
  "game_id": "default_game",
  "last_poll": "2026-03-11T12:00:00"
}
```

#### Get All Players
**GET** `/players`

Get all players in the current game.

**Response:**
```json
[
  {"player_id": 281412419, "game_id": "default_game"},
  {"player_id": 123456, "game_id": "default_game"}
]
```

### Game State

#### Get Game State
**GET** `/game/state`

Get the current game state.

**Response:**
```json
{
  "day": 1,
  "status": "active",
  "last_updated": "2026-03-11T12:00:00"
}
```

#### Get Current Game Day
**GET** `/game/current-day`

Get the current game day episode.

**Response:**
```json
{
  "day": 1,
  "story": "Daily story narrative...",
  "npc_dialogues": [
    {"npc": "Captain", "dialogue": "..."}
  ],
  "player_actions": [
    {"id": "action_1", "text": "..."}
  ],
  "generated_content": {
    "image": "/content/day_1/scene.jpg",
    "comic": "/content/day_1/comic.webp"
  }
}
```

#### Get Game Day by Number
**GET** `/game/day/{day_num}`

Get a specific day's episode.

**Path Parameters:**
- `day_num` - The day number

**Response:** Same as `/game/current-day`

#### Poll Game Updates
**GET** `/game/poll/{player_id}`

Poll for new game updates since last poll.

**Path Parameters:**
- `player_id` - The player ID

**Query Parameters:**
- `since` - Optional, ISO timestamp for last poll

**Response:**
```json
{
  "new_game_day": {
    "day": 1,
    "story": "...",
    "npc_dialogues": [...]
  },
  "pending_actions": [
    {"id": "action_1", "text": "..."}
  ],
  "messages_from_gm": [],
  "npc_messages": []
}
```

### Player Actions

#### Submit Player Action
**POST** `/game/actions`

Submit a player's action selection.

**Request Body:**
```json
{
  "player_id": 281412419,
  "day": 1,
  "action_id": "action_1",
  "choice": "selected"
}
```

**Response:**
```json
{
  "status": "accepted",
  "action": {...}
}
```

#### Get Player Actions
**GET** `/game/actions/{player_id}/{day}`

Get player actions for a specific day.

**Path Parameters:**
- `player_id` - The player ID
- `day` - The day number

**Response:**
```json
{
  "actions": [...]
}
```

### Messages

#### Submit Game Message
**POST** `/game/messages`

Submit a message to the game master and get a response.

**Request Body:**
```json
{
  "player_id": 281412419,
  "message": "Hello, can you help me?",
  "message_type": "text"
}
```

**Response:**
```json
{
  "status": "processed",
  "response": "Game master response..."
}
```

#### Get Game Messages
**GET** `/game/messages/{player_id}`

Get a player's message history.

**Path Parameters:**
- `player_id` - The player ID

**Query Parameters:**
- `limit` - Optional, max number of messages (default: 10)

**Response:**
```json
{
  "messages": [
    {
      "id": 1,
      "player_id": 281412419,
      "message": "...",
      "message_type": "text",
      "timestamp": "2026-03-11T12:00:00"
    }
  ]
}
```

### Admin Endpoints

#### Generate Daily Episode
**POST** `/admin/generate-day`

Generate a new daily episode (called by game master scheduler).

**Request Body:**
```json
{
  "language": "en",
  "previous_actions": [],
  "team_assembly_status": {}
}
```

**Response:** Same as `/game/current-day`

#### Generate Personalized Comic
**POST** `/admin/generate-comic/{player_id}`

Generate a personalized comic for a player.

**Path Parameters:**
- `player_id` - The player ID

**Query Parameters:**
- `day` - Optional, specific day number (defaults to current day)

**Response:**
```json
{
  "player_id": 281412419,
  "day": 1,
  "comic_url": "http://...",
  "role": "Chief Engineer"
}
```

### Health & Status

#### Root Endpoint
**GET** `/`

**Response:**
```json
{
  "service": "AI Game Master API",
  "status": "running"
}
```

#### Health Check
**GET** `/health`

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2026-03-11T12:00:00"
}
```

## Error Responses

### 400 Bad Request
```json
{
  "detail": "Player already has a profile"
}
```

### 404 Not Found
```json
{
  "detail": "Player profile not found. Complete onboarding first."
}
```

### 422 Unprocessable Entity
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "player_id"],
      "msg": "Field required",
      "input": null
    }
  ]
}
```

## Data Models

### OnboardingQuestion
```json
{
  "id": 1,
  "text": "Question text",
  "options": [
    {"value": "option_value", "label": "Display label"}
  ]
}
```

### PlayerProfile
```json
{
  "player_id": 281412419,
  "role": "Chief Engineer",
  "role_description": "...",
  "personality_traits": ["trait1", "trait2"],
  "avatar_url": "http://...",
  "game_id": "default_game",
  "last_poll": "2026-03-11T12:00:00"
}
```

### GameDay
```json
{
  "day": 1,
  "story": "...",
  "npc_dialogues": [
    {"npc": "NPC Name", "dialogue": "..."}
  ],
  "player_actions": [
    {"id": "action_id", "text": "Action text"}
  ],
  "generated_content": {
    "image": "/path/to/image.jpg",
    "comic": "/path/to/comic.webp"
  }
}
```