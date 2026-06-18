# CampaignWiki

A Python CLI that auto-builds and maintains an [Obsidian](https://obsidian.md) wiki for RPG campaigns. Feed it session transcripts, AI responses, or your own notes — it extracts every entity (characters, places, lore, items, events, quests, secrets) and writes or updates structured Markdown notes with wikilinks, frontmatter properties, and cross-references.

---

## Requirements

- Python 3.10+ (Anaconda recommended on Windows)
- An [Anthropic API key](https://console.anthropic.com)
- An Obsidian vault

Install dependencies:

```
pip install -r requirements.txt
```

---

## Setup

```
python wiki.py setup --vault "C:\path\to\your\vault" --api-key sk-ant-...
```

Config is saved to `~/.campaignwiki/config.json`. You only need to run setup once.

---

## Commands

### `extract` — Extract from any text

Runs entity extraction on any text using Claude. Accepts inline text, a file, or clipboard.

```
python wiki.py extract "Bregor revealed his father was a member of the Iron Council."

python wiki.py extract --file notes.txt

python wiki.py extract --clipboard

python wiki.py extract --context "GM notes about the Steelhammer Clan" --file steelhammer.txt
```

Options:
- `--file / -f` — read from a file
- `--clipboard / -c` — read from clipboard
- `--context` — hint for Claude about what the text is
- `--chunk-size` — max chars per API call (default 6000; auto-splits if exceeded)
- `--stub-threshold` — significance score below which new entities become stubs (default 3)
- `--prior-sessions N` — inject last N session summaries as context (default 3)
- `--dry-run` — preview without writing files

---

### `session` — Process a session transcript

Designed for voice-to-text output or written session notes. Cleans up OOC table talk, extracts all entities, and always creates a session summary note.

```
python wiki.py session transcript.txt
```

Options same as `extract`, plus:
- `--number N` — manually set session number on the session entity

Prior session context is injected automatically.

---

### `ingest` — Import pre-extracted JSON

Use this with the external AI workflow (see below). Takes a JSON file produced by an external AI and imports all entities.

```
python wiki.py ingest session7.json
```

Options:
- `--number N` — override session number on the session entity
- `--stub-threshold` — same as extract
- `--dry-run` — preview without writing

---

### `context` — Output prior session context

Prints the last N session summaries and the known entity list — paste this into an external AI before running the analyst prompt.

```
python wiki.py context

python wiki.py context --clipboard
```

Options:
- `--clipboard / -c` — copy to clipboard instead of printing
- `--prior-sessions N` — how many sessions to include (default 3)
- `--no-entities` — omit the entity list, just sessions

---

### `alias` — Resolve an unknown identity

When a placeholder entity ("Hooded Informant") is revealed to be a specific person, this command merges them: rewrites all `[[wikilinks]]` in the vault to point to the true name.

```
python wiki.py alias "Hooded Informant" "Lady Morrova"
```

Options:
- `--dry-run` — preview which files would be updated

---

### `distinct` — Prevent two entities from being merged

When two similarly-named entities keep getting conflated, this marks them as permanently distinct. Adds `no_dedup_with` to each note's frontmatter so the deduplication system skips them.

```
python wiki.py distinct "Steelhammer Clan" "Bregor Stonehammer"
```

Partial names work if unambiguous:

```
python wiki.py distinct "Steelhammer Clan" "Stonehammer"
```

---

### `dashboard` — Regenerate the campaign dashboard

Writes `_System/Dashboard.md` — a live overview of the campaign. Also runs automatically after every `extract`, `session`, and `ingest`.

```
python wiki.py dashboard
```

Dashboard sections:
- **Party** — PC table with race/class, status, player
- **Last Session** — summary, events, cliffhanger
- **Active Quests** — quests with status `active`
- **Notable Events** — event notes table
- **Unresolved Identities** — entities with `unknown-identity: true`
- **Contradicted Information** — entries marked `reliability: contradicted`
- **Unverified Rumors** — entries marked `reliability: rumored`
- **Key NPCs** — alive / deceased / other status
- **Factions** — table with leader and PC relations
- **Session History** — all sessions in reverse order

---

### `timeline` — Regenerate the campaign timeline

Writes `_System/Timeline.md` — sessions in chronological order, events sorted by `date_order`. Also runs automatically after every `extract`, `session`, and `ingest`.

```
python wiki.py timeline
```

To position an event on the timeline, add `date_order: N` to its frontmatter (lower = earlier). Events without `date_order` appear in an **Unplaced Events** section.

The `## Manual Entries` section at the bottom of the file is never overwritten — use it for era headings, turning points, or custom notes.

---

### `audit` — Vault health check

Scans the entire vault and reports issues:

```
python wiki.py audit
```

| # | Check |
|---|-------|
| 1 | **Broken wikilinks** — `[[links]]` pointing to non-existent notes |
| 2 | **Stubs** — placeholder notes flagged for expansion (sorted by significance) |
| 3 | **Missing status** — PCs/NPCs with no `status` field |
| 4 | **Isolated notes** — notes with no wikilinks (disconnected from graph) |
| 5 | **Unresolved identities** — `unknown-identity: true` placeholders |
| 6 | **Name mismatches** — frontmatter `name` doesn't match the filename |
| 7 | **Reliability flags** — contradicted facts and unconfirmed lore |

---

### `ls` — List vault entities

```
python wiki.py ls

python wiki.py ls --type npc

python wiki.py ls --unknown

python wiki.py ls --reliability rumored
```

Options:
- `--type` — filter by entity type (`npc`, `pc`, `place`, `lore`, `item`, `faction`, `quest`, `event`, `session`, `secret`)
- `--unknown` — show only `unknown-identity: true` entities
- `--reliability` — filter by `confirmed`, `rumored`, `contradicted`, or `unknown`

---

### `fixlinks` — Repair broken wikilinks

Scans all notes and reports or rewrites broken wikilinks.

```
python wiki.py fixlinks
```

---

### `reindex` — Rebuild the entity index

Rebuilds `_System/state/entity-index.json` from current vault files. Use if the index gets out of sync.

```
python wiki.py reindex
```

---

## Entity Types

| Type | Vault Folder | Description |
|------|-------------|-------------|
| `npc` | `10 Characters/NPCs` | Non-player characters |
| `pc` | `10 Characters/PCs` | Player characters |
| `faction` | `10 Characters/Factions` | Organizations, guilds, cults, armies |
| `place` | `20 Places` | Locations — cities, dungeons, regions, buildings |
| `lore` | `30 Lore` | History, religion, magic, politics, legends |
| `event` | `30 Lore/Events` | Specific named occurrences — battles, ceremonies, disasters |
| `quest` | `30 Lore/Quests` | Missions, hooks, plot threads |
| `item` | `40 Items` | Weapons, artifacts, significant objects |
| `session` | `50 Sessions` | Play session summaries |
| `secret` | `_System/Secrets` | DM-only hidden information |

---

## Key Features

### Smart Merge
Existing notes are updated by Claude rather than overwritten. New information is woven in, frontmatter properties are updated, and narrative sections like **Status & Condition** are added automatically. Merge output is capped at 16,000 tokens — sufficient for very long wiki entries.

### Deduplication (3 layers)
1. Exact filename match in the entity's type folder
2. Fuzzy name search — same-type threshold 0.82, cross-type threshold 0.92 (prevents related-but-distinct entities like a faction and its leader from collapsing)
3. Alias scan — catches identity reveals where an existing note lists the incoming name as an alias

Use `wiki.py distinct` to explicitly block two notes from ever being merged.

### Status Tracking
Every NPC and PC carries a `status` field (`alive` / `dead` / `undead` / `missing` / `incapacitated` / `unknown`). When status changes, frontmatter is updated and a **Status & Condition** body section records the narrative details.

### Reliability Tagging
Every entity has a `reliability` field (`confirmed` / `rumored` / `contradicted` / `unknown`) and a `source` field. Rumored and contradicted entries surface on the Dashboard and are flagged in `audit`.

### Unknown Identity Resolution
Entities with unknown true identities are tagged `unknown-identity: true`. When the true name is revealed, `wiki.py alias` rewrites every wikilink vault-wide in one step.

### Prior Session Context
The last 3 session summaries are automatically injected into every extraction prompt so Claude recognises recurring entities and tracks continuity across sessions.

### Auto-Split on Token Limit
If a text chunk is too large for a single API call, extraction recursively splits it (up to 3 levels = up to 8x reduction) without losing entities.

---

## External AI Workflow

Use any AI (Claude.ai, ChatGPT, Gemini) for deep analysis, then import the results:

**1. Get prior context:**
```
python wiki.py context --clipboard
```

**2. In your AI chat:** paste the context block first, then paste the appropriate analyst prompt, then paste your source text.

**3. Save the AI's JSON response** to a file, e.g. `session8.json`

**4. Import:**
```
python wiki.py ingest session8.json
```

### Analyst Prompts

| File | Use for |
|------|---------|
| `prompts/session-analyst.md` | Voice-to-text or written session transcripts |
| `prompts/lore-analyst.md` | GM notes, written lore, AI world-building responses, NPC backstories |

Both prompts produce JSON matching the ingest schema, with full instructions for status tracking, reliability tagging, identity reveals, and significance scoring.

---

## Frontmatter Reference

### All entity types
| Field | Description |
|-------|-------------|
| `reliability` | `confirmed` / `rumored` / `contradicted` / `unknown` |
| `source` | Who or what provided this information |
| `no_dedup_with` | List of names that should never be merged with this note |
| `unknown-identity` | `true` if this is a placeholder for an unidentified entity |

### Characters (`npc` / `pc`)
| Field | Values |
|-------|--------|
| `status` | `alive` / `dead` / `undead` / `missing` / `incapacitated` / `unknown` |

### Events (`event`)
| Field | Description |
|-------|-------------|
| `event_type` | `battle` / `siege` / `ceremony` / `disaster` / `journey` / `meeting` / `assassination` / `other` |
| `date_order` | Integer — lower = earlier on the timeline |

### Quests (`quest`)
| Field | Values |
|-------|--------|
| `status` | `active` / `completed` / `failed` / `rumored` / `available` |

---

## Obsidian Plugins (Recommended)

- **Dataview** — query frontmatter fields dynamically inside any note. For example: list all dead NPCs, all active quests, or all rumored lore entries.
- **Fantasy Calendar** — define a custom in-game calendar system and mark events visually. Pairs well with the `date` field on event notes.
- **Obsidian Timelines** — renders a visual timeline from tagged notes if you prefer a graphical view over `_System/Timeline.md`.
