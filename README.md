# WFRP4e → The Old World: Compendium Converter

A desktop GUI tool that converts **Warhammer Fantasy Roleplay 4th Edition** Foundry VTT compendium exports into **Warhammer: The Old World RPG** (TOW) format, ready for import via [Mana's Compendium Importer](https://foundryvtt.com/packages/compendium-importer).

---

## Features

- Converts actors (NPCs, beasts, characters)
- Converts items: weapons, armour, talents, trappings, spells, abilities
- Converts journal entries — strips WFRP mechanics, rewrites in TOW terminology
- Extracts shared weapons, armour, and trappings into separate library files
- Scales WFRP characteristics to TOW d10 range
- Maps WFRP lores to TOW lore equivalents
- Cross-platform desktop app (macOS, Windows, Linux)

---

## Download (No Python Required)

Pre-built executables are attached to each [GitHub Release](../../releases).

| Platform | File | Instructions |
|---|---|---|
| macOS | `WFRP4e_to_TOW_Converter_mac.zip` | Unzip → right-click → Open |
| Windows | `WFRP4e_to_TOW_Converter.exe` | Run directly |
| Linux | `WFRP4e_to_TOW_Converter_linux.tar.gz` | Extract → `chmod +x` → run |

---

## Usage

1. Export a WFRP4e compendium from Foundry VTT as JSON (one document per file or array of documents).
2. Open the converter app.
3. Select your input JSON file and an output folder.
4. Click **Convert**.
5. Import the output file(s) into your TOW world via Mana's Compendium Importer.

---

## Custom Icon

The default icon is a placeholder. To brand with your own logo:

1. Replace `assets/icon.png` with your 512×512 PNG.
2. Regenerate the platform icons:

```bash
# macOS .icns
mkdir -p assets/icon.iconset
python3 -c "
from PIL import Image
img = Image.open('assets/icon.png')
for s in [16,32,64,128,256,512]:
    img.resize((s,s)).save(f'assets/icon.iconset/icon_{s}x{s}.png')
    if s<=256: img.resize((s*2,s*2)).save(f'assets/icon.iconset/icon_{s}x{s}@2x.png')
"
iconutil -c icns assets/icon.iconset -o assets/icon.icns

# Windows .ico
python3 -c "
from PIL import Image
img = Image.open('assets/icon.png')
sizes = [(s,s) for s in [16,32,48,64,128,256]]
img.save('assets/icon.ico', format='ICO', sizes=sizes)
"
```

3. Rebuild with `pyinstaller wfrp_tow_converter.spec --noconfirm`.

---

## Building from Source

```bash
pip install pyinstaller pillow
pyinstaller wfrp_tow_converter.spec --noconfirm
```

Or use the provided platform scripts:

```bash
# macOS
bash build_scripts/build_mac.sh

# Linux
bash build_scripts/build_linux.sh

# Windows (Command Prompt)
build_scripts\build_windows.bat
```

---

## Automated Releases (GitHub Actions)

Push a version tag to trigger a multi-platform build and release:

```bash
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions will build on macOS, Windows, and Ubuntu runners and attach all three binaries to a GitHub Release automatically.

---

## License

Fan-made utility. Not affiliated with Cubicle 7, Games Workshop, or Foundry Gaming LLC.
Warhammer Fantasy Roleplay and Warhammer: The Old World are trademarks of Games Workshop Ltd.
