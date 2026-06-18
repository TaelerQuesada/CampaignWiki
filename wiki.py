#!/usr/bin/env python3
"""
CampaignWiki — Auto-documentation pipeline for tabletop RPG campaigns.

Extracts entities (NPCs, places, lore, items, secrets, etc.) from AI responses
and session transcripts into Obsidian markdown notes with cross-references.

Vault structure expected (matches your existing sample-vault):
  10 Characters/NPCs/
  10 Characters/PCs/
  10 Characters/Factions/
  20 Places/
  30 Lore/
  30 Lore/Quests/
  40 Items/
  50 Sessions/
  _System/state/entity-index.json

Commands:
  wiki.py setup --vault PATH --api-key KEY
  wiki.py extract "text"               # from inline text
  wiki.py extract --file response.txt  # from file
  wiki.py extract --clipboard          # from clipboard
  wiki.py session transcript.txt --number 5
  wiki.py ls [--type npc]
  wiki.py reindex
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import anthropic
import click
import yaml

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".campaignwiki" / "config.json"
DEFAULT_VAULT = Path.home() / "Documents" / "CampaignWiki"

# ── Vault folder map (matches existing sample-vault structure) ────────────────

ENTITY_FOLDERS = {
    "npc":     "10 Characters/NPCs",
    "pc":      "10 Characters/PCs",
    "faction": "10 Characters/Factions",
    "place":   "20 Places",
    "lore":    "30 Lore",
    "event":   "30 Lore/Events",
    "quest":   "30 Lore/Quests",
    "item":    "40 Items",
    "session": "50 Sessions",
    "secret":  "_System/Secrets",
}

INDEX_PATH = "_System/state/entity-index.json"

# ── Prompts ───────────────────────────────────────────────────────────────────

EXTRACT_SYSTEM = """\
You are a campaign wiki assistant for a tabletop RPG campaign (AD&D 2nd Edition).
Extract ALL notable entities from the provided text and return structured JSON.

Entity types and their data fields:

npc     → race, class_level, role, alignment, location,
           status(alive/dead/undead/missing/incapacitated/unknown),
           description, personality, motivations, relationships(dict name→relation),
           secrets(list), condition, notes
pc      → player, race, class_level, alignment,
           status(alive/dead/undead/missing/incapacitated/unknown),
           description, backstory, goals(list), condition, notes
place   → place_type(city/town/village/dungeon/wilderness/building/region), region,
           description, atmosphere, notable_features(list), inhabitants(list),
           hazards(list), secrets(list), connections_to(list)
lore    → category(history/religion/magic/politics/legend/organization),
           content, source, related_entities(list)
event   → event_type(battle/siege/ceremony/disaster/journey/meeting/assassination/other),
           date, location, participants(list), description,
           outcome, consequences, notes
item    → item_type, rarity(common/uncommon/rare/legendary/artifact), description,
           magical_properties(list), charges, attunement(bool),
           history, current_holder, value_gp
faction → faction_type, alignment, headquarters, leader, goals(list),
           membership_size, resources(list), pc_relations(ally/enemy/neutral/unknown)
quest   → status(active/completed/failed/rumored/available), quest_giver,
           objective, reward, location, complications(list), notes
session → session_number(int), in_game_date, real_date, location,
           participants(list), summary, events(list), loot_found(list),
           xp_awarded(int), cliffhanger
secret  → content, related_entities(list), impact, how_to_reveal, revealed_to(list)

Also score each entity's significance on a 1–5 scale:
  5 — central to this text (PC, major NPC, key location, primary plot item)
  4 — clearly important, named and characterised, multiple interactions
  3 — named, present, a detail or two (will likely recur)
  2 — brief appearance, minimal detail, may not matter again
  1 — single throwaway mention, unnamed or barely described

STATUS CHANGES — track these for every character (NPC and PC):
  Always set "status" — never leave it empty. Default to "alive" unless the text
  says otherwise. Valid values:
    alive         — currently living
    dead          — confirmed dead
    undead        — killed and returned as undead (vampire, zombie, etc.)
    missing       — whereabouts unknown, fled, vanished
    incapacitated — unconscious, imprisoned, polymorphed, petrified, etc.
    unknown       — fate genuinely unclear

  When a status change occurs this session, ALSO populate the "condition" field
  with a narrative description: who died, how, when (session number if known),
  who was responsible, and any campaign implications.
  Example: "Slain by Lord Vayne in Session 4 during the siege of Ironholt. Body
  taken by cultists — likely to be raised. Party did not recover the remains."

  When a status changes back (resurrection, escape, recovery), update "status"
  and note the reversal in "condition".

RELIABILITY — assess how well-established each entity's information is:
  confirmed    — party directly witnessed or verified firsthand
  rumored      — heard from an NPC, read in a document, or secondhand
  contradicted — was believed true but has been proven or revealed to be false
  unknown      — not enough information to assess (use as default)

  Rules for assigning reliability:
  - Party saw or did it themselves → confirmed
  - An NPC told the party → rumored (NPCs can lie or be wrong)
  - Found written in a document → rumored (documents can be falsified)
  - The party verified a rumor → confirmed
  - Something proven wrong this session → contradicted
  - Unclear or mixed sources → unknown

  Set "source" to describe where the information came from — who said it,
  what document it was found in, or how the party learned it.
  Example sources: "Innkeeper at The Broken Wheel", "Ancient scroll in crypt",
  "Lord Vayne told the party directly", "Party witnessed firsthand"

  For lore entries, reliability and source are especially important since lore
  is almost always learned secondhand. For NPCs, reliability reflects how much
  of what the party knows about them is verified vs. assumed.

IDENTITY REVEALS — important:
  If this text reveals that a previously unknown or placeholder entity
  (e.g. "the skeletal wizard", "the masked figure", "unknown assassin")
  is now known to be a specific named individual, record it like this:
    - Use the true revealed name as "name"
    - Put all placeholder names / epithets in "aliases"
    - Set "unknown_identity": false
    - Note the revelation in "summary"
  If an entity's true identity is still unknown, use a descriptive
  placeholder name (e.g. "Hooded Informant") and set "unknown_identity": true.

Return this JSON structure only — no prose, no code fences:
{
  "entities": [
    {
      "type": "...",
      "name": "...",
      "slug": "kebab-case-no-special-chars",
      "aliases": [],
      "summary": "one sentence",
      "significance": 3,
      "unknown_identity": false,
      "reliability": "unknown",
      "source": "",
      "quote": "",
      "quote_attribution": "",
      "tags": [],
      "links": ["name of a related entity", ...],
      "data": { ...type-specific fields... }
    }
  ]
}

QUOTES — optional, never forced:
  If the source text contains a line that captures this entity particularly well —
  a character's memorable words, a piece of in-world lore, a vivid description —
  populate "quote" with that line (verbatim or lightly cleaned up).
  Set "quote_attribution" to who said or wrote it if known (e.g. "Drogrum Steelhammer",
  "inscription above the gate", "The Book of Embers").
  Leave both fields empty if nothing fits naturally. Do not invent quotes.

Be thorough — extract everything, even minor mentions. Use significance to flag importance.
Inside data field values, reference other entities as [[Entity Name]] for Obsidian wikilinks.\
"""

SESSION_SYSTEM = """\
You are a campaign wiki assistant processing a raw session transcript.
The transcript may be voice-to-text with recognition errors, crosstalk, and
out-of-character (OOC) table talk mixed into the in-game narrative.

Your job:
1. Separate in-character events from OOC table talk (rules discussions, jokes, side conversations)
2. Extract ALL notable in-game entities: named NPCs, locations, items, lore, secrets, plot threads
3. Always create a "session" type entity that summarizes the whole session
4. Cross-reference entities with each other using their names in the links list
5. If PRIOR SESSION CONTEXT is provided above the transcript, use it to:
   - Recognise entities from earlier sessions and use their exact canonical names
   - Connect new events to established storylines, relationships, and plot threads
   - Identify status changes (e.g. an NPC seen alive in a prior session now appearing dead)
   - Resolve voice-to-text ambiguities by matching against known entity names
   - Understand references like "the wizard we met last time" or "the thing from the tower"

Voice-to-text artifacts to handle:
- Misheard words — use campaign context and prior session data to infer correct names
- Incomplete or run-on sentences — extract the meaning
- Rules discussions — skip unless the rule outcome matters to the story
- OOC jokes and side chat — skip

Focus on:
- Named NPCs: what they said, did, revealed, wanted
- Locations: first appearances, new details about known places
- Items: found, bought, lost, identified, used
- Lore: anything the party learned about the world
- Secrets: things revealed to players (or still hidden in the world)
- Combat: outcomes, casualties, notable moments
- Unresolved hooks and cliffhangers

""" + EXTRACT_SYSTEM

MERGE_SYSTEM = """\
You are a campaign wiki editor merging new information into an existing Obsidian note.

Rules — follow strictly:
1. NEVER delete existing content — only add or update
2. Add new details not already present
3. For contradictions in descriptive or narrative content: keep the old info and
   append the update as "(Session N: [new information])"
4. For factual state fields — UPDATE them directly rather than appending. These
   reflect current truth and must stay accurate:
     - status (alive/dead/undead/missing/incapacitated/unknown)
     - location, current_holder
   Preserve all other YAML frontmatter fields and structure exactly.
5. When a character's status changes (death, resurrection, capture, disappearance,
   escape):
     a. Update the `status` frontmatter field to the new value
     b. Add or update a ## Status & Condition section in the note body with a
        narrative description: what happened, when (session if known), who was
        involved, and what it means for the campaign going forward
6. Update `reliability` and `source` frontmatter when new information changes the
   assessment — these are factual state fields like `status`:
     rumored/unknown → confirmed  when the party verifies the information firsthand
     any value       → contradicted  when information is proven false
   When reliability changes, add a note in the body explaining what changed it.
7. Replace bare entity names with [[wikilinks]] where they appear unlinked
8. Keep sections organised and readable — merge related bullet points, don't duplicate
9. QUOTES — if the note has no opening quote block and the new content contains a
   genuinely memorable line (character dialogue, in-world inscription, vivid description),
   add one directly after the # heading in this format:
     > *"The quote text."*
     > — Attribution (if known)
   Only add when it fits naturally. Never invent one. If a quote already exists, preserve it.
10. Return ONLY the complete updated markdown — no code fences, no preamble\
"""

FILL_SYSTEM = """\
You are a campaign wiki assistant. An entity is referenced across existing wiki notes
but has no page of its own. You are given every snippet of text that mentions it,
labelled with the note it came from.

Your task: produce a single JSON entity document capturing everything that can be
INFERRED from the references. Strict rules:
- Do NOT invent details not supported by the references
- Infer the entity type from context:
    npc     — a person, creature, or named individual
    pc      — a player character
    place   — a location, building, region, dungeon
    faction — an organisation, group, clan, guild, cult
    lore    — a piece of history, legend, religion, or world knowledge
    event   — a specific named occurrence (battle, ceremony, disaster)
    item    — an object, weapon, artifact
    quest   — a mission or plot thread
    secret  — hidden information the players may not know
- Set significance 1–5 based on how often and how meaningfully it is referenced
- Set reliability based on how the references treat the information
- Set unknown_identity: true if the true nature of the entity is unclear
- Populate quote only if a reference contains a memorable line — never invent one
- Leave data fields empty rather than guessing

Return ONLY this JSON — no prose, no code fences:
{
  "entities": [
    {
      "type": "...",
      "name": "...",
      "slug": "kebab-case",
      "aliases": [],
      "summary": "one sentence — what is known from references",
      "significance": 3,
      "unknown_identity": false,
      "reliability": "unknown",
      "source": "inferred from vault references",
      "quote": "",
      "quote_attribution": "",
      "tags": [],
      "links": [],
      "data": {}
    }
  ]
}\
"""

# ── Config helpers ────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}

def save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def get_vault(cfg: dict) -> Path:
    vault = Path(cfg.get("vault_path", DEFAULT_VAULT))
    vault.mkdir(parents=True, exist_ok=True)
    return vault

def get_client(cfg: dict) -> anthropic.Anthropic:
    key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        click.echo(
            "Error: No API key found.\n"
            "  Run: python wiki.py setup --api-key YOUR_KEY\n"
            "  Or set environment variable: ANTHROPIC_API_KEY",
            err=True,
        )
        sys.exit(1)
    return anthropic.Anthropic(api_key=key)

# ── Entity index ──────────────────────────────────────────────────────────────

def load_index(vault: Path) -> dict:
    idx_file = vault / INDEX_PATH
    if idx_file.exists():
        return json.loads(idx_file.read_text(encoding="utf-8"))
    return {"GeneratedAt": "", "Entities": []}

def save_index(vault: Path, index: dict):
    idx_file = vault / INDEX_PATH
    idx_file.parent.mkdir(parents=True, exist_ok=True)
    index["GeneratedAt"] = datetime.now(timezone.utc).isoformat()
    idx_file.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

def find_in_index(index: dict, name: str, slug: str) -> dict | None:
    entity_id_suffix = f"-{slug}"
    name_lower = name.lower()
    slug_lower = slug.lower()
    for entry in index["Entities"]:
        if entry["Id"].endswith(entity_id_suffix):
            return entry
        if entry["Name"].lower() == name_lower:
            return entry
        if slug_lower in [a.lower() for a in entry.get("Aliases", [])]:
            return entry
    return None

def upsert_index(index: dict, etype: str, name: str, slug: str,
                  aliases: list, rel_path: str, references: list):
    entity_id = f"{etype}-{slug}"
    existing = find_in_index(index, name, slug)
    all_aliases = list(set([name] + aliases + (existing.get("Aliases", []) if existing else [])))
    entry = {
        "Id": entity_id,
        "Type": {"Value": etype},
        "Name": name,
        "Aliases": all_aliases,
        "RelativePath": rel_path,
        "References": references,
    }
    if existing:
        index["Entities"] = [e for e in index["Entities"] if e["Id"] != existing["Id"]]
    index["Entities"].append(entry)

# ── Utility ───────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")

def safe_filename(name: str) -> str:
    """Keep display name capitalisation but strip chars Windows/Obsidian won't accept."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    s = re.sub(r"-+", "-", s).strip("-. ")
    return s or "unnamed"

FUZZY_THRESHOLD       = 0.82   # same-type fuzzy match (allows spelling variants)
CROSS_TYPE_THRESHOLD  = 0.92   # cross-type match — must be near-identical to deduplicate

def _name_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def find_existing_anywhere(
    vault: Path, name: str, slug: str, own_type: str = ""
) -> tuple[Path, str] | None:
    """Search every entity folder for a name match, fuzzy across spelling variants.

    Uses FUZZY_THRESHOLD for same-type matches and the stricter CROSS_TYPE_THRESHOLD
    for cross-type matches, preventing related-but-distinct entities (e.g. a faction
    and its leader) from being collapsed into each other.
    """
    name_lower = name.lower()
    slug_lower = slug.lower()
    best: tuple[float, Path, str] | None = None

    for etype, folder_rel in ENTITY_FOLDERS.items():
        folder = vault / folder_rel
        if not folder.exists():
            continue
        threshold = FUZZY_THRESHOLD if (not own_type or etype == own_type) else CROSS_TYPE_THRESHOLD
        for f in folder.glob("*.md"):
            stem = f.stem.lower()
            # Exact hit — return immediately
            if stem in (name_lower, slug_lower):
                return f, etype
            score = max(
                _name_sim(name_lower, stem),
                _name_sim(slug_lower, stem.replace("-", " ")),
            )
            if score >= threshold and (best is None or score > best[0]):
                best = (score, f, etype)

    if best:
        return best[1], best[2]
    return None

def _is_excluded_from_dedup(existing_path: Path, incoming_name: str) -> bool:
    """Return True if the existing note's no_dedup_with list contains incoming_name."""
    try:
        content  = existing_path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not fm_match:
            return False
        fm = yaml.safe_load(fm_match.group(1)) or {}
        exclusions = fm.get("no_dedup_with", [])
        if isinstance(exclusions, str):
            exclusions = [exclusions]
        name_lower = incoming_name.lower()
        return any(e.lower() == name_lower for e in exclusions)
    except Exception:
        return False


def find_by_alias(vault: Path, name: str, slug: str) -> tuple[Path, str] | None:
    """Scan frontmatter aliases in every note — catches identity reveals where the
    incoming entity's name matches an alias recorded on an existing note."""
    name_lower = name.lower()
    slug_lower = slug.lower()
    for etype, folder_rel in ENTITY_FOLDERS.items():
        folder = vault / folder_rel
        if not folder.exists():
            continue
        for f in folder.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                if not fm_match:
                    continue
                fm = yaml.safe_load(fm_match.group(1)) or {}
                for alias in fm.get("aliases", []):
                    if alias.lower() in (name_lower, slug_lower):
                        return f, etype
            except Exception:
                continue
    return None


def _rewrite_wikilinks(content: str, old_name: str, new_name: str) -> str:
    """Replace [[old_name]] and [[old_name|display]] with [[new_name]] variants."""
    pattern = re.compile(r"\[\[" + re.escape(old_name) + r"(\|[^\]]*)?\]\]", re.IGNORECASE)
    def _sub(m: re.Match) -> str:
        display = m.group(1) or ""
        return f"[[{new_name}{display}]]"
    return pattern.sub(_sub, content)


def _patch_frontmatter(md: str, patches: dict) -> str:
    """Apply patches to YAML frontmatter. 'aliases' values are appended, not replaced."""
    fm_match = re.match(r"^---\n(.*?)\n---\n", md, re.DOTALL)
    if not fm_match:
        return md
    try:
        fm = yaml.safe_load(fm_match.group(1)) or {}
    except yaml.YAMLError:
        return md
    for k, v in patches.items():
        if v is None:
            continue
        if k == "aliases":
            new_aliases = v if isinstance(v, list) else [v]
            existing = fm.get("aliases", [])
            fm["aliases"] = list(dict.fromkeys(existing + new_aliases))
        else:
            fm[k] = v
    new_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{new_yaml}---\n" + md[fm_match.end():]


def build_known_entities_context(index: dict) -> str:
    """Return a compact list of known entities to prepend to extraction prompts."""
    entities = index.get("Entities", [])
    if not entities:
        return ""
    lines = ["Known campaign entities — use these exact canonical names and types:"]
    for e in sorted(entities, key=lambda x: x["Name"]):
        etype = e.get("Type", {}).get("Value", "?")
        aliases = [a for a in e.get("Aliases", []) if a.lower() != e["Name"].lower()]
        alias_str = f"  (also known as: {', '.join(aliases)})" if aliases else ""
        lines.append(f"- {e['Name']} [{etype}]{alias_str}")
    return "\n".join(lines)

def _session_sort_key(num) -> float:
    """Return a numeric sort key for a session_number value.
    Handles ints, plain strings ('4'), and range strings ('4-6') by taking
    the first number. Returns inf for None or unparseable values."""
    if num is None:
        return float("inf")
    s = str(num).strip()
    import re as _re
    m = _re.search(r"\d+", s)
    return float(m.group()) if m else float("inf")


def _fm_value(val):
    """Convert frontmatter values with multiple wikilinks into a YAML list.

    Obsidian only resolves [[links]] in property fields when each link is its
    own list item. A string like "[[A]], [[B]]" renders as unclickable plain
    text; a list ["[[A]]", "[[B]]"] renders as two clickable link pills.
    """
    if not isinstance(val, str):
        return val
    links = re.findall(r"\[\[[^\]]+\]\]", val)
    if len(links) >= 2:
        return links     # let PyYAML serialise as a block list
    return val           # scalar — single link or plain text, leave alone


def _extract_section(body: str, section_name: str, max_chars: int = 1200) -> str:
    """Pull the text content of a ## Section from a markdown body, capped at max_chars."""
    pattern = re.compile(
        r"^## " + re.escape(section_name) + r"\s*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(body)
    if not m:
        return ""
    text = m.group(1).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n  [... truncated]"
    return text


def build_session_context(vault: Path, n: int = 3) -> str:
    """Return a formatted block of the N most recent session summaries for use as
    prior-context in extraction prompts."""
    folder = vault / ENTITY_FOLDERS["session"]
    if not folder.exists():
        return ""

    sessions: list[dict] = []
    for f in folder.glob("*.md"):
        try:
            content  = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1)) or {}
            num = fm.get("session_number")
            # sort numbered sessions by number; unnumbered sessions by mtime
            sort_key = (0, _session_sort_key(num)) if num is not None else (1, float(f.stat().st_mtime))
            body = content[fm_match.end():]
            sessions.append({
                "number":     num,
                "sort_key":   sort_key,
                "name":       fm.get("name", f.stem),
                "location":   fm.get("location", ""),
                "summary":    _extract_section(body, "Summary"),
                "events":     _extract_section(body, "Events"),
                "cliffhanger":_extract_section(body, "Cliffhanger"),
            })
        except Exception:
            continue

    if not sessions:
        return ""

    sessions.sort(key=lambda x: x["sort_key"])
    recent = sessions[-n:]

    lines = [
        "PRIOR SESSION CONTEXT",
        "Use these summaries to recognise recurring entities, resolve name ambiguities,",
        "and connect new events to established storylines.",
        "",
    ]
    for s in recent:
        if s["number"] is not None:
            header = f"Session {s['number']}"
        else:
            header = s["name"]
        if s["location"]:
            loc = s["location"]
            # location may be a list (multi-location sessions)
            if isinstance(loc, list):
                loc = loc[0] if loc else ""
            if loc:
                header += f" — {loc}"
        lines.append(f"[{header}]")
        if s["summary"]:
            lines.append(f"Summary: {s['summary']}")
        if s["events"]:
            lines.append(f"Events:\n{s['events']}")
        if s["cliffhanger"]:
            lines.append(f"Cliffhanger: {s['cliffhanger']}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _read_folder_entities(folder: Path) -> list[dict]:
    """Read all .md files in a folder, returning parsed frontmatter + body for each."""
    if not folder.exists():
        return []
    results = []
    for f in sorted(folder.glob("*.md")):
        try:
            content  = f.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            if not fm_match:
                continue
            fm = yaml.safe_load(fm_match.group(1)) or {}
            results.append({
                "name": fm.get("name", f.stem),
                "path": f,
                "fm":   fm,
                "body": content[fm_match.end():],
            })
        except Exception:
            continue
    return results


def _dash_link(val) -> str:
    """Format a frontmatter value as a single clean wikilink for the dashboard.

    Handles the three messy cases that arise from _fm_value storage:
      - List of wikilink strings  → use first item, strip brackets, re-wrap
      - String already containing [[...]]  → return as-is (no double-wrap)
      - Plain name string  → wrap in [[...]]
    Returns "" for empty/None values.
    """
    if not val:
        return ""
    if isinstance(val, list):
        val = val[0] if val else ""
    if not val:
        return ""
    val = str(val).strip()
    if not val:
        return ""
    # Already a bare wikilink — return as-is
    if val.startswith("[[") and val.endswith("]]"):
        return val
    # Contains wikilinks inside prose — return as-is to avoid double-wrapping
    if "[[" in val:
        return val
    # Plain name — wrap
    return f"[[{val}]]"


def _dash_plain(val) -> str:
    """Extract a plain-text location/name for use in headings (no wikilinks)."""
    if not val:
        return ""
    if isinstance(val, list):
        val = val[0] if val else ""
    if not val:
        return ""
    val = str(val).strip()
    # Strip surrounding [[ ]]
    if val.startswith("[[") and val.endswith("]]"):
        val = val[2:-2]
    # Strip any remaining brackets
    val = val.replace("[[", "").replace("]]", "")
    return val.split("|")[-1] if "|" in val else val


def generate_dashboard(vault: Path, index: dict) -> Path:
    """Regenerate _System/Dashboard.md from current vault state. No API calls."""

    out_path = vault / "_System" / "Dashboard.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    L: list[str] = []

    L += [
        "---",
        "type: dashboard",
        f'generated: "{now_str}"',
        "---",
        "",
        "# Campaign Dashboard",
        f"*Auto-generated · {now_str}*",
        "",
    ]

    # ── Party ────────────────────────────────────────────────────────────────
    pcs = _read_folder_entities(vault / ENTITY_FOLDERS["pc"])
    if pcs:
        L += ["## Party", "",
              "| Character | Race & Class | Status | Player |",
              "|-----------|-------------|:------:|--------|"]
        for pc in sorted(pcs, key=lambda x: x["name"]):
            fm     = pc["fm"]
            rc     = f"{fm.get('race', '')} {fm.get('class_level', '')}".strip()
            status = fm.get("status", "alive")
            player = fm.get("player", "")
            L.append(f"| [[{pc['name']}]] | {rc} | {status} | {player} |")
        L.append("")

    # ── Last Session ─────────────────────────────────────────────────────────
    all_sessions = [
        s for s in _read_folder_entities(vault / ENTITY_FOLDERS["session"])
        if s["fm"].get("session_number") is not None
    ]
    if all_sessions:
        all_sessions.sort(key=lambda x: _session_sort_key(x["fm"]["session_number"]))
        last = all_sessions[-1]
        fm   = last["fm"]
        num  = fm.get("session_number", "?")
        loc    = _dash_plain(fm.get("location", ""))
        header = f"Session {num}" + (f" — {loc}" if loc else "")
        summary    = _extract_section(last["body"], "Summary",    max_chars=600)
        events     = _extract_section(last["body"], "Events",     max_chars=1000)
        cliffhanger = _extract_section(last["body"], "Cliffhanger", max_chars=400)
        L += [f"## Last Session — [[{last['name']}|{header}]]", ""]
        if summary:
            L += [f"> {summary.replace(chr(10), ' ')}", ""]
        if events:
            L += ["**Events:**", "", events, ""]
        if cliffhanger:
            L += [f"**Cliffhanger:** {cliffhanger.strip()}", ""]

    # ── Active Quests ────────────────────────────────────────────────────────
    active_quests = [
        q for q in _read_folder_entities(vault / ENTITY_FOLDERS["quest"])
        if q["fm"].get("status") == "active"
    ]
    if active_quests:
        L += ["## Active Quests", ""]
        for q in sorted(active_quests, key=lambda x: x["name"]):
            fm    = q["fm"]
            parts = [f"**[[{q['name']}]]**"]
            if fm.get("quest_giver"):
                giver = _dash_link(fm["quest_giver"])
                parts.append(f"given by {giver}" if giver else "")
            if fm.get("location"):
                qloc = _dash_link(fm["location"])
                parts.append(f"at {qloc}" if qloc else "")
            parts = [p for p in parts if p]
            L.append("- " + " · ".join(parts))
        L.append("")

    # ── Notable Events ───────────────────────────────────────────────────────
    events = [e for e in _read_folder_entities(vault / ENTITY_FOLDERS["event"])
              if not e["fm"].get("stub")]
    if events:
        L += ["## Notable Events", "",
              "| Event | Type | Location |",
              "|-------|------|---------|"]
        for ev in sorted(events, key=lambda x: x["name"]):
            fm  = ev["fm"]
            loc = _dash_link(fm.get("location", ""))
            L.append(f"| [[{ev['name']}]] | {fm.get('event_type', '')} | {loc} |")
        L.append("")

    # ── Unresolved Identities ────────────────────────────────────────────────
    unknowns: list[tuple[str, str]] = []
    for etype, folder_rel in ENTITY_FOLDERS.items():
        folder = vault / folder_rel
        if not folder.exists():
            continue
        for f in folder.glob("*.md"):
            try:
                content  = f.read_text(encoding="utf-8")
                fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                if not fm_match:
                    continue
                fm = yaml.safe_load(fm_match.group(1)) or {}
                if fm.get("unknown-identity"):
                    unknowns.append((fm.get("name", f.stem), etype))
            except Exception:
                continue
    if unknowns:
        L += ["## Unresolved Identities", "",
              "*Resolve with: `python wiki.py alias \"Placeholder\" \"True Name\"`*", ""]
        for name, etype in sorted(unknowns):
            L.append(f"- [[{name}]] · _{etype}_")
        L.append("")

    # ── Reliability Alerts ───────────────────────────────────────────────────
    contradicted_items: list[tuple[str, str, str]] = []  # name, etype, source
    rumored_items:      list[tuple[str, str, str]] = []

    for etype, folder_rel in ENTITY_FOLDERS.items():
        if etype in ("session", "secret"):
            continue
        folder = vault / folder_rel
        if not folder.exists():
            continue
        for f in folder.glob("*.md"):
            try:
                content  = f.read_text(encoding="utf-8")
                fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                if not fm_match:
                    continue
                fm  = yaml.safe_load(fm_match.group(1)) or {}
                rel = fm.get("reliability", "")
                if rel == "contradicted":
                    contradicted_items.append((fm.get("name", f.stem), etype, fm.get("source", "")))
                elif rel == "rumored":
                    rumored_items.append((fm.get("name", f.stem), etype, fm.get("source", "")))
            except Exception:
                continue

    if contradicted_items:
        L += ["## Contradicted Information", "",
              "*The following entries have been proven false or superseded:*", ""]
        for name, etype, source in sorted(contradicted_items):
            src_str = f" - _{source}_" if source else ""
            L.append(f"- [[{name}]] ({etype}){src_str}")
        L.append("")

    if rumored_items:
        L += ["## Unverified Rumors", "",
              "*Not yet confirmed — treat with caution:*", ""]
        for name, etype, source in sorted(rumored_items):
            src_str = f" - _{source}_" if source else ""
            L.append(f"- [[{name}]] ({etype}){src_str}")
        L.append("")

    # ── Key NPCs ─────────────────────────────────────────────────────────────
    npcs = [n for n in _read_folder_entities(vault / ENTITY_FOLDERS["npc"])
            if not n["fm"].get("stub")]
    if npcs:
        L += ["## Key NPCs", ""]
        alive = [n for n in npcs if n["fm"].get("status", "alive") == "alive"]
        dead  = [n for n in npcs if n["fm"].get("status") == "dead"]
        other = [n for n in npcs if n["fm"].get("status") not in ("alive", "dead", None, "")]

        if alive:
            L += ["| NPC | Role | Location |",
                  "|-----|------|---------|"]
            for n in sorted(alive, key=lambda x: x["name"]):
                fm  = n["fm"]
                loc = _dash_link(fm.get("location", ""))
                L.append(f"| [[{n['name']}]] | {fm.get('role', '')} | {loc} |")
            L.append("")

        if dead:
            L += ["**Deceased:**", ""]
            for n in sorted(dead, key=lambda x: x["name"]):
                L.append(f"- [[{n['name']}]] *(dead)*")
            L.append("")

        if other:
            L += ["**Other status:**", ""]
            for n in sorted(other, key=lambda x: x["name"]):
                L.append(f"- [[{n['name']}]] *({n['fm'].get('status', 'unknown')})*")
            L.append("")

    # ── Factions ─────────────────────────────────────────────────────────────
    factions = [f for f in _read_folder_entities(vault / ENTITY_FOLDERS["faction"])
                if not f["fm"].get("stub")]
    if factions:
        L += ["## Factions", "",
              "| Faction | Leader | PC Relations |",
              "|---------|--------|:------------:|"]
        for fac in sorted(factions, key=lambda x: x["name"]):
            fm     = fac["fm"]
            leader = _dash_link(fm.get("leader", ""))
            L.append(f"| [[{fac['name']}]] | {leader} | {fm.get('pc_relations', '')} |")
        L.append("")

    # ── Session History ───────────────────────────────────────────────────────
    if len(all_sessions) > 1:
        L += ["## Session History", ""]
        for s in reversed(all_sessions):
            fm  = s["fm"]
            num = fm.get("session_number", "?")
            loc = fm.get("location", "")
            loc_str = f" — {loc}" if loc else ""
            L.append(f"- [[{s['name']}|Session {num}{loc_str}]]")
        L.append("")

    out_path.write_text("\n".join(L), encoding="utf-8")
    return out_path


_MANUAL_ENTRIES_HEADER = "## Manual Entries"
_MANUAL_ENTRIES_PLACEHOLDER = (
    "<!-- Add your own entries here. "
    "This section is preserved exactly as-is on every regeneration. -->"
)


def generate_timeline(vault: Path) -> Path:
    """Regenerate _System/Timeline.md — sessions in order, events sorted by date_order.
    Preserves any existing ## Manual Entries section unchanged."""

    out_path = vault / "_System" / "Timeline.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Preserve manual entries block ────────────────────────────────────────
    manual_block = _MANUAL_ENTRIES_PLACEHOLDER
    if out_path.exists():
        try:
            existing = out_path.read_text(encoding="utf-8")
            idx = existing.find(_MANUAL_ENTRIES_HEADER)
            if idx != -1:
                # Grab everything after the header line
                after_header = existing[idx + len(_MANUAL_ENTRIES_HEADER):]
                manual_block = after_header.strip()
        except Exception:
            pass

    # ── Sessions ─────────────────────────────────────────────────────────────
    all_sessions = [
        s for s in _read_folder_entities(vault / ENTITY_FOLDERS["session"])
    ]
    all_sessions.sort(key=lambda x: _session_sort_key(x["fm"].get("session_number")))

    # ── Events ───────────────────────────────────────────────────────────────
    all_events = _read_folder_entities(vault / ENTITY_FOLDERS["event"])

    def _event_sort(e):
        order = e["fm"].get("date_order")
        if order is not None:
            try:
                return (0, float(order))
            except (ValueError, TypeError):
                pass
        return (1, e["name"].lower())

    ordered_events   = [e for e in all_events if e["fm"].get("date_order") is not None]
    unordered_events = [e for e in all_events if e["fm"].get("date_order") is None]
    ordered_events.sort(key=_event_sort)
    unordered_events.sort(key=lambda x: x["name"].lower())

    # ── Build output ─────────────────────────────────────────────────────────
    L: list[str] = [
        "---",
        "type: timeline",
        f'generated: "{now_str}"',
        "---",
        "",
        "# Campaign Timeline",
        f"*Auto-generated · {now_str}*",
        "",
        "*To position an event chronologically, add `date_order: N` to its frontmatter*",
        "*(lower number = earlier; events without it appear in the Unplaced section).*",
        "",
    ]

    # Sessions table
    if all_sessions:
        L += ["## Sessions", "",
              "| # | Session | In-Game Date | Location |",
              "|---|---------|-------------|---------|"]
        for s in all_sessions:
            fm      = s["fm"]
            num     = fm.get("session_number", "?")
            date    = fm.get("in_game_date", "")
            loc     = _dash_plain(fm.get("location", ""))
            L.append(f"| {num} | [[{s['name']}]] | {date} | {loc} |")
        L.append("")

    # Ordered events
    if ordered_events or unordered_events:
        L += ["## Events", ""]

    if ordered_events:
        L += ["| Order | Event | Type | In-Game Date | Location |",
              "|------:|-------|------|-------------|---------|"]
        for e in ordered_events:
            fm    = e["fm"]
            order = fm.get("date_order", "")
            etype = fm.get("event_type", "")
            date  = fm.get("date", "")
            loc   = _dash_link(fm.get("location", ""))
            L.append(f"| {order} | [[{e['name']}]] | {etype} | {date} | {loc} |")
        L.append("")

    if unordered_events:
        L += ["### Unplaced Events",
              "*Add `date_order: N` to move these into the table above.*", ""]
        for e in unordered_events:
            fm    = e["fm"]
            etype = fm.get("event_type", "")
            date  = fm.get("date", "")
            loc   = _dash_link(fm.get("location", ""))
            date_clean = date[0] if isinstance(date, list) else str(date) if date else ""
            type_str = f" ({etype})" if etype else ""
            date_str = f" — {date_clean}" if date_clean and date_clean.lower() not in ("unknown", "") else ""
            loc_str  = f" — {loc}"  if loc else ""
            L.append(f"- [[{e['name']}]]{type_str}{date_str}{loc_str}")
        L.append("")

    # Manual entries — always last, always preserved
    L += [_MANUAL_ENTRIES_HEADER, "", manual_block, ""]

    out_path.write_text("\n".join(L), encoding="utf-8")
    return out_path


def as_bullet_list(val) -> str:
    if isinstance(val, list):
        return "\n".join(f"- {item}" for item in val)
    return str(val)

def as_kv_list(val) -> str:
    if isinstance(val, dict):
        return "\n".join(f"- **{k}**: {v}" for k, v in val.items())
    return str(val)

# ── Markdown rendering ────────────────────────────────────────────────────────

BODY_SECTIONS = {
    "npc": [
        ("Description", ["description"]),
        ("Personality & Motivations", ["personality", "motivations"]),
        ("Relationships", ["relationships"]),
        ("Status & Condition", ["condition"]),
        ("Secrets", ["secrets"]),
        ("Notes", ["notes"]),
    ],
    "pc": [
        ("Backstory", ["backstory"]),
        ("Goals", ["goals"]),
        ("Status & Condition", ["condition"]),
        ("Notes", ["notes"]),
    ],
    "place": [
        ("Description", ["description", "atmosphere"]),
        ("Notable Features", ["notable_features"]),
        ("Inhabitants", ["inhabitants"]),
        ("Hazards", ["hazards"]),
        ("Secrets", ["secrets"]),
    ],
    "lore": [
        ("Content", ["content"]),
        ("Related", ["related_entities"]),
        ("Source", ["source"]),
    ],
    "event": [
        ("Description", ["description"]),
        ("Participants", ["participants"]),
        ("Outcome", ["outcome", "consequences"]),
        ("Notes", ["notes"]),
    ],
    "item": [
        ("Description", ["description"]),
        ("Magical Properties", ["magical_properties"]),
        ("History", ["history"]),
    ],
    "faction": [
        ("Goals", ["goals"]),
        ("Resources & Size", ["resources", "membership_size"]),
        ("PC Relations", ["pc_relations"]),
    ],
    "quest": [
        ("Objective", ["objective"]),
        ("Reward", ["reward"]),
        ("Complications", ["complications"]),
        ("Notes", ["notes"]),
    ],
    "session": [
        ("Summary", ["summary"]),
        ("Events", ["events"]),
        ("Loot Found", ["loot_found"]),
        ("Cliffhanger", ["cliffhanger"]),
    ],
    "secret": [
        ("Details", ["content"]),
        ("Impact", ["impact"]),
        ("How to Reveal", ["how_to_reveal"]),
        ("Revealed To", ["revealed_to"]),
    ],
}

FM_FIELDS = {
    "npc":     ["race", "class_level", "role", "alignment", "location", "status",
                "reliability", "source"],
    "pc":      ["player", "race", "class_level", "alignment", "status"],
    "place":   ["place_type", "region", "reliability", "source"],
    "item":    ["item_type", "rarity", "current_holder", "attunement",
                "reliability", "source"],
    "lore":    ["category", "reliability", "source"],
    "event":   ["event_type", "date", "date_order", "location", "reliability", "source"],
    "faction": ["faction_type", "alignment", "headquarters", "leader", "pc_relations",
                "reliability", "source"],
    "quest":   ["status", "quest_giver", "location", "reliability", "source"],
    "session": ["session_number", "in_game_date", "real_date", "location"],
    "secret":  [],
}

def entity_to_markdown(entity: dict) -> str:
    etype          = entity.get("type", "misc")
    name           = entity.get("name", "Unknown")
    summary        = entity.get("summary", "")
    tags           = entity.get("tags", [])
    aliases        = entity.get("aliases", [])
    links          = entity.get("links", [])
    data           = entity.get("data", {})
    unknown_id     = entity.get("unknown_identity", False)
    reliability    = entity.get("reliability", "") or data.get("reliability", "")
    source         = entity.get("source", "")     or data.get("source", "")

    tag_list = list(dict.fromkeys([etype] + [t for t in tags if t]))
    if unknown_id:
        tag_list = list(dict.fromkeys(tag_list + ["unknown-identity"]))
    if reliability == "contradicted":
        tag_list = list(dict.fromkeys(tag_list + ["contradicted"]))

    fm: dict = {"type": etype, "name": name, "tags": tag_list}
    if unknown_id:
        fm["unknown-identity"] = True

    clean_aliases = [a for a in aliases if a and a != name]
    if clean_aliases:
        fm["aliases"] = clean_aliases

    for field in FM_FIELDS.get(etype, []):
        # reliability and source may come from the top-level entity dict OR data —
        # prefer top-level since that's where EXTRACT_SYSTEM puts them
        if field == "reliability":
            val = reliability
        elif field == "source":
            val = source
        else:
            # check data first, then fall back to top-level entity — external AIs
            # sometimes place fields like session_number at the entity root
            val = data.get(field)
            if val is None:
                val = entity.get(field)
        if val not in (None, "", [], {}):
            fm[field] = _fm_value(val)

    yaml_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    lines = [f"---\n{yaml_str}---\n\n", f"# {name}\n"]

    quote = entity.get("quote", "").strip()
    quote_attr = entity.get("quote_attribution", "").strip()
    if quote:
        lines.append(f"\n> *\"{quote}\"*\n")
        if quote_attr:
            lines.append(f"> — {quote_attr}\n")

    if summary:
        lines.append(f"\n> {summary}\n")

    for section_title, fields in BODY_SECTIONS.get(etype, []):
        parts = []
        for field in fields:
            val = data.get(field)
            if val not in (None, "", [], {}):
                if isinstance(val, dict):
                    parts.append(as_kv_list(val))
                elif isinstance(val, list):
                    parts.append(as_bullet_list(val))
                else:
                    parts.append(str(val))
        if parts:
            lines.append(f"\n## {section_title}\n\n")
            lines.append("\n\n".join(parts))
            lines.append("\n")

    if links:
        lines.append("\n## Related\n\n")
        lines.append("\n".join(f"- [[{link}]]" for link in links))
        lines.append("\n")

    return "".join(lines)

def entity_to_stub(entity: dict) -> str:
    """Minimal note for low-significance entities — name, type, one-line summary, tags."""
    etype       = entity.get("type", "misc")
    name        = entity.get("name", "Unknown")
    summary     = entity.get("summary", "")
    tags        = entity.get("tags", [])
    links       = entity.get("links", [])
    sig         = entity.get("significance", 1)
    unknown_id  = entity.get("unknown_identity", False)
    reliability = entity.get("reliability", "") or entity.get("data", {}).get("reliability", "")
    source      = entity.get("source", "")      or entity.get("data", {}).get("source", "")

    tag_list = list(dict.fromkeys([etype, "stub"] + [t for t in tags if t]))
    if unknown_id:
        tag_list = list(dict.fromkeys(tag_list + ["unknown-identity"]))
    if reliability == "contradicted":
        tag_list = list(dict.fromkeys(tag_list + ["contradicted"]))

    fm = {
        "type":         etype,
        "name":         name,
        "tags":         tag_list,
        "significance": sig,
        "stub":         True,
    }
    if unknown_id:
        fm["unknown-identity"] = True
    if reliability:
        fm["reliability"] = reliability
    if source:
        fm["source"] = source
    yaml_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    lines = [f"---\n{yaml_str}---\n\n", f"# {name}\n"]
    if summary:
        lines.append(f"\n> {summary}\n")
    lines.append("\n*Stub — expand when more is known.*\n")
    if links:
        lines.append("\n## Related\n\n")
        lines.append("\n".join(f"- [[{link}]]" for link in links))
        lines.append("\n")
    return "".join(lines)

# ── LLM helpers ───────────────────────────────────────────────────────────────

def _call(client: anthropic.Anthropic, system: str, user: str, max_tokens: int = 8192) -> tuple[str, str]:
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text.strip(), msg.stop_reason

def _salvage_entities(raw: str) -> list:
    """Extract complete entity objects from a truncated JSON response using a stack parser."""
    entities = []
    stack = []  # tracks start positions of every open {
    in_string = False
    escape = False

    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            fragment = raw[start : i + 1]
            try:
                obj = json.loads(fragment)
                if isinstance(obj, dict) and "type" in obj and "name" in obj:
                    entities.append(obj)
            except json.JSONDecodeError:
                pass

    return entities

def extract_entities(
    client: anthropic.Anthropic,
    text: str,
    context: str = "",
    known_context: str = "",
    _depth: int = 0,
) -> list:
    parts = []
    if known_context:
        parts.append(known_context)
    if context:
        parts.append(f"Context: {context}")
    parts.append("---")
    parts.append(text)
    prompt = "\n\n".join(parts)
    raw, stop_reason = _call(client, EXTRACT_SYSTEM, prompt)
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw).get("entities", [])
    except json.JSONDecodeError:
        if stop_reason == "max_tokens":
            if _depth < 3:
                # Output was cut off — auto-split this chunk and retry both halves
                mid   = len(text) // 2
                split = text.rfind(" ", 0, mid) or mid
                indent = "  " * (_depth + 1)
                click.echo(
                    f"{indent}Output too large — auto-splitting"
                    f" ({len(text):,} chars -> 2x {split:,}/{len(text)-split:,})",
                    err=True,
                )
                left  = extract_entities(client, text[:split],  context, known_context, _depth + 1)
                right = extract_entities(client, text[split:], context, known_context, _depth + 1)
                return left + right
            # Depth limit reached — fall back to salvage parser
            salvaged = _salvage_entities(raw)
            if salvaged:
                click.echo(
                    f"Warning: chunk still too dense after 3 splits — "
                    f"recovered {len(salvaged)} entities (some may be missing).",
                    err=True,
                )
                return salvaged
        click.echo(f"Warning: could not parse Claude response (stop_reason={stop_reason})", err=True)
        click.echo(f"  Snippet: {raw[:300]}{'...' if len(raw) > 300 else ''}", err=True)
        return []

def merge_note(client: anthropic.Anthropic, existing: str, new_md: str, name: str) -> str:
    prompt = (
        f"EXISTING NOTE:\n---\n{existing}\n---\n\n"
        f"NEW INFORMATION TO MERGE:\n---\n{new_md}\n---\n\n"
        f"Entity name: {name}"
    )
    text, stop_reason = _call(client, MERGE_SYSTEM, prompt, max_tokens=16_000)
    if stop_reason == "max_tokens":
        click.echo(
            f"Warning: merged note for '{name}' hit the 16,000-token output limit and"
            f" may be truncated. The note has grown very large — consider splitting it manually.",
            err=True,
        )
    return text

# ── Vault read / write ────────────────────────────────────────────────────────

def note_folder(vault: Path, etype: str) -> Path:
    folder = vault / ENTITY_FOLDERS.get(etype, "Misc")
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def find_existing_note(vault: Path, etype: str, slug: str, name: str) -> Path | None:
    folder = note_folder(vault, etype)
    # Prefer display-name file (new convention), fall back to slug file (legacy)
    for candidate in (safe_filename(name), slug):
        p = folder / f"{candidate}.md"
        if p.exists():
            return p
    # Case-insensitive scan as last resort
    name_lower = name.lower()
    slug_lower = slug.lower()
    for f in folder.glob("*.md"):
        if f.stem.lower() in (name_lower, slug_lower):
            return f
    return None

def write_entity(
    vault: Path,
    entity: dict,
    client: anthropic.Anthropic,
    index: dict,
    dry_run: bool = False,
    stub_threshold: int = 3,
) -> tuple[Path, str]:
    etype   = entity.get("type", "misc")
    name    = entity.get("name", "Unknown")
    slug    = entity.get("slug") or slugify(name)
    aliases = entity.get("aliases", [])
    links   = entity.get("links", [])
    sig     = entity.get("significance", 3)

    # Files use display name so [[Oor the Carved One]] wikilinks resolve naturally
    target_path = note_folder(vault, etype) / f"{safe_filename(name)}.md"
    references  = [{"TargetId": slugify(lnk), "ReferenceType": "wikilink"} for lnk in links]

    # 1. Look in the expected type folder first
    existing = find_existing_note(vault, etype, slug, name)

    # 2. Cross-type fuzzy search — catches "Orr" finding "Oor", NPC dup of a PC, etc.
    if not existing:
        result = find_existing_anywhere(vault, name, slug, own_type=etype)
        if result:
            candidate, found_type = result
            if _is_excluded_from_dedup(candidate, name):
                click.echo(
                    f"    [distinct] '{name}' fuzzy-matched '{candidate.stem}' but no_dedup_with is set — creating separately",
                    err=True,
                )
            else:
                existing = candidate
                if found_type != etype:
                    click.echo(
                        f"    [dedup] '{name}' ({etype}) matched existing '{candidate.stem}' ({found_type})"
                        f" — merging instead of creating duplicate",
                        err=True,
                    )

    # 3. Alias scan — catches identity reveals where old note lists new name as alias
    #    e.g. "Lady Morrova" arriving when "Skeletal Wizard" note has aliases: [Lady Morrova]
    #    OR new entity has aliases that match existing filenames (analyst flagged the reveal)
    if not existing:
        result = find_by_alias(vault, name, slug)
        if result:
            existing, found_type = result
            click.echo(
                f"    [reveal] '{name}' matched via alias on '{existing.stem}' ({found_type})"
                f" — identity reveal, merging",
                err=True,
            )
        else:
            # Also check if any of this entity's own aliases match an existing note
            for alias in aliases:
                result = find_existing_anywhere(vault, alias, slugify(alias), own_type=etype)
                if not result:
                    result = find_by_alias(vault, alias, slugify(alias))
                if result:
                    candidate, found_type = result
                    if _is_excluded_from_dedup(candidate, name):
                        continue
                    existing = candidate
                    click.echo(
                        f"    [reveal] alias '{alias}' matched existing '{existing.stem}' ({found_type})"
                        f" — identity reveal, merging",
                        err=True,
                    )
                    break

    if existing:
        # Always merge into existing notes regardless of significance —
        # don't downgrade a full note to a stub
        action = "updated"
        if not dry_run:
            existing_md = existing.read_text(encoding="utf-8")
            is_stub     = "stub: true" in existing_md and sig >= stub_threshold
            new_md      = entity_to_markdown(entity) if (sig >= stub_threshold or is_stub) else entity_to_stub(entity)
            merged      = merge_note(client, existing_md, new_md, name)
            existing.write_text(merged, encoding="utf-8")
            target_path = existing
    else:
        # New entity — full page or stub based on significance
        is_stub = sig < stub_threshold
        action  = "stub" if is_stub else "created"
        if not dry_run:
            content = entity_to_stub(entity) if is_stub else entity_to_markdown(entity)
            target_path.write_text(content, encoding="utf-8")

    if not dry_run:
        rel = str(target_path.relative_to(vault)).replace("\\", "/")
        upsert_index(index, etype, name, slug, aliases, rel, references)

    return target_path, action

# ── CLI ───────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """CampaignWiki — RPG auto-documentation pipeline."""
    pass


@cli.command()
@click.option("--vault",   help="Path to your Obsidian vault folder")
@click.option("--api-key", help="Anthropic API key")
def setup(vault, api_key):
    """Configure vault path and/or API key."""
    cfg = load_config()
    changed = False
    if vault:
        cfg["vault_path"] = str(Path(vault).expanduser().resolve())
        click.echo(f"Vault set to: {cfg['vault_path']}")
        changed = True
    if api_key:
        cfg["api_key"] = api_key
        click.echo("API key saved.")
        changed = True
    if changed:
        save_config(cfg)
    else:
        v = cfg.get("vault_path", str(DEFAULT_VAULT))
        has_key = bool(cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY"))
        click.echo(f"Vault  : {v}")
        click.echo(f"API key: {'set' if has_key else 'NOT SET — use --api-key or ANTHROPIC_API_KEY env var'}")


@cli.command()
@click.argument("text", required=False)
@click.option("--file", "-f", "src_file", type=click.Path(exists=True), help="Read text from file")
@click.option("--clipboard", "-c", is_flag=True, help="Read text from clipboard")
@click.option("--context",   help="Optional hint for Claude (e.g. 'ChatGPT response about the Underdark')")
@click.option("--chunk-size", default=6_000, show_default=True, metavar="CHARS",
              help="Auto-chunk long inputs at this character threshold")
@click.option("--stub-threshold", default=3, show_default=True, metavar="1-5",
              help="Significance score below which new entities become stubs instead of full pages")
@click.option("--prior-sessions", default=3, show_default=True, metavar="N",
              help="Number of recent session notes to inject as prior context (0 to disable)")
@click.option("--dry-run",   is_flag=True, help="Preview without writing any files")
def extract(text, src_file, clipboard, context, chunk_size, stub_threshold, prior_sessions, dry_run):
    """Extract wiki entries from an AI response (text, file, or clipboard)."""
    cfg = load_config()

    if clipboard:
        try:
            import pyperclip
            text = pyperclip.paste()
        except ImportError:
            click.echo("Install pyperclip for clipboard support: pip install pyperclip", err=True)
            sys.exit(1)
        if not text:
            click.echo("Clipboard is empty.", err=True)
            sys.exit(1)
    elif src_file:
        text = Path(src_file).read_text(encoding="utf-8")
    elif not text:
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            click.echo("Provide text as an argument, --file, --clipboard, or pipe via stdin.", err=True)
            sys.exit(1)

    client = get_client(cfg)
    vault  = get_vault(cfg)
    index  = load_index(vault)
    known  = build_known_entities_context(index)
    prior  = build_session_context(vault, prior_sessions) if prior_sessions > 0 else ""
    known_ctx = "\n\n".join(filter(None, [prior, known]))

    if prior:
        n_sessions = len(re.findall(r"^\[Session ", prior, re.MULTILINE))
        click.echo(f"Loaded prior context from {n_sessions} session(s).")

    if len(text) <= chunk_size:
        click.echo("Extracting entities...")
        entities = extract_entities(client, text, context or "", known_ctx)
    else:
        chunks = _chunk_text(text, chunk_size)
        click.echo(f"Input is {len(text):,} chars — processing in {len(chunks)} chunks...")
        seen: dict[str, dict] = {}
        for i, chunk in enumerate(chunks, 1):
            click.echo(f"  Chunk {i}/{len(chunks)}...")
            for e in extract_entities(client, chunk, f"{context or ''} (chunk {i}/{len(chunks)})".strip(), known_ctx):
                key = f"{e['type']}:{e['name'].lower()}"
                if key not in seen:
                    seen[key] = e
                else:
                    existing_e = seen[key]
                    for k, v in e.get("data", {}).items():
                        if v and not existing_e.get("data", {}).get(k):
                            existing_e.setdefault("data", {})[k] = v
                    existing_e["links"]   = list(set(existing_e.get("links", [])   + e.get("links", [])))
                    existing_e["aliases"] = list(set(existing_e.get("aliases", []) + e.get("aliases", [])))
        entities = list(seen.values())

    if not entities:
        click.echo("No entities found.")
        return

    _process_entities(entities, vault, client, index, dry_run, stub_threshold)


@cli.command()
@click.argument("transcript_file", type=click.Path(exists=True))
@click.option("--number", "-n", type=int, help="Session number")
@click.option("--context",    help="Extra context (campaign name, date, etc.)")
@click.option("--chunk-size", default=6_000, show_default=True, metavar="CHARS",
              help="Characters per processing chunk for long transcripts")
@click.option("--stub-threshold", default=3, show_default=True, metavar="1-5",
              help="Significance score below which new entities become stubs instead of full pages")
@click.option("--prior-sessions", default=3, show_default=True, metavar="N",
              help="Number of recent session notes to inject as prior context (0 to disable)")
@click.option("--dry-run", is_flag=True)
def session(transcript_file, number, context, chunk_size, stub_threshold, prior_sessions, dry_run):
    """Process a session transcript (plain text or combine_transcripts.py output)."""
    cfg    = load_config()
    client = get_client(cfg)
    vault  = get_vault(cfg)
    index  = load_index(vault)
    known  = build_known_entities_context(index)
    prior  = build_session_context(vault, prior_sessions) if prior_sessions > 0 else ""
    known_ctx = "\n\n".join(filter(None, [prior, known]))

    if prior:
        n_sessions = len(re.findall(r"^\[Session ", prior, re.MULTILINE))
        click.echo(f"Loaded prior context from {n_sessions} session(s).")

    text = Path(transcript_file).read_text(encoding="utf-8")

    ctx_parts = ["AD&D 2e session transcript"]
    if number:
        ctx_parts.append(f"Session {number}")
    if context:
        ctx_parts.append(context)
    ctx = ", ".join(ctx_parts)

    if len(text) <= chunk_size:
        click.echo("Processing transcript...")
        entities = extract_entities(client, text, ctx, known_ctx)
    else:
        chunks   = _chunk_text(text, chunk_size)
        total    = len(chunks)
        click.echo(f"Transcript is {len(text):,} chars — processing in {total} chunks...")

        seen: dict[str, dict] = {}
        for i, chunk in enumerate(chunks, 1):
            click.echo(f"  Chunk {i}/{total}...")
            for e in extract_entities(client, chunk, f"{ctx} (chunk {i}/{total})", known_ctx):
                key = f"{e['type']}:{e['name'].lower()}"
                if key not in seen:
                    seen[key] = e
                else:
                    existing_e = seen[key]
                    for k, v in e.get("data", {}).items():
                        if v and not existing_e.get("data", {}).get(k):
                            existing_e.setdefault("data", {})[k] = v
                    existing_e["links"]   = list(set(existing_e.get("links", [])   + e.get("links", [])))
                    existing_e["aliases"] = list(set(existing_e.get("aliases", []) + e.get("aliases", [])))

        entities = list(seen.values())

    if not entities:
        click.echo("No entities found.")
        return

    # Stamp session number on session-type entities
    if number:
        for e in entities:
            if e.get("type") == "session":
                e.setdefault("data", {}).setdefault("session_number", number)

    _process_entities(entities, vault, client, index, dry_run, stub_threshold)


@cli.command()
@click.option("--prior-sessions", default=3, show_default=True, metavar="N",
              help="Number of recent session notes to include (0 to omit)")
@click.option("--no-entities", is_flag=True,
              help="Omit the known entities list — output session context only")
@click.option("--clipboard", "-c", is_flag=True,
              help="Copy output to clipboard instead of printing")
def context(prior_sessions, no_entities, clipboard):
    """Print campaign context for pasting into an external AI before your transcript.

    Outputs the last N session summaries plus the full known-entity list.
    Paste this block before the session-analyst prompt and your transcript so
    the AI has full campaign awareness when generating the JSON for ingest.

    Typical workflow:

    \b
      1. python wiki.py context --clipboard
      2. Paste into AI chat
      3. Paste the session-analyst prompt (prompts/session-analyst.md)
      4. Paste your transcript
      5. Save the AI's JSON response as session5.json
      6. python wiki.py ingest session5.json
    """
    cfg   = load_config()
    vault = get_vault(cfg)
    index = load_index(vault)

    parts: list[str] = []

    if prior_sessions > 0:
        prior = build_session_context(vault, prior_sessions)
        if prior:
            parts.append(prior)
        else:
            click.echo("(No session notes found in vault yet — skipping prior context)", err=True)

    if not no_entities:
        known = build_known_entities_context(index)
        if known:
            parts.append(known)

    if not parts:
        click.echo("Nothing to output — no sessions or entities in vault yet.", err=True)
        return

    output = "\n\n---\n\n".join(parts)

    if clipboard:
        try:
            import pyperclip
            pyperclip.copy(output)
            n_sess = len(re.findall(r"^\[Session ", parts[0], re.MULTILINE)) if prior_sessions > 0 and parts else 0
            click.echo(
                f"Copied to clipboard — {n_sess} session(s) + "
                f"{'known entities' if not no_entities else 'no entity list'} "
                f"({len(output):,} chars).",
                err=True,
            )
        except ImportError:
            click.echo("Install pyperclip for clipboard support: pip install pyperclip", err=True)
            sys.exit(1)
    else:
        click.echo(output)


@cli.command()
@click.argument("json_file", type=click.Path(exists=True))
@click.option("--stub-threshold", default=3, show_default=True, metavar="1-5",
              help="Significance score below which new entities become stubs instead of full pages")
@click.option("--dry-run", is_flag=True, help="Preview without writing any files")
def ingest(json_file, stub_threshold, dry_run):
    """Write wiki notes from a pre-extracted JSON file (skips AI extraction step).

    The JSON file must contain either:
      {"entities": [...]}   — the format produced by the analyst prompts
      [...]                 — a bare array of entity objects

    Use this after running the session-analyst or lore-analyst prompt in any AI
    and saving its response to a .json file.
    """
    cfg   = load_config()
    vault = get_vault(cfg)
    index = load_index(vault)

    raw = Path(json_file).read_text(encoding="utf-8")

    # Strip code fences in case the AI wrapped the JSON anyway
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        click.echo(f"Error: could not parse JSON from {json_file}: {exc}", err=True)
        sys.exit(1)

    if isinstance(data, list):
        entities = data
    elif isinstance(data, dict):
        entities = data.get("entities", [])
    else:
        click.echo("Error: JSON must be an object with an 'entities' key, or a bare array.", err=True)
        sys.exit(1)

    if not entities:
        click.echo("No entities found in JSON file.")
        return

    click.echo(f"Loaded {len(entities)} entities from {Path(json_file).name}")

    # merge_note still needs the Claude client when updating existing notes
    client = get_client(cfg)
    _process_entities(entities, vault, client, index, dry_run, stub_threshold)


@cli.command()
@click.argument("old_name")
@click.argument("new_name")
@click.option("--dry-run", is_flag=True, help="Preview changes without writing anything")
def alias(old_name, new_name, dry_run):
    """Merge a placeholder entity into a revealed identity.

    Use this when a previously-unknown entity ("Skeletal Wizard") turns out
    to be a named individual ("Lady Morrova"):

      python wiki.py alias "Skeletal Wizard" "Lady Morrova"

    This will:
      - Find the old note, find or create the new note
      - Merge old note content into the new note via Claude
      - Add the old name as an alias on the new note
      - Rewrite every [[Old Name]] wikilink in the vault to [[New Name]]
      - Remove the old note and update the entity index
    """
    cfg   = load_config()
    vault = get_vault(cfg)
    index = load_index(vault)

    # Locate old entity
    old_result = find_existing_anywhere(vault, old_name, slugify(old_name))
    if not old_result:
        old_result = find_by_alias(vault, old_name, slugify(old_name))
    if not old_result:
        click.echo(f"Error: no existing note found for '{old_name}'", err=True)
        sys.exit(1)
    old_path, old_type = old_result
    click.echo(f"Old: {old_path.relative_to(vault)} [{old_type}]")

    # Locate new entity (may or may not exist yet)
    new_result = find_existing_anywhere(vault, new_name, slugify(new_name))
    if not new_result:
        new_result = find_by_alias(vault, new_name, slugify(new_name))

    client = get_client(cfg)

    if new_result:
        new_path, new_type = new_result
        click.echo(f"New: {new_path.relative_to(vault)} [{new_type}]")
        if not dry_run:
            old_md  = old_path.read_text(encoding="utf-8")
            new_md  = new_path.read_text(encoding="utf-8")
            # Annotate old content so Claude understands the context
            annotated_old = f"# Content from prior note '{old_name}' (now revealed as {new_name})\n\n{old_md}"
            merged = merge_note(client, new_md, annotated_old, new_name)
            # Ensure old name is recorded as alias and unknown-identity cleared
            merged = _patch_frontmatter(merged, {
                "aliases":          [old_name],
                "unknown-identity": None,   # remove the flag now identity is known
            })
            # Remove unknown-identity tag if present
            merged = re.sub(r"\bunknown-identity\b,?\s*", "", merged)
            new_path.write_text(merged, encoding="utf-8")
            click.echo(f"Merged '{old_name}' content into '{new_name}'")
    else:
        # New entity doesn't exist — rename the old file in place
        new_folder = note_folder(vault, old_type)
        new_path   = new_folder / f"{safe_filename(new_name)}.md"
        click.echo(f"'{new_name}' not found — renaming old note to new identity")
        if not dry_run:
            old_md  = old_path.read_text(encoding="utf-8")
            updated = _patch_frontmatter(old_md, {
                "name":             new_name,
                "aliases":          [old_name],
                "unknown-identity": None,
                "stub":             None,
            })
            # Remove unknown-identity tag
            updated = re.sub(r"\bunknown-identity\b,?\s*", "", updated)
            # Replace the h1 title
            updated = re.sub(r"^# .+$", f"# {new_name}", updated, count=1, flags=re.MULTILINE)
            new_path.write_text(updated, encoding="utf-8")
            click.echo(f"Created: {new_path.relative_to(vault)}")

    # Rewrite wikilinks across the entire vault
    rewritten: list[str] = []
    for md_file in vault.rglob("*.md"):
        if md_file == old_path:
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        updated = _rewrite_wikilinks(content, old_name, new_name)
        if updated != content:
            rewritten.append(str(md_file.relative_to(vault)))
            if not dry_run:
                md_file.write_text(updated, encoding="utf-8")

    for rel in rewritten:
        click.echo(f"  Links updated: {rel}")

    # Remove old note (after links are rewritten)
    if old_path != new_path:
        if not dry_run:
            old_path.unlink()
        click.echo(f"Removed: {old_path.relative_to(vault)}")

    # Scrub old entity from index
    if not dry_run:
        old_slug = slugify(old_name)
        index["Entities"] = [
            e for e in index["Entities"]
            if e["Name"].lower() != old_name.lower()
            and not e["Id"].endswith(f"-{old_slug}")
        ]
        save_index(vault, index)

    if dry_run:
        click.echo(f"\n[Dry run — {len(rewritten)} file(s) would have wikilinks updated]")
    else:
        click.echo(f"\nDone — {len(rewritten)} file(s) had wikilinks updated")


@cli.command()
@click.argument("name_a")
@click.argument("name_b")
def distinct(name_a, name_b):
    """Mark two entities as permanently distinct — never merge them even if names are similar.

    Adds each entity's name to the other's  no_dedup_with  frontmatter list.
    After this, the deduplication system will always create them as separate notes.

    Example:
      python wiki.py distinct "Steelhammer" "Stonehammer"
    """
    cfg   = load_config()
    vault = get_vault(cfg)

    def _find_note(search: str):
        """Find a note by exact name, fuzzy match, or substring fallback."""
        result = find_existing_anywhere(vault, search, slugify(search))
        if not result:
            result = find_by_alias(vault, search, slugify(search))
        if result:
            return result[0]
        # Substring fallback — find notes whose stem contains the search term
        search_lower = search.lower()
        matches = []
        for folder_rel in ENTITY_FOLDERS.values():
            folder = vault / folder_rel
            if not folder.exists():
                continue
            for f in folder.glob("*.md"):
                if search_lower in f.stem.lower():
                    matches.append(f)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            click.echo(f"Ambiguous: '{search}' matches multiple notes:", err=True)
            for m in matches:
                click.echo(f"  {m.relative_to(vault)}", err=True)
            click.echo("Use the full note name to be specific.", err=True)
            return None
        return None

    results = {}
    for name in (name_a, name_b):
        path = _find_note(name)
        if path:
            results[name] = path
        else:
            click.echo(f"Could not find a note for '{name}' — make sure it exists in the vault.", err=True)
            return

    path_a, path_b = results[name_a], results[name_b]

    for path, other_name in ((path_a, name_b), (path_b, name_a)):
        patched = _patch_frontmatter(path.read_text(encoding="utf-8"), {"no_dedup_with": [other_name]})
        # _patch_frontmatter appends to aliases; replicate that for no_dedup_with manually
        # Actually we need custom logic — read fm, merge list, write back
        content  = path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        if fm_match:
            fm = yaml.safe_load(fm_match.group(1)) or {}
            existing_excl = fm.get("no_dedup_with", [])
            if isinstance(existing_excl, str):
                existing_excl = [existing_excl]
            if other_name not in existing_excl:
                existing_excl.append(other_name)
            fm["no_dedup_with"] = existing_excl
            new_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
            path.write_text(f"---\n{new_yaml}---\n" + content[fm_match.end():], encoding="utf-8")
            click.echo(f"  Updated: {path.relative_to(vault)}")
        else:
            click.echo(f"  Warning: no frontmatter found in {path.name}", err=True)

    click.echo(f"Done — '{name_a}' and '{name_b}' will never be merged.")


@cli.command("ls")
@click.option("--type", "etype", help="Filter by type (npc/place/lore/item/session/...)")
@click.option("--unknown", is_flag=True, help="Show only unresolved unknown-identity entities")
@click.option("--reliability", "rel_filter",
              type=click.Choice(["confirmed", "rumored", "contradicted", "unknown"]),
              help="Filter by reliability status")
def list_entries(etype, unknown, rel_filter):
    """List all wiki entries from the entity index."""
    cfg   = load_config()
    vault = get_vault(cfg)
    index = load_index(vault)

    if rel_filter:
        results: list[tuple[str, str, str, str]] = []
        for r_etype, folder_rel in ENTITY_FOLDERS.items():
            if etype and r_etype != etype:
                continue
            folder = vault / folder_rel
            if not folder.exists():
                continue
            for f in folder.glob("*.md"):
                try:
                    content  = f.read_text(encoding="utf-8")
                    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                    if not fm_match:
                        continue
                    fm = yaml.safe_load(fm_match.group(1)) or {}
                    if fm.get("reliability") == rel_filter:
                        src = fm.get("source", "")
                        results.append((fm.get("name", f.stem), r_etype, src, str(f.relative_to(vault))))
                except Exception:
                    continue
        if not results:
            click.echo(f"No {rel_filter} entries found.")
        else:
            click.echo(f"\n{rel_filter.upper()} ({len(results)}):\n")
            for name, r_etype, src, path in sorted(results):
                src_str = f"  (source: {src})" if src else ""
                click.echo(f"  [{r_etype:8}] {name}{src_str}")
        return

    if unknown:
        # Scan vault files directly — index doesn't store unknown-identity flag
        results: list[tuple[str, str, str]] = []
        for u_etype, folder_rel in ENTITY_FOLDERS.items():
            folder = vault / folder_rel
            if not folder.exists():
                continue
            for f in folder.glob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
                    if not fm_match:
                        continue
                    fm = yaml.safe_load(fm_match.group(1)) or {}
                    if fm.get("unknown-identity"):
                        results.append((fm.get("name", f.stem), u_etype, str(f.relative_to(vault))))
                except Exception:
                    continue
        if not results:
            click.echo("No unresolved unknown-identity entities found.")
        else:
            click.echo(f"\nUnresolved identities ({len(results)}):\n")
            for name, u_etype, path in sorted(results):
                click.echo(f"  [{u_etype:8}] {name}")
                click.echo(f"             {path}")
            click.echo(f"\nResolve with: python wiki.py alias \"Placeholder Name\" \"True Name\"")
        return

    entities = index.get("Entities", [])
    if etype:
        entities = [e for e in entities if e.get("Type", {}).get("Value") == etype]

    if not entities:
        msg = f"No entries found (type: {etype})." if etype else "No entries found."
        click.echo(msg)
        return

    by_type: dict[str, list] = {}
    for e in entities:
        t = e.get("Type", {}).get("Value", "misc")
        by_type.setdefault(t, []).append(e)

    for t, items in sorted(by_type.items()):
        folder = ENTITY_FOLDERS.get(t, t)
        click.echo(f"\n{folder}  ({len(items)})")
        for item in sorted(items, key=lambda x: x["Name"].lower()):
            aliases = [a for a in item.get("Aliases", []) if a != item["Name"]]
            alias_str = f"  [{', '.join(aliases[:3])}]" if aliases else ""
            refs = len(item.get("References", []))
            ref_str = f"  ({refs} link{'s' if refs != 1 else ''})" if refs else ""
            click.echo(f"  {item['Name']}{alias_str}{ref_str}")


@cli.command()
@click.option("--dry-run", is_flag=True, help="Preview changes without writing")
def fixlinks(dry_run):
    """Fix multi-link frontmatter fields in existing notes.

    Obsidian only turns [[links]] into clickable pills in properties when each
    link is its own YAML list item.  This command scans every note in the vault
    and converts any frontmatter string field that contains two or more [[links]]
    into a proper YAML list so all links resolve correctly.

    Safe to run on an existing vault — it only rewrites fields that need fixing
    and leaves everything else untouched.
    """
    cfg   = load_config()
    vault = get_vault(cfg)

    fixed_files = 0
    fixed_fields = 0

    for etype, folder_rel in ENTITY_FOLDERS.items():
        folder = vault / folder_rel
        if not folder.exists():
            continue
        for md_file in folder.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            if not fm_match:
                continue

            try:
                fm = yaml.safe_load(fm_match.group(1)) or {}
            except yaml.YAMLError:
                continue

            changed = False
            for key, val in list(fm.items()):
                converted = _fm_value(val)
                if converted is not val and converted != val:
                    fm[key]  = converted
                    changed  = True
                    fixed_fields += 1
                    click.echo(f"  {'[DRY] ' if dry_run else ''}fix [{key}] in {md_file.relative_to(vault)}")

            if changed:
                fixed_files += 1
                if not dry_run:
                    new_yaml = yaml.dump(
                        fm, default_flow_style=False, allow_unicode=True, sort_keys=False
                    )
                    new_content = f"---\n{new_yaml}---\n" + content[fm_match.end():]
                    md_file.write_text(new_content, encoding="utf-8")

    if fixed_files == 0:
        click.echo("All frontmatter link fields are already correct.")
    elif dry_run:
        click.echo(f"\n[Dry run] {fixed_fields} field(s) in {fixed_files} file(s) would be converted.")
    else:
        click.echo(f"\nFixed {fixed_fields} field(s) across {fixed_files} file(s).")


@cli.command()
def dashboard():
    """Regenerate _System/Dashboard.md from current vault state.

    The dashboard is also updated automatically after every session, extract,
    and ingest run. Use this command to force a refresh manually — for example
    after editing notes directly in Obsidian, or after running wiki.py alias.
    """
    cfg   = load_config()
    vault = get_vault(cfg)
    index = load_index(vault)
    path  = generate_dashboard(vault, index)
    click.echo(f"Dashboard written: {path.relative_to(vault)}")


@cli.command()
def timeline():
    """Regenerate _System/Timeline.md — sessions in order, events sorted chronologically.

    To position an event on the timeline, add  date_order: N  to its frontmatter
    (lower = earlier). Events without date_order appear in an Unplaced section.

    A '## Manual Entries' section at the bottom of the file is never overwritten —
    use it to add custom timeline notes, turning points, or era headings by hand.

    The timeline is also regenerated automatically after every ingest and session run.
    """
    cfg   = load_config()
    vault = get_vault(cfg)
    path  = generate_timeline(vault)
    click.echo(f"Timeline written: {path.relative_to(vault)}")


@cli.command()
def audit():
    """Health check the vault — surface issues that need attention.

    Checks:
      1. Broken wikilinks  — [[links]] that point to notes that don't exist
      2. Stubs             — placeholder notes that may need expansion
      3. Missing status    — PCs/NPCs without a status field
      4. Isolated notes    — notes with no wikilinks (orphaned from the graph)
      5. Unresolved identities — placeholder names not yet aliased to a real entity
      6. Name mismatches   — frontmatter name doesn't match the filename
      7. Reliability flags — contradicted facts and unconfirmed lore
    """
    cfg   = load_config()
    vault = get_vault(cfg)

    click.echo(f"Auditing: {vault}\n")

    # ── Build a full stem index so we can resolve every [[link]] ─────────────
    # stem.lower() -> Path  (last writer wins if stems collide, which is rare)
    all_notes: dict[str, Path] = {}
    for md in vault.rglob("*.md"):
        all_notes[md.stem.lower()] = md
    # Dashboard is auto-generated; skip it for broken-link reporting
    dashboard_rel = str((vault / "_System" / "Dashboard.md")).lower()

    total = 0

    # Accumulators
    broken_links:      dict[Path, list[str]]       = {}
    stubs:             list[tuple[Path, str, int]]  = []   # path, etype, sig
    missing_status:    list[tuple[Path, str]]       = []   # path, etype
    isolated:          list[tuple[Path, str]]       = []   # path, etype
    unknowns:          list[tuple[str,  str]]       = []   # name, etype
    name_mismatch:     list[tuple[Path, str, str]]  = []   # path, stem, fm_name
    contradicted_recs: list[tuple[str, str, str]]   = []   # name, etype, source
    unverified_lore:   list[tuple[str, str, str]]   = []   # name, etype, source

    for etype, folder_rel in ENTITY_FOLDERS.items():
        folder = vault / folder_rel
        if not folder.exists():
            continue
        for md_file in folder.glob("*.md"):
            total += 1
            try:
                content  = md_file.read_text(encoding="utf-8")
                fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
                fm   = yaml.safe_load(fm_match.group(1)) or {} if fm_match else {}
                body = content[fm_match.end():] if fm_match else content
            except Exception:
                continue

            # All [[links]] anywhere in the file
            all_links  = re.findall(r"\[\[([^\]|#\n]+?)(?:[|#][^\]]*)?\]\]", content)
            body_links = re.findall(r"\[\[([^\]|#\n]+?)(?:[|#][^\]]*)?\]\]", body)

            # 1. Broken wikilinks
            if str(md_file).lower() != dashboard_rel:
                broken_here = [
                    lnk.strip() for lnk in all_links
                    if lnk.strip().lower() not in all_notes
                ]
                if broken_here:
                    broken_links[md_file] = list(dict.fromkeys(broken_here))

            # 2. Stubs
            if fm.get("stub"):
                stubs.append((md_file, etype, int(fm.get("significance", 0))))

            # 3. Missing status (PCs and NPCs only)
            if etype in ("pc", "npc") and not fm.get("status"):
                missing_status.append((md_file, etype))

            # 4. Isolated — no wikilinks anywhere in the body
            #    (secrets often have no body links by design — skip them)
            if not body_links and etype not in ("secret",):
                isolated.append((md_file, etype))

            # 5. Unknown identities
            if fm.get("unknown-identity"):
                unknowns.append((fm.get("name", md_file.stem), etype))

            # 6. Name / filename mismatch
            fm_name = fm.get("name", "")
            if fm_name and fm_name.lower() != md_file.stem.lower():
                name_mismatch.append((md_file, md_file.stem, fm_name))

            # 7. Reliability flags
            rel = fm.get("reliability", "")
            if rel == "contradicted":
                contradicted_recs.append((fm.get("name", md_file.stem), etype, fm.get("source", "")))
            elif rel == "rumored" and etype == "lore":
                unverified_lore.append((fm.get("name", md_file.stem), etype, fm.get("source", "")))

    click.echo(f"Scanned {total} notes across {len(ENTITY_FOLDERS)} folders.\n")

    found_any = False

    # ── 1. Broken wikilinks ───────────────────────────────────────────────────
    total_broken = sum(len(v) for v in broken_links.values())
    if broken_links:
        found_any = True
        click.echo(f"[1] BROKEN WIKILINKS — {total_broken} link(s) in {len(broken_links)} note(s)")
        click.echo("    These will create empty pages in Obsidian when clicked.")
        click.echo("    Fix: ingest content about these entities, or create notes manually.\n")
        for path in sorted(broken_links):
            click.echo(f"  {path.relative_to(vault)}")
            for lnk in broken_links[path]:
                click.echo(f"    -> [[{lnk}]]")
        click.echo("")

    # ── 2. Stubs ──────────────────────────────────────────────────────────────
    if stubs:
        found_any = True
        click.echo(f"[2] STUBS — {len(stubs)} note(s) flagged for expansion")
        click.echo("    Fix: ingest more content about these entities, or promote manually.\n")
        # Group by significance descending so highest-priority stubs show first
        for path, etype, sig in sorted(stubs, key=lambda x: (-x[2], str(x[0]))):
            click.echo(f"  [sig:{sig}] [{etype:8}] {path.relative_to(vault)}")
        click.echo("")

    # ── 3. Missing status ────────────────────────────────────────────────────
    if missing_status:
        found_any = True
        click.echo(f"[3] MISSING STATUS — {len(missing_status)} character(s) have no status field")
        click.echo("    Fix: add `status: alive` (or dead/missing/etc.) to frontmatter.\n")
        for path, etype in sorted(missing_status, key=lambda x: str(x[0])):
            click.echo(f"  [{etype:3}] {path.relative_to(vault)}")
        click.echo("")

    # ── 4. Isolated notes ────────────────────────────────────────────────────
    if isolated:
        found_any = True
        click.echo(f"[4] ISOLATED NOTES — {len(isolated)} note(s) have no wikilinks")
        click.echo("    These are disconnected from your knowledge graph.\n")
        for path, etype in sorted(isolated, key=lambda x: str(x[0])):
            click.echo(f"  [{etype:8}] {path.relative_to(vault)}")
        click.echo("")

    # ── 5. Unresolved identities ─────────────────────────────────────────────
    if unknowns:
        found_any = True
        click.echo(f"[5] UNRESOLVED IDENTITIES — {len(unknowns)} placeholder(s) pending identification")
        click.echo("    Fix: python wiki.py alias \"Placeholder Name\" \"True Name\"\n")
        for name, etype in sorted(unknowns):
            click.echo(f"  [{etype:8}] {name}")
        click.echo("")

    # ── 6. Name / filename mismatches ────────────────────────────────────────
    if name_mismatch:
        found_any = True
        click.echo(f"[6] NAME MISMATCHES — {len(name_mismatch)} note(s) where filename != frontmatter name")
        click.echo("    Wikilinks use the filename; the frontmatter name is shown in Obsidian.")
        click.echo("    Fix: rename the file to match `name:` in frontmatter, or vice versa.\n")
        for path, stem, fm_name in sorted(name_mismatch, key=lambda x: str(x[0])):
            click.echo(f"  file : {path.relative_to(vault)}")
            click.echo(f"  name : {fm_name}")
        click.echo("")

    # ── 7. Reliability flags ─────────────────────────────────────────────────
    if contradicted_recs or unverified_lore:
        found_any = True
        total_rel = len(contradicted_recs) + len(unverified_lore)
        click.echo(f"[7] RELIABILITY FLAGS -- {total_rel} note(s) flagged")
        if contradicted_recs:
            click.echo(f"    {len(contradicted_recs)} note(s) marked 'contradicted' (proven false info):\n")
            for name, etype, source in sorted(contradicted_recs):
                src_str = f"  [source: {source}]" if source else ""
                click.echo(f"  [{etype:8}] {name}{src_str}")
            click.echo("")
        if unverified_lore:
            click.echo(f"    {len(unverified_lore)} lore note(s) still 'rumored' (unconfirmed):\n")
            for name, etype, source in sorted(unverified_lore):
                src_str = f"  [source: {source}]" if source else ""
                click.echo(f"  [{etype:8}] {name}{src_str}")
            click.echo("")

    # ── Summary ───────────────────────────────────────────────────────────────
    sep = "-" * 55
    click.echo(sep)
    if not found_any:
        click.echo("All clear -- no issues found.")
    else:
        parts = []
        if broken_links:      parts.append(f"{total_broken} broken link(s)")
        if stubs:             parts.append(f"{len(stubs)} stub(s)")
        if missing_status:    parts.append(f"{len(missing_status)} missing status")
        if isolated:          parts.append(f"{len(isolated)} isolated")
        if unknowns:          parts.append(f"{len(unknowns)} unknown identity")
        if name_mismatch:     parts.append(f"{len(name_mismatch)} name mismatch")
        if contradicted_recs: parts.append(f"{len(contradicted_recs)} contradicted")
        if unverified_lore:   parts.append(f"{len(unverified_lore)} unverified lore")
        click.echo("Issues: " + ", ".join(parts))

        clear = []
        if not broken_links:      clear.append("broken links")
        if not stubs:             clear.append("stubs")
        if not missing_status:    clear.append("status fields")
        if not isolated:          clear.append("isolated notes")
        if not unknowns:          clear.append("unknown identities")
        if not name_mismatch:     clear.append("name mismatches")
        if not contradicted_recs: clear.append("contradicted info")
        if not unverified_lore:   clear.append("unverified lore")
        if clear:
            click.echo("All clear: " + ", ".join(clear))
    click.echo(sep)


def _gather_reference_snippets(vault: Path, entity_name: str, context_chars: int = 400) -> list[dict]:
    """Find every mention of entity_name in the vault and return surrounding context snippets."""
    pattern = re.compile(r"\[\[" + re.escape(entity_name) + r"(?:[|\]][^\]]*)?(?:\]\])", re.IGNORECASE)
    # Also match without closing — partial link check
    loose   = re.compile(re.escape(entity_name), re.IGNORECASE)
    snippets: list[dict] = []

    for md_file in vault.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        if entity_name.lower() not in content.lower():
            continue
        # Find all positions of the name
        for m in loose.finditer(content):
            start = max(0, m.start() - context_chars // 2)
            end   = min(len(content), m.end() + context_chars // 2)
            snippet = content[start:end].strip()
            # Strip frontmatter from snippets — not useful as context
            snippet = re.sub(r"^---\n.*?\n---\n", "", snippet, flags=re.DOTALL).strip()
            if snippet:
                snippets.append({
                    "source_note": md_file.stem,
                    "snippet":     snippet,
                })
    # Deduplicate very similar snippets
    seen: list[str] = []
    unique = []
    for s in snippets:
        if not any(_name_sim(s["snippet"][:80], x[:80]) > 0.9 for x in seen):
            seen.append(s["snippet"])
            unique.append(s)
    return unique


@cli.command()
@click.argument("name", required=False)
@click.option("--all",   "fill_all", is_flag=True, help="Fill every broken wikilink in the vault")
@click.option("--yes",   "auto_yes", is_flag=True, help="Skip confirmation prompts (use with --all)")
@click.option("--force", is_flag=True, help="Fill even if a note already exists (useful for empty stubs)")
@click.option("--dry-run", is_flag=True, help="Preview without writing files")
@click.option("--stub-threshold", default=3, show_default=True, metavar="1-5",
              help="Significance below which new entities become stubs")
def fill(name, fill_all, auto_yes, force, dry_run, stub_threshold):
    """Generate a note for a referenced-but-missing entity.

    Collects every snippet of text that mentions the entity across the vault,
    then asks Claude to build a note from what can be inferred — no invented details.

    Examples:

      python wiki.py fill "Voice in Runeigil's Head"

      python wiki.py fill --all

      python wiki.py fill --all --yes
    """
    cfg    = load_config()
    vault  = get_vault(cfg)
    client = get_client(cfg)
    index  = load_index(vault)

    if not name and not fill_all:
        click.echo("Provide an entity name or use --all to process every broken link.", err=True)
        return

    # ── Build list of targets ─────────────────────────────────────────────────
    if fill_all:
        # Collect unique broken wikilinks across the vault
        all_notes: dict[str, Path] = {md.stem.lower(): md for md in vault.rglob("*.md")}
        broken: dict[str, int] = {}  # name -> reference count
        for md_file in vault.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            for lnk in re.findall(r"\[\[([^\]|#\n]+?)(?:[|#][^\]]*)?\]\]", content):
                lnk = lnk.strip()
                if lnk.lower() not in all_notes:
                    broken[lnk] = broken.get(lnk, 0) + 1
        if not broken:
            click.echo("No broken wikilinks found.")
            return
        targets = sorted(broken.items(), key=lambda x: -x[1])
        click.echo(f"Found {len(targets)} unreferenced entities:\n")
        for n, count in targets:
            click.echo(f"  ({count:3d} refs)  {n}")
        click.echo("")
    else:
        targets = [(name, None)]

    # ── Process each target ───────────────────────────────────────────────────
    filled = skipped = 0
    for target_name, ref_count in targets:
        # Skip if a note already exists (unless --force)
        existing_result = find_existing_anywhere(vault, target_name, slugify(target_name))
        if existing_result:
            existing_path = existing_result[0]
            try:
                body = existing_path.read_text(encoding="utf-8")
                fm_match = re.match(r"^---\n.*?\n---\n", body, re.DOTALL)
                body_text = body[fm_match.end():].strip() if fm_match else body.strip()
            except Exception:
                body_text = ""
            is_empty = len(body_text) < 80  # frontmatter-only or near-empty
            if force or is_empty:
                if is_empty:
                    click.echo(f"  Note exists but appears empty — refilling: {existing_path.relative_to(vault)}")
                else:
                    click.echo(f"  --force: refilling existing note: {existing_path.relative_to(vault)}")
            else:
                click.echo(f"  Already exists: {existing_path.relative_to(vault)} — use --force to regenerate")
                continue

        if fill_all and not auto_yes:
            answer = click.prompt(f"Generate note for '{target_name}'? [y/n/q]",
                                  default="n", show_default=False)
            if answer.lower() == "q":
                break
            if answer.lower() != "y":
                skipped += 1
                continue

        snippets = _gather_reference_snippets(vault, target_name)
        if not snippets:
            click.echo(f"  No context found for '{target_name}' — skipping", err=True)
            skipped += 1
            continue

        ref_count_str = f" ({len(snippets)} snippet(s))" if fill_all else f" — {len(snippets)} snippet(s) found"
        click.echo(f"Filling '{target_name}'{ref_count_str}...")

        # Build prompt
        snippet_block = "\n\n".join(
            f"[From: {s['source_note']}]\n{s['snippet']}" for s in snippets[:20]
        )
        prompt = (
            f"Entity name: {target_name}\n\n"
            f"References found in the vault:\n\n{snippet_block}"
        )

        raw, stop_reason = _call(client, FILL_SYSTEM, prompt)
        if stop_reason == "max_tokens":
            click.echo(f"  Warning: response hit token limit for '{target_name}'", err=True)

        raw_clean = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw_clean = re.sub(r"\s*```\s*$", "", raw_clean, flags=re.MULTILINE).strip()
        try:
            entities = json.loads(raw_clean).get("entities", [])
        except json.JSONDecodeError:
            entities = _salvage_entities(raw_clean)
        if not entities:
            click.echo(f"  Could not parse entity for '{target_name}' — skipping", err=True)
            skipped += 1
            continue

        # Force the name to match what's in the vault links
        entities[0]["name"] = target_name

        if dry_run:
            click.echo(f"  [dry-run] Would create: {entities[0].get('type', '?')} — {target_name}")
            filled += 1
            continue

        _process_entities(entities, vault, client, index, dry_run=False,
                          stub_threshold=stub_threshold, _skip_dashboard=True)
        filled += 1

    if filled or skipped:
        click.echo(f"\nDone — {filled} generated, {skipped} skipped.")
        if filled and not dry_run:
            dash = generate_dashboard(vault, index)
            tl   = generate_timeline(vault)
            click.echo(f"Dashboard + Timeline updated.")


@cli.command()
def reindex():
    """Rebuild entity-index.json by scanning all vault markdown files."""
    cfg   = load_config()
    vault = get_vault(cfg)
    index = {"GeneratedAt": "", "Entities": []}
    count = 0

    for etype, folder_rel in ENTITY_FOLDERS.items():
        folder = vault / folder_rel
        if not folder.exists():
            continue
        for md_file in folder.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if not fm_match:
                continue
            try:
                fm = yaml.safe_load(fm_match.group(1)) or {}
            except yaml.YAMLError:
                continue

            name    = fm.get("name", md_file.stem)
            aliases = fm.get("aliases", [])
            slug    = slugify(name)
            rel     = str(md_file.relative_to(vault)).replace("\\", "/")

            wikilinks  = re.findall(r"\[\[([^\]|#]+?)(?:[|#][^\]]*)?\]\]", content)
            references = [{"TargetId": slugify(w), "ReferenceType": "wikilink"} for w in wikilinks]

            upsert_index(index, etype, name, slug, aliases, rel, references)
            count += 1

    save_index(vault, index)
    click.echo(f"Reindexed {count} files -> {vault / INDEX_PATH}")


# ── Shared helpers ────────────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int) -> list[str]:
    """Split text on word boundaries near chunk_size characters."""
    words   = text.split()
    chunks, current, cur_len = [], [], 0
    for word in words:
        if cur_len + len(word) + 1 > chunk_size and current:
            chunks.append(" ".join(current))
            current, cur_len = [], 0
        current.append(word)
        cur_len += len(word) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def _process_entities(
    entities: list,
    vault: Path,
    client: anthropic.Anthropic,
    index: dict,
    dry_run: bool,
    stub_threshold: int = 3,
    _skip_dashboard: bool = False,
):
    click.echo(f"Found {len(entities)} entities — writing notes...")
    counts: dict[str, list] = {"created": [], "stub": [], "updated": []}

    for entity in entities:
        etype = entity.get("type", "misc")
        name  = entity.get("name", "Unknown")
        sig   = entity.get("significance", 3)

        path, action = write_entity(
            vault, entity, client, index,
            dry_run=dry_run, stub_threshold=stub_threshold,
        )

        if dry_run:
            rel = f"{ENTITY_FOLDERS.get(etype, 'Misc')}/{safe_filename(name)}.md"
        else:
            rel = str(path.relative_to(vault))

        sig_tag = f" [sig:{sig}]"
        tag     = "[DRY] " if dry_run else ""
        click.echo(f"  {tag}[{action.upper():7}]{sig_tag} {rel}")
        counts.get(action, counts["created"]).append(f"{name} ({etype})")

    if dry_run:
        click.echo("\n[Dry run — no files written]")
    else:
        save_index(vault, index)
        n_created = len(counts["created"])
        n_stubs   = len(counts["stub"])
        n_updated = len(counts["updated"])
        click.echo(f"\nDone — {n_created} created, {n_stubs} stubs, {n_updated} updated")
        if not _skip_dashboard:
            dash = generate_dashboard(vault, index)
            click.echo(f"Dashboard updated: {dash.relative_to(vault)}")
            tl = generate_timeline(vault)
            click.echo(f"Timeline updated:  {tl.relative_to(vault)}")


if __name__ == "__main__":
    cli()
