# Icon Placeholder

This directory should contain application icons for different platforms.

For a real Tauri application, you need to generate icons using:

```bash
npm install -g @tauri-apps/cli
tauri icon path/to/icon.png
```

This will generate icons for all platforms in the correct sizes:

- icon.ico (Windows)
- icon.icns (macOS)
- Various PNG sizes for Linux

## Temporary Solution

For this skeleton, you can:

1. Use the default Tauri icon (build will show warning but work)
2. Create a simple icon with any image editor
3. Use icon generators online to create .ico files

Recommended icon size for generation: 1024x1024 PNG
