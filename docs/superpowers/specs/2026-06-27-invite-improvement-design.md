# Invite Command Improvement

## Goal

Make `/invite` compelling: show mission image + mission text + crew avatars
so players are motivated to share.

## Design

Two modes depending on whether mission exists:

### A. Mission exists (after game started)

1. Fetch `GET /game/mission`, `GET /game/bridge-image`, `GET /game/team`
2. Send **photo** (bridge image) with caption = mission name + description +
   inline button → `t.me/share/url?text=Mission: {name}. {description}`
3. Send **album** (media group) with crew avatars (players + NPCs with `avatar_url`), max 10

### B. No mission yet (before game start)

1. Fetch `GET /content/splash-image` for a random game image
2. Send **photo** (splash image) with caption = game title +
   inline button → `t.me/share/url?text=Join the game «{title}»!`
3. No crew album (no mission means game hasn't started — crew may be incomplete)

### Share text (`t.me/share/url`)

- With mission: `«Mission: {name}» — {description[:120]}... Join the game «{title}»!`
- Without mission: current text, just `Join the game «{title}»!`

## Files to change

- `telegram-bot/bot.py` — `cmd_invite`, `_build_share_text`
- `telegram-bot/language.py` — new/missing invite strings

## Future: Telegraph.ph (saved for later)

- Create telegraph article with bridge image + mission text
- Share URL gets `og:image` preview in Telegram
- Requires Telegraph API integration
