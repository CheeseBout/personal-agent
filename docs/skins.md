# Making an avatar skin

A skin is a folder with a `manifest.json` and your images. Put it in `config/avatar/<name>/` or `%APPDATA%\personal-agent\skins\<name>\`,
then pick it under Settings -> General -> Skin (or set `ui.skin: <name>`). Skins **hot-reload**: save a file and the avatar updates in about 0.3 s.

```json
{
  "name": "mine",
  "size": [160, 160],
  "fallback": "idle",
  "states": {
    "idle":     {"frames": ["idle_0.png", "idle_1.png", "idle_2.png"], "fps": 4},
    "thinking": {"gif": "think.gif"},
    "success":  {"frames": ["yay.png"], "fps": 1, "loop": false},
    "error":    null
  }
}
```

- **States** the agent drives: `idle, listening, thinking, working, speaking, awaiting_approval, success, error, sleeping`.
- A state may use `frames` (PNG/WebP sequence), `gif` (an animated GIF/APNG), or `builtin` (one of the built-in drawings, e.g. `{"builtin": "error"}`).
- `fps` is 0.2-30 (default 6; for a GIF the file's own timing). `loop: false` stops on the last frame (good for one-shot states like `success`).
- **Fallbacks:** a state that is missing, `null`, or fails to load uses `fallback`, then `idle`, then the built-in drawing. A skin with problems still works;
  the problems are listed in `Skin.problems`.
- `size` is the square size in pixels at 1x (32-512); frames of another height are scaled to it. Use PNG with a transparent background:
  clicks pass through the transparent pixels.
- Files must stay inside the skin folder (`../x.png` is refused). Keep sequences under 240 frames per state.
- Animation only runs while the avatar is visible, and a one-frame state uses no timer at all, so small skins are nearly free.

Tip: start from `config/avatar/manifest.json` (it lists every state with its built-in drawing) and replace states one by one.
