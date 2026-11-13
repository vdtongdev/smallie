# Theme palettes

Reference images for colour themes. Nothing here is imported by the app, so it never ships in `dist`. Use it when you build a new theme in `src/index.css`.

## chatroom.png

The Sherwin-Williams "Chatroom" palette. Hex values sampled from the image:

| Colour | Hex | Where the Chatroom themes use it |
| --- | --- | --- |
| Chatroom | `#b1ab9d` | not used directly; the neutral the others sit on |
| Lucent Yellow | `#e5d0a5` | dark: `--primary`, `--ring`, `--sidebar-primary`, `--sidebar-ring` |
| Rocky River | `#5f706a` | dark: `--secondary` (`#4f5f59`) is a darker step of it |
| Spiced Cider | `#b0785d` | `--destructive`: a darker step in light (`#8f4a31`), a lighter one in dark (`#e8a58a`) |
| Mediterranean | `#60797d` | light and Light: `--ring`, `--sidebar-ring`; dark: `--accent` (`#4f6a6e`) is a darker step of it |
| Lemon Chiffon | `#f4e5bc` | light and Light: `--secondary`; dark: `--secondary-foreground` |
| Tree Branch | `#8a7363` | light and Light: `--muted-foreground` (`#6b5646`) is a darker step of it |
| Naval | `#303d4d` | light and Light: `--primary`, `--secondary-foreground`, `--accent-foreground`, `--sidebar-primary`; dark: `--card`, `--popover`, `--sidebar` |
| Whole Wheat | `#cdb593` | light and Light: `--accent` (`#ece1cf`) is a lighter step of it |

Themes that use it: `chatroom` (always dark) and `light` (always the light values), both in `src/index.css`. Where the table says "a darker/lighter step", the token was adjusted to keep text contrast at 4.5:1 (checked by `src/lib/contrast.test.ts`).
