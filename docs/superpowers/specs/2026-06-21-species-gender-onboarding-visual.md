# Species/Gender Onboarding with Cumulative Visuals

## Problem

Currently, species questions (SPECIES_QUESTIONS_DATA, 10 questions) and gender questions (GENDER_QUESTIONS_DATA, 4 questions) are appended sequentially — all species first, then all gender. This allows players to predict which answer pattern yields "human + male" (just press [1] every time). Additionally:

- Gender hybrid detection is not supported (only species has hybrid)
- No visual feedback during species/gender selection
- Answer option order is static and repeatable

## Goals

1. **Interleave** species and gender questions randomly so they alternate
2. **Shuffle** answer options within each question (different per session)
3. **Accumulate** species_tags and gender_tags across all answers (already works)
4. **Show images** for each answer option showing cumulative hybrid visual effect
5. **Generate LLM prompts** for each option image — creative but short
6. **Support hybrids for both species and gender**
7. **Display hybrid results** in `/profile` and final avatar

## Architecture

### 1. Question Interleaving (`prompts.py`)

Replace `build_species_gender_questions()` with `build_interleaved_species_gender_questions()`:

```
function build_interleaved_species_gender_questions(language):
    species = shuffle(SPECIES_QUESTIONS_DATA[language])
    gender = shuffle(GENDER_QUESTIONS_DATA[language])
    result = []
    i, j = 0, 0
    while i < len(species) or j < len(gender):
        if i < len(species):
            result.append(species[i]); i++
        if j < len(gender):
            result.append(gender[j]); j++
    return result
```

Each question's options are also shuffled with a per-session random seed stored in `onboarding_sessions.shuffle_seed`.

### 2. Session Seed (`database.py`)

New migration #26: `ALTER TABLE onboarding_sessions ADD COLUMN shuffle_seed INTEGER DEFAULT 0`. Set to `random.randint(0, 2**32)` on session creation in `create_onboarding_session()`.

Options are shuffled deterministically using `random.Random(seed).shuffle(options)`.

### 3. Gender Hybrid Support (`game_server.py`)

Modify `calculate_gender_from_answers()`:

```python
@staticmethod
def calculate_gender_from_answers(answers, questions=None):
    tag_counts = GameMasterAgent._count_tags_from_answers(
        answers, "gender_tags", questions
    )
    if not tag_counts:
        return {"primary": "", "secondary": "", "hybrid": False}
    
    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
    primary = sorted_tags[0][0]
    primary_count = sorted_tags[0][1]
    secondary = ""
    hybrid = False
    if len(sorted_tags) > 1 and sorted_tags[1][1] == primary_count:
        secondary = sorted_tags[1][0]
        hybrid = True
    
    return {"primary": primary, "secondary": secondary, "hybrid": hybrid}
```

Same algorithm as `calculate_species_from_answers()`.

### 4. ComfyUI Concurrency Semaphore (`image_generator.py`)

```python
COMFYUI_IMAGE_CONCURRENCY = int(os.getenv("COMFYUI_IMAGE_CONCURRENCY", "4"))
_image_semaphore = asyncio.Semaphore(COMFYUI_IMAGE_CONCURRENCY)
```

All `generate_*` methods acquire `_image_semaphore` before calling ComfyUI.

### 5. Option Image Generation (`main.py`)

New async function `generate_species_gender_option_images()`:

```python
async def generate_species_gender_option_images(
    game_id: str,
    question: OnboardingQuestion,
    accumulated_tags: Dict[str, int],  # species_tags or gender_tags accumulated so far
    language: str,
    player_id: int,
) -> List[Optional[str]]:
    """Generate one image per option showing cumulative visual effect."""
    
    # 1. Ask LLM to generate a creative but short prompt for each option
    game_master = create_game_master_agent(language=language)
    prompts = game_master.generate_species_option_prompts(
        question=question,
        accumulated_tags=accumulated_tags,
    )
    # prompts is a dict: option_value -> prompt string
    
    # 2. Generate images in parallel (bounded by semaphore)
    image_generator = create_image_generator()
    tasks = []
    for opt in question.options:
        prompt = prompts.get(opt["value"], "")
        if not prompt:
            continue
        tasks.append(
            image_generator.generate_image(
                prompt=prompt,
                filename_prefix=f"species_q_{game_id}_{player_id}_{question.id}_{opt['value']}",
                width=512,  # Smaller preview images
                height=512,
            )
        )
    
    urls = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 3. Attach URLs back to options
    for i, (opt, url_or_err) in enumerate(zip(question.options, urls)):
        if isinstance(url_or_err, str) and url_or_err:
            opt["image_url"] = url_or_err
    
    return question
```

### 6. LLM Prompt Generation (`game_server.py`)

New method `generate_species_option_prompts()`:

```json
{
    "type": "json_schema",
    "json_schema": {
        "name": "species_option_prompts",
        "strict": true,
        "schema": {
            "type": "object",
            "properties": {
                "prompts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "option_value": {"type": "string"},
                            "prompt": {
                                "type": "string",
                                "description": "Short creative image prompt in English for Diffusion Model"
                            }
                        },
                        "required": ["option_value", "prompt"],
                        "additionalProperties": false
                    }
                }
            },
            "required": ["prompts"],
            "additionalProperties": false
        }
    }
}
```

```
Input: question text, options with their species/gender tags, accumulated tags so far
Output: dict option_value -> short image prompt (~20-30 words)

Prompt template (English, for Stable Diffusion):
"A Starfleet officer whose form shows [accumulated species traits] 
 and [this option's species trait], standing on a starship bridge, 
 cinematic portrait, dramatic lighting, 4K quality."

Example for accumulated {human: 2, humanoid: 1} + current option "symbiotic":
"A Starfleet officer: distinctly human with subtle humanoid features, 
 but symbiotic tendrils weave through their uniform, merging with their form. 
 Cinematic portrait, bridge background, dramatic lighting, 4K."
```

### 7. Flow: Submit Answer → Next Question with Images

```
POST /onboarding/{session_id}/answer
1. Save answer, update accumulated_tags
2. If not completed:
   a. Get next question from session
   b. Calculate accumulated species_tags and gender_tags so far
   c. If next question is species type:
      - Call LLM to generate prompts for each option
      - Generate 6 images in parallel (bounded by semaphore)
   d. If next question is gender type:
      - Call LLM to generate prompts for each option  
      - Generate up to 8 images in parallel (bounded by semaphore)
   e. Attach image_urls to option dicts
   f. Return question with images
```

**Note:** HTTP timeout for the `/onboarding/{session_id}/answer` endpoint must be increased to at least 300s for bot.py's `api_request()` (currently defaults to 600s, which is sufficient).

### 8. Hybrid Display in Profile (`bot.py`, `language.py`)

Profile text changes:

```
**Вид:** Человек + Гуманоид (гибрид)
**Пол:** Мужской
```

If hybrid:

```
**Вид:** Гибрид: Энергетическая форма жизни + Кибернетическая
**Пол:** Гибрид: Мужской + Женский
```

In `PROFILE` messages, add support for hybrid display:

- `species`: if hybrid → "Гибрид: {primary} + {secondary}" else "{primary}"
- `gender`: if hybrid → "Гибрид: {primary} + {secondary}" else "{primary}"

### 9. Final Avatar with Hybrid Info

When `complete_onboarding()` generates the final avatar, pass both `species_result` (may be hybrid) and `gender_result` (may be hybrid) to the LLM. The avatar prompt should describe the combined being.

## Files Changed

| File | Changes |
|------|---------|
| `game-server/prompts.py` | New `build_interleaved_species_gender_questions()`, shuffled options |
| `game-server/main.py` | New `generate_species_gender_option_images()`, updated answer flow |
| `game-server/game_server.py` | New `generate_species_option_prompts()`, updated `calculate_gender_from_answers()` |
| `game-server/image_generator.py` | Add `COMFYUI_IMAGE_CONCURRENCY` semaphore |
| `game-server/database.py` | Add `shuffle_seed` column to `onboarding_sessions` |
| `telegram-bot/language.py` | Add hybrid display strings for species/gender in profile |
| `telegram-bot/bot.py` | Display hybrid info in `/profile` |

## Open Questions

- Image size for option previews: 512×512 vs 768×768? **Resolution:** 512×512 for speed; ComfyUI generates faster at lower resolution.
- LLM max tokens for option prompts: ~512 tokens (should fit 6-8 short English prompts)
- First question images: yes, show them even with zero accumulation (will show pure species/gender type without hybrid blend).

## Rejected Alternatives

- **Pre-generating all images at `/onboarding/start`**: ~92 images, too slow even with parallelism
- **Pre-generating only one image per species/gender type**: Not truly cumulative
- **No images during onboarding**: User explicitly chose the full-visual approach
