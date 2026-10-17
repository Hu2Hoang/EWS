# Automated Demo Video

Records the 3:30 demo video without a human at the keyboard. A script drives the app through the scenes in
`scenes.json`, with an animated cursor, highlight boxes and captions. Each scene is paced by its Gemini TTS voice-over,
and the result is assembled into `out/demo.mp4` (1920×1200, H.264, voice normalised to -16 LUFS).

The written script with timings is in [`../DEMO.md`](../DEMO.md). This folder automates it.

## Requirements

- Google Chrome at `/usr/bin/google-chrome` (or set `CHROME=/path/to/chrome`)
- Node 22.22+ and `npm install` in this folder
- ffmpeg with libx264
- `backend/.venv` with `google-genai`, and `GEMINI_API_KEY` in `backend/.env`
- The app running: locally (`npm start` in `frontend/` plus the API on :8000), or deployed

## Run

```bash
# 1. Voice-over: one WAV per scene. Only scenes whose text changed are regenerated
backend/.venv/bin/python demo-video/tts.py                   # options: --voice Charon --model gemini-3.8-flash-tts

# 2. Record (about 3.5 minutes). This RESETS the app's demo state first (POST /live/reset)
cd demo-video && npm install && node record.mjs                       # local: http://localhost:4200
cd demo-video && node record.mjs https://<your-cloud-run-url>         # deployed: use this one for the submission

# 3. Assemble out/demo.mp4 and check that it is 3:00 to 4:00
backend/.venv/bin/python demo-video/build.py
```

## Changing the Video

- **Narration or captions:** edit `scenes.json`, then run all three steps. A scene lasts as long as its voice-over
  (plus 0.45 s), so the total length follows the text: about 375 words ≈ 3:35.
- **What happens on screen:** edit the matching `scene('…', async (at) => …)` block in `record.mjs`. `at(seconds)`
  waits until that point of the scene's voice-over, so actions line up with the words. The recorder prints a ⚠
  warning when the actions of a scene run past its voice-over.
- Run `scripts.warm_cache` before recording, so Customer A's Gemini analysis appears instantly.
