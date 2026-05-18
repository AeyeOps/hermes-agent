---
name: video-transcript-timeline
description: Use when ingesting local or remotely accessible MP4/video files to create a timestamp-aligned transcript, screenshot timeline, slices, summaries at multiple fidelity levels, and packaged outputs.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [video, mp4, transcript, whisper, screenshots, timeline, ffmpeg, summaries]
    related_skills: [youtube-content, ocr-and-documents]
---

# Video Transcript Timeline

## Overview

Use this skill to turn an accessible video source into a review-ready, time-synchronized evidence package:

- full raw transcript with timestamps
- SRT/VTT captions
- screenshot/keyframe extracts at a user-selected interval
- timeline mapping screenshots to transcript slices
- summaries at multiple fidelity levels
- structured outputs such as Markdown, JSON, CSV, captions, and optional Drive/Doc packages

This skill is source-agnostic. The video may come from local disk, Google Drive, GitHub, Gmail/email attachment, a direct URL, a Telegram/Chat attachment, or another accessible source. The agent first retrieves the video to a local working file, then runs the local processing pipeline.

## When to Use

Use when the user asks to:

- ingest an `.mp4`, `.mov`, `.mkv`, `.webm`, or meeting recording
- extract a full transcript from a video
- align transcript text with timestamps and screenshots
- slice a video into time-based review chunks
- create a screenshot timeline every N seconds/minutes
- produce chapter summaries, detailed notes, or executive summaries from video content
- package meeting recordings, demos, webinars, interviews, trainings, or screen recordings for review

Do **not** use this skill for:

- YouTube videos with built-in captions where the user only wants a simple transcript or summary — use `youtube-content` first.
- Pure document OCR — use `ocr-and-documents`.
- Creative video generation or transformation — use `ascii-video` or other creative media skills.

## Required Intake

Before processing, collect or infer the following. Ask the user only for missing fields that materially affect the run. If the user gave enough info, proceed with defaults.

1. **Video source** — where the MP4/video is.
   - Examples: local path, Google Drive file ID/link, Gmail message+attachment, GitHub URL/path, direct URL, uploaded Telegram attachment path.
2. **Output destination** — where to save the package.
   - If omitted, default to `./video-transcript-output/<video-stem>-YYYYMMDD-HHMMSS/`.
   - If the user wants Google Drive, process locally first, then upload the package or selected outputs.
3. **Screenshot interval** — time slice period for screenshots.
   - If omitted, default to `60s` for meetings/webinars, `30s` for demos/screen recordings, or `15s` for short UI walkthroughs.
   - Accept formats like `15s`, `30s`, `1m`, `2m`, `00:01:30`.
4. **Screenshot position inside each slice** — start, midpoint, or end.
   - Default: `midpoint` to avoid black transition frames at exact boundaries.
5. **Transcript engine / fidelity**.
   - Default: local `faster-whisper` if available. Use model `small` for speed, `medium`/`large-v3` for higher fidelity.
   - If exact word timing is needed, enable word timestamps.
6. **Output formats**.
   - Defaults: `markdown,json,srt,vtt,csv`.
   - Timeline Markdown, JSON, and CSV are package-required and should still be generated when the user asks for only captions or a subset.
   - Optional: Google Doc, DOCX, ZIP, clips, screenshot contact sheet.
7. **Summary levels**.
   - Defaults: `executive`, `standard`, `detailed`.
   - Optional: `slice-by-slice`, `chapter`, `action-items`, `decisions`, `risks`.

### Clarifying Question Template

If required details are missing, ask a compact multi-choice/open question:

> I can build the transcript/timeline package. Please send:
> - video source or attachment
> - output destination (local path or Drive folder)
> - screenshot interval (default 60s)
> - summary fidelity: executive / standard / detailed / all
> - desired output: markdown / JSON / SRT/VTT / Google Doc / ZIP

If the source is already attached in the platform and accessible, do not ask for the source again; use the attachment path or download tool.

## Source Acquisition

The processing script expects a **local file path**. Use the right tool/source step first.

### Local file

Use the path directly after verifying it exists.

```bash
python3 SKILL_DIR/scripts/process_video_timeline.py \
  /path/to/video.mp4 \
  --output-dir /path/to/output \
  --slice-seconds 60
```

### Google Drive

1. Use Drive tools to identify/download the file.
2. For native Drive file downloads, request the original or a usable export format.
3. Save to local disk.
4. Run the script on the local file.
5. If requested, upload the output folder artifacts back to Drive.

Typical tools:

- `mcp_workspace_mm_search_drive_files` to find the recording by name.
- `mcp_workspace_mm_get_drive_file_download_url` to download it locally.
- `mcp_workspace_mm_create_drive_folder` and `mcp_workspace_mm_create_drive_file`/upload flows to store results.

### Gmail / email attachment

1. Search/read the message.
2. Identify the video attachment ID.
3. Download attachment locally.
4. Run the script.
5. Optionally draft/send a reply with outputs.

Typical tools:

- `mcp_workspace_mm_search_gmail_messages`
- `mcp_workspace_mm_get_gmail_message_content`
- `mcp_workspace_mm_get_gmail_attachment_content`

### GitHub

Use one of:

- `gh release download`, for release assets.
- `gh api`/raw URL, for repository files or LFS pointers.
- `curl -L`, for direct raw/public assets.

Verify that the result is an actual video file, not an HTML page or Git LFS pointer text.

### Direct URL

Use `curl -L --fail --output video.mp4 URL` for normal files.

For streaming/video pages, use `yt-dlp` only when appropriate and allowed:

```bash
yt-dlp -o '%(title).80s.%(ext)s' 'URL'
```

### Messaging-platform attachments

If the current platform provides a local media path, use it directly. If it provides only a URL or attachment ID, download it first with the platform-specific tool.

## Local Pipeline

### 1. Check prerequisites

Required:

```bash
ffmpeg -version
ffprobe -version
python3 --version
```

Recommended transcription dependency:

```bash
python3 - <<'PY'
import importlib.util
print('faster_whisper', bool(importlib.util.find_spec('faster_whisper')))
PY
```

Install if missing and permitted:

```bash
pip install faster-whisper
```

Optional:

```bash
pip install scenedetect opencv-python pillow
```

### 2. Run the helper script

```bash
python3 SKILL_DIR/scripts/process_video_timeline.py \
  input.mp4 \
  --output-dir ./video-transcript-output/input-$(date +%Y%m%d-%H%M%S) \
  --slice-seconds 60 \
  --frame-position midpoint \
  --whisper-model small \
  --summary-levels executive,standard,detailed \
  --formats markdown,json,srt,vtt,csv
```

For faster screenshot/timeline extraction without transcription:

```bash
python3 SKILL_DIR/scripts/process_video_timeline.py input.mp4 --no-transcribe
```

For higher transcription fidelity:

```bash
python3 SKILL_DIR/scripts/process_video_timeline.py input.mp4 \
  --whisper-model large-v3 \
  --word-timestamps \
  --language en
```

### 3. Agent summary pass

The helper script creates raw artifacts plus `summary_prompt.md`. After it runs, read `timeline.md`, `transcript_segments.json`, and `summary_prompt.md`, then produce the requested summaries using the agent. Do not rely on the helper script for abstractive summaries unless a future local summarizer is added.

Recommended summary outputs:

- `summary_executive.md` — 5-10 bullets, key outcomes, asks, decisions.
- `summary_standard.md` — chapter/section overview with timestamps.
- `summary_detailed.md` — dense narrative summary preserving important details.
- `summary_slice_by_slice.md` — one entry per screenshot/time slice.
- `actions_decisions_risks.md` — if meeting/workflow oriented.

## Output Package Contract

The output directory should contain:

```text
output-dir/
  input_video_metadata.json
  audio.wav                         # optional/intermediate
  transcript_raw.txt                # full text with segment timestamps
  transcript_segments.json          # segment-level timestamps/text
  transcript_words.json             # optional word-level timings
  subtitles.srt
  subtitles.vtt
  screenshots/
    slice_0001_00-00-30.jpg
    slice_0002_00-01-30.jpg
  timeline.json                     # slice -> screenshot -> transcript segments
  timeline.csv
  timeline.md                       # human-readable screenshot/transcript timeline
  summary_prompt.md                 # prompt/context for agent summary pass
  summary_executive.md              # agent-written when requested
  summary_standard.md               # agent-written when requested
  summary_detailed.md               # agent-written when requested
  manifest.json                     # run options and artifact inventory
```

If the user requests clips, add:

```text
clips/
  slice_0001_00-00-00_to_00-01-00.mp4
```

If the user requests a portable package, add:

```text
<video-stem>-transcript-timeline.zip
```

## Timing and Alignment Rules

- Treat the video file duration from `ffprobe` as the timing source of truth.
- Transcript segments inherit Whisper/faster-whisper start/end timestamps.
- Screenshot slices use the requested interval and frame-position rule:
  - `start`: frame at slice start
  - `midpoint`: frame halfway through slice
  - `end`: frame just before slice end
- Timeline rows map each slice `[slice_start, slice_end)` to transcript segments where:
  - `segment.end > slice_start` and `segment.start < slice_end`
- Preserve the full raw transcript separately. Summaries may omit detail, but the raw transcript must be complete.
- If audio/video start offsets are discovered, record them in `manifest.json` and state the adjustment in the final report.

## Summary Fidelity Definitions

### Executive

Use for leadership review. Keep it short:

- 5-10 bullets
- top decisions/outcomes
- open asks
- risks/blockers
- links to key timestamps/screenshots

### Standard

Use for team review:

- timestamped chapters
- 1 paragraph per chapter
- key examples or demo steps
- action items and owner references if present

### Detailed

Use for audit/reconstruction:

- preserve important nuance
- include timestamp ranges
- cite representative screenshot paths
- include decisions, questions, objections, dependencies, and unresolved items

### Slice-by-slice

Use for review workflows and evidence trails:

- one entry per screenshot interval
- timestamp range
- screenshot filename
- transcript excerpt
- concise interpretation

## Output Format Guidance

Default to filesystem artifacts. Add platform-native outputs only when requested.

### Markdown

Best for review, GitHub, and agent follow-up. Use relative links to screenshots:

```markdown
## 00:05:00-00:06:00

![00:05:30](screenshots/slice_0006_00-05-30.jpg)

Transcript excerpt:
> ...
```

### JSON

Best for automation. Preserve exact timestamps as floats and display labels.

### CSV

Best for spreadsheet review. Include:

- slice_index
- slice_start
- slice_end
- screenshot
- transcript_excerpt
- segment_ids

### SRT/VTT

Best for captions and video players. Generate from transcript segments.

### Google Doc / Drive

If requested:

1. Create/upload a Drive folder for the output package.
2. Upload screenshots and raw artifacts.
3. Create or import a Google Doc with the summary and timeline.
4. Include a link to the folder and doc in the final response.

### ZIP

Use when the user wants a single downloadable bundle. Verify the archive exists and include `MEDIA:/path/to/archive.zip` in the final response when on a messaging platform that supports media.

## Quality Checks

Before reporting completion:

- Confirm the output directory exists.
- Confirm `manifest.json`, `timeline.md`, and `timeline.json` exist.
- If transcription was requested, confirm transcript files are non-empty.
- Confirm screenshot count roughly matches `ceil(duration / slice_seconds)`.
- Spot-check at least one early, middle, and late timeline row.
- If summaries were produced, verify they cite timestamps and do not contradict the transcript.
- If packaging/upload was requested, verify the archive or Drive links are accessible.

## Common Pitfalls

1. **Processing a Drive/GitHub HTML page instead of the video.** Verify MIME/type with `file` and `ffprobe` before running the pipeline.
2. **Exact-boundary screenshots are black.** Use `--frame-position midpoint` by default.
3. **Whisper hallucination on silent sections.** Preserve segment timestamps; mark long gaps/silence rather than forcing text.
4. **Huge videos exceed context.** Do not paste full transcript into chat. Write artifacts to disk and summarize in chunks.
5. **Overwriting prior runs.** Use timestamped output directories unless the user explicitly requests overwrite.
6. **Assuming summary equals transcript.** Always save the full raw transcript separately before summarizing.
7. **Missing ffmpeg.** Install or ask for a different environment; the pipeline depends on ffmpeg/ffprobe.
8. **Word timestamps are slower.** Enable only when the user needs word-level alignment.

## Verification Checklist

- [ ] Source video retrieved locally and verified with `ffprobe`.
- [ ] User/default output destination selected.
- [ ] Screenshot interval and frame-position recorded.
- [ ] Transcript generated or a no-transcribe exception explicitly recorded.
- [ ] Screenshots extracted and named with timestamps.
- [ ] Timeline maps transcript segments to screenshot intervals.
- [ ] Required formats generated.
- [ ] Summary fidelity levels produced if requested.
- [ ] Final response includes output location and key artifact list.
