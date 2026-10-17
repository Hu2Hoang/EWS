"""Assembles out/demo.mp4 from the recorded frames (out/frames, out/timeline.json) and the voice-over (out/audio).

Usage (from the repo root):  backend/.venv/bin/python demo-video/build.py
Needs ffmpeg with libx264. Frames arrive at irregular times (Chrome only sends one when the page repaints), so each
frame is held until the next one, then the video is resampled to a constant 30 fps.
"""
import json
import subprocess
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
RATE = 24000  # Gemini TTS sample rate


def main():
    tl = json.loads((OUT / "timeline.json").read_text())
    frames, marks, end = tl["frames"], tl["marks"], tl["end"]
    t0 = marks[0]["t"] - 0.15
    skew = frames[0]["ts"] - marks[0]["t"]
    if abs(skew) > 30:
        raise SystemExit(f"Frame clock and scene clock differ by {skew:.0f}s; cannot align audio")

    # Video: an ffconcat list where each frame lasts until the next one
    lines = ["ffconcat version 1.0"]
    first = max((i for i, f in enumerate(frames) if f["ts"] <= t0), default=0)
    last_name = None
    for i in range(first, len(frames)):
        start = max(frames[i]["ts"], t0)
        nxt = min(frames[i + 1]["ts"] if i + 1 < len(frames) else end, end)
        if nxt <= start:
            continue
        lines += [f"file 'frames/{frames[i]['name']}'", f"duration {nxt - start:.4f}"]
        last_name = frames[i]["name"]
    lines.append(f"file 'frames/{last_name}'")  # the concat demuxer ignores the last duration otherwise
    (OUT / "frames.ffconcat").write_text("\n".join(lines) + "\n")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(OUT / "frames.ffconcat"), "-vf", "fps=30,scale=1920:1200:flags=lanczos,format=yuv420p",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-movflags", "+faststart", str(OUT / "video.mp4")],
                   check=True)

    # Audio: each scene's voice-over placed at the moment its scene started
    total = int((end - t0) * RATE)
    track = bytearray(total * 2)
    for m in marks:
        with wave.open(str(OUT / "audio" / f"{m['id']}.wav")) as w:
            pcm = w.readframes(w.getnframes())
        at = int((m["t"] - t0) * RATE) * 2
        pcm = pcm[: max(0, len(track) - at)]
        track[at: at + len(pcm)] = pcm
    with wave.open(str(OUT / "voice.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(track))

    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(OUT / "video.mp4"), "-i", str(OUT / "voice.wav"),
                    "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
                    str(OUT / "demo.mp4")], check=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(OUT / "demo.mp4")], capture_output=True, text=True, check=True).stdout)
    ok = 180 <= dur <= 240
    print(f"{OUT / 'demo.mp4'}: {int(dur // 60)}:{dur % 60:04.1f} {'(within 3 to 4 minutes)' if ok else '(OUTSIDE 3 to 4 minutes!)'}")
    for a, b in zip(marks, marks[1:] + [{"id": "end", "t": end}]):
        print(f"  {a['t'] - t0:6.1f}s  {a['id']}  ({b['t'] - a['t']:.1f}s)")


if __name__ == "__main__":
    main()
