#!/usr/bin/env python3
"""Build a transcript + screenshot timeline package from a local video file.

This helper intentionally focuses on local processing. Use Hermes tools to download
Google Drive, Gmail, GitHub, URL, or messaging-platform sources to a local file first.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass
class SliceSpec:
    index: int
    start: float
    end: float
    frame_time: float
    label: str
    screenshot: str


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def run(cmd: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise RuntimeError(
            "Command failed (exit %s): %s\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (proc.returncode, " ".join(cmd), proc.stdout[-4000:], proc.stderr[-4000:])
        )
    return proc


def require_executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"Missing required executable: {name}. Install ffmpeg/ffprobe and retry.")
    return path


def parse_time(value: str | int | float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if not s:
        raise ValueError("empty time value")
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", s)
    if m:
        num = float(m.group(1))
        unit = m.group(2)
        return num / 1000 if unit == "ms" else num if unit == "s" else num * 60 if unit == "m" else num * 3600
    parts = s.split(":")
    if 1 < len(parts) <= 3:
        vals = [float(p) for p in parts]
        while len(vals) < 3:
            vals.insert(0, 0.0)
        h, m_, sec = vals
        return h * 3600 + m_ * 60 + sec
    raise ValueError(f"Unsupported time format: {value!r}")


def fmt_hms(seconds: float, *, sep: str = ":") -> str:
    seconds = max(0.0, float(seconds))
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h:
        return f"{h:02d}{sep}{m:02d}{sep}{s:02d}"
    return f"{m:02d}{sep}{s:02d}"


def fmt_file_ts(seconds: float) -> str:
    total = int(max(0, seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}-{m:02d}-{s:02d}"


def fmt_srt(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total = total_ms // 1000
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_vtt(seconds: float) -> str:
    return fmt_srt(seconds).replace(",", ".")


def safe_stem(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-._")
    return stem or "video"


def ffprobe(video: Path) -> Dict[str, Any]:
    proc = run([
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video),
    ])
    return json.loads(proc.stdout)


def get_duration_seconds(meta: Dict[str, Any]) -> float:
    fmt = meta.get("format") or {}
    if fmt.get("duration") not in (None, "N/A"):
        try:
            return float(fmt["duration"])
        except Exception:
            pass
    durations: List[float] = []
    for stream in meta.get("streams") or []:
        value = stream.get("duration")
        if value and value != "N/A":
            try:
                durations.append(float(value))
            except Exception:
                pass
    if durations:
        return max(durations)
    raise RuntimeError("Could not determine video duration from ffprobe output")


def extract_audio(video: Path, audio_out: Path) -> None:
    run([
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio_out),
    ])


def transcribe_with_faster_whisper(
    audio_path: Path,
    *,
    model_name: str,
    language: Optional[str],
    device: str,
    compute_type: str,
    word_timestamps: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "faster-whisper is not installed. Install with `pip install faster-whisper`, "
            "or rerun with --no-transcribe / --transcript-json."
        ) from exc

    eprint(f"Loading faster-whisper model={model_name} device={device} compute_type={compute_type} ...")
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    lang = None if not language or language.lower() in {"auto", "detect"} else language
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=lang,
        vad_filter=True,
        word_timestamps=word_timestamps,
    )

    segments: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []
    for i, seg in enumerate(segments_iter, start=1):
        item = {
            "id": i,
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text.strip(),
        }
        segments.append(item)
        if word_timestamps and getattr(seg, "words", None):
            for w in seg.words:
                words.append(
                    {
                        "segment_id": i,
                        "start": None if w.start is None else float(w.start),
                        "end": None if w.end is None else float(w.end),
                        "word": w.word,
                        "probability": None if w.probability is None else float(w.probability),
                    }
                )

    info_dict = {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "duration_after_vad": getattr(info, "duration_after_vad", None),
        "model": model_name,
        "engine": "faster-whisper",
    }
    return segments, words, info_dict


def load_transcript_json(path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data, [], {"engine": "provided-json"}
    segments = data.get("segments") or data.get("transcript_segments") or []
    words = data.get("words") or data.get("transcript_words") or []
    meta = data.get("metadata") or {"engine": "provided-json"}
    return segments, words, meta


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_transcript_outputs(out_dir: Path, segments: List[Dict[str, Any]], words: List[Dict[str, Any]], formats: set[str]) -> None:
    if not segments:
        return

    raw_lines: List[str] = []
    for seg in segments:
        raw_lines.append(f"[{fmt_hms(seg['start'])} - {fmt_hms(seg['end'])}] {seg.get('text','').strip()}")
    (out_dir / "transcript_raw.txt").write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
    write_json(out_dir / "transcript_segments.json", segments)
    if words:
        write_json(out_dir / "transcript_words.json", words)

    if "srt" in formats:
        chunks = []
        for idx, seg in enumerate(segments, start=1):
            chunks.append(f"{idx}\n{fmt_srt(seg['start'])} --> {fmt_srt(seg['end'])}\n{seg.get('text','').strip()}\n")
        (out_dir / "subtitles.srt").write_text("\n".join(chunks), encoding="utf-8")

    if "vtt" in formats:
        chunks = ["WEBVTT\n"]
        for seg in segments:
            chunks.append(f"{fmt_vtt(seg['start'])} --> {fmt_vtt(seg['end'])}\n{seg.get('text','').strip()}\n")
        (out_dir / "subtitles.vtt").write_text("\n".join(chunks), encoding="utf-8")


def make_slices(duration: float, period: float, frame_position: str, screenshots_dir: Path) -> List[SliceSpec]:
    if period <= 0:
        raise ValueError("slice period must be > 0")
    count = int(math.ceil(duration / period))
    specs: List[SliceSpec] = []
    for i in range(count):
        start = i * period
        end = min(duration, (i + 1) * period)
        if frame_position == "start":
            frame_time = start
        elif frame_position == "end":
            frame_time = max(start, end - 0.10)
        else:
            frame_time = start + max(0.0, (end - start) / 2.0)
        frame_time = min(max(0.0, frame_time), max(0.0, duration - 0.05))
        label = fmt_file_ts(frame_time)
        screenshot_name = f"slice_{i+1:04d}_{label}.jpg"
        specs.append(
            SliceSpec(
                index=i + 1,
                start=start,
                end=end,
                frame_time=frame_time,
                label=label,
                screenshot=str(Path("screenshots") / screenshot_name),
            )
        )
    return specs


def extract_screenshots(video: Path, specs: Iterable[SliceSpec], out_dir: Path, max_width: int = 0) -> int:
    screenshots_dir = out_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    failures: List[str] = []
    for spec in specs:
        target = out_dir / spec.screenshot
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{spec.frame_time:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
        ]
        if max_width and max_width > 0:
            cmd += ["-vf", f"scale={max_width}:-2"]
        cmd += ["-q:v", "2", str(target)]
        proc = run(cmd, check=False)
        if proc.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            detail = proc.stderr[-500:].strip() or "ffmpeg returned no screenshot file"
            failures.append(f"slice {spec.index} at {spec.frame_time:.3f}s: {detail}")
            continue
        created += 1

    if failures:
        preview = "\n".join(failures[:5])
        remaining = "" if len(failures) <= 5 else f"\n... {len(failures) - 5} more failures"
        raise RuntimeError(f"Failed to extract {len(failures)} screenshot(s):\n{preview}{remaining}")
    return created


def segments_for_slice(segments: List[Dict[str, Any]], start: float, end: float) -> List[Dict[str, Any]]:
    result = []
    for seg in segments:
        try:
            s = float(seg.get("start", 0))
            e = float(seg.get("end", s))
        except Exception:
            continue
        if e > start and s < end:
            result.append(seg)
    return result


def compact_excerpt(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def build_timeline(specs: List[SliceSpec], segments: List[Dict[str, Any]], excerpt_chars: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for spec in specs:
        segs = segments_for_slice(segments, spec.start, spec.end)
        excerpt = compact_excerpt(" ".join(str(s.get("text", "")).strip() for s in segs), excerpt_chars)
        rows.append(
            {
                "slice_index": spec.index,
                "slice_start": spec.start,
                "slice_end": spec.end,
                "slice_start_label": fmt_hms(spec.start),
                "slice_end_label": fmt_hms(spec.end),
                "frame_time": spec.frame_time,
                "frame_time_label": fmt_hms(spec.frame_time),
                "screenshot": spec.screenshot,
                "segment_ids": [s.get("id") for s in segs],
                "transcript_excerpt": excerpt,
            }
        )
    return rows


def write_timeline_outputs(out_dir: Path, rows: List[Dict[str, Any]], formats: set[str]) -> None:
    if "json" in formats:
        write_json(out_dir / "timeline.json", rows)

    if "csv" in formats:
        with (out_dir / "timeline.csv").open("w", encoding="utf-8", newline="") as f:
            fieldnames = [
                "slice_index",
                "slice_start_label",
                "slice_end_label",
                "frame_time_label",
                "screenshot",
                "segment_ids",
                "transcript_excerpt",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                out = {k: row.get(k, "") for k in fieldnames}
                out["segment_ids"] = ",".join(str(x) for x in row.get("segment_ids") or [])
                writer.writerow(out)

    if "markdown" in formats or "md" in formats:
        lines = ["# Video Transcript Timeline", ""]
        for row in rows:
            start = row["slice_start_label"]
            end = row["slice_end_label"]
            frame = row["frame_time_label"]
            screenshot = row["screenshot"]
            lines.extend(
                [
                    f"## {start}–{end}",
                    "",
                    f"![Frame at {frame}]({screenshot})",
                    "",
                    f"- Frame time: `{frame}`",
                    f"- Segment IDs: `{','.join(str(x) for x in row.get('segment_ids') or [])}`",
                    "",
                    "Transcript excerpt:",
                    "",
                    f"> {row.get('transcript_excerpt') or '_No transcript text in this slice._'}",
                    "",
                ]
            )
        (out_dir / "timeline.md").write_text("\n".join(lines), encoding="utf-8")


def write_summary_prompt(out_dir: Path, levels: List[str], video_name: str) -> None:
    prompt = f"""# Summary Prompt for {video_name}

Use the artifacts in this directory to create the requested summary files.

Primary inputs:
- `transcript_raw.txt` — complete raw timestamped transcript
- `transcript_segments.json` — segment-level timing data
- `timeline.md` — screenshot-aligned slice timeline
- `timeline.json` — machine-readable screenshot/transcript map

Requested summary levels: {', '.join(levels) if levels else 'executive, standard, detailed'}

Instructions:
1. Preserve timestamp references for every material claim.
2. Do not invent details absent from the transcript.
3. Cite screenshot paths when a visual moment matters.
4. Keep the raw transcript separate from summaries.
5. Suggested outputs:
   - `summary_executive.md`: concise leadership bullets, decisions, asks, risks.
   - `summary_standard.md`: timestamped chapter/section summaries.
   - `summary_detailed.md`: dense reconstruction with important nuance.
   - `summary_slice_by_slice.md`: one row per screenshot interval when requested.
   - `actions_decisions_risks.md`: if action items, decisions, or risks are present.
"""
    (out_dir / "summary_prompt.md").write_text(prompt, encoding="utf-8")


def extract_clips(video: Path, specs: Iterable[SliceSpec], out_dir: Path) -> int:
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for spec in specs:
        clip = clips_dir / f"slice_{spec.index:04d}_{fmt_file_ts(spec.start)}_to_{fmt_file_ts(spec.end)}.mp4"
        duration = max(0.01, spec.end - spec.start)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{spec.start:.3f}",
            "-i",
            str(video),
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            str(clip),
        ]
        proc = run(cmd, check=False)
        if proc.returncode != 0 or not clip.exists() or clip.stat().st_size == 0:
            raise RuntimeError(
                "Failed to extract clip for slice %s (%s-%s): %s"
                % (spec.index, fmt_hms(spec.start), fmt_hms(spec.end), proc.stderr[-1000:])
            )
        created += 1
    return created


def collect_artifacts(out_dir: Path) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_file():
            artifacts.append(
                {
                    "path": str(path.relative_to(out_dir)),
                    "bytes": path.stat().st_size,
                }
            )
    return artifacts


def make_zip(out_dir: Path) -> Path:
    archive_base = out_dir.parent / out_dir.name
    zip_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=out_dir))
    return zip_path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create timestamped transcript and screenshot timeline artifacts from a local video.")
    parser.add_argument("video", help="Local path to MP4/MOV/MKV/WebM/etc.")
    parser.add_argument("--output-dir", help="Output directory. Default: ./video-transcript-output/<stem>-<timestamp>")
    parser.add_argument("--slice-seconds", default="60s", help="Screenshot/timeline slice interval, e.g. 30s, 1m, 00:01:30. Default: 60s")
    parser.add_argument("--frame-position", choices=["start", "midpoint", "end"], default="midpoint", help="Where in each slice to capture screenshot. Default: midpoint")
    parser.add_argument("--formats", default="markdown,json,srt,vtt,csv", help="Comma-separated outputs: markdown,json,csv,srt,vtt. Timeline markdown/json/csv are always generated. Default: all common formats")
    parser.add_argument("--summary-levels", default="executive,standard,detailed", help="Comma-separated summary levels to request in summary_prompt.md")
    parser.add_argument("--no-transcribe", action="store_true", help="Skip audio extraction/transcription; screenshots/timeline only")
    parser.add_argument("--transcript-json", help="Use an existing transcript JSON instead of running faster-whisper")
    parser.add_argument("--whisper-model", default="small", help="faster-whisper model name. Default: small")
    parser.add_argument("--language", default=None, help="Language code for Whisper, e.g. en. Omit/auto for detection")
    parser.add_argument("--device", default="auto", help="faster-whisper device: auto,cpu,cuda. Default: auto")
    parser.add_argument("--compute-type", default="default", help="faster-whisper compute type. Default: default")
    parser.add_argument("--word-timestamps", action="store_true", help="Enable word-level timestamps when supported")
    parser.add_argument("--screenshot-max-width", type=int, default=0, help="Optional screenshot width in px; 0 preserves native size")
    parser.add_argument("--excerpt-chars", type=int, default=1800, help="Max transcript excerpt chars per timeline slice")
    parser.add_argument("--keep-audio", action="store_true", help="Keep extracted audio.wav. Default removes it after successful transcription")
    parser.add_argument("--extract-clips", action="store_true", help="Also extract one MP4 clip per slice")
    parser.add_argument("--make-zip", action="store_true", help="Create a .zip archive next to the output directory")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    require_executable("ffmpeg")
    require_executable("ffprobe")

    video = Path(args.video).expanduser().resolve()
    if not video.exists() or not video.is_file():
        raise SystemExit(f"Video file not found: {video}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = safe_stem(video)
    out_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else Path.cwd() / "video-transcript-output" / f"{stem}-{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    formats = {x.strip().lower() for x in args.formats.split(",") if x.strip()}
    # The timeline package contract always includes the human-readable,
    # machine-readable, and spreadsheet timeline artifacts. User-requested
    # formats add captions or aliases, but should not remove required outputs.
    formats.update({"markdown", "json", "csv"})
    summary_levels = [x.strip().lower() for x in args.summary_levels.split(",") if x.strip()]
    slice_seconds = parse_time(args.slice_seconds)

    eprint("Probing video...")
    metadata = ffprobe(video)
    duration = get_duration_seconds(metadata)
    write_json(out_dir / "input_video_metadata.json", metadata)

    transcript_segments: List[Dict[str, Any]] = []
    transcript_words: List[Dict[str, Any]] = []
    transcript_info: Dict[str, Any] = {"engine": "none"}

    audio_path = out_dir / "audio.wav"
    if args.transcript_json:
        transcript_segments, transcript_words, transcript_info = load_transcript_json(Path(args.transcript_json).expanduser().resolve())
    elif not args.no_transcribe:
        eprint("Extracting audio...")
        extract_audio(video, audio_path)
        transcript_segments, transcript_words, transcript_info = transcribe_with_faster_whisper(
            audio_path,
            model_name=args.whisper_model,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
            word_timestamps=args.word_timestamps,
        )
        if not args.keep_audio:
            try:
                audio_path.unlink()
            except FileNotFoundError:
                pass

    write_transcript_outputs(out_dir, transcript_segments, transcript_words, formats)

    eprint("Generating slice specs...")
    specs = make_slices(duration, slice_seconds, args.frame_position, out_dir / "screenshots")
    write_json(out_dir / "slice_specs.json", [asdict(s) for s in specs])

    eprint(f"Extracting {len(specs)} screenshots...")
    screenshot_count = extract_screenshots(video, specs, out_dir, max_width=args.screenshot_max_width)

    eprint("Building timeline...")
    rows = build_timeline(specs, transcript_segments, args.excerpt_chars)
    write_timeline_outputs(out_dir, rows, formats)
    write_summary_prompt(out_dir, summary_levels, video.name)

    if args.extract_clips:
        eprint("Extracting clips...")
        clip_count = extract_clips(video, specs, out_dir)
    else:
        clip_count = 0

    zip_path = (out_dir.parent / f"{out_dir.name}.zip") if args.make_zip else None

    manifest: Dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_video": str(video),
        "output_dir": str(out_dir),
        "duration_seconds": duration,
        "duration_label": fmt_hms(duration),
        "slice_seconds": slice_seconds,
        "frame_position": args.frame_position,
        "formats": sorted(formats),
        "summary_levels": summary_levels,
        "transcription": transcript_info,
        "segment_count": len(transcript_segments),
        "word_count": len(transcript_words),
        "screenshot_count": screenshot_count,
        "expected_screenshot_count": len(specs),
        "clip_count": clip_count,
        "zip": str(zip_path) if zip_path else None,
        "artifacts": [],
        "external_artifacts": [
            {
                "path": str(zip_path),
                "kind": "zip_package",
                "note": "Created next to output_dir; not included inside output_dir artifact inventory.",
            }
        ] if zip_path else [],
        "notes": [],
    }
    if args.no_transcribe:
        manifest["notes"].append("Transcription skipped via --no-transcribe.")
    if not transcript_segments and not args.no_transcribe:
        manifest["notes"].append("No transcript segments were generated; check audio or transcription settings.")

    # Write once before collecting, then rewrite with full artifact inventory.
    write_json(out_dir / "manifest.json", manifest)
    manifest["artifacts"] = collect_artifacts(out_dir)
    write_json(out_dir / "manifest.json", manifest)

    if zip_path:
        zip_path = make_zip(out_dir)
        eprint(f"Created ZIP: {zip_path}")

    print(json.dumps({"output_dir": str(out_dir), "manifest": str(out_dir / "manifest.json"), "zip": str(zip_path) if zip_path else None}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
