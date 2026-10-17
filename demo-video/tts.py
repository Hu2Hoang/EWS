"""Voice-over for the demo video: one WAV per scene with Gemini TTS, plus durations.json for the recorder.

Usage (from the repo root):  backend/.venv/bin/python demo-video/tts.py [--voice Charon] [--model gemini-3.8-flash-tts]
Uses GEMINI_API_KEY from backend/.env. A scene is only regenerated when its text, voice or model changed.
"""
import argparse
import hashlib
import json
import os
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "audio"
STYLE = "Read this as an energetic, confident product-demo narrator. Brisk, natural pace, short pauses between sentences:"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="Charon")
    ap.add_argument("--model", default="gemini-3.8-flash-tts")
    args = ap.parse_args()
    load_dotenv(HERE.parent / "backend" / ".env")
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    OUT.mkdir(parents=True, exist_ok=True)
    scenes = json.loads((HERE / "scenes.json").read_text())
    durations = {}
    for s in scenes:
        wav, meta = OUT / f"{s['id']}.wav", OUT / f"{s['id']}.json"
        key = hashlib.sha1(f"{args.model}|{args.voice}|{s['narration']}".encode()).hexdigest()
        if not (wav.exists() and meta.exists() and json.loads(meta.read_text()).get("key") == key):
            for attempt in range(4):
                try:
                    resp = client.models.generate_content(
                        model=args.model, contents=f"{STYLE} {s['narration']}",
                        config=types.GenerateContentConfig(
                            response_modalities=["AUDIO"],
                            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=args.voice)))))
                    pcm = resp.candidates[0].content.parts[0].inline_data.data
                    break
                except Exception as e:  # rate limits on preview TTS models are tight
                    print(f"  {s['id']}: {type(e).__name__}, retrying", flush=True)
                    time.sleep(10 * (attempt + 1))
            else:
                raise SystemExit(f"TTS failed for {s['id']}")
            with wave.open(str(wav), "wb") as w:  # Gemini TTS returns 24 kHz, 16-bit, mono PCM
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm)
            meta.write_text(json.dumps({"key": key}))
        with wave.open(str(wav)) as w:
            durations[s["id"]] = round(w.getnframes() / w.getframerate(), 2)
        print(f"{s['id']}: {durations[s['id']]:.1f}s", flush=True)
    (HERE / "out" / "durations.json").write_text(json.dumps(durations, indent=1))
    print(f"total voice: {sum(durations.values()):.1f}s")


if __name__ == "__main__":
    main()
