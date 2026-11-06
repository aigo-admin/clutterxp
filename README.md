# ClutterXP

> **Tired of a messy Downloads folder?** ClutterXP quietly tidies your files into the right place—on your terms, without ever losing a single file.

---

Whether your `Downloads` folder is buried under months of receipts, PDFs, wallpapers, installers, and screenshots, or you just want your desktop kept neat, **ClutterXP** does the heavy lifting for you.

Use the **clean visual desktop app** or run quick commands from your terminal.

---

## 🛡️ Why you can actually trust it

Most file cleanup tools feel risky—what if they delete something important or move a file you didn't want touched? We built ClutterXP with ironclad safety guarantees:

- **Never deletes or overwrites**: Files only move if the destination is safe. If a file with the same name already exists, ClutterXP renames the new one (e.g. `report (1).pdf`) or skips it. Your existing files are never replaced or destroyed.
- **Only moves what you tell it to**: If a file doesn't match a rule you created, it stays exactly where it is. No surprise moves, no mystery "unsorted" dump folders.
- **One-click instant undo**: Every action is saved in a local journal. If you ever change your mind, `clutterxp undo` (or a single click in the app) puts every file right back where it was originally.
- **100% private and offline**: Runs completely on your machine. No accounts, no cloud sync, no tracking, and zero internet requests.

---

## ⚡ Getting Started

### 1. Installation

Install ClutterXP with Python (3.10+):

```bash
# Recommended: install with desktop app (GUI) support
pip install "clutterxp[gui]"

# Or on Kali Linux / Debian using pipx:
pipx install .
```

### 2. Choose how you want to use it

#### Option A: The Desktop App
Just run:
```bash
clutterxp gui
```
You'll get an intuitive dashboard where you can:
- **Preview moves before they happen** with a clear interactive table.
- **Set up simple rules** with live matching tests (type a filename and see which rule catches it instantly).
- **Find duplicate files** and safely quarantine them without risking accidental deletion.
- **Undo any batch** with a single button.

#### Option B: The Terminal (Fast & Scriptable)
Prefer working from the command line? Use `clutterxp` or the short alias `cxp`:

```bash
# 1. Create your default starter config
clutterxp init

# 2. Test which rule matches a filename
clutterxp rules test "invoice_march.pdf"

# 3. Preview what would be organized (dry-run, touches nothing)
clutterxp organize ~/Downloads --dry-run

# 4. Organize your Downloads folder
clutterxp organize ~/Downloads --yes

# 5. Changed your mind? Undo the entire batch instantly!
clutterxp undo --yes
```

---

## Features that make life easier

### Set it and forget it (Background Watcher)
Turn on automatic sorting. Whenever a new download finishes, ClutterXP waits until it's completely written to disk, then quietly sorts it into place.
```bash
clutterxp watch
```
You can even enable it as a system background service:
```bash
clutterxp service install
clutterxp service status
```

### Find and clean duplicate downloads
Downloaded the same PDF or installer three times? ClutterXP safely identifies duplicate files taking up storage, shows you where they live, and lets you isolate them safely.
```bash
clutterxp duplicates scan ~/Downloads
```

### Customize your rules in seconds
Rules are dead simple. Group by keywords, file extensions, size, or age:
- *"Move anything with `invoice` or `receipt` to `~/Documents/Invoices`"*
- *"Move `.png` and `.jpg` with `wallpaper` to `~/Pictures/Wallpapers`"*
- *"Move `.deb`, `.iso`, `.rpm` to `~/Downloads/Installers`"*

You can edit them directly in the visual app or via `clutterxp rules add`.

---

## Cheat Sheet

| What you want to do | Command |
|:---|:---|
| Open the visual desktop app | `clutterxp gui` |
| Preview organization without moving files | `cxp organize ~/Downloads --dry-run` |
| Organize a folder | `cxp organize ~/Downloads` |
| Undo the last batch of moves | `cxp undo` |
| Watch a folder and sort new files automatically | `cxp watch` |
| Install background watcher service | `cxp service install` |
| Scan for duplicate files | `cxp duplicates scan ~/Downloads` |
| Check system health and file integrity | `cxp doctor` |
| View organization history | `cxp history` |

---

## License

MIT
