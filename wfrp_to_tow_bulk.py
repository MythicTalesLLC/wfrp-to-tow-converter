#!/usr/bin/env python3
"""
wfrp_to_tow_bulk.py
===================
Bulk converter: Warhammer Fantasy Roleplay 4e compendium JSON
                → Warhammer: The Old World (Foundry VTT) JSON

Source system : WFRP4e (moo-man/WFRP4e-FoundryVTT, system ID "wfrp4e")
Target system : Warhammer: The Old World (moo-man/OldWorld-FoundryVTT, ID "whtow")

WFRP4e data model facts (verified from template.json + model source):
  - Actor types: character, npc, creature, vehicle
  - Characteristics stored in system.characteristics.{ws,bs,s,t,i,ag,dex,int,wp,fel}
      Each has: { initial (d10-scale bonus, NOT percentile), modifier, advances, bonusMod }
  - Skills stored as embedded items (type="skill"), NOT a dict on the actor.
      Each skill item: { system.characteristic.value, system.advances.value (percentile) }
      Skill total = char_initial + round(advances / 10)
  - Wounds: system.status.wounds.{value, max}
  - Move:   system.details.move.value (integer 1–8+)
  - Item types: ammunition, armour, career, container, critical, disease, injury,
                money, mutation, prayer, psychology, talent, trapping, skill,
                spell, trait, weapon, extendedTest, vehicleMod, vehicleRole,
                vehicleTest, cargo, template

TOW data model facts (verified from system.json + model source):
  - Actor types: character, npc, vehicle
  - Characteristics: system.characteristics.{ws,bs,s,t,i,ag,re,fel}  (8, not 10)
      Each: { base (d10 int), modifier (int) }  — value = base + modifier (computed)
  - Skills: system.skills.{melee,defence,shooting,throwing,brawn,toil,survival,
                             endurance,awareness,dexterity,athletics,stealth,
                             willpower,recall,leadership,charm}
      Each: { base (int), modifier (int), characteristic (str) }
  - NPC sub-type: system.type  = "minion" | "brute" | "monstrosity"
  - Speed:        system.speed = { value: "slow"|"normal"|"fast", modifier: 0 }
  - Resilience:   system.resilience = { value: 0, modifier: 0 }  (computed from T)
  - Item types: weapon, armour, talent, lore, toolKit, spell, blessing, trapping,
                asset, ability, career, origin, injury, condition,
                vehicleRole, vehicleMod

Supports:
  - Single full-compendium JSON (top-level array of documents)
  - Compendium-Exporter wrapper format { metadata: {...}, items: [...] }
  - Folder of individual document JSON files

Usage:
  python wfrp_to_tow_bulk.py <input> <output> [options]

Options:
  --config PATH   External JSON file overriding default mappings
  --backup        Save a .bak.json copy of each source file before converting
  --validate      Run basic post-conversion sanity checks
  -v / --verbose  Enable DEBUG-level logging
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# STATIC MAPPINGS  (not overridable — derived from system source code)
# ─────────────────────────────────────────────────────────────────────────────

# WFRP4e characteristic key → TOW characteristic key.
# WFRP has 10 characteristics; TOW has 8.
#   Dropped: dex (no direct equivalent), int (merged into recall skill)
#   Renamed: wp → re  (Willpower → Resolve)
WFRP_TO_TOW_CHAR: dict[str, str | None] = {
    "ws":  "ws",
    "bs":  "bs",
    "s":   "s",
    "t":   "t",
    "i":   "i",
    "ag":  "ag",
    "dex": None,   # dropped — absorbed into dexterity/agility skills
    "int": None,   # dropped — absorbed into recall skill
    "wp":  "re",   # Willpower → Resolve
    "fel": "fel",
}

# TOW NPC skill key → default linked characteristic key (TOW convention).
# Used when reconstructing system.skills from embedded WFRP skill items.
TOW_SKILL_CHAR: dict[str, str] = {
    "melee":      "ws",
    "defence":    "ws",
    "shooting":   "bs",
    "throwing":   "bs",
    "brawn":      "s",
    "toil":       "s",
    "survival":   "t",
    "endurance":  "t",
    "awareness":  "i",
    "dexterity":  "i",
    "athletics":  "ag",
    "stealth":    "ag",
    "willpower":  "re",
    "recall":     "re",
    "leadership": "fel",
    "charm":      "fel",
}

# WFRP4e item type → TOW item type.
# None = remove item from output (no equivalent in TOW).
WFRP_TO_TOW_ITEM_TYPE: dict[str, str | None] = {
    # kept with same name
    "weapon":       "weapon",
    "armour":       "armour",
    "talent":       "talent",
    "spell":        "spell",
    "injury":       "injury",
    "trapping":     "trapping",
    # renamed
    "trait":        "ability",    # WFRP trait → TOW ability
    "prayer":       "blessing",   # WFRP prayer → TOW blessing
    "critical":     "injury",     # WFRP critical wound → TOW injury
    "container":    "trapping",   # no container type in TOW
    "ammunition":   "trapping",   # no ammo type in TOW
    "mutation":     "ability",    # represent as ability
    "psychology":   "ability",    # represent as ability
    "disease":      "ability",    # represent as ability (no disease type in TOW)
    # removed — no equivalent in TOW
    "skill":        None,   # absorbed into actor system.skills
    "money":        None,
    "career":       None,
    "extendedTest": None,
    "vehicleMod":   None,
    "vehicleRole":  None,
    "vehicleTest":  None,
    "cargo":        None,
    "template":     None,
}

# WFRP4e actor type → TOW actor type.
WFRP_TO_TOW_ACTOR_TYPE: dict[str, str] = {
    "npc":      "npc",
    "character": "character",
    "creature": "npc",      # TOW has no creature type; use npc
    "vehicle":  "vehicle",
}

# WFRP size key → size-tier integer used by classify_npc_subtype
_SIZE_TIER: dict[str, int] = {
    "tiny":      0,   # < small
    "little":    1,   # small
    "small":     1,
    "avg":       2,   # average / man-sized
    "average":   2,
    "lrg":       3,   # large (ogre, troll)
    "large":     3,
    "enrm":      4,   # enormous (giant, dragon)
    "enormous":  4,
    "mnst":      4,
    "monstrous": 4,
}

# WFRP ability / trait / talent name → (TOW ability name, TOW statblock description)
# or None to drop entirely (no meaningful TOW equivalent or explicitly excluded).
#
# Key   = WFRP base name, before any parenthetical spec e.g. "Fear" not "Fear (2)"
# Value = None                      → drop silently
#         (str, str)                → (tow_name, one-sentence TOW description)
#
# Rules used when writing descriptions (TOW-only mechanics):
#   +/−X dice on a specific test, once-per-turn / per-round limits,
#   triggered effects on Wounds, granting/removing Staggered/Broken/Give Ground,
#   Armour Save improvement, adding a weapon Trait, reaction attacks.
# NEVER: Success Levels, % bonuses, WFRP conditions, Critical Table references.
#
# Abilities whose spec should be kept in the item name (e.g. "Immunity (Fire)"):
_ABILITY_KEEP_SPEC: frozenset = frozenset({
    "Immunity", "Hatred", "Fearless", "Resistance",
})

_WFRP_ABILITY_LIBRARY: dict[str, tuple[str, str] | None] = {

    # ── Fear & Morale ───────────────────────────────────────────────────────
    "Fear": (
        "Fearsome",
        "Fearsome — Any enemy that ends its turn within Close range of this "
        "creature must pass a Willpower test or become Staggered.",
    ),
    "Terror": (
        "Terrifying",
        "Terrifying — Enemies within Close range suffer −2 dice on Willpower "
        "tests. On a failure they become Broken.",
    ),
    "Hatred": (
        "Hatred",
        "Hatred — This creature gains +2 dice on all Melee tests against enemies "
        "of the hated type.",
    ),
    "Immune to Psychology": (
        "Fearless",
        "Fearless — This creature automatically passes all Willpower tests to "
        "resist fear, terror, and breaking.",
    ),
    "Fearless": (
        "Fearless",
        "Fearless — This creature automatically passes all Willpower tests to "
        "resist fear, terror, and breaking.",
    ),
    "Menacing": (
        "Menacing",
        "Menacing — Enemies within Close range suffer −1 die on Willpower tests "
        "while this creature is visible.",
    ),
    "Frightening": (
        "Fearsome",
        "Fearsome — Any enemy that ends its turn within Close range of this "
        "creature must pass a Willpower test or become Staggered.",
    ),

    # ── Combat ─────────────────────────────────────────────────────────────
    "Frenzy": (
        "Frenzied",
        "Frenzied — When this creature suffers its 1st Wound, it gains +1 die on "
        "all Melee tests for the rest of the combat but suffers −1 die on Defence "
        "tests.",
    ),
    "Berserk Charge": (
        "Berserk Charge",
        "Berserk Charge — When this creature charges, its first attack this turn "
        "gains +2 dice.",
    ),
    "Combat Aware": (
        "Combat Aware",
        "Combat Aware — This creature cannot be surprised, and enemies do not gain "
        "bonus dice from flanking or rear attacks.",
    ),
    "Combat Reflexes": (
        "Combat Reflexes",
        "Combat Reflexes — Once per round, when an enemy misses this creature in "
        "melee, it may immediately make one free Melee attack against that enemy.",
    ),
    "Strike Mighty Blow": (
        "Mighty Blow",
        "Mighty Blow — Once per turn, one attack made by this creature gains the "
        "Impact trait.",
    ),
    "Strike to Stun": (
        "Stunning Blow",
        "Stunning Blow — This creature's attacks gain the Concussive trait.",
    ),
    "Dirty Fighting": (
        "Dirty Fighter",
        "Dirty Fighter — Attacks made against enemies that are already Staggered "
        "also gain the Concussive trait.",
    ),
    "Multiple Arms": (
        "Multiple Arms",
        "Multiple Arms — This creature may make one additional Melee attack per "
        "turn.",
    ),
    "Very Strong": (
        "Mighty",
        "Mighty — This creature's Strength increases by 1 for the purpose of "
        "determining damage. Its melee attacks gain the Impact trait.",
    ),
    "Warrior Born": (
        "Born Warrior",
        "Born Warrior — +1 die on all Melee tests.",
    ),
    "Lightning Reflexes": (
        "Lightning Reflexes",
        "Lightning Reflexes — +1 die on all Defence tests.",
    ),
    "Quick Draw": (
        "Quick Draw",
        "Quick Draw — Drawing or switching weapons costs this creature no action.",
    ),
    "Fast Hands": (
        "Quick Draw",
        "Quick Draw — Drawing or switching weapons costs this creature no action.",
    ),
    "Dual Wielder": (
        "Dual Wielder",
        "Dual Wielder — When fighting with two weapons, this creature may make one "
        "additional Melee attack per turn at −1 die.",
    ),
    "Ambusher": (
        "Ambusher",
        "Ambusher — When attacking from surprise or concealment, this creature "
        "gains +2 dice on its first attack.",
    ),
    "Implacable": (
        "Relentless",
        "Relentless — This creature cannot be pushed back or made to Give Ground "
        "by enemy actions.",
    ),
    "Relentless": (
        "Relentless",
        "Relentless — This creature cannot be pushed back or made to Give Ground "
        "by enemy actions.",
    ),
    "Wall of Muscle": (
        "Unstoppable",
        "Unstoppable — This creature cannot be pushed back, knocked prone, or "
        "repositioned by any enemy ability.",
    ),
    "Impassable": (
        "Unstoppable",
        "Unstoppable — This creature cannot be pushed back, knocked prone, or "
        "repositioned by any enemy ability.",
    ),
    "Shieldsman": (
        "Shield Expert",
        "Shield Expert — When using a shield, this creature's Armour Save is "
        "improved by one step, and it gains +1 die on Defence tests.",
    ),
    "Enclosed Fighter": (
        "Tunnel Fighter",
        "Tunnel Fighter — This creature ignores all penalties for fighting in "
        "confined spaces and gains +1 die on Melee tests made in tight quarters.",
    ),
    "Feint": (
        "Feint",
        "Feint — Once per turn, this creature may force one enemy in Close range "
        "to reroll a successful Defence test.",
    ),
    "Reversal": (
        "Riposte",
        "Riposte — Once per round, after a successful Defence test, this creature "
        "may immediately make one free Melee attack against the attacker.",
    ),
    "Sixth Sense": (
        "Sixth Sense",
        "Sixth Sense — This creature cannot be ambushed and automatically detects "
        "hidden enemies within Medium range.",
    ),

    # ── Resilience / Defence ────────────────────────────────────────────────
    "Painless": (
        "Painless",
        "Painless — This creature ignores the Staggered condition and all "
        "penalties from wound-threshold effects until it is defeated.",
    ),
    "Armoured": (
        "Heavily Armoured",
        "Heavily Armoured — This creature's Armour Save is improved by one step "
        "(e.g. 6+ becomes 5+).",
    ),
    "Very Resilient": (
        "Tough as Nails",
        "Tough as Nails — This creature's Resilience is increased by 1.",
    ),
    "Robust": (
        "Tough as Nails",
        "Tough as Nails — This creature's Resilience is increased by 1.",
    ),
    "Tough": (
        "Tough as Nails",
        "Tough as Nails — This creature's Resilience is increased by 1.",
    ),
    "Hardy": (
        "Hardy",
        "Hardy — Once per round, this creature may reroll one failed Endurance "
        "test.",
    ),
    "Strong-minded": (
        "Iron Will",
        "Iron Will — +1 die on all Willpower tests.",
    ),
    "Iron Will": (
        "Iron Will",
        "Iron Will — +1 die on all Willpower tests.",
    ),
    "Resolute": (
        "Iron Will",
        "Iron Will — +1 die on all Willpower tests.",
    ),
    "Tenacious": (
        "Tenacious",
        "Tenacious — When this creature suffers its last Wound, it does not "
        "immediately collapse — it may take one final action before being removed "
        "from play.",
    ),
    "Regenerate": (
        "Regenerate",
        "Regenerate — At the end of each round, if this creature has suffered at "
        "least one Wound, remove one Wound (to a minimum of 1 remaining).",
    ),
    "Resistance": (
        "Resistance",
        "Resistance — This creature gains +2 dice on Endurance tests to resist "
        "the specified effect or damage type.",
    ),
    "Magic Resistance": (
        "Magic Resistance",
        "Magic Resistance — This creature gains +2 dice on all Willpower tests "
        "made to resist spells and magical effects.",
    ),
    "Ward": (
        "Magic Resistance",
        "Magic Resistance — This creature gains +2 dice on all Willpower tests "
        "made to resist spells and magical effects.",
    ),

    # ── Movement & Positioning ──────────────────────────────────────────────
    "Fly": (
        "Fly",
        "Fly — This creature can fly. While airborne it ignores all terrain and "
        "moves freely in three dimensions at its normal Speed.",
    ),
    "Winged": (
        "Fly",
        "Fly — This creature can fly. While airborne it ignores all terrain and "
        "moves freely in three dimensions at its normal Speed.",
    ),
    "Spider Climb": (
        "Spider Climb",
        "Spider Climb — This creature can walk on walls and ceilings. It ignores "
        "all movement penalties from vertical surfaces.",
    ),
    "Aquatic": (
        "Aquatic",
        "Aquatic — This creature ignores difficult terrain caused by water and may "
        "move and fight at full effectiveness while submerged.",
    ),
    "Tunnel Rat": (
        "Tunnel Fighter",
        "Tunnel Fighter — This creature ignores all movement penalties in confined "
        "spaces and gains +1 die on Melee tests in tight quarters.",
    ),
    "Stealthy": (
        "Stealthy",
        "Stealthy — +2 dice on all Stealth tests. This creature may attempt to "
        "ambush enemies even in open terrain.",
    ),
    "Sneaky": (
        "Stealthy",
        "Stealthy — +2 dice on all Stealth tests. This creature may attempt to "
        "ambush enemies even in open terrain.",
    ),
    "Shadow": (
        "Stealthy",
        "Stealthy — +2 dice on all Stealth tests. This creature may attempt to "
        "ambush enemies even in open terrain.",
    ),
    "Shadowing": (
        "Stealthy",
        "Stealthy — +2 dice on all Stealth tests. This creature may attempt to "
        "ambush enemies even in open terrain.",
    ),
    "Alley Cat": (
        "Stealthy",
        "Stealthy — +2 dice on all Stealth tests. This creature may attempt to "
        "ambush enemies even in open terrain.",
    ),
    "Flee!": (
        "Fleet-footed",
        "Fleet-footed — When this creature Runs, it may move one range band "
        "further than normal.",
    ),
    "Beneath Notice": (
        "Unassuming",
        "Unassuming — Enemies do not target this creature with ranged attacks or "
        "spells unless it is the only valid target.",
    ),

    # ── Senses ──────────────────────────────────────────────────────────────
    "Dark Vision": (
        "Night Vision",
        "Night Vision — This creature ignores all Stealth or Concealment bonuses "
        "granted by darkness or dim light.",
    ),
    "Night Vision": (
        "Night Vision",
        "Night Vision — This creature ignores all Stealth or Concealment bonuses "
        "granted by darkness or dim light.",
    ),
    "Keen Senses": (
        "Keen Senses",
        "Keen Senses — +2 dice on Awareness tests. This creature cannot be "
        "surprised.",
    ),
    "Acute Sense": (
        "Keen Senses",
        "Keen Senses — +2 dice on Awareness tests. This creature cannot be "
        "surprised.",
    ),
    "Acute Senses": (
        "Keen Senses",
        "Keen Senses — +2 dice on Awareness tests. This creature cannot be "
        "surprised.",
    ),
    "Sharp": (
        "Keen Senses",
        "Keen Senses — +2 dice on Awareness tests. This creature cannot be "
        "surprised.",
    ),
    "Tracker": (
        "Tracker",
        "Tracker — +2 dice on Survival tests to track. This creature cannot be "
        "ambushed.",
    ),
    "Second Sight": (
        "Magical Sense",
        "Magical Sense — This creature can sense active spells, magical items, "
        "and supernatural creatures within Medium range without a test.",
    ),
    "Magical Sense": (
        "Magical Sense",
        "Magical Sense — This creature can sense active spells, magical items, "
        "and supernatural creatures within Medium range without a test.",
    ),

    # ── Nature / Type ────────────────────────────────────────────────────────
    "Undead": (
        "Undead",
        "Undead — This creature is immune to poison, disease, fear, and all "
        "effects that require a living target. It does not need sleep, air, "
        "or food.",
    ),
    "Daemon": (
        "Daemon",
        "Daemon — This creature is immune to mundane weapons. On defeat it is "
        "banished to the Realm of Chaos rather than slain.",
    ),
    "Spirit": (
        "Spirit",
        "Spirit — This creature can only be harmed by magical weapons. It may "
        "pass through solid objects as a free action.",
    ),
    "Ethereal": (
        "Ethereal",
        "Ethereal — This creature can only be harmed by magical weapons. "
        "Non-magical attacks deal no damage.",
    ),
    "Swarm": (
        "Swarm",
        "Swarm — This creature occupies a 3×3 area and cannot be engaged in "
        "standard melee. Any enemy ending its turn within the swarm's area is "
        "automatically attacked once.",
    ),
    "Instability": (
        "Instability",
        "Instability — At the end of each round in which this creature has not "
        "dealt a Wound, it suffers 1 automatic Wound with no saves or reductions "
        "allowed.",
    ),
    "Magical": (
        "Magical",
        "Magical — This creature's attacks count as magical and can harm enemies "
        "immune to mundane weapons.",
    ),
    "Bestial": (
        "Bestial",
        "Bestial — This creature cannot Rally. When Broken, it flees at maximum "
        "speed toward the nearest exit.",
    ),
    "Flammable": (
        "Flammable",
        "Flammable — If this creature suffers damage from fire, it suffers one "
        "additional Wound with no saves allowed.",
    ),
    "Animosity": (
        "Animosity",
        "Animosity — At the start of each round, this creature must pass a "
        "Willpower test or it can only target the nearest visible enemy this turn.",
    ),
    "Stupidity": (
        "Stupidity",
        "Stupidity — At the start of each round, this creature must pass a "
        "Willpower test or it loses its action this turn.",
    ),

    # ── Magic & Divine ───────────────────────────────────────────────────────
    "Spellcaster": (
        "Spellcaster",
        "Spellcaster — This creature may cast spells using its Willpower skill. "
        "Refer to its spell list for known spells.",
    ),
    "Arcane Magic": (
        "Spellcaster",
        "Spellcaster — This creature may cast spells using its Willpower skill. "
        "Refer to its spell list for known spells.",
    ),
    "Petty Magic": (
        "Petty Spellcaster",
        "Petty Spellcaster — This creature knows a handful of minor spells. Once "
        "per combat it may cast one petty spell using its Willpower skill.",
    ),
    "Bless": (
        "Divine Caster",
        "Divine Caster — This creature may call upon divine power using its "
        "Willpower skill. Refer to its blessing list for known blessings.",
    ),
    "Invoke": (
        "Divine Caster",
        "Divine Caster — This creature may call upon divine power using its "
        "Willpower skill. Refer to its blessing list for known blessings.",
    ),
    "Aethyric Attunement": (
        "Aethyric Attunement",
        "Aethyric Attunement — +1 die on all Willpower tests made to cast spells "
        "or resist magical effects.",
    ),

    # ── Venom & Poison ───────────────────────────────────────────────────────
    "Venom": (
        "Venomous",
        "Venomous — Any attack by this creature that inflicts a Wound also deals "
        "1 automatic damage at the start of the victim's next turn (no save "
        "allowed).",
    ),
    "Poison": (
        "Venomous",
        "Venomous — Any attack by this creature that inflicts a Wound also deals "
        "1 automatic damage at the start of the victim's next turn (no save "
        "allowed).",
    ),
    "Noxious": (
        "Noxious Aura",
        "Noxious Aura — Enemies within Close range suffer −1 die on Endurance "
        "tests while within the aura.",
    ),
    "Foul Stench": (
        "Foul Stench",
        "Foul Stench — Enemies within Close range suffer −1 die on all Willpower "
        "tests.",
    ),
    "Miasma of Pestilence": (
        "Miasma",
        "Miasma — At the start of each round, all enemies within Close range "
        "suffer 1 automatic damage with no saves allowed.",
    ),

    # ── Immunity ─────────────────────────────────────────────────────────────
    "Immunity": (
        "Immunity",
        "Immunity — This creature is immune to the specified damage type or "
        "condition. Attacks and effects of that type have no effect.",
    ),

    # ── Trapper / Terrain ────────────────────────────────────────────────────
    "Trapper": (
        "Trapper",
        "Trapper — Once per combat, this creature may place a snare. Any enemy "
        "that moves through its space must pass an Athletics test or become "
        "Staggered.",
    ),
    "Rover": (
        "Ranger",
        "Ranger — This creature ignores difficult terrain and never suffers "
        "movement penalties from natural obstacles.",
    ),

    # ── Explicitly dropped (weapon-natural-attacks, WFRP-only, or inconvertible) ─
    "Fury":                 None,
    "Crawler":              None,
    "Creeping":             None,
    "Weapon":               None,
    "Bite":                 None,
    "Claws":                None,
    "Horns":                None,
    "Tail":                 None,
    "Lash":                 None,
    "Ram":                  None,
    "Tongue":               None,
    "Ranged":               None,
    "Breathe":              None,
    "Breath":               None,
    "Wing Buffet":          None,
    "Disease":              None,
    "Infected":             None,
    "Mutation":             None,
    "Chaos Manifestation":  None,
    "Corrupting Aura":      None,
    "Doombolt":             None,
    "Champion":             None,
    "Ecological":           None,
    "Old":                  None,
    "Rabies":               None,
    "Possessed":            None,
    "Ride":                 None,
    "Trained":              None,
    "Webber":               None,
    "Wingless":             None,
    "Lair":                 None,
    "Territorial":          None,
    "Shadow Lurker":        None,
    "Knockdown":            None,
    "Sturdy":               None,
    "Hungry":               None,
    "Prey":                 None,
    "Mounted":              None,
    "Instinct":             None,
    "Size":                 None,
    "Swamp Dweller":        None,
    "Corrupt":              None,
    "Mental Corruption":    None,
    "Corruption":           None,
    "Small":                None,
    "Doomed":               None,
    "Luck":                 None,
    "Argumentative":        None,
    "Artistic":             None,
    "Attractive":           None,
    "Blather":              None,
    "Bookish":              None,
    "Break and Enter":      None,
    "Briber":               None,
    "Carouser":             None,
    "Concoct":              None,
    "Craftsman":            None,
    "Criminal":             None,
    "Dealmaker":            None,
    "Detect Artefact":      None,
    "Embezzle":             None,
    "Etiquette":            None,
    "Field Dressing":       None,
    "Fisherman":            None,
    "Gregarious":           None,
    "Holy Visions":         None,
    "Impassioned Zeal":     None,
    "Instinctive Diction":  None,
    "Master Tradesman":     None,
    "Nimble Fingered":      None,
    "Noble Blood":          None,
    "Numismatics":          None,
    "Orientation":          None,
    "Perfect Pitch":        None,
    "Pharmacist":           None,
    "Public Speaker":       None,
    "Pure Soul":            None,
    "Read/Write":           None,
    "Savant":               None,
    "Savvy":                None,
    "Schemer":              None,
    "Seasoned Traveller":   None,
    "Secret Identity":      None,
    "Stone Soup":           None,
    "Strider":              None,
    "Strong Back":          None,
    "Strong Legs":          None,
    "Strong Swimmer":       None,
    "Super Numerate":       None,
    "Supportive":           None,
    "Surgery":              None,
    "Tinker":               None,
    "Tower of Memories":    None,
    "Suave":                None,
    "Ambidextrous":         None,
    "Animal Affinity":      None,
    "Marksman":             None,
    "Witch!":               None,
}

# ─────────────────────────────────────────────────────────────────────────────
# LIBRARY EXTRACTION SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

# Maps each TOW item type → the output file "bucket" it belongs to.
# Multiple types can share a bucket (their items are merged into one file).
# Override via cfg["library_type_map"] in an external --config JSON.
#
# Output files are written alongside the main output as:
#   library_weapons.json, library_armour.json, library_abilities.json, etc.
LIBRARY_TYPE_BUCKETS: dict[str, str] = {
    "weapon":   "weapons",
    "armour":   "armour",
    "ability":  "abilities",
    "talent":   "abilities",   # non-NPC talents go into abilities bucket
    "trapping": "trappings",
    "toolKit":  "trappings",   # tools / workshops merged into trappings
    "asset":    "trappings",   # books / documents merged into trappings
    "blessing": "abilities",   # blessings merged into abilities
    "spell":    "abilities",   # spells merged into abilities
    "lore":     "abilities",
    "injury":   "trappings",
}

# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT MAPPINGS  (overridable via --config JSON)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict[str, Any] = {
    # WFRP skill name → TOW skill key.
    # TOW's 16 consolidated skills:
    #   melee, defence, shooting, throwing, brawn, toil,
    #   survival, endurance, awareness, dexterity, athletics, stealth,
    #   willpower, recall, leadership, charm
    "skill_map": {
        # ── Melee ──────────────────────────────────────────────────────────
        "Melee (Basic)":          "melee",
        "Melee (Any)":            "melee",
        "Melee (Brawling)":       "melee",
        "Melee (Cavalry)":        "melee",
        "Melee (Fencing)":        "melee",
        "Melee (Flail)":          "melee",
        "Melee (Great Weapon)":   "melee",
        "Melee (Polearm)":        "melee",
        "Melee (Shield)":         "melee",
        "Melee (Two-handed)":     "melee",
        "Melee":                  "melee",   # bare form
        # ── Defence ────────────────────────────────────────────────────────
        "Dodge":                  "defence",
        # ── Shooting / Throwing ────────────────────────────────────────────
        "Ranged (Bow)":           "shooting",
        "Ranged (Crossbow)":      "shooting",
        "Ranged (Pistol)":        "shooting",
        "Ranged (Blackpowder)":   "shooting",
        "Ranged (Engineering)":   "shooting",
        "Ranged (Any)":           "shooting",
        "Ranged (Sling)":         "throwing",
        "Ranged (Throwing)":      "throwing",
        # ── Brawn ──────────────────────────────────────────────────────────
        "Brawn":                  "brawn",
        "Wrestle":                "brawn",
        # ── Toil ───────────────────────────────────────────────────────────
        "Consume Alcohol":        "toil",
        "Drive":                  "toil",
        "Row":                    "toil",
        "Sail":                   "toil",
        "Trade (Any)":            "toil",
        "Trade":                  "toil",
        "Craft (Any)":            "toil",
        "Craft":                  "toil",
        # ── Survival ───────────────────────────────────────────────────────
        "Heal":                   "survival",
        "Survival":               "survival",
        "Track":                  "survival",
        "Outdoor Survival":       "survival",
        # ── Endurance ──────────────────────────────────────────────────────
        "Endurance":              "endurance",
        # ── Awareness ──────────────────────────────────────────────────────
        "Perception":             "awareness",
        "Intuition":              "awareness",
        "Lip Reading":            "awareness",
        # ── Dexterity ──────────────────────────────────────────────────────
        "Art (Any)":              "dexterity",
        "Sleight of Hand":        "dexterity",
        "Pickpocket":             "dexterity",
        "Pick Lock":              "dexterity",
        "Set Trap":               "dexterity",
        "Skulduggery":            "dexterity",
        # ── Athletics ──────────────────────────────────────────────────────
        "Athletics":              "athletics",
        "Swim":                   "athletics",
        "Climb":                  "athletics",
        "Ride (Any)":             "athletics",
        "Ride (Horse)":           "athletics",
        "Ride":                   "athletics",
        # ── Stealth ────────────────────────────────────────────────────────
        "Stealth":                "stealth",
        "Stealth (Any)":          "stealth",
        "Stealth (Rural)":        "stealth",
        "Stealth (Urban)":        "stealth",
        # ── Willpower ──────────────────────────────────────────────────────
        "Cool":                   "willpower",
        "Willpower":              "willpower",
        "Pray":                   "willpower",
        "Channelling (Any)":      "willpower",
        "Channelling":            "willpower",
        # ── Recall ─────────────────────────────────────────────────────────
        "Lore (Any)":             "recall",
        "Lore (Reikland)":        "recall",
        "Lore (Theology)":        "recall",
        "Lore (Magic)":           "recall",
        "Language (Any)":         "recall",
        "Language (Reikspiel)":   "recall",
        "Evaluate":               "recall",
        "Gamble":                 "recall",
        "Navigation":             "recall",
        "Research":               "recall",
        "Secret Signs (Any)":     "recall",
        "Secret Signs (Ranger)":  "recall",
        "Secret Signs (Thief)":   "recall",
        "Secret Signs":           "recall",  # prefix fallback
        # ── Willpower (channelling = magic willpower) ───────────────────────
        "Channelling (Any)":      "willpower",
        "Channelling (Aqshy)":    "willpower",
        "Channelling (Azyr)":     "willpower",
        "Channelling (Chamon)":   "willpower",
        "Channelling (Ghur)":     "willpower",
        "Channelling (Ghyran)":   "willpower",
        "Channelling (Hysh)":     "willpower",
        "Channelling (Shyish)":   "willpower",
        "Channelling (Ulgu)":     "willpower",
        "Channelling":            "willpower",  # prefix fallback
        "Pray":                   "willpower",
        "Endure":                 "willpower",
        # ── Survival (nature / animal / outdoor skills) ────────────────────
        "Animal Care":            "survival",
        "Animal Training":        "survival",
        "Animal Handling":        "survival",
        "Heal":                   "survival",
        "Outdoor Survival":       "survival",
        "Swim":                   "survival",
        "Climb":                  "survival",
        # ── Melee additional variants ───────────────────────────────────────
        "Melee (Parry)":          "defence",
        "Dodge":                  "defence",
        "Dodge Blow":             "defence",
        # ── Leadership ─────────────────────────────────────────────────────
        "Leadership":             "leadership",
        "Intimidate":             "leadership",
        "Command":                "leadership",
        # ── Charm ──────────────────────────────────────────────────────────
        "Gossip":                 "charm",
        "Haggle":                 "charm",
        "Charm":                  "charm",
        "Charm Animal":           "charm",
        "Perform (Any)":          "charm",
        "Perform":                "charm",   # bare form
        "Entertain (Any)":        "charm",
        "Entertain":              "charm",   # bare form
        "Play (Any)":             "charm",
        "Play":                   "charm",   # prefix fallback (instruments)
        "Persuasion":             "charm",
        "Bribery":                "charm",
        # ── Dexterity (also covers bare Art) ───────────────────────────────
        "Art (Any)":              "dexterity",
        "Art":                    "dexterity",  # bare form
        "Sleight of Hand":        "dexterity",
        "Pickpocket":             "dexterity",
        "Pick Lock":              "dexterity",
        "Set Trap":               "dexterity",
        "Skulduggery":            "dexterity",
        # ── Recall (language & lore variants) ──────────────────────────────
        "Lore (Any)":             "recall",
        "Lore (Reikland)":        "recall",
        "Lore (Theology)":        "recall",
        "Lore (Magic)":           "recall",
        "Lore (Dark Magic)":      "recall",
        "Lore (Engineering)":     "recall",
        "Lore (Heraldry)":        "recall",
        "Lore (History)":         "recall",
        "Lore (Law)":             "recall",
        "Lore (Middenland)":      "recall",
        "Lore (Skaven)":          "recall",
        "Lore (Underworld)":      "recall",
        "Language (Any)":         "recall",
        "Language (Reikspiel)":   "recall",
        "Language (Khazalid)":    "recall",
        "Language (Eltharin)":    "recall",
        "Language (Tilean)":      "recall",
        "Language (Queekish)":    "recall",
        "Language (Classical)":   "recall",
        "Language (Dark Tongue)": "recall",
        "Language (Bretonnian)":  "recall",
        "Language (Estalian)":    "recall",
        "Language (Norse)":       "recall",
        "Language (Wastelander)": "recall",
        "Language (Magick)":      "recall",
        "Language (Battle)":      "recall",
        "Language":               "recall",   # prefix fallback
        "Lore (Any)":             "recall",
        "Lore (Reikland)":        "recall",
        "Lore (Theology)":        "recall",
        "Lore (Magic)":           "recall",
        "Lore (Dark Magic)":      "recall",
        "Lore (Engineering)":     "recall",
        "Lore (Heraldry)":        "recall",
        "Lore (History)":         "recall",
        "Lore (Law)":             "recall",
        "Lore (Middenland)":      "recall",
        "Lore (Skaven)":          "recall",
        "Lore (Underworld)":      "recall",
        "Lore":                   "recall",   # prefix fallback
        # ── Toil (trade variants) ───────────────────────────────────────────
        "Consume Alcohol":          "toil",
        "Drive":                    "toil",
        "Row":                      "toil",
        "Sail":                     "toil",
        "Trade (Any)":              "toil",
        "Trade":                    "toil",
        "Trade (Apothecary)":       "toil",
        "Trade (Armourer)":         "toil",
        "Trade (Bowyer)":           "toil",
        "Trade (Cartographer)":     "toil",
        "Trade (Coin Stamping)":    "toil",
        "Trade (Cook)":             "toil",
        "Trade (Embalmer)":         "toil",
        "Trade (Gunsmith)":         "toil",
        "Trade (Herbalist)":        "toil",
        "Trade (Jeweller)":         "toil",
        "Trade (Poisoner)":         "toil",
        "Trade (Shipwright)":       "toil",
        "Trade (Stoneworker)":      "toil",
        "Trade (Tailor)":           "toil",
        "Trade (Tanner)":           "toil",
        "Trade (Weaponsmith)":      "toil",
        "Craft (Any)":              "toil",
        "Craft":                    "toil",
    },

    # WFRP weaponGroup.value → TOW skill key (fallback when no skill item)
    "weapon_group_map": {
        "basic":       "melee",
        "cavalry":     "melee",
        "fencing":     "melee",
        "flail":       "melee",
        "brawling":    "melee",
        "polearm":     "melee",
        "twohanded":   "melee",
        "parrying":    "melee",
        "bow":         "shooting",
        "crossbow":    "shooting",
        "blackpowder": "shooting",
        "engineering": "shooting",
        "entangling":  "throwing",
        "sling":       "throwing",
        "throwing":    "throwing",
    },

    # WFRP quality / flaw name → TOW trait key
    "quality_map": {
        "Accurate":        "accurate",
        "Armour Piercing": "penetrating",   # absorbed into ap integer; trait = penetrating
        "Balanced":        "balanced",
        "Bash":            "concussive",
        "Blast":           "area",
        "Concussive":      "concussive",
        "Damaging":        None,            # absorbed into +1 damage value
        "Dangerous":       "unreliable",
        "Distract":        "distract",
        "Fast":            "fast",
        "Hack":            "slashing",
        "Impact":          "impact",
        "Impale":          "penetrating",
        "Penetrating":     "penetrating",
        "Pummel":          "concussive",
        "Reach":           "reach",
        "Shield":          "parry",
        "Slow":            "slow",
        "Snare":           "snare",
        "Tiring":          "slow",
        "Undamaging":      None,
        "Unreliable":      "unreliable",
        "Reload+":         "slow",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# STATS TRACKER
# ─────────────────────────────────────────────────────────────────────────────

class ConversionStats:
    """Accumulates counters and messages throughout a conversion run."""

    def __init__(self) -> None:
        self.documents_converted:       int = 0
        self.journals_converted:        int = 0
        self.characteristics_scaled:    int = 0
        self.skills_mapped:             int = 0
        self.items_converted:           int = 0
        self.active_effects_simplified: int = 0
        self.library_counts: dict[str, int] = {}  # bucket → unique item count written
        self.library_files:  list[str]      = []  # filenames of written library files
        self.warnings:  list[str] = []
        self.errors:    list[str] = []

    def report(self) -> str:
        divider = "─" * 52
        lines = [
            divider,
            "  CONVERSION SUMMARY",
            divider,
            f"  Documents converted       : {self.documents_converted}",
            f"  Journals converted        : {self.journals_converted}",
            f"  Characteristics converted : {self.characteristics_scaled}",
            f"  Skills extracted          : {self.skills_mapped}",
            f"  Embedded items processed  : {self.items_converted}",
            f"  Active effects simplified : {self.active_effects_simplified}",
        ]
        if self.library_counts:
            total_lib = sum(self.library_counts.values())
            lines.append(f"  Library items extracted   : {total_lib} unique")
            for bucket in sorted(self.library_counts):
                lines.append(f"      {bucket:<22} : {self.library_counts[bucket]}")
            lines.append(f"  Library files written     : {len(self.library_files)}")
            for fname in self.library_files:
                lines.append(f"      {fname}")
        if self.warnings:
            lines.append(f"  Warnings                  : {len(self.warnings)}")
            for w in self.warnings[:10]:
                lines.append(f"      ⚠  {w}")
            if len(self.warnings) > 10:
                lines.append(f"      … and {len(self.warnings) - 10} more")
        if self.errors:
            lines.append(f"  Errors                    : {len(self.errors)}")
            for e in self.errors[:10]:
                lines.append(f"      ✗  {e}")
            if len(self.errors) > 10:
                lines.append(f"      … and {len(self.errors) - 10} more")
        lines.append(divider)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CORE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

log = logging.getLogger("wfrp_tow")


def scale_char(val: Any, stats: ConversionStats) -> Any:
    """
    Convert a WFRP percentile characteristic value to TOW d10 scale.
    NOTE: In practice, WFRP4e exports already store the characteristic BONUS
    (value÷10) in the `initial` field, so this function is kept for any
    edge-case actors whose values happen to be in full percentile form.
    Formula: max(1, round(val / 10)).  Leaves non-numeric values unchanged.
    """
    if isinstance(val, (int, float)) and val > 0:
        stats.characteristics_scaled += 1
        return max(1, round(val / 10))
    return val


def build_tow_characteristics(wfrp_chars: dict, stats: ConversionStats) -> dict:
    """
    Convert WFRP4e characteristics dict to TOW format.

    WFRP4e stores 10 characteristics; TOW has 8:
      - Dropped: dex, int  (absorbed into skills)
      - Renamed: wp → re   (Willpower → Resolve)
    Each WFRP char: { initial (d10 bonus), modifier, advances, bonusMod, ... }
    Each TOW char:  { base (int), modifier (int) }

    WFRP4e stores the characteristic `initial` as the d10 bonus in most exports
    (value 2–7), but some full-compendium exports store the full percentile value
    (20–70).  We auto-detect: if any initial > 10 it's percentile and we divide.
    """
    tow_chars: dict = {}
    # Detect scale: if any initial value is > 10, the export is in percentile form
    initials = []
    for char in wfrp_chars.values():
        if isinstance(char, dict):
            v = char.get("initial", 0)
            if isinstance(v, (int, float)):
                initials.append(v)
    is_percentile = any(v > 10 for v in initials)

    for wfrp_key, tow_key in WFRP_TO_TOW_CHAR.items():
        if tow_key is None:
            continue  # dex / int — dropped
        char = wfrp_chars.get(wfrp_key)
        if not isinstance(char, dict):
            continue
        # Support both WFRP4e 'initial' and already-TOW 'base' fields
        initial = char.get("initial") if "initial" in char else char.get("base", 0)
        if not isinstance(initial, (int, float)):
            initial = 0
        modifier = char.get("modifier", 0)
        if not isinstance(modifier, (int, float)):
            modifier = 0
        # Convert to d10 scale; also scale the modifier when percentile.
        # Guard: any modifier with |value| > 5 is clearly in percentile scale
        # even when is_percentile wasn't triggered (e.g. re-processing half-
        # converted data). Divide by 10 and clamp to a sane d10 range.
        if is_percentile:
            base     = max(1, round(initial  / 10))
            modifier = round(modifier / 10)
        else:
            base = max(1, int(initial))
            if abs(modifier) > 5:
                modifier = round(modifier / 10)
        modifier = max(-5, min(5, int(modifier)))
        tow_chars[tow_key] = {"base": base, "modifier": modifier}
        stats.characteristics_scaled += 1
    return tow_chars


def extract_tow_skills(
    items: list,
    wfrp_chars: dict,
    cfg: dict,
    stats: ConversionStats,
) -> dict:
    """
    Build a TOW NPC skills dict by reading embedded skill items.

    WFRP4e stores skills as embedded items (type="skill") rather than a flat
    dict on the actor.  Each skill item has:
      - name                              — the WFRP skill name
      - system.characteristic.value      — the linked characteristic key
      - system.advances.value            — the number of percentile advances

    TOW skill base = char_initial + round(advances / 10)

    The returned dict has the shape:
      { "melee": { "base": 5, "modifier": 0, "characteristic": "ws" }, ... }

    Skills that don't map to a known TOW key are logged as warnings and skipped.
    """
    skill_map: dict[str, str] = cfg.get("skill_map", {})
    result: dict = {}

    for item in items:
        if not isinstance(item, dict) or item.get("type") != "skill":
            continue

        name: str = item.get("name", "")
        item_sys = item.get("system") or {}
        char_key: str = (item_sys.get("characteristic") or {}).get("value", "ws")
        advances_raw = (item_sys.get("advances") or {}).get("value", 0)
        advances: int = int(advances_raw) if isinstance(advances_raw, (int, float)) else 0

        # Look up the TOW skill key — exact name first, then prefix before "("
        tow_skill = skill_map.get(name)
        if tow_skill is None and "(" in name:
            prefix = name.split("(")[0].strip()
            tow_skill = skill_map.get(prefix)
        if tow_skill is None:
            # fallback: lowercase first word, strip spaces
            tow_skill = name.lower().split("(")[0].strip().replace(" ", "")
            stats.warnings.append(f"Unmapped skill '{name}' → fallback '{tow_skill}'")
            log.debug("Unmapped skill '%s' → fallback '%s'", name, tow_skill)

        # Skip if the resulting key is not a known TOW skill
        if tow_skill not in TOW_SKILL_CHAR:
            log.debug("Discarding non-TOW skill '%s' (mapped to '%s')", name, tow_skill)
            continue

        # Characteristic initial value (already d10 scale)
        wfrp_char = wfrp_chars.get(char_key) or {}
        char_initial = wfrp_char.get("initial", 3) if isinstance(wfrp_char, dict) else 3
        if not isinstance(char_initial, (int, float)):
            char_initial = 3

        # Total d10 skill base = char bonus + fraction of advances, capped at 6
        tow_base = min(6, max(1, round(char_initial + advances / 10)))

        # Keep whichever mapped entry has the highest base
        existing = result.get(tow_skill)
        if existing is None or tow_base > existing["base"]:
            tow_char_mapped = WFRP_TO_TOW_CHAR.get(char_key) or TOW_SKILL_CHAR.get(tow_skill, "ws")
            result[tow_skill] = {
                "base": tow_base,
                "modifier": 0,
                "characteristic": tow_char_mapped,
            }
            stats.skills_mapped += 1

    return result


def classify_npc_subtype(
    wounds: int,
    t_bonus: int,
    size_key: str,
    is_named: bool,
) -> tuple[str, int]:
    """
    Classify a WFRP4e NPC into a TOW sub-type and scale the Wounds accordingly.

    TOW sub-type decision (checked in priority order):
      1. Enormous size (tier ≥4)  → Monstrosity
      2. WFRP Wounds ≥ 25         → Monstrosity
      3. Named NPC  (is_named)    → Champion
      4. Wounds ≤ 10 AND avg/small (tier ≤2) → Minion
      5. Everything else          → Brute

    TOW Wounds scaling — based on official TOW wound ranges:
      Minion      → 1            (dies on any single Wound)
      Brute       → max(2, min(cap, round(WFRP_wounds / 4)))
                    cap = 5 for avg/small, 7 for large (trolls, ogres)
                    e.g. orc 14W → 4, large troll 20W → 5, large brute 24W → 6
      Champion    → max(5, min(15, round(WFRP_wounds × 0.75)))
                    PC-style injury table, 8–15 is the designed range
                    e.g. Chaos Champion 18W → 14, named goblin 8W → 6
      Monstrosity → max(6, min(15, round(WFRP_wounds / 2)))
                    ~half WFRP value, hard cap 15; TOW is not a bag-of-HP system
                    e.g. giant 30W → 15, dragon 40W → 15, troll 20W → 10
    """
    size_tier = _SIZE_TIER.get(size_key, 2)   # default to average

    # ── 1. Sub-type classification ─────────────────────────────────────────
    if size_tier >= 4:                          # enormous / monstrous
        subtype = "monstrosity"
    elif wounds >= 25:                          # legendary toughness
        subtype = "monstrosity"
    elif is_named:                              # boss / named NPC
        subtype = "champion"
    elif wounds <= 10 and size_tier <= 2:       # disposable swarm
        subtype = "minion"
    else:                                       # solid rank-and-file
        subtype = "brute"

    # ── 2. TOW Wounds scaling ──────────────────────────────────────────────
    if subtype == "minion":
        tow_wounds = 1

    elif subtype == "brute":
        # Large creatures (trolls, ogres) can reach 6–7; avg rank-and-file cap at 5
        brute_cap = 7 if size_tier >= 3 else 5
        tow_wounds = max(2, min(brute_cap, round(wounds / 4)))

    elif subtype == "champion":
        # PC injury-table range: 8–15. Keep formula proportional, clamp to range.
        tow_wounds = max(5, min(15, round(wounds * 0.75)))

    else:  # monstrosity
        # ~½ WFRP value; TOW cap ~15 even for ancient dragons / greater daemons
        tow_wounds = max(6, min(15, round(wounds / 2)))

    return subtype, tow_wounds


    """
    Return the TOW skill key for a given WFRP skill name.
    Falls back to lower-casing the first word before any parenthesis.
    (Used for weapon skill mapping, not the main actor skill extraction.)
    """
    skill_map: dict[str, str] = cfg.get("skill_map", {})
    if wfrp_name in skill_map:
        stats.skills_mapped += 1
        return skill_map[wfrp_name]
    fallback = wfrp_name.lower().split("(")[0].strip().replace(" ", "")
    stats.warnings.append(f"Unmapped skill '{wfrp_name}' → fallback '{fallback}'")
    log.debug("Unmapped skill '%s' → fallback '%s'", wfrp_name, fallback)
    return fallback


def move_to_speed(move_val: Any) -> str:
    """Map a WFRP4e move value (integer) to a TOW speed string."""
    try:
        m = int(move_val)
    except (TypeError, ValueError):
        return "normal"
    if m <= 2:
        return "slow"
    if m >= 6:
        return "fast"
    return "normal"


def _keep_only(sys_data: dict, *keys: str) -> None:
    """
    Remove ALL system fields except the named keys (in-place whitelist).
    Used to strip WFRP-only system fields after each item conversion, so the
    output only contains fields that the TOW system model actually reads.
    """
    to_delete = [k for k in list(sys_data) if k not in keys]
    for k in to_delete:
        del sys_data[k]


def extract_qualities(sys_data: dict) -> list[str]:
    """
    Extract quality/flaw names from various WFRP structures:
      - list of strings
      - list of {name: ...} dicts
      - dict keyed by quality name
    """
    raw = sys_data.get("qualities") or sys_data.get("flaws") or []
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict) and "name" in item:
                names.append(item["name"])
    elif isinstance(raw, dict):
        names.extend(raw.keys())
    return names


def qualities_to_traits(sys_data: dict, cfg: dict) -> list[str]:
    """Map WFRP quality/flaw names to TOW trait keys and return as a list."""
    quality_map: dict[str, str] = cfg.get("quality_map", {})
    traits: list[str] = []
    for name in extract_qualities(sys_data):
        traits.append(quality_map.get(name, name.lower().replace(" ", "")))
    return traits


def simplify_active_effects(effects: list, stats: ConversionStats) -> list:
    """
    Reduce WFRP ActiveEffects to TOW-compatible stubs.

    Strategy:
      - Preserve: name, icon, disabled, description, and simple numeric changes.
      - Strip: WFRP system-path keys, JS script strings, and roll formula keys
               (these reference WFRP data paths that do not exist in TOW).
    """
    # WFRP-specific key fragments that have no meaning in TOW
    _WFRP_KEY_FRAGMENTS = (
        "flags.wfrp4e",
        "data.status",
        "system.status",
        "script",
        "roll",
        "SL",
        "advantage",
    )

    simplified: list = []
    for eff in effects:
        if not isinstance(eff, dict):
            continue
        new_eff: dict = {
            "name":     eff.get("name", ""),
            "icon":     eff.get("icon", ""),
            "disabled": eff.get("disabled", False),
        }
        if eff.get("description"):
            new_eff["description"] = eff["description"]

        # Keep only changes with non-WFRP keys and simple scalar values
        kept_changes: list = []
        for change in eff.get("changes", []):
            key: str = change.get("key", "")
            value = change.get("value")
            if any(frag in key for frag in _WFRP_KEY_FRAGMENTS):
                continue
            if isinstance(value, (int, float, str)):
                kept_changes.append(change)
        new_eff["changes"] = kept_changes

        simplified.append(new_eff)
        stats.active_effects_simplified += 1

    return simplified


# ─────────────────────────────────────────────────────────────────────────────
# TYPE-SPECIFIC ITEM CONVERTERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_damage_formula(formula: str, s_bonus: int) -> int:
    """
    Convert a WFRP4e damage formula string to a TOW integer damage value.

    Examples:
      "SB+3"  with s_bonus=4  → 7
      "SB"    with s_bonus=5  → 5
      "SB-2"  with s_bonus=4  → 2
      "4"                     → 4
      "1d10"                  → 5  (midpoint: (die+1)//2)
    Strategy: replace SB, then use regex to extract (sign, value) pairs so
    subtraction is handled correctly.  Minimum return value is 1.
    """
    if not formula:
        return 1
    import re as _re
    formula = formula.strip().upper().replace("SB", str(s_bonus))
    # Normalise: prepend '+' so every term has an explicit sign
    if formula and formula[0] not in "+-":
        formula = "+" + formula
    total = 0
    for m in _re.finditer(r"([+-])(\d*[Dd]\d+|\d+)", formula):
        sign = 1 if m.group(1) == "+" else -1
        val_str = m.group(2)
        dm = _re.match(r"(\d*)[Dd](\d+)", val_str)
        if dm:
            num_dice = int(dm.group(1) or 1)
            die_size = int(dm.group(2))
            total += sign * num_dice * ((die_size + 1) // 2)
        else:
            total += sign * int(val_str)
    return max(1, total)  # a weapon must always deal at least 1 damage


# ── Weapon conversion constants ──────────────────────────────────────────────

# Weapon group → base TOW dice pool bonus
_WEAPON_GROUP_DICE: dict[str, int] = {
    "basic":       4,   # standard hand weapon
    "cavalry":     4,   # cavalry sword / lance
    "fencing":     4,   # rapier, foil
    "parrying":    3,   # main-gauche, buckler
    "brawling":    3,   # fists / improvised
    "flail":       4,   # flails
    "polearm":     5,   # halberd, spear (reach + power)
    "twohanded":   5,   # great sword, great axe
    "bow":         4,
    "crossbow":    4,
    "blackpowder": 4,
    "engineering": 4,
    "entangling":  3,   # nets, whips
    "sling":       3,
    "throwing":    3,
}

# Weapon groups that require two hands
_TWO_HANDED_GROUPS: frozenset = frozenset({"twohanded", "polearm", "flail"})

# WFRP quality → AP (Armour Piercing) integer bonus (take highest across qualities)
_QUALITY_AP: dict[str, int] = {
    "Armour Piercing": 1,
    "Penetrating":     2,
    "Impale":          1,
    "Hack":            1,
}

# WFRP quality → TOW trait string
# None = absorbed into damage/dice value, not surfaced as a trait
_QUALITY_TRAIT: dict[str, str | None] = {
    "Hack":       "slashing",
    "Impale":     "penetrating",
    "Blast":      "area",
    "Accurate":   "accurate",
    "Fast":       "fast",
    "Shield":     "parry",
    "Snare":      "snare",
    "Pummel":     "concussive",
    "Concussive": "concussive",
    "Reach":      "reach",
    "Impact":     "impact",
    "Damaging":   None,    # +1 damage absorbed into flat value
    "Reload+":    None,    # represented by twoHanded + description
    "Slow":       None,    # represented by twoHanded
    "Tiring":     None,
    "Undamaging": None,
}


def convert_weapon(doc: dict, sys_data: dict, cfg: dict, stats: ConversionStats) -> None:
    """
    Convert a WFRP weapon item's system fields to TOW equivalents (in-place).

    TOW weapon fields produced:
      skill      — melee / shooting / throwing
      twoHanded  — bool (polearm, great weapons, flails)
      damage.value — integer (from WFRP formula; +1 if Damaging; capped at 12)
      diceBonus  — int (+3–+5 by group; +1 for Accurate quality)
      ap         — int (Armour Piercing, derived from qualities)
      traits     — list[str], max 2 most meaningful TOW entries
    """
    weapon_group_map: dict[str, str] = cfg.get("weapon_group_map", {})

    # 1. Weapon group → skill + two-handed flag
    raw_group = sys_data.get("weaponGroup") or sys_data.get("weapontype") or ""
    if isinstance(raw_group, dict):
        raw_group = raw_group.get("value", "")
    group_key = str(raw_group).lower().replace(" ", "").replace("-", "")

    # Fallback: infer group from weapon name when no group data is present
    if not group_key or group_key == "none":
        name_lower = doc.get("name", "").lower()
        if any(k in name_lower for k in ("halberd", "spear", "pole", "lance",
                                           "quarterstaff", "staff", "trident")):
            group_key = "polearm"
        elif any(k in name_lower for k in ("great sword", "greatsword", "great axe",
                                           "greataxe", "zweihander", "two-handed")):
            group_key = "twohanded"
        elif any(k in name_lower for k in ("flail", "morning star", "morningstar")):
            group_key = "flail"
        elif any(k in name_lower for k in ("dagger", "knife", "dirk", "stiletto",
                                           "main-gauche", "kris")):
            group_key = "fencing"
        elif any(k in name_lower for k in ("rapier", "foil", "epee")):
            group_key = "fencing"
        elif any(k in name_lower for k in ("buckler", "parrying")):
            group_key = "parrying"
        elif any(k in name_lower for k in ("fist", "knuckle", "brawl", "punch",
                                           "unarmed", "improvised")):
            group_key = "brawling"
        elif any(k in name_lower for k in ("net", "whip", "lasso", "catcher",
                                           "entangling")):
            group_key = "entangling"
        elif any(k in name_lower for k in ("bow", "longbow", "shortbow")):
            group_key = "bow"
        elif any(k in name_lower for k in ("crossbow", "arbalest")):
            group_key = "crossbow"
        elif any(k in name_lower for k in ("pistol", "musket", "handgun",
                                           "blunderbuss", "rifle", "gun")):
            group_key = "blackpowder"
        elif any(k in name_lower for k in ("sling",)):
            group_key = "sling"
        elif any(k in name_lower for k in ("throwing", "javelin", "dart",
                                           "shuriken")):
            group_key = "throwing"
        else:
            group_key = "basic"   # hand weapon default

    sys_data["skill"]     = weapon_group_map.get(group_key, "melee")
    sys_data["twoHanded"] = group_key in _TWO_HANDED_GROUPS

    # 2. Extract WFRP qualities / flaws for use in steps 3–5
    quality_names: list[str] = extract_qualities(sys_data)

    # 3. Damage value: parse WFRP formula → TOW integer; min 1, cap 12
    s_bonus: int = int(cfg.get("_actor_s_bonus", 3))
    dmg = sys_data.get("damage")
    if isinstance(dmg, dict):
        raw_formula: str = str(dmg.get("value") if dmg.get("value") is not None else (dmg.get("formula") or ""))
        tow_dmg_val = _parse_damage_formula(raw_formula, s_bonus)
    elif isinstance(dmg, (int, float)) and int(dmg) > 0:
        tow_dmg_val = int(dmg)
    else:
        # No usable damage data — use group-appropriate fallback
        _GROUP_DEFAULT_DMG = {
            "brawling": 3, "parrying": 3, "entangling": 3, "sling": 3, "throwing": 3,
            "basic": 5, "fencing": 5, "cavalry": 5, "flail": 5, "bow": 5,
            "crossbow": 5, "blackpowder": 6, "engineering": 6,
            "polearm": 6, "twohanded": 7,
        }
        tow_dmg_val = _GROUP_DEFAULT_DMG.get(group_key, 5)

    # Cap at 12 (non-magical weapons)
    sys_data["damage"] = {"value": max(1, min(12, tow_dmg_val))}

    # 4. Dice bonus: base from weapon group + 1 for Accurate quality
    base_dice = _WEAPON_GROUP_DICE.get(group_key, 4)
    if "Accurate" in quality_names:
        base_dice += 1
    sys_data["diceBonus"] = base_dice

    # 5. AP (Armour Piercing): highest value from relevant qualities
    ap = max((_QUALITY_AP.get(q, 0) for q in quality_names), default=0)
    sys_data["ap"] = ap

    # 6. Traits: map WFRP qualities → TOW strings; keep the 2 most impactful
    traits: list[str] = []
    for q in quality_names:
        t = _QUALITY_TRAIT.get(q)
        if t is not None and t not in traits:
            traits.append(t)
    sys_data["traits"] = traits[:2]

    # 7. Strip all WFRP-only fields — keep only the TOW weapon schema fields.
    # Any unknown field in the system dict can confuse Foundry's TOW WeaponModel
    # (e.g. WFRP's damageToItem.value=0 has been seen to shadow damage.value).
    _keep_only(sys_data,
        "description",   # {public, gm}
        "skill",         # string key: "melee" | "shooting" | "throwing"
        "twoHanded",     # bool
        "damage",        # {value: int}
        "diceBonus",     # int
        "ap",            # int
        "traits",        # list[str]
    )


def convert_armour(doc: dict, sys_data: dict, cfg: dict, stats: ConversionStats) -> None:
    """
    Convert WFRP armour AP to TOW protection value (in-place).

    Conversion model (per the TOW design doc):
      - Average AP across all covered locations → round to nearest int
      - Shields (detected by item name) add +1 protection and the "parry" trait
      - Result stored as system.protection (integer)
      - Armour items contribute to the parent actor's resilience.modifier
        (see convert_document step 7 for aggregation)

    WFRP AP  → typical TOW Protection
      1–2 (light leather)   → 1
      3–4 (mail / partial)  → 2–3
      5–6 (full plate)      → 3–4  (+ "heavyArmour" trait if applicable)
    """
    ap_data = sys_data.get("AP") or sys_data.get("currentAP") or {}

    if isinstance(ap_data, dict):
        ap_values = [v for v in ap_data.values() if isinstance(v, (int, float))]
        # Average AP across all locations (including 0 for uncovered ones),
        # capped at 4 (TOW's practical maximum for non-magical armour)
        protection = min(4, round(sum(ap_values) / len(ap_values))) if ap_values else None
    elif isinstance(ap_data, (int, float)):
        protection = min(4, round(float(ap_data) / 2))
    else:
        protection = None

    # Shield detection: +1 protection + parry trait
    is_shield = "shield" in doc.get("name", "").lower()

    # If no WFRP AP data was found, fall back in priority order:
    #   1. Existing protection value already in system data (re-processing guard)
    #   2. Name-based inference (partial set of known armour categories)
    #   3. 0 (no protection — at least correct for clothing / unprotected pieces)
    if protection is None:
        existing = sys_data.get("protection")
        if isinstance(existing, (int, float)) and int(existing) > 0:
            protection = int(existing)
        else:
            name_lower = doc.get("name", "").lower()
            if any(k in name_lower for k in ("plate",)):
                protection = 2
            elif any(k in name_lower for k in ("mail", "chain", "brigandine", "scalemail")):
                protection = 2
            elif any(k in name_lower for k in ("leather", "hide", "boiled", "studded")):
                protection = 1
            elif is_shield:
                protection = 0  # shield adds its own bonus below
            else:
                protection = 0

    if is_shield:
        protection += 1

    sys_data["protection"] = max(0, protection)

    # Armour Save tier (TOW abstracted save number)
    # No armour: 7+  Light(1): 6+  Partial(2): 5+  Heavy(3): 4+  Plate(4+): 3+
    _SAVE_TIERS = {0: "7+", 1: "6+", 2: "5+", 3: "4+", 4: "3+"}
    sys_data["armourSave"] = _SAVE_TIERS.get(min(4, sys_data["protection"]), "7+")

    # Append save info to item description so it's visible in Foundry
    desc = sys_data.get("description", {})
    if not isinstance(desc, dict):
        desc = {}
    save_note = f"Armour Save: {sys_data['armourSave']}  |  Protection: +{sys_data['protection']}"
    if is_shield:
        save_note += "  |  Parry"
    desc["public"] = (desc.get("public") or "").rstrip() + ("\n\n" if desc.get("public") else "") + save_note
    sys_data["description"] = desc

    # Traits (max 2)
    quality_names: list[str] = extract_qualities(sys_data)
    traits: list[str] = []
    if is_shield:
        traits.append("parry")
    for q in quality_names:
        t = _QUALITY_TRAIT.get(q)
        if t is not None and t not in traits:
            traits.append(t)
    sys_data["traits"] = traits[:2]

    # Strip all WFRP-only fields — keep only the TOW armour schema fields.
    _keep_only(sys_data,
        "description",   # {public, gm}
        "protection",    # int 0-4
        "armourSave",    # str "7+" … "3+"
        "traits",        # list[str]
    )


def convert_talent(doc: dict, sys_data: dict, cfg: dict, stats: ConversionStats) -> None:
    """
    Flatten WFRP talent fields to TOW talent / ability shape (in-place).

    TOW talents have: name, description, cost, requirement.
    WFRP talents have: max, advances (current/value), tests, description.
    """
    advances = sys_data.get("advances") or {}
    if isinstance(advances, dict):
        current = advances.get("current") or advances.get("value") or 0
        sys_data["cost"] = max(1, int(current))

    # Arcane/Divine talents are better represented as TOW "ability" items
    category = sys_data.get("category") or {}
    category_str = str(category).lower()
    if "arcane" in category_str or "divine" in category_str:
        doc["type"] = "ability"

    # Strip all WFRP-only fields — keep only TOW talent / ability schema
    _keep_only(sys_data, "description", "cost", "requirement")


def convert_trapping(doc: dict, sys_data: dict, cfg: dict, stats: ConversionStats) -> None:
    """
    Map WFRP trapping subtypes to appropriate TOW item types (in-place).

      toolsandmaterials / tool / craft → toolKit
      book / document / scroll        → asset
      all others                      → trapping (unchanged)
    """
    raw_type = sys_data.get("trappingType") or {}
    if isinstance(raw_type, dict):
        subtype = str(raw_type.get("value", "")).lower()
    else:
        subtype = str(raw_type).lower()
    subtype = subtype.replace(" ", "").replace("_", "")

    _TOOLKIT_SUBTYPES = {"toolsandmaterials", "tool", "craft", "trade", "workshop"}
    _ASSET_SUBTYPES   = {"bookormysterioussample", "book", "document", "scroll"}

    if subtype in _TOOLKIT_SUBTYPES:
        doc["type"] = "toolKit"
    elif subtype in _ASSET_SUBTYPES:
        doc["type"] = "asset"
    # else: keep "trapping"

    # Strip all WFRP-only fields — keep only TOW trapping / asset schema
    _keep_only(sys_data, "description")


# ─────────────────────────────────────────────────────────────────────────────
# SPELL CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

def _cn_to_cv(cn: int) -> int:
    """Map WFRP Casting Number to TOW Casting Value."""
    if cn <= 0:   return 3
    if cn <= 2:   return 4
    if cn <= 4:   return 5
    if cn <= 6:   return 6
    if cn <= 8:   return 7
    if cn <= 10:  return 8
    if cn <= 12:  return 9
    return 10


_WFRP_LORE_TO_TOW: dict[str, str] = {
    "petty":       "Arcane (Petty)",
    "fire":        "Elementalism (Fire)",
    "light":       "High Magic",
    "heavens":     "Battle Magic",
    "metal":       "Battle Magic",
    "death":       "Necromancy",
    "shadows":     "Illusion",
    "life":        "High Magic",
    "beasts":      "Elementalism",
    "witchcraft":  "Dark Magic",
    "hedgecraft":  "Arcane (Improvised)",
    "necromancy":  "Necromancy",
    "daemonology": "Dark Magic",
    "nurgle":      "Dark Magic",
    "tzeentch":    "Dark Magic",
    "slaanesh":    "Dark Magic",
    "khorne":      "Dark Magic",
    "undivided":   "Dark Magic",
}


# Named spell library:  base_name_lowercase → (tow_name, cv_override, lore_override, tow_description)
# base_name: spell name with any trailing lore tag stripped, e.g. "Bolt (Fire)" → "bolt".
# cv_override=0  → derive from CN.
# lore_override="" → derive from WFRP lore.
# tow_description: 1–2 sentences in pure TOW language.
_WFRP_SPELL_LIBRARY: dict[str, tuple[str, int, str, str]] = {
    "aethyric armour": (
        "Aethyric Armour", 0, "",
        "The caster is wrapped in a sheath of magical force; their Armour Save improves by one step "
        "(minimum 6+) until the start of their next turn.",
    ),
    "aethyric arms": (
        "Aethyric Arms", 0, "Battle Magic",
        "The caster conjures a magical melee weapon whose damage equals their Willpower skill base; "
        "it counts as magical and lasts until the end of combat.",
    ),
    "animal friend": (
        "Animal Friend", 0, "Arcane (Petty)",
        "One non-hostile Bestial creature of Minion size within Close range becomes friendly for one "
        "scene and will not attack the caster or their allies.",
    ),
    "aqshy's aegis": (
        "Aqshy's Aegis", 0, "Elementalism (Fire)",
        "The caster is immune to non-magical fire and heat and gains +1 die to Defence when targeted "
        "by fire attacks; lasts until end of combat.",
    ),
    "banishment": (
        "Banishment", 0, "High Magic",
        "A cleansing pulse of holy light bursts outward from the caster. Every Undead or Daemonic "
        "creature within Close range with lower Toughness than the caster's Willpower must pass an "
        "exacting Willpower test or be immediately destroyed.",
    ),
    "blast": (
        "Blast", 0, "",
        "The caster channels magic into an explosive burst centred within Short range. Every target in "
        "a Small area must pass an exacting Willpower test or suffer 1 Wound.",
    ),
    "blight": (
        "Blight", 0, "Dark Magic",
        "The caster lays a dark curse on a target within Short range, rendering it withered and "
        "useless. In combat, one target is Staggered for the rest of the encounter.",
    ),
    "blinding light": (
        "Blinding Light", 0, "High Magic",
        "The caster emits a searing flash of holy light. Every enemy within Close range facing the "
        "caster must pass an exacting Willpower test or gain the Staggered condition.",
    ),
    "bolt": (
        "Bolt", 0, "",
        "The caster hurls a focused bolt of magical energy at one target within Short range. The "
        "target must pass an exacting Willpower test or suffer 1 Wound; the attack is magical.",
    ),
    "breath": (
        "Breath", 0, "Elementalism (Fire)",
        "The caster exhales a torrent of magical fire in a cone reaching Short range. Every target "
        "in the cone must pass an exacting Willpower test or suffer 1 Wound with the Burning trait.",
    ),
    "cauterise": (
        "Cauterise", 0, "Elementalism (Fire)",
        "The caster channels searing heat through their hands to close wounds on a willing ally within "
        "Close range, removing 1 Wound and all Bleeding conditions.",
    ),
    "chain attack": (
        "Chain Attack", 0, "Elementalism (Fire)",
        "The caster launches a spur of twisting magical fire at one target within Short range; the "
        "target must pass an exacting Willpower test or suffer 1 Wound, and if defeated the attack "
        "leaps to the nearest enemy within Close range.",
    ),
    "clarity of thought": (
        "Clarity of Thought", 0, "High Magic",
        "All negative conditions affecting the mental focus of one willing target within Close range "
        "are instantly removed.",
    ),
    "creeping menace": (
        "Creeping Menace", 0, "Dark Magic",
        "The caster summons a swarm of vermin within Close range that immediately engages one target "
        "as a Minion; the swarm lasts until destroyed or the end of combat.",
    ),
    "crown of flame": (
        "Crown of Flame", 0, "Elementalism (Fire)",
        "A crown of magical fire settles on the caster's brow, making them Fearsome; all allies "
        "within Close range gain +1 die on Willpower tests to resist fear until the start of the "
        "caster's next turn.",
    ),
    "curse of crippling pain": (
        "Curse of Crippling Pain", 0, "Dark Magic",
        "One target within Short range is overcome with supernatural agony: it suffers \u22122 dice on "
        "all tests and gains the Staggered condition until it passes Willpower at the start of its turn.",
    ),
    "curse of ill-fortune": (
        "Curse of Ill-Fortune", 0, "Dark Magic",
        "One target within Short range is cursed with misfortune; it suffers \u22121 die on all tests "
        "and cannot spend Fortune until the Staggered condition is removed.",
    ),
    "dart": (
        "Dart", 0, "Arcane (Petty)",
        "The caster fires a dart of pure magical energy at one target within Short range; the target "
        "must pass an exacting Willpower test or suffer 1 Wound, and the attack ignores mundane cover.",
    ),
    "dazzle": (
        "Dazzle", 0, "Arcane (Petty)",
        "One target within Close range gains the Staggered condition; at the start of each of its "
        "turns it must pass Willpower or remain Staggered.",
    ),
    "dome": (
        "Dome", 0, "Elementalism (Fire)",
        "A dome of magical energy forms over the caster's position. All allies within a Small area "
        "gain +1 die on Defence tests against ranged and magical attacks until the start of the "
        "caster's next turn.",
    ),
    "drain": (
        "Drain", 0, "Arcane (Petty)",
        "The caster touches a target within Close range; the target must pass an exacting Willpower "
        "test or suffer 1 Wound (ignoring Armour Save), and the caster removes one Staggered "
        "condition from themselves.",
    ),
    "drop": (
        "Drop", 0, "Elementalism (Fire)",
        "One target within Short range must pass Agility or immediately drop whatever they are holding.",
    ),
    "eavesdrop": (
        "Eavesdrop", 0, "Arcane (Petty)",
        "The caster can hear everything said by one target within Short range as if standing next "
        "to them, for the rest of the scene.",
    ),
    "fearsome": (
        "Fearsome Aura", 0, "",
        "The caster takes on a fearsome magical presence for the rest of combat. Any enemy that ends "
        "its turn within Close range must pass an exacting Willpower test or be Staggered.",
    ),
    "firewall": (
        "Firewall", 0, "Elementalism (Fire)",
        "The caster conjures a wall of magical fire up to Short range wide. Any creature that passes "
        "through it must pass an exacting Willpower test or suffer 1 Wound with the Burning trait; "
        "the wall lasts until end of combat.",
    ),
    "flaming hearts": (
        "Flaming Hearts", 0, "Elementalism (Fire)",
        "Aqshy's passion ignites the hearts of all allies within Close range: they lose all Staggered "
        "and negative conditions and gain +1 die on Melee tests until the end of their next turn.",
    ),
    "flaming sword of rhuin": (
        "Flaming Sword of Rhuin", 0, "Elementalism (Fire)",
        "One weapon within Close range is wreathed in magical flame; it deals +2 damage and gains "
        "the Burning trait, and any target struck gains the Staggered condition. Lasts until end of "
        "combat.",
    ),
    "goodwill": (
        "Goodwill", 0, "Arcane (Improvised)",
        "The caster creates an aura of warmth and friendliness within Close range: all social tests "
        "gain +1 die and Hatred abilities do not trigger until the start of the caster's next turn.",
    ),
    "great fires of u'zhul": (
        "Great Fires of U'Zhul", 0, "Elementalism (Fire)",
        "The caster hurls a massive fireball into a Large area within Short range. Every target must "
        "pass an exacting Willpower test or suffer 2 Wounds with the Burning trait; Armour Save has "
        "no effect against this spell.",
    ),
    "haunting horror": (
        "Haunting Horror", 0, "Dark Magic",
        "Supernatural dread grips every creature within Close range: each gains the Staggered "
        "condition and must pass an exacting Willpower test or Give Ground immediately.",
    ),
    "light": (
        "Light", 0, "Arcane (Petty)",
        "The caster conjures a globe of magical light equal to torchlight; it is anchored to their "
        "person and lasts for the rest of the scene, and can be dismissed as a free action.",
    ),
    "magic flame": (
        "Magic Flame", 0, "Arcane (Petty)",
        "The caster creates a small, harmless flame in their palm that lasts for the rest of the "
        "scene and can ignite flammable objects on contact.",
    ),
    "murmured whisper": (
        "Murmured Whisper", 0, "Arcane (Petty)",
        "The caster projects their voice to any point within Short range for one round; all within "
        "earshot of that point hear it as if the caster were standing there.",
    ),
    "net of amyntok": (
        "Net of Amyntok", 0, "High Magic",
        "One target within Short range is overwhelmed with insoluble mental puzzles: it gains the "
        "Staggered condition and loses its next action.",
    ),
    "ph\u00e2's protection": (
        "Ph\u00e2's Protection", 0, "High Magic",
        "A protective aura of holy light surrounds the caster's position. All allies within Close "
        "range gain +1 die to Defence and their Armour Save improves one step against Undead and "
        "Daemonic attacks until the start of the caster's next turn.",
    ),
    "produce small animal": (
        "Produce Small Animal", 0, "Arcane (Petty)",
        "The caster produces a small, harmless creature from a bag or nearby hiding spot. Narrative "
        "use only; no combat application.",
    ),
    "purge": (
        "Purge", 0, "Elementalism (Fire)",
        "Intense magical flame scorches a Large area within Short range. Every creature in the area "
        "must pass an exacting Willpower test or gain the Staggered condition and suffer 1 Wound "
        "with the Burning trait.",
    ),
    "purify water": (
        "Purify Water", 0, "Arcane (Petty)",
        "The caster removes all non-magical contaminants from one container of liquid within Close "
        "range. Narrative use only; no combat application.",
    ),
    "rot": (
        "Rot", 0, "Arcane (Petty)",
        "The caster causes a fist-sized volume of organic material to instantly decay. In combat, "
        "one target's Armour Save is reduced by one step for the rest of the encounter.",
    ),
    "shock": (
        "Shock", 0, "Arcane (Petty)",
        "One target within Close range must pass Toughness or gain the Staggered condition.",
    ),
    "speed of thought": (
        "Speed of Thought", 0, "High Magic",
        "A lattice of High Magic overlays the caster's mind: they gain +2 dice on Awareness and "
        "Recall tests and act first each Round for the rest of the combat.",
    ),
    "spring": (
        "Spring", 0, "Arcane (Petty)",
        "The caster causes fresh water to well up from the ground at their feet. Narrative use only; "
        "no combat application.",
    ),
    "teleport": (
        "Teleport", 0, "Arcane (Improvised)",
        "The caster instantly moves up to a Short range in any direction, ignoring terrain and "
        "obstacles; they cannot teleport into an occupied space.",
    ),
    "terrifying": (
        "Terrifying Aura", 0, "",
        "The caster emanates pure supernatural terror for the rest of combat. Enemies within Close "
        "range suffer \u22122 dice on Willpower tests and must pass an exacting Willpower test or "
        "Give Ground immediately.",
    ),
    "the evil eye": (
        "The Evil Eye", 0, "Dark Magic",
        "The caster locks eyes with one target within Close range; it must pass an exacting Willpower "
        "test or gain the Staggered condition — on a critical failure it Gives Ground instead.",
    ),
    "ward": (
        "Ward", 0, "",
        "The caster wraps themselves in a magical ward for the rest of combat. Any attack that would "
        "inflict a Wound may be negated: roll 1d6 — on a 5+ the Wound is ignored.",
    ),
}


def _strip_wfrp_html(html: str) -> str:
    """
    Strip HTML tags and WFRP-specific markup from a spell description, returning
    plain text suitable for keyword classification.
    """
    import re as _re
    text = _re.sub(r"<[^>]+>", " ", html)
    # Strip the Lore flavour block appended after the spell text
    text = _re.sub(r"(?i)Lore\s*:.*", "", text, flags=_re.DOTALL)
    # Expand @Condition[X]{label} / @UUID[...]{label} to their label text
    text = _re.sub(r"@\w+\[[^\]]+\]\{([^}]+)\}", r"\1", text)
    # Remove any remaining bare @references
    text = _re.sub(r"@\w+\[[^\]]+\]", "", text)
    for entity, char in (
        ("&mdash;", "\u2014"), ("&ndash;", "\u2013"), ("&rsquo;", "\u2019"),
        ("&amp;", "&"), ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
    ):
        text = text.replace(entity, char)
    return _re.sub(r"\s+", " ", text).strip()


def _classify_spell_effect(sys_data: dict, plain_desc: str) -> str:
    """Return one of: area_damage | damage | summon | healing | control | buff | debuff | utility"""
    missile = bool((sys_data.get("magicMissile") or {}).get("value", False))
    aoe     = bool((sys_data.get("target")       or {}).get("aoe",   False))
    dmg_val = str( (sys_data.get("damage")        or {}).get("value", "") or "")
    tgt_v   = str( (sys_data.get("target")        or {}).get("value", "") or "").lower()
    desc_l  = plain_desc.lower()

    if missile and aoe:
        return "area_damage"
    if missile or dmg_val:
        return "damage"
    if "summon" in desc_l or ("create" in desc_l and ("creature" in desc_l or "swarm" in desc_l)):
        return "summon"
    if ("heal" in desc_l or "remove" in desc_l) and "wound" in desc_l:
        return "healing"
    if any(w in desc_l for w in ("blinded", "stunned", "staggered", "paralyz")):
        return "control"
    if tgt_v in ("you", "self") or "you gain" in desc_l or "yourself" in desc_l:
        return "buff"
    if "wound" in desc_l and any(w in desc_l for w in ("inflict", "suffer", "damage")):
        return "damage"
    if any(w in desc_l for w in ("penalty", "suffers", "\u2013", "-1")):
        return "debuff"
    return "utility"


def convert_spell(doc: dict, sys_data: dict, cfg: dict, stats: ConversionStats) -> None:
    """Convert a WFRP4e spell item to TOW format (in-place)."""
    import re as _re

    # ── 1. Extract WFRP fields ─────────────────────────────────────────────
    cn        = int((sys_data.get("cn")          or {}).get("value", 0) or 0)
    lore_raw  = (sys_data.get("lore")            or {}).get("value", [])
    wfrp_lore = lore_raw[0].lower() if lore_raw else ""
    orig_desc = (sys_data.get("description")     or {}).get("public", "")

    # ── 2. Derive TOW Casting Value and Lore ──────────────────────────────
    cv       = _cn_to_cv(cn)
    tow_lore = _WFRP_LORE_TO_TOW.get(wfrp_lore, "Battle Magic")

    # ── 3. Named spell lookup ─────────────────────────────────────────────
    # Strip trailing lore-variant tag: "Bolt (Fire)" → "bolt"
    # Strip trailing lore tag and normalise typographic apostrophes before lookup
    base_name = _re.sub(r"\s*\([^)]+\)\s*$", "", doc.get("name", "")).strip().lower()
    base_name = base_name.replace("\u2019", "'").replace("\u2018", "'")
    entry = _WFRP_SPELL_LIBRARY.get(base_name)

    if entry:
        _tow_name, cv_override, lore_override, tow_desc = entry
        if cv_override:
            cv = cv_override
        if lore_override:
            tow_lore = lore_override
    else:
        # ── 4. Fallback: classify effect and generate description ──────────
        plain = _strip_wfrp_html(orig_desc)
        effect = _classify_spell_effect(sys_data, plain)

        _RANGE_MAP = {
            "touch": "Close range", "you": "self",
            "short": "Short range", "medium": "Medium range",
            "long": "Long range",   "extended": "Long range",
        }
        rng_raw  = str((sys_data.get("range") or {}).get("value", "") or "").lower()
        rng_band = next((v for k, v in _RANGE_MAP.items() if k in rng_raw), "Short range")

        _EFFECT_TEMPLATES: dict[str, str] = {
            "area_damage": (
                f"The caster unleashes a burst of magical energy in a Small area within {rng_band}. "
                "Every target must pass an exacting Willpower test or suffer 1 Wound."
            ),
            "damage": (
                f"The caster launches a magical attack at one target within {rng_band}. "
                "The target must pass an exacting Willpower test or suffer 1 Wound; the attack is magical."
            ),
            "summon": (
                f"The caster summons a supernatural presence within {rng_band}. "
                "One Minion appears and acts on the caster's initiative; it lasts until destroyed or end of scene."
            ),
            "healing": (
                "The caster channels restorative magic into a willing target within Close range, "
                "removing 1 Wound and one negative condition."
            ),
            "control": (
                f"One target within {rng_band} must pass an exacting Willpower test or gain the "
                "Staggered condition until it passes Willpower at the start of its turn."
            ),
            "buff": (
                "The caster is bolstered by magical energy for the rest of combat. "
                "They gain +1 die on one chosen skill test each round."
            ),
            "debuff": (
                f"One target within {rng_band} is afflicted by a magical curse: it suffers \u22121 die "
                "on all tests and gains the Staggered condition until it passes Willpower."
            ),
            "utility": (
                f"The caster works a subtle magical effect within {rng_band}. "
                + ((plain[:120] + "\u2026") if len(plain) > 120 else plain)
            ),
        }
        tow_desc = _EFFECT_TEMPLATES.get(effect, _EFFECT_TEMPLATES["utility"])

    # ── 5. Write TOW fields and strip all WFRP keys ───────────────────────
    sys_data["description"]  = {"public": tow_desc, "gm": ""}
    sys_data["castingValue"] = cv
    sys_data["lore"]         = tow_lore
    _keep_only(sys_data, "description", "castingValue", "lore")


# ─────────────────────────────────────────────────────────────────────────────
# JOURNAL CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

# Ordered list of (pattern, replacement) substitutions applied to the HTML
# content of every JournalEntry page of type "text".
#
# Rules:
#   - Process most-specific patterns first to avoid double-substitution.
#   - Never modify HTML tags themselves (only text nodes / attribute values).
#   - Preserve Foundry same-world @UUID[JournalEntry...] links intact.
#   - Strip wfrp4e-compendium @UUID links; keep only display label text.
#   - Translate WFRP4e mechanics into TOW equivalents per the spec above.
_JOURNAL_RAW_SUBS: list[tuple[str, str]] = [

    # ── Foundry link sanitisation ─────────────────────────────────────────
    # wfrp4e compendium refs don't exist in TOW worlds — keep only the label
    (r"@UUID\[Compendium\.wfrp4e[^\]]*\]\{([^}]+)\}", r"\1"),
    # @Embed[...]{label} → label
    (r"@Embed\[[^\]]*\]\{([^}]+)\}", r"\1"),
    # @Condition[X]{label} → label
    (r"@Condition\[[^\]]*\]\{([^}]+)\}", r"\1"),
    # @Table / @RollTable with label → label
    (r"@(?:Table|RollTable)\[[^\]]*\]\{([^}]+)\}", r"\1"),
    # Bare @Condition / @Table references with no label → strip
    (r"@(?:Condition|Table|RollTable|Embed)\[[^\]]*\]", ""),
    # Inline dice roll markup [[/r 1d10]] or [[/roll 2d6+3]] → the formula
    (r"\[\[/r(?:oll)?\s+([^\]]+)\]\]", r"\1"),

    # ── WFRP difficulty labels → TOW plain language ───────────────────────
    (r"\bEasy\s*\(\+40\)", "easy"),
    (r"\bAverage\s*\(\+20\)", "routine"),
    (r"\bChallenging\s*\(\+0\)", "standard"),
    (r"\bDifficult\s*\(-10\)", "challenging"),
    (r"\bHard\s*\(-20\)", "difficult"),
    (r"\bVery Hard\s*\(-30\)", "very difficult"),

    # ── Core mechanic translations ────────────────────────────────────────
    (r"\bSuccess Levels?\b", "dice pool successes"),
    # "N SL" / "+N SL" → "N dice successes"
    (r"([+\-]?\d+)\s+SL\b", r"\1 dice successes"),
    # Bare SL abbreviation (not part of a word)
    (r"\bSL\b", "dice successes"),
    (r"\bCasting Number\b", "Casting Value"),
    (r"\bCritically Wounds?\b", "inflicts an Injury on"),
    (r"\bCritical (?:Hit|Wound)s?\b", "Injury Table result"),
    (r"\bCritical Tables?\b", "Injury Table"),
    (r"\bArmour Points?\b", "Protection"),
    # AP when used as a standalone abbreviation (word-boundary safe)
    (r"\bAPs?\b", "Protection"),

    # ── WFRP conditions → nearest TOW equivalents ─────────────────────────
    # (WFRP uses named Conditions as a resource; TOW uses Staggered / Give
    # Ground / Broken. We map to the closest functional analogue.)
    (r"\bBlinded Conditions?\b", "Staggered condition"),
    (r"\bStunned Conditions?\b", "Staggered condition"),
    (r"\bFatigued Conditions?\b", "Staggered condition"),
    (r"\bEntangled Conditions?\b", "Staggered condition"),
    (r"\bDeafened Conditions?\b", "Staggered condition"),
    (r"\bInfected Conditions?\b", "Staggered condition"),
    (r"\bPoisoned Conditions?\b", "Staggered condition"),
    (r"\bSuffocating Conditions?\b", "Staggered condition"),
    (r"\bAblaze Conditions?\b", "Burning condition"),
    (r"\bBleeding Conditions?\b", "Bleeding condition"),
    (r"\bBroken Conditions?\b", "Broken condition"),
    (r"\bProne Conditions?\b", "Give Ground effect"),
    (r"\bWounded Conditions?\b", "Injured condition"),

    # ── Chaos / Corruption ────────────────────────────────────────────────
    (r"Tzeentch\u2019s\s+Curse", "curse of the Ruinous Powers"),
    (r"Tzeentch's\s+Curse", "curse of the Ruinous Powers"),
    (r"\bCorruption (?:roll|table)\b", "Corruption test"),
    (r"\bMutation (?:roll|table|results?)\b", "Corruption effect"),
    (r"\bChaos Mutations?\b", "Chaos mutations (see Corruption rules)"),

    # ── Percentages ───────────────────────────────────────────────────────
    # Isolated stat percentages like "WS 45%" → strip (stat blocks gone)
    (r"\b(?:WS|BS|Str|T|I|Ag|Dex|Int|WP|Fel)\s+\d{1,3}%", ""),
    # (Percentage-to-fraction conversion is handled by _JOURNAL_CALLABLE_PATTERNS
    # below, which applies math division; bare % strings are left for that pass.)

    # ── Wound pool language ───────────────────────────────────────────────
    (r"\bFull Wounds?\b", "maximum Resilience"),
    (r"\bWounds? Pool\b", "Resilience"),

    # ── TOW range bands (WFRP uses yards; nudge phrasing to bands) ────────
    # Only applies when "yards" follows a WP/characteristic bonus expression
    (r"\bWillpower Bonus yards?\b", "Short range"),
    (r"\bInitiative Bonus yards?\b", "Close range"),

    # ── Common WFRP career / advance jargon ──────────────────────────────
    (r"\bAdvance Scheme\b", "progression"),
    (r"\bCareer (?:Path|Level)\b", "background"),
]

# Compiled once at import time
import re as _re_module
_JOURNAL_COMPILED_SUBS: list[tuple[object, str]] = [
    (_re_module.compile(pat, _re_module.IGNORECASE), repl)
    for pat, repl in _JOURNAL_RAW_SUBS
]

# Callable substitutions that need math (percentage → fraction).
# Applied AFTER _JOURNAL_COMPILED_SUBS in _rewrite_journal_html.


def _pct_to_fraction_chance(m: object) -> str:
    pct = int(m.group(1))  # type: ignore[attr-defined]
    n = max(1, round(pct / 10))
    return f"a {n}-in-10 chance"


def _pct_to_fraction(m: object) -> str:
    pct = int(m.group(1))  # type: ignore[attr-defined]
    n = max(1, round(pct / 10))
    return f"{n}-in-10"


_JOURNAL_CALLABLE_PATTERNS: list[tuple[object, object]] = [
    # "N% chance" → "a K-in-10 chance" (applied first before bare %)
    (_re_module.compile(r"\b(\d{1,3})%\s+chance\b", _re_module.IGNORECASE), _pct_to_fraction_chance),
    # Bare "N%" → "K-in-10"  (trailing \b omitted — % is non-word so \b never fires after it)
    (_re_module.compile(r"\b(\d{1,3})%",             _re_module.IGNORECASE), _pct_to_fraction),
]


def _rewrite_journal_html(html: str) -> str:
    """
    Apply TOW terminology substitutions to a journal page's HTML content.

    The approach is a conservative text-pass that avoids disturbing HTML
    structure:
      1. Split the HTML into tag / non-tag segments on '<' boundaries.
      2. Apply substitutions only to non-tag segments.
      3. Reassemble.
    This preserves all HTML tags, attributes (including class="secret"),
    and Foundry same-world @UUID[JournalEntry...] links.
    """
    # Split into alternating [text, tag, text, tag, ...] chunks.
    # Each even-index chunk is outside tags; odd-index chunks are inside '<...>'.
    parts = _re_module.split(r"(<[^>]+>)", html)
    out_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Inside an HTML tag — pass through unchanged
            out_parts.append(part)
        else:
            # Text node — apply all substitutions
            for pattern, repl in _JOURNAL_COMPILED_SUBS:
                part = pattern.sub(repl, part)
            for pattern, repl in _JOURNAL_CALLABLE_PATTERNS:
                part = pattern.sub(repl, part)
            out_parts.append(part)
    return "".join(out_parts)


# Journal type classification based on page name keywords
_JOURNAL_GM_KEYWORDS = frozenset({
    "gm", "gamemaster", "game master", "gm notes", "gm only",
    "secret", "hidden", "backstory", "hook", "plot",
})


def convert_journal(doc: dict, stats: ConversionStats) -> None:
    """
    Rewrite WFRP4e journal page content to TOW-compatible text (in-place).

    Foundry JournalEntry documents contain a `pages` array. Each page has:
      - type: "text" | "image" | "pdf" | "video" | "map"
      - text.content: HTML string (for type="text" pages)
      - name: page title

    This function:
      1. Rewrites text content using TOW terminology substitutions.
      2. Updates the systemId in _stats.
      3. Preserves all structural fields, images, links, and secret blocks.
      4. Adds a brief GM annotation to pages that look like GM-only content.
    """
    import re as _re

    # Update system ID
    if "_stats" in doc and isinstance(doc["_stats"], dict):
        doc["_stats"]["systemId"] = "whtow"

    pages = doc.get("pages")
    if not isinstance(pages, list):
        return

    for page in pages:
        if not isinstance(page, dict):
            continue
        if page.get("type") != "text":
            continue  # images, PDFs, maps — no text to rewrite

        text_block = page.get("text")
        if not isinstance(text_block, dict):
            continue

        content = text_block.get("content", "") or ""
        if not content:
            continue

        rewritten = _rewrite_journal_html(content)

        # If this looks like a GM-only page and it has no existing TOW hook
        # annotation, prepend a small GM note in the existing style
        page_name_lower = page.get("name", "").lower()
        is_gm_page = (
            any(kw in page_name_lower for kw in _JOURNAL_GM_KEYWORDS)
            or bool(_re.search(r'class=["\']secret["\']', content))
        )
        if is_gm_page and "[TOW]" not in rewritten[:200]:
            gm_note = (
                '<p><em>[TOW GM Note: Review any remaining mechanical references '
                'below and link to the converted Actor/Item sheets. '
                'Consider whether this content should become a Grim Portent '
                'or Dark Thread.]</em></p>'
            )
            rewritten = gm_note + rewritten

        text_block["content"] = rewritten

    stats.journals_converted += 1


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DOCUMENT CONVERTER
# ─────────────────────────────────────────────────────────────────────────────

# WFRP actor types that map to TOW actor type "npc" (i.e., use NPCModel)
_NPC_ACTOR_TYPES = {"npc", "creature"}

def convert_document(
    doc: Any,
    cfg: dict,
    stats: ConversionStats,
    _depth: int = 0,
) -> Any:
    """
    Convert a single WFRP4e Foundry document to TOW format.

    Top-level actors (depth=0) get a full system rebuild:
      - Characteristics: remap keys, keep d10-scale initial as `base`
      - Skills: extracted from embedded skill items → system.skills dict
      - Speed: WFRP move → TOW speed string
      - NPC sub-type: default "minion"
      - Description: migrated from WFRP details.biography
      - Embedded items: WFRP item types remapped; skill/money/career items removed

    Embedded items (depth>0) get type remapping and item-specific field conversion.
    """
    if not isinstance(doc, dict):
        return doc

    # ── 0. Journal Entry (early return) ──────────────────────────────────────
    # Journals are identified by a top-level `pages` list. They have no
    # `system` data in the WFRP4e sense — only their text content needs
    # rewriting. Delegate immediately so the actor/item path never touches them.
    if isinstance(doc.get("pages"), list):
        convert_journal(doc, stats)
        if _depth == 0:
            stats.documents_converted += 1
        return doc

    # ── 1. System ID ─────────────────────────────────────────────────────────
    if "_stats" in doc and isinstance(doc["_stats"], dict):
        doc["_stats"]["systemId"] = "whtow"

    actor_type: str = doc.get("type", "")
    is_actor = actor_type in WFRP_TO_TOW_ACTOR_TYPE
    # Ensure doc["system"] is always a real dict so sys_data is a live reference.
    # If it's None or missing, writes to sys_data would otherwise be lost.
    if not isinstance(doc.get("system"), dict):
        doc["system"] = {}
    sys_data: dict = doc["system"]

    # ── 2. Actor type remapping ───────────────────────────────────────────────
    if is_actor:
        doc["type"] = WFRP_TO_TOW_ACTOR_TYPE.get(actor_type, actor_type)

    # ── 3. Characteristics (actors only) ─────────────────────────────────────
    if is_actor:
        wfrp_chars = sys_data.get("characteristics")
        if isinstance(wfrp_chars, dict):
            sys_data["characteristics"] = build_tow_characteristics(wfrp_chars, stats)

    # ── 4. Active Effects: drop all — WFRP effect keys don't exist in TOW ───
    if "effects" in doc:
        doc["effects"] = []

    # ── 4b. Read WFRP size AND wounds BEFORE details/status is stripped ────────
    _wfrp_size          = ""
    _wfrp_wounds        = 0
    _existing_subtype   = ""    # TOW subtype already in data (re-processing guard)
    _existing_resilience = 0   # TOW resilience.value already in data
    if is_actor:
        size_raw = (sys_data.get("details") or {}).get("size", {})
        _wfrp_size = str(
            size_raw.get("value", "") if isinstance(size_raw, dict) else size_raw
        ).lower()
        wounds_raw = (sys_data.get("status") or {}).get("wounds", {})
        if isinstance(wounds_raw, dict):
            _wfrp_wounds = int(wounds_raw.get("max", wounds_raw.get("value", 0)) or 0)
        elif isinstance(wounds_raw, (int, float)):
            _wfrp_wounds = int(wounds_raw)

        # Fallback when no WFRP status block (data is already in TOW format):
        # preserve whatever classification and wound count are already stored.
        if _wfrp_wounds == 0:
            _existing_subtype    = str(sys_data.get("type", ""))
            _existing_resilience = int(
                (sys_data.get("resilience") or {}).get("value", 0) or 0
            )

    # ── 5. Embedded items: type-remap, filter abilities/talents, extract skills ─
    raw_items: list = doc.get("items") or []
    _is_npc_actor = is_actor and actor_type in _NPC_ACTOR_TYPES
    if isinstance(raw_items, list):
        kept_items: list = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            wfrp_item_type = item.get("type", "")
            tow_item_type = WFRP_TO_TOW_ITEM_TYPE.get(wfrp_item_type, wfrp_item_type)
            if tow_item_type is None:
                # Removed types: skill, money, career, extendedTest, etc.
                continue
            item["type"] = tow_item_type

            # ── Ability / talent whitelist (NPC actors only) ────────────────
            # For NPCs: both ability-typed items (from trait/psychology/mutation)
            # AND talent items are filtered through _WFRP_ABILITY_LIBRARY.
            # Items with a library entry → become "ability" with TOW description.
            # Items with no entry (or explicitly None) → dropped entirely.
            # For non-NPC actors: talent items pass through unchanged.
            if _is_npc_actor and tow_item_type in ("ability", "talent"):
                trait_name: str = item.get("name", "")
                # Strip parenthetical spec, e.g. "Fear (2)" → "Fear"; "(Fire)" kept
                base_name = trait_name.split("(")[0].strip()
                entry = _WFRP_ABILITY_LIBRARY.get(base_name)
                if entry is None:
                    log.debug("Dropping unmapped ability/talent '%s'", trait_name)
                    continue
                tow_name, tow_desc = entry
                if tow_name is None:
                    log.debug("Dropping explicitly excluded ability '%s'", trait_name)
                    continue
                # Always convert to "ability" type
                item["type"] = "ability"
                # Keep parenthetical spec for certain ability types (e.g. Immunity (Fire))
                spec = trait_name[len(base_name):].strip()
                if base_name in _ABILITY_KEEP_SPEC and spec:
                    item["name"] = f"{tow_name} {spec}"
                else:
                    item["name"] = tow_name
                # Write TOW description now — prevents WFRP text migration in §8
                item.setdefault("system", {})["description"] = {
                    "public": tow_desc,
                    "gm": "",
                }
                # Strip all WFRP system fields now — only keep description.
                # This avoids leftover WFRP fields confusing Foundry's ability model.
                _keep_only(item["system"], "description")

            # Recurse to convert item-level fields
            item = convert_document(item, cfg, stats, _depth + 1)
            kept_items.append(item)

        # Deduplicate abilities: when multiple WFRP talents/traits map to the same
        # TOW ability name+spec, keep only the first occurrence.
        if _is_npc_actor:
            seen_abilities: set = set()
            deduped: list = []
            for item in kept_items:
                if item.get("type") == "ability":
                    key = item.get("name", "")
                    if key in seen_abilities:
                        log.debug("Deduplicating ability '%s'", key)
                        continue
                    seen_abilities.add(key)
                deduped.append(item)
            kept_items = deduped

        doc["items"] = kept_items
        if _depth == 0:
            stats.items_converted += len(kept_items)

    # ── 6. Extract skills from original items (before they were filtered) ────
    if is_actor and actor_type in _NPC_ACTOR_TYPES:
        # sys_data["characteristics"] is already in TOW format {base, modifier}.
        # Use base values for skill total calculation.
        tow_chars_for_skills: dict = {}
        for tow_key, char in (sys_data.get("characteristics") or {}).items():
            if isinstance(char, dict):
                tow_chars_for_skills[tow_key] = {"initial": char.get("base", 3)}
        # Also need WFRP key aliases (wp→re) for characteristic lookup in skill items
        # WFRP skill items store char_key as the WFRP key (e.g. "wp", not "re")
        for wfrp_key, tow_key in WFRP_TO_TOW_CHAR.items():
            if tow_key and tow_key in tow_chars_for_skills:
                tow_chars_for_skills[wfrp_key] = tow_chars_for_skills[tow_key]

        # ── Build a full 16-skill set ──────────────────────────────────────
        # Priority (lowest → highest):
        #   1. Characteristic-derived defaults for every TOW skill (fills gaps)
        #   2. Existing skills already on the actor (preserves re-processing)
        #   3. Skills extracted from embedded WFRP skill items (most specific)

        # Step 1: defaults from converted characteristics
        merged_skills: dict = {}
        for tow_skill_key, char_key in TOW_SKILL_CHAR.items():
            char = (sys_data.get("characteristics") or {}).get(char_key, {})
            char_base = char.get("base", 3) if isinstance(char, dict) else 3
            merged_skills[tow_skill_key] = {
                "base": char_base,
                "modifier": 0,
                "characteristic": char_key,
            }

        # Step 2: overlay existing TOW-format skills
        for skill_key, skill_val in (sys_data.get("skills") or {}).items():
            if isinstance(skill_val, dict) and skill_key in TOW_SKILL_CHAR:
                merged_skills[skill_key] = skill_val

        # Step 3: overlay skills extracted from WFRP embedded skill items
        extracted_skills = extract_tow_skills(raw_items, tow_chars_for_skills, cfg, stats)
        merged_skills.update(extracted_skills)

        sys_data["skills"] = merged_skills

        # Stash a fixed SB constant for weapon damage formula resolution.
        # TOW weapon damage values are calibrated to an average S bonus of ~2
        # (e.g. hand weapon "SB+4" → Damage 6 in TOW, not the actor's real S).
        # Using the actor's actual S inflates damage values significantly.
        cfg["_actor_s_bonus"] = 2

    # ── 7. Actor-level TOW system fields ────────────────────────────────────
    if is_actor:
        tow_actor_type = doc["type"]

        # Speed: WFRP details.move.value → TOW speed.value string
        # Only write speed when we actually have a WFRP move value; otherwise
        # preserve whatever is already on the actor (re-processing guard).
        move_raw = (sys_data.get("details") or {}).get("move", {})
        if isinstance(move_raw, dict):
            move_val = move_raw.get("value")
        elif isinstance(move_raw, (int, float)):
            move_val = move_raw
        else:
            move_val = None
        if move_val is not None:
            sys_data["speed"] = {
                "value": move_to_speed(move_val),
                "modifier": 0,
            }
        elif "speed" not in sys_data:
            sys_data["speed"] = {"value": "normal", "modifier": 0}

        # Resilience: initialise to 0; TOW computes from T automatically
        sys_data.setdefault("resilience", {"value": 0, "modifier": 0})

        # Aggregate protection from converted armour items → resilience.modifier
        # (armour items write system.protection; we sum them here for the actor)
        total_armour_prot: int = 0
        for embedded in doc.get("items", []):
            if embedded.get("type") == "armour":
                prot = embedded.get("system", {}).get("protection", 0)
                if isinstance(prot, (int, float)):
                    total_armour_prot += int(prot)
        if total_armour_prot:
            sys_data["resilience"]["modifier"] = total_armour_prot

        # NPC sub-type + TOW Wounds: multi-factor classification
        if tow_actor_type == "npc":
            # Toughness Bonus from the already-converted TOW characteristics
            t_char = sys_data.get("characteristics", {}).get("t", {})
            t_bonus = int(t_char.get("base", 3))

            # A WFRP "character" actor (not creature/npc) is always a named NPC.
            # A WFRP "npc" actor (humanoid individual, not generic creature) with
            # very high wounds is almost certainly a boss/villain → champion.
            is_named = (actor_type == "character") or (
                actor_type == "npc" and _wfrp_wounds >= 20
            )

            # Guard: if no WFRP source data was found (data is already TOW format),
            # preserve the existing subtype and wound count rather than forcing
            # everything to "minion" due to wounds=0.
            _valid_subtypes = {"minion", "brute", "champion", "monstrosity"}
            if _wfrp_wounds == 0 and not _wfrp_size and _existing_subtype in _valid_subtypes:
                npc_subtype = _existing_subtype
                tow_wounds  = _existing_resilience or 1
                log.debug(
                    "NPC subtype: no WFRP source data — preserving existing '%s' (resilience=%d)",
                    npc_subtype, tow_wounds,
                )
            else:
                npc_subtype, tow_wounds = classify_npc_subtype(
                    wounds   = _wfrp_wounds,
                    t_bonus  = t_bonus,
                    size_key = _wfrp_size,
                    is_named = is_named,
                )
                log.debug(
                    "NPC subtype: wounds=%d t=%d size=%r named=%s → %s (tow_wounds=%d)",
                    _wfrp_wounds, t_bonus, _wfrp_size, is_named, npc_subtype, tow_wounds,
                )
            sys_data["type"] = npc_subtype
            # Write the adjusted wound count into resilience.value
            sys_data.setdefault("resilience", {})
            sys_data["resilience"]["value"] = tow_wounds

            # Generate wound-threshold effect stubs for Brutes and Monstrosities.
            # TOW statblocks list predefined effects that trigger as the creature
            # loses each Wound (replacing the WFRP critical table).
            if npc_subtype in ("brute", "monstrosity") and tow_wounds >= 2:
                desc_dict = sys_data.setdefault("description", {})
                gm_notes = desc_dict.get("gm", "") or ""
                effect_lines = ["\n\n[WOUND THRESHOLD EFFECTS — customise as needed]"]
                for wound_num in range(2, tow_wounds + 1):
                    if wound_num == tow_wounds:
                        effect_lines.append(
                            f"After {wound_num}th Wound (last): Defeated. (Optional: Frenzied/Berserk "
                            f"reaction — one final desperate action before removal.)"
                        )
                    elif wound_num == 2:
                        effect_lines.append(
                            f"After 2nd Wound: Staggered (−1 die to all tests until end of next turn)."
                        )
                    else:
                        effect_lines.append(
                            f"After {wound_num}th Wound: loses one attack action."
                        )
                desc_dict["gm"] = gm_notes + "\n".join(effect_lines)

        # Description: migrate WFRP biography
        details = sys_data.get("details") or {}
        biography = (details.get("biography") or {}).get("value", "")
        gmnotes   = (details.get("gmnotes")   or {}).get("value", "")
        if "description" not in sys_data or not isinstance(sys_data.get("description"), dict):
            sys_data["description"] = {}
        if biography and not sys_data["description"].get("public"):
            sys_data["description"]["public"] = biography
        if gmnotes and not sys_data["description"].get("gm"):
            sys_data["description"]["gm"] = gmnotes

        # Remove WFRP-only top-level system fields that have no TOW equivalent
        for dead_key in ("status", "details", "basicSkills", "ap",
                         "encumbrance", "mount", "passengers",
                         "settings", "excludedTraits"):
            sys_data.pop(dead_key, None)

        # Fix prototypeToken bar attributes: WFRP 'status.wounds' doesn't exist in TOW
        pt = doc.get("prototypeToken")
        if isinstance(pt, dict):
            for bar_key in ("bar1", "bar2"):
                bar = pt.get(bar_key)
                if isinstance(bar, dict) and bar.get("attribute") == "status.wounds":
                    bar["attribute"] = ""
                elif isinstance(bar, dict) and bar.get("attribute") == "status.advantage":
                    bar["attribute"] = ""

        # Strip WFRP4e module-specific actor flags
        actor_flags = doc.get("flags")
        if isinstance(actor_flags, dict):
            # Keep only generic 'core' namespace flags
            to_remove = [k for k in actor_flags
                         if k not in ("core", "warhammer-lib", "whtow")]
            for k in to_remove:
                actor_flags.pop(k)

    # ── 8. Item-level conversion ─────────────────────────────────────────────
    if not is_actor:
        # Migrate WFRP description.value → TOW description.{public, gm}
        # TOW BaseItemModel uses description: {public: HTMLField, gm: HTMLField}
        # WFRP stores description: {value: "..."} and gmdescription: {value: "..."}
        # Skip migration if description.public was already set (e.g. by the TOW
        # ability library write-through in section 5).
        desc = sys_data.get("description")
        gmdesc = sys_data.get("gmdescription")
        if isinstance(desc, dict) and "value" in desc:
            already_set = bool(desc.get("public"))
            if not already_set:
                public_text = desc.get("value") or ""
                gm_text = gmdesc.get("value") or "" if isinstance(gmdesc, dict) else ""
                sys_data["description"] = {"public": public_text, "gm": gm_text}
                sys_data.pop("gmdescription", None)

        item_type = doc.get("type", "")
        try:
            if item_type == "weapon":
                convert_weapon(doc, sys_data, cfg, stats)
            elif item_type == "armour":
                convert_armour(doc, sys_data, cfg, stats)
            elif item_type == "talent":
                convert_talent(doc, sys_data, cfg, stats)
            elif item_type == "trapping":
                convert_trapping(doc, sys_data, cfg, stats)
            elif item_type == "spell":
                convert_spell(doc, sys_data, cfg, stats)
            elif item_type in ("ability", "blessing", "injury", "lore"):
                # TOW ability/blessing items only need description.
                # Strip all WFRP-only system fields; description migration
                # above already populated description.{public, gm}.
                _keep_only(sys_data, "description")
        except Exception as exc:
            name = doc.get("name", "<unnamed>")
            msg = f"Item conversion failed on '{name}' (type={item_type}): {exc}"
            stats.errors.append(msg)
            log.warning(msg)

    # Count top-level documents
    if _depth == 0:
        stats.documents_converted += 1

    return doc


# ─────────────────────────────────────────────────────────────────────────────
# POST-CONVERSION VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_document(doc: dict, stats: ConversionStats) -> list[str]:
    """
    Run basic sanity checks on a converted document.
    Returns a list of human-readable issue strings (empty = all clear).
    """
    if not isinstance(doc, dict):
        return []

    issues: list[str] = []
    name = doc.get("name", "<unnamed>")

    # System ID must have been updated
    sys_id = (doc.get("_stats") or {}).get("systemId", "")
    if sys_id and sys_id != "whtow":
        issues.append(f"[{name}] systemId='{sys_id}' (expected 'whtow')")

    sys_data = doc.get("system") or {}

    # Characteristic values should be in a reasonable TOW range (1–10)
    for char_key, char in (sys_data.get("characteristics") or {}).items():
        if isinstance(char, dict):
            v = char.get("base")
            if isinstance(v, int) and not (1 <= v <= 10):
                issues.append(
                    f"[{name}] characteristic {char_key}.base={v} outside 1–10"
                )

    # Resilience should be a positive integer for armour items
    if doc.get("type") == "armour":
        res = sys_data.get("resilience")
        if res is not None and not isinstance(res, int):
            issues.append(f"[{name}] resilience is {type(res).__name__}, expected int")

    # Recurse into embedded items
    for item in (doc.get("items") or []):
        issues.extend(validate_document(item, stats))

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# COMPENDIUM WRAPPER HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def is_compendium_wrapper(data: Any) -> bool:
    """
    Return True when `data` is the wrapper dict produced by Foundry's
    Compendium Exporter / Mana's Compendium Importer, i.e.:
      { "metadata": { "system": "wfrp4e", ... }, "items": [...] }
    as opposed to a bare Actor/Item document or a flat array.
    """
    return (
        isinstance(data, dict)
        and isinstance(data.get("metadata"), dict)
        and "system" in data["metadata"]
        and isinstance(data.get("items") or data.get("documents"), list)
    )


def patch_compendium_wrapper(
    data: dict,
    cfg: dict,
    stats: ConversionStats,
    validate: bool = False,
) -> dict:
    """
    Convert a Mana's-Compendium-Importer wrapper dict in-place.

    Patches:
      - metadata.system   → "whtow"
      - metadata.id       → replaces "wfrp4e" prefix with "whtow"
      - package           → same replacement
    Then converts every document in data["items"] as a top-level document
    (so ConversionStats.documents_converted counts actors/items, not the wrapper).
    """
    meta: dict = data["metadata"]

    # ── Patch system identifier ──────────────────────────────────────────────
    old_system = meta.get("system", "")
    meta["system"] = "whtow"
    log.info("Patched metadata.system: '%s' → 'whtow'", old_system)

    # ── Patch top-level "source" block (read by Mana's Compendium Importer) ─
    # This is what Foundry displays as Source > System / Version / World in the
    # Import Compendium dialog.  Leaving it as wfrp4e causes "Unresolved conflict".
    source = data.get("source")
    if isinstance(source, dict):
        old_src = source.get("system", "")
        source["system"] = "whtow"
        # Clear the version so it doesn't show a stale wfrp4e system version
        if isinstance(source.get("version"), dict):
            source["version"].pop("system", None)
        log.info("Patched source.system: '%s' → 'whtow'", old_src)

    # ── Patch metadata.name (used by Foundry as the compendium pack ID) ───────
    # Append "-tow" so it never collides with the original wfrp4e pack.
    if isinstance(meta.get("name"), str) and not meta["name"].endswith("-tow"):
        meta["name"] = meta["name"] + "-tow"
        log.info("Patched metadata.name → '%s' (avoids ID conflict in Foundry)", meta["name"])

    # ── Patch package/id strings (replace wfrp4e prefix) ────────────────────
    for key in ("id", "packageName"):
        if isinstance(meta.get(key), str) and "wfrp4e" in meta[key]:
            meta[key] = meta[key].replace("wfrp4e", "whtow")
    if isinstance(data.get("package"), str) and "wfrp4e" in data["package"]:
        data["package"] = data["package"].replace("wfrp4e", "whtow")

    # ── Convert each document, preserving the original list key name ──────────
    # Mana's Compendium Importer / Compendium Exporter use "items" as the
    # document list key.  We keep whichever key the source file used so the
    # importer can find the data.
    list_key: str = "documents" if "documents" in data else "items"
    raw_docs: list = data.get(list_key, [])
    converted_docs: list = []
    for doc in raw_docs:
        converted = convert_document(doc, cfg, stats, _depth=0)
        if validate:
            for issue in validate_document(converted, stats):
                stats.warnings.append(issue)
                log.warning("Validation: %s", issue)
        converted_docs.append(converted)
    data[list_key] = converted_docs
    log.info("Wrote %d documents under '%s' key", len(converted_docs), list_key)

    return data


# ─────────────────────────────────────────────────────────────────────────────
# LIBRARY ITEM EXTRACTION  (post-conversion; actor-embedded items → own files)
# ─────────────────────────────────────────────────────────────────────────────

def _docs_from_converted(converted: Any) -> list:
    """
    Return the flat list of top-level converted documents regardless of the
    outer data shape (flat list, compendium wrapper, or single document dict).
    """
    if isinstance(converted, list):
        return converted
    if isinstance(converted, dict):
        for key in ("items", "documents"):
            if isinstance(converted.get(key), list):
                return converted[key]
        return [converted]
    return []


def extract_library_items(
    docs: list,
    type_buckets: dict[str, str] | None = None,
) -> dict[str, list]:
    """
    Walk converted documents (actors or top-level items) and collect all
    embeddable items for export to library compendium files.

    Deduplication key: (item_name.strip().lower(), item_type)
    Items that share the same (name, type) key across multiple actors are kept
    once — the first occurrence wins.

    Returns a dict mapping bucket-name → list of unique item dicts, e.g.:
      {"weapons": [...], "armour": [...], "abilities": [...], "trappings": [...]}
    """
    buckets = type_buckets or LIBRARY_TYPE_BUCKETS

    # TOW actor types as returned *after* actor-type remapping
    _TOW_ACTOR_TYPES = set(WFRP_TO_TOW_ACTOR_TYPE.values())

    seen:   set[tuple[str, str]] = set()
    result: dict[str, list]      = {}

    for doc in docs:
        if not isinstance(doc, dict):
            continue

        doc_type = doc.get("type", "")

        if doc_type in _TOW_ACTOR_TYPES:
            # Actor document — mine its embedded items array
            for item in (doc.get("items") or []):
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")
                bucket    = buckets.get(item_type)
                if not bucket:
                    continue
                key = (item.get("name", "").strip().lower(), item_type)
                if key in seen:
                    continue
                seen.add(key)
                result.setdefault(bucket, []).append(item)
        else:
            # Top-level item document — add it directly
            bucket = buckets.get(doc_type)
            if not bucket:
                continue
            key = (doc.get("name", "").strip().lower(), doc_type)
            if key in seen:
                continue
            seen.add(key)
            result.setdefault(bucket, []).append(doc)

    return result


def write_library_files(
    library_items: dict[str, list],
    output_path: Path,
    source_label: str,
    stats: ConversionStats,
) -> dict[str, Path]:
    """
    Write one compendium JSON file per bucket alongside the main output.

    File naming convention:  library_{bucket}.json
      e.g. library_weapons.json, library_abilities.json

    Each file is a Mana's Compendium Importer wrapper:
      {
        "metadata": { "system": "whtow", "type": "Item", ... },
        "items": [ ... ]
      }

    Import order for Foundry:
      1. Import each library_*.json first (establishes the item compendiums).
      2. Then import the main actors file.

    Returns a dict mapping bucket → written Path for every file that was written.
    Buckets with zero items are silently skipped.
    """
    output_dir = output_path.parent
    written: dict[str, Path] = {}

    for bucket in sorted(library_items):
        items = library_items[bucket]
        if not items:
            continue

        out_path = output_dir / f"library_{bucket}.json"
        wrapper: dict = {
            "metadata": {
                "system":      "whtow",
                "type":        "Item",
                "label":       f"{source_label} — {bucket.title()} Library",
                "name":        f"library-{bucket}-tow",
                "packageType": "world",
            },
            # Import instructions stored in source block so they survive round-trips
            "source": {
                "system":  "whtow",
                "module":  "wfrp-to-tow-converter",
                "note":    (
                    "Import this file BEFORE importing the actors compendium. "
                    "These are the unique items extracted from all converted actors."
                ),
            },
            "items": items,
        }
        try:
            out_path.write_text(
                json.dumps(wrapper, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            stats.library_counts[bucket] = len(items)
            stats.library_files.append(out_path.name)
            written[bucket] = out_path
            log.info(
                "Library: %d unique %s → %s",
                len(items), bucket, out_path.name,
            )
        except OSError as exc:
            msg = f"Could not write library file '{out_path.name}': {exc}"
            stats.errors.append(msg)
            log.error(msg)

    return written


# ─────────────────────────────────────────────────────────────────────────────
# FILE I/O
# ─────────────────────────────────────────────────────────────────────────────

def convert_file(
    input_path: Path,
    output_path: Path,
    cfg: dict,
    stats: ConversionStats,
    backup: bool = False,
    validate: bool = False,
) -> None:
    """Load, convert, optionally validate, and write a single JSON file."""
    try:
        raw = input_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError as exc:
        stats.errors.append(f"Cannot read '{input_path}': {exc}")
        log.error("Cannot read '%s': %s", input_path, exc)
        return
    except json.JSONDecodeError as exc:
        stats.errors.append(f"Invalid JSON in '{input_path}': {exc}")
        log.error("Invalid JSON in '%s': %s", input_path, exc)
        return

    # Optional backup of the source file
    if backup:
        backup_path = input_path.with_suffix(".bak.json")
        shutil.copy2(input_path, backup_path)
        log.info("Backup saved: %s", backup_path)

    # Deep-copy before mutating so the original in-memory data is untouched
    data = copy.deepcopy(data)

    if isinstance(data, list):
        # Flat array of documents (bare compendium export)
        converted: Any = [convert_document(doc, cfg, stats) for doc in data]
        if validate:
            for doc in converted:
                for issue in validate_document(doc, stats):
                    stats.warnings.append(issue)
                    log.warning("Validation: %s", issue)
    elif is_compendium_wrapper(data):
        # Compendium Exporter / Mana's Compendium Importer wrapper format:
        # { "metadata": {...}, "items": [...] }
        # Patch in-place and PRESERVE the wrapper so Mana's Compendium Importer
        # can directly import the output file without further changes.
        meta = data.get("metadata", {})
        list_key: str = "documents" if "documents" in data else "items"
        log.info(
            "Detected wrapper: label='%s' type='%s' — converting %d documents.",
            meta.get("label", "?"), meta.get("type", "?"),
            len(data.get(list_key, [])),
        )
        converted = patch_compendium_wrapper(data, cfg, stats, validate)
        n_docs = len(converted.get(list_key, []))
        log.info(
            "Output: compendium wrapper with %d documents (import via Mana's Compendium Importer).",
            n_docs,
        )
    elif isinstance(data, dict):
        # Single bare document
        converted = convert_document(data, cfg, stats)
        if validate:
            for issue in validate_document(converted, stats):
                stats.warnings.append(issue)
                log.warning("Validation: %s", issue)
    else:
        stats.errors.append(f"Unexpected top-level JSON type in '{input_path}'")
        log.error("Unexpected top-level JSON type in '%s'", input_path)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(converted, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Converted: %s → %s", input_path.name, output_path.name)

    # ── Extract embedded items → per-type library compendium files ───────────
    # Collect all top-level documents from the converted data, then extract
    # unique embedded items and write one library file per item-type bucket.
    all_docs = _docs_from_converted(converted)
    if all_docs:
        type_buckets = cfg.get("library_type_map") or None
        library_items = extract_library_items(all_docs, type_buckets)
        if library_items:
            write_library_files(
                library_items,
                output_path,
                source_label=input_path.stem,
                stats=stats,
            )


def batch_convert(
    input_dir: Path,
    output_dir: Path,
    cfg: dict,
    stats: ConversionStats,
    backup: bool = False,
    validate: bool = False,
) -> None:
    """Convert every .json file found directly in input_dir."""
    json_files = sorted(input_dir.glob("*.json"))
    if not json_files:
        log.warning("No .json files found in '%s'", input_dir)
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    # Use tqdm progress bar when available; fall back to plain iteration
    try:
        from tqdm import tqdm  # type: ignore
        iterable = tqdm(json_files, desc="Converting", unit="file")
    except ImportError:
        iterable = json_files  # type: ignore[assignment]
        log.debug("tqdm not installed — no progress bar (pip install tqdm to enable)")

    for json_file in iterable:
        out_file = output_dir / json_file.name
        convert_file(json_file, out_file, cfg, stats, backup=backup, validate=validate)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_config(config_path: Path | None) -> dict:
    """
    Return a config dict by merging an optional user JSON file over DEFAULT_CONFIG.

    Dict values (like skill_map) are merged key-by-key so user files only need
    to contain the entries they want to add or override.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if config_path is None:
        return cfg
    try:
        user_cfg: dict = json.loads(config_path.read_text(encoding="utf-8"))
        for key, val in user_cfg.items():
            if isinstance(val, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(val)
            else:
                cfg[key] = val
        log.info("Loaded config overrides from '%s'", config_path)
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Could not load config '%s': %s — using defaults", config_path, exc)
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wfrp_to_tow_bulk",
        description=(
            "Bulk-convert Warhammer Fantasy Roleplay 4e compendium JSON\n"
            "to Warhammer: The Old World (Foundry VTT) format."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Single compendium file
  python wfrp_to_tow_bulk.py wfrp_creatures.json tow_creatures.json

  # Folder of individual document files
  python wfrp_to_tow_bulk.py wfrp_exports/ tow_converted/

  # External config + validation
  python wfrp_to_tow_bulk.py input.json output.json --config mappings.json --validate

  # Backup originals, verbose logging
  python wfrp_to_tow_bulk.py wfrp_exports/ tow_converted/ --backup --verbose
""",
    )
    p.add_argument("input",  type=Path, help="Input .json file or directory of .json files")
    p.add_argument("output", type=Path, help="Output .json file or directory")
    p.add_argument(
        "--config", type=Path, default=None,
        help="JSON file with skill_map / weapon_group_map / quality_map overrides",
    )
    p.add_argument(
        "--backup", action="store_true",
        help="Save a .bak.json copy of each source file before converting",
    )
    p.add_argument(
        "--validate", action="store_true",
        help="Run basic post-conversion sanity checks and report issues",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logging",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
    )

    cfg   = load_config(args.config)
    stats = ConversionStats()

    if args.input.is_dir():
        batch_convert(
            args.input, args.output, cfg, stats,
            backup=args.backup, validate=args.validate,
        )
    elif args.input.is_file():
        convert_file(
            args.input, args.output, cfg, stats,
            backup=args.backup, validate=args.validate,
        )
    else:
        log.error("Input path '%s' does not exist.", args.input)
        return 1

    print(stats.report())
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())