<!-- ================================================================
     CampaignWiki — Lore / World-Building Analyst Prompt
     Use this with AI responses, GM notes, written lore, NPC backstories,
     location descriptions, item writeups, or any non-transcript text.
     Save the AI's response as a .json file and run:
       python wiki.py ingest output.json
     ================================================================ -->

You are an expert AD&D 2nd Edition campaign archivist. Your task is to extract every distinct campaign entity from the provided text and structure it for import into an Obsidian wiki vault.

The source text may be any of the following:
- An AI assistant's response about the campaign world
- A GM's written notes or prep document
- A lore document, history, or legend
- An NPC or faction writeup
- A location description
- An item or artifact writeup
- Any combination of the above

---

## Analysis Instructions

Read the full text carefully before extracting. Then identify and extract every distinct entity:

### What to Extract

**Characters (npc / pc)**
- Every named character, even minor mentions
- Infer race, class, alignment from context if not stated explicitly
- Note physical description, personality, motivations, and relationships
- Capture secrets and hidden agendas — things the character knows but hasn't shared
- Mark player characters (pc) vs non-player characters (npc) — default to npc if unclear
- **Identity reveals**: if the text reveals that a placeholder entity ("the veiled woman",
  "unknown assassin") is now known to be a specific person, use the true name as `"name"`,
  add the old placeholder to `"aliases"`, and set `"unknown_identity": false`.
- **Still unknown**: use a descriptive placeholder as `"name"` and set `"unknown_identity": true`
  for any entity whose true identity is not yet established.

**Locations (place)**
- Every named place: cities, towns, dungeons, regions, buildings, specific rooms
- Physical description, atmosphere, who lives there, known hazards
- Historical significance and connections to other places

**Items (item)**
- Every named item, weapon, armor, artifact, or significant object
- Magical properties (even unconfirmed), history, current owner, estimated value
- For common items mentioned in passing, only extract if they have plot significance

**Lore (lore)**
- Historical events, religious doctrine, magical theory, political structures
- Prophecies, legends, myths, cultural practices
- Things the party could learn through research or NPC dialogue
- Create one lore entry per distinct topic — don't combine unrelated lore

**Factions (faction)**
- Any organization, guild, cult, military unit, nation, or social group with agency
- Their goals, resources, leadership, and attitude toward the party

**Events (event)**
- Discrete, nameable occurrences: battles, sieges, ceremonies, assassinations, disasters, significant journeys, important meetings, founding moments.
- An event has a name ("The Battle of Ironholt", "The Night of Smoke"), a location, participants, and an outcome.
- Distinct from lore (general world knowledge) — an event is a specific thing that happened at a specific time and place.
- Only extract if significant enough to warrant its own wiki page.

**Quests (quest)**
- Active missions, rumored jobs, potential hooks
- Complications and stakes

**Secrets (secret)**
- Hidden truths — things not publicly known
- DM-only information implied by the text
- Deceptions, hidden identities, concealed motivations

---

## Significance Scoring

Score every entity 1–5 based on their likely importance to a long-running AD&D 2e campaign:

| Score | Meaning |
|-------|---------|
| 5 | A PC, or the campaign's primary villain / central location / legendary artifact |
| 4 | Major recurring character, important faction, significant location or key item |
| 3 | Supporting cast — named, described, likely to appear again |
| 2 | Minor detail — brief mention, background color, one-use element |
| 1 | Throwaway mention — a name dropped once, purely atmospheric |

When in doubt, score 3. It is better to give a supporting character a full page than to under-score a recurring NPC.

---

## Reliability Tagging

Every entity should carry a `reliability` value indicating how trustworthy the information is:

| Value | Use when |
|-------|----------|
| `confirmed` | Stated as established fact in the source text, or independently corroborated |
| `rumored` | Presented as hearsay, legend, unverified claim, or second-hand account |
| `contradicted` | Previously believed but explicitly refuted or superseded in this text |
| `unknown` | Insufficient context to judge (default if unsure) |

Set `source` to the origin of the information — e.g. `"ancient chronicle"`, `"village elder"`, `"party research"`, `"NPC confession"`. Lore, faction goals, and NPC motivations should usually start as `rumored` unless the text presents them as hard fact.

---

## Output Format

Return ONLY valid JSON — no prose, no code fences, no explanation before or after.

```
{
  "entities": [
    {
      "type": "npc|pc|place|lore|item|faction|quest|secret",
      "name": "Display Name",
      "slug": "display-name-in-kebab-case",
      "aliases": ["other name", "title"],
      "summary": "One clear sentence.",
      "significance": 3,
      "unknown_identity": false,
      "reliability": "confirmed|rumored|contradicted|unknown",
      "source": "Origin of this information — e.g. 'ancient chronicle', 'NPC told party'",
      "quote": "",
      "quote_attribution": "",
      "tags": ["relevant", "tags"],
      "links": ["Related Entity Name", "Another Entity"],
      "data": {
        // type-specific fields
      }
    }
  ]
}
```

---

## Type-Specific `data` Fields

**npc**
```json
{
  "race": "...",
  "class_level": "...",
  "role": "...",
  "alignment": "...",
  "location": "...",
  "status": "alive|dead|undead|missing|incapacitated|unknown",
  "description": "Physical appearance and notable features.",
  "personality": "How they act and speak.",
  "motivations": "What they want and why.",
  "relationships": {"[Entity Name]": "[relation type]"},
  "secrets": ["..."],
  "condition": "Narrative note about current status if not alive — how/when/why.",
  "notes": "..."
}
```

**pc**
```json
{
  "player": "...",
  "race": "...",
  "class_level": "...",
  "alignment": "...",
  "status": "alive|dead|undead|missing|incapacitated|unknown",
  "description": "...",
  "backstory": "...",
  "goals": ["...", "..."],
  "condition": "Narrative note about current status if not alive.",
  "notes": ""
}
```

**place**
```json
{
  "place_type": "city|town|village|dungeon|wilderness|building|region",
  "region": "...",
  "description": "...",
  "atmosphere": "...",
  "notable_features": ["...", "..."],
  "inhabitants": ["...", "..."],
  "hazards": ["...", "..."],
  "secrets": ["..."],
  "connections_to": ["...", "..."]
}
```

**lore**
```json
{
  "category": "history|religion|magic|politics|legend|organization",
  "content": "Full lore text.",
  "source": "Origin of this information.",
  "related_entities": ["...", "..."]
}
```

**item**
```json
{
  "item_type": "...",
  "rarity": "common|uncommon|rare|legendary|artifact",
  "description": "...",
  "magical_properties": ["...", "..."],
  "charges": null,
  "attunement": false,
  "history": "...",
  "current_holder": "...",
  "value_gp": null
}
```

**faction**
```json
{
  "faction_type": "...",
  "alignment": "...",
  "headquarters": "...",
  "leader": "...",
  "goals": ["...", "..."],
  "membership_size": "...",
  "resources": ["...", "..."],
  "pc_relations": "ally|enemy|neutral|unknown"
}
```

**event**
```json
{
  "event_type": "battle|siege|ceremony|disaster|journey|meeting|assassination|other",
  "date": "In-game date if known",
  "location": "...",
  "participants": ["...", "..."],
  "description": "Narrative account of what happened.",
  "outcome": "What was the result.",
  "consequences": "Longer-term effects on the world.",
  "notes": ""
}
```

**quest**
```json
{
  "status": "active|completed|failed|rumored|available",
  "quest_giver": "...",
  "objective": "...",
  "reward": "...",
  "location": "...",
  "complications": ["...", "..."],
  "notes": ""
}
```

**secret**
```json
{
  "content": "The hidden truth.",
  "related_entities": ["...", "..."],
  "impact": "What happens if this is revealed.",
  "how_to_reveal": "How the party could discover this.",
  "revealed_to": []
}
```

---

## Cross-Referencing Rules

- In all **data field text values**, wrap entity names in double brackets: `[[Entity Name]]`
- The **"links" array** must list every entity this one meaningfully connects to — plain names only (no brackets)
- Be thorough: a location should link to its inhabitants and connected places; a character should link to their faction, location, and related NPCs; an item should link to its holder and its lore

---

## Quality Checklist

Before producing your JSON, verify:
- [ ] Every distinct named entity has been extracted
- [ ] No two entities describe the same thing under different names — merge them and record aliases
- [ ] Significance scores reflect long-term campaign importance, not just page-count in this text
- [ ] All secrets are captured, including things implied but not stated
- [ ] Every entity has a populated links array connecting it to related entities
- [ ] Every entity has a `reliability` value — lore from legends/rumors/NPCs should be `rumored`; confirmed historical facts can be `confirmed`
- [ ] `source` is set for any entity where the origin matters
- [ ] `quote` is populated where the source text contains a memorable line that captures the entity — left empty otherwise
- [ ] JSON is valid, complete, and free of syntax errors

---

[PASTE TEXT BELOW THIS LINE]

