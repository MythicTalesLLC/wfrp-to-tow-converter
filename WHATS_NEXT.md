# What's Next — Proposed Enhancements

A prioritised backlog of improvements planned for future versions of the WFRP4e → TOW Compendium Converter.

---

## HIGH PRIORITY

### 1. Apple Code-Signing & Notarization
**Problem:** macOS blocks the app on first launch with a Gatekeeper warning.
**Solution:** Sign the `.app` with an Apple Developer certificate and submit it for notarization. Users would then be able to open it like any other Mac app with no extra steps.
**Requires:** Apple Developer Program membership ($99/year).
**Build impact:** Add `codesign` and `xcrun notarytool` steps to `build_mac.sh` and the GitHub Actions macOS job.

---

### 2. Windows Code-Signing
**Problem:** Windows SmartScreen flags unsigned executables as potentially unsafe.
**Solution:** Purchase an EV Code Signing certificate and sign the `.exe` with `signtool.exe` post-build.
**Build impact:** Add signing step to `build_windows.bat` and the GitHub Actions Windows job.

---

### 3. Actor Conversion: Traits & Special Abilities
**Current state:** NPC traits (`fearsome`, `ethereal`, `ward`, `undead`, etc.) are carried as raw items but not mapped to TOW equivalents.
**Enhancement:** Add a `_WFRP_TRAIT_TO_TOW` lookup table (similar to `_WFRP_SPELL_LIBRARY`) that maps common WFRP traits to their TOW counterparts in `system.traits`, and strips or transforms the rest.

---

### 4. Spell Conversion: Missing / Fallback Spells
**Current state:** Unknown spells fall back to a generic "Arcane" classification.
**Enhancement:** Expand `_WFRP_SPELL_LIBRARY` with the full WFRP core + supplement spell list (~200 spells). Add a "review needed" flag to the output for spells that couldn't be matched, and surface those in the GUI summary.

---

### 5. Batch File / Folder Conversion
**Current state:** The GUI processes one JSON file at a time.
**Enhancement:** Allow the user to select an entire folder of JSON files and convert them all in a single run, with a per-file progress bar and a combined summary report.

---

### 6. Preview Panel — Journal Pages
**Current state:** The preview/card panel shows actor and item stats but has no journal viewer.
**Enhancement:** Add a "Journal" tab to the preview panel that renders the rewritten HTML content in a read-only scrollable text widget, so the user can spot-check terminology replacements before writing the output file.

---

## MEDIUM PRIORITY

### 7. Drag-and-Drop File Input
**Current state:** Files are selected via a Browse dialog.
**Enhancement:** Allow dragging a JSON file from Finder/Explorer directly onto the app window to load it instantly.

### 8. Dark Mode Support
**Current state:** The GUI uses a fixed parchment/gold palette.
**Enhancement:** Detect the OS dark/light mode setting and switch between the current parchment theme and a darker variant automatically.

### 9. Configurable Output Format
**Current state:** Output is always wrapped in Mana's Compendium Importer format.
**Enhancement:** Add an option to output raw Foundry-native JSON (no wrapper), for users who want to import via other methods or inspect the raw data.

### 10. Undo / Re-convert
**Current state:** Once a conversion is written, there is no way to re-run it with different settings without deleting the output manually.
**Enhancement:** Add a "Re-convert" button in the GUI that clears the output folder and re-runs the last conversion with current settings.

### 11. Manual Override Panel — Stat Editing
**Current state:** The card view shows converted stats in editable fields, but those edits are not written back to the output JSON.
**Enhancement:** Wire the card-view editable fields (characteristics, skills, resilience) back to the in-memory document so users can make minor manual corrections and export an adjusted version.

---

## LOW PRIORITY / FUTURE IDEAS

### 12. Foundry Module Packaging
Package the converted compendium output directly as a Foundry VTT module (with `module.json` manifest and `packs/` folder), so it can be installed via the module manager rather than requiring Mana's Importer.

### 13. Reverse Conversion (TOW → WFRP4e)
Allow converting in the opposite direction for groups moving back or cross-referencing between systems.

### 14. Web Version
A browser-based version (Python compiled to WASM via Pyodide, or a rewrite in TypeScript) that requires no installation at all — users upload a JSON and download the result.

### 15. Plugin Architecture
Allow third-party lookup tables and converter plugins to be dropped into an `extensions/` folder, so the community can add support for unofficial WFRP4e content (fan-made bestiary, third-party adventures) without modifying core code.
