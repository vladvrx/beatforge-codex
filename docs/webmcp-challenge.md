# BeatForge + WebMCP Challenge

## Short elevator pitch (200 characters or less)

BeatForge uses WebMCP to let people and agents co-create Beat Saber maps: humans set intent, agents configure and review, and only humans certify playability.

The longer Devpost project story is in [`docs/devpost-project-story.md`](devpost-project-story.md).

## 300-word elevator pitch

BeatForge is a solo-built studio that turns any rights-cleared full song into a Beat Saber map. I built the pipeline myself, with OpenAI Codex as the only AI coding assistant. The hard part is translating musical energy into readable movement while checking timing, reach, collision, and flow rules across five difficulties.

WebMCP makes this a collaboration instead of a button-click macro. A person supplies the song, taste, and headset judgment. An agent reads structured studio state, helps refine the mapping brief, starts a run, interprets safety results, and records only evidence the person explicitly provides.

The input path is clear. A user uploads a mastered track as an MP3, WAV, OGG, FLAC, M4A, or MP4. The WebMCP browser connection can look up metadata, artwork, and a short preview, but it never downloads a full copyrighted recording. The 30-second preview is for discovery and the public demo. Full generation uses the user's local audio file.

BeatForge analyzes the track with sample-level timing. Demucs separates it into drums, bass, guitar, piano, vocals, and other stems. Stem analysis reveals which musical layers drive each section. A full-mix tracker consensus has to pass strict timing gates. The generator then creates Easy through Expert+ charts, checks hand motion and safety constraints, validates the output, and waits for human playtesting before release.

The WebMCP implementation registers eight typed JSON-Schema tools through `document.modelContext.registerTool` and logs every call in the interface. In my testing, MCP access makes generation iteration nearly 10 times faster than my previous manual workflow. I estimate the combined analysis, constraints, and review process produces roughly 100 times better consistency and playable quality. Those are my observed project results, not a controlled industry benchmark.

BeatForge gives agents useful actions while keeping musical authorship and physical safety with the person who will actually play.

## WebMCP workflow

The page registers these tools when the browser exposes WebMCP:

- `get_studio_context` — read the current track, resolved metadata/artwork/palette, creative brief, mapping plan, run state, QA summary, and next action.
- `find_song_metadata` — search public catalog metadata from a title and artist, populate the visible fields, show the album cover, and prepare cover-derived map colors.
- `import_song_preview` — import only the provider's permitted short preview; it is not a full-recording downloader.
- `set_mapping_plan` — update title, artist, mapper, seed, selected difficulties, and creative brief.
- `load_collaboration_demo` — load a synthetic, rights-safe 30-second session.
- `generate_beatmap` — start either the real local-audio pipeline or the demo preview.
- `review_current_beatmap` — return safety-gated status and QA information.
- `record_human_playtest` — record evidence supplied by a human after they actually play.

The local FastAPI flow exposes `GET /api/song-metadata`. It caches the matched
album artwork under the local metadata cache, and a subsequent generation
request carries its `metadataId` so the premium generator derives the map
color scheme from that exact cover. Metadata lookup does not grant permission
to fetch a full copyrighted recording; complete runs still require a local
mastered audio file.

The GitHub Pages demo is <https://vladvrx.github.io/beatforge-codex/>. It builds
the same `web/index.html` and `web/webmcp.js` into a rights-safe static preview;
the full FastAPI app remains available through the included Render definition.

The public demo video is <https://www.youtube.com/watch?v=ZVOPshpw5hY>.

## Test locally

```powershell
$env:PYTHONPATH = "src"
uvicorn beatforge.api:app --host 127.0.0.1 --port 8001
```

Open `http://127.0.0.1:8001/?demo=1` and click **Load collaboration demo**. In ChatGPT’s in-app browser, ask the agent to call `get_studio_context`, refine the plan with `set_mapping_plan`, and call `generate_beatmap`. In Chrome 149+, enable `chrome://flags/#enable-webmcp-testing` first. The real generation flow still accepts local MP3, WAV, OGG, FLAC, M4A, or MP4 files and keeps the existing safety gates.

## Submission checklist

- Live URL: use the deployed GitHub Pages demo at <https://vladvrx.github.io/beatforge-codex/>. The included `render.yaml` remains available for the full FastAPI app.
- Repository: keep `LICENSE` at the repository root and publish the source publicly.
- Demo: submit the public sub-three-minute video at <https://www.youtube.com/watch?v=ZVOPshpw5hY>, which shows the demo session, tool discovery, a plan change, generation, review, and human-evidence boundary.
- Verification: use ChatGPT’s in-app browser or Chrome WebMCP testing and confirm the eight tools are discoverable and callable.
