# BeatForge Studio

BeatForge is a local Windows studio console for generating and reviewing five-difficulty Beat Saber Standard packs. The website and the default `beatforge` CLI invoke the bundled `beat-saber-mapping` premium pipeline (`generate_map.py --profile official-premium --full-spread`). They do not use the legacy `beatforge.place` chart generator unless you pass `--legacy`.

The WebMCP Challenge edition was built as a solo project with OpenAI Codex as the only AI coding assistant. In my own testing, MCP access makes generation iteration nearly 10 times faster than my previous manual workflow. I estimate the combined analysis, constraints, and review process produces roughly 100 times better consistency and playable quality. These are project observations, not a controlled industry benchmark.

Live rights-safe demo: <https://vladvrx.github.io/beatforge-codex/>. The full FastAPI studio can still be run locally or deployed with `render.yaml`.

Every run is sample-based, deterministic, and refusal-gated. Uncertain timing returns `needs_anchors`. Missing or unreadable artwork returns `needs_palette`. Same-hand flow conflicts, inward-facing handclaps, saber collisions, arc or chain ownership errors, bomb paths, walls, vision blocks, schema errors, and timing failures block the output.

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python skills\beat-saber-mapping\scripts\bootstrap.py --tier core
```

Optional audio models (Beat This, BeatNet+, All-In-One, Demucs) need a working
local PyTorch install. If `torch_python.dll` fails to load from the bootstrap
`--target` cache, install Torch into the project venv instead of weakening
timing thresholds:

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cpu
python skills\beat-saber-mapping\scripts\bootstrap.py --tier models
```

GPU wheels are optional. Missing models must surface as `needs_anchors`.
A CUDA wheel that fails to load is not a reason to loosen the 10 ms / 20 ms
gates. Fall back to the CPU index above, or run
`python skills\beat-saber-mapping\scripts\bootstrap.py --tier models --reinstall-torch`.

Demucs is optional (`demucs==4.0.1` via `--tier models`). When it is importable,
BeatForge must use **`htdemucs_6s`** (six stems: drums, bass, guitar, piano,
vocals, other) through `scripts/demucs_stems.py`. Do not use the Demucs CLI
`--two-stems` karaoke mode. If 6-stem weights fail, four-stem `htdemucs` is
recorded as a fallback in `analysis.json` / provenance. PCM16 stems are written
with the stdlib `wave` module so Windows studios do not need TorchCodec.

The portable mapping skill also includes a local PPO reinforcement-learning
environment. I ran it for a week while improving candidate search. The policy
observes beat-grid features, Demucs stem energy, hand kinematics, difficulty
targets, and lookahead context. Rewards favor onset alignment, musical density,
readable flow, and safe two-hand coordination; penalties cover repeated cuts,
recovery violations, handclaps, saber-path collisions, and vision blocks. RL
improves search and iteration, but deterministic validation and human VR tests
remain mandatory release gates.
Full-mix consensus still requires median ≤ 10 ms, p95 ≤ 20 ms, and drift ≤
20 ms; stem trackers cannot pass that gate by themselves.

The optional Codex review key lives in Windows Credential Manager as
`BeatForge:codex`. The studio writes it from the Codex setup dialog; it never
prints it back.

## Failure recovery

| Studio state | What happened | What to do |
|---|---|---|
| `needs_anchors` | Tracker ensemble missed median ≤ 10 ms or p95 ≤ 20 ms | Confirm opening, middle, and ending beat/time pairs. Do not generate a CustomLevels folder until `timing_verified`. |
| `needs_palette` | No readable cover or ΔE pair | Add embedded art, approve a Cover Art Archive match, or run an AI mood palette. Audio is not sent for that step. |
| `corpus_incomplete` | Official library index failed | Re-run `official_corpus.py sync` against the local Beat Saber install and inspect the report. Unexplained extraction failures block premium generation. |
| `invalid` | A hard mapping gate failed | Open QA findings. Same-hand flow, handclaps, and collisions cannot be dismissed by an AI review. |
| Torch import / `WinError 126` | Managed `--target` cache has the wrong ABI | Install Torch into `.venv` from the CPU index, or pass `--reinstall-torch`. Do not import the broken cache. |

`release_candidate` is disabled in this studio. Enable it only with `BEATFORGE_AI_RELEASE_ROUTE=1` when maps go through independent AI review. Software never assigns that state from a simulated playtest.

```powershell
$env:PYTHONPATH = "src"
uvicorn beatforge.api:app --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001/>.

The site uses the installed Beat Saber library and private official corpus. Build or refresh it with:

```powershell
python skills\beat-saber-mapping\scripts\official_corpus.py sync --game-root "C:\path\to\Beat Saber"
python skills\beat-saber-mapping\scripts\official_corpus.py report
```

Exact official maps and first-party audio remain in the excluded local cache. The project contains only tools, schemas, aggregate profiles, and portable skill copies.

Copy a generated map folder into `data/imports/` to attach it for headset evidence without writing into CustomLevels. The studio lists those folders next to installed BeatForge maps.

## Studio pipeline

```powershell
python skills\beat-saber-mapping\scripts\analyze_audio.py song.ogg
python skills\beat-saber-mapping\scripts\generate_map.py song.ogg --out output --profile official-premium --full-spread
python skills\beat-saber-mapping\scripts\validate_map.py output
```

Album colors come from embedded art or an exact, official MusicBrainz and Cover Art Archive match. The two saber colors must clear ΔE2000 30 normally and 20 under protanopia and deuteranopia simulation. When no confident artwork exists, OpenAI Codex can propose a mood palette from approved metadata and the local analysis summary; audio is not sent for that step.

## Codex review

The Codex review action prepares a hash-bound bundle and shows the model, files, transfer size, cost ceiling, and rights attestation before a run. The credential is stored in Windows Credential Manager and injected only into the Codex child process. The reviewer is read-only and cannot dismiss a local hard failure.

Provider setup fetches the models available to the user’s account and pins the selected model in provenance. It never silently falls back to a different model.

## API

- `POST /api/generate`
- `GET /api/jobs/{id}`
- `GET /api/jobs/{id}/events`
- `POST /api/jobs/{id}/anchors`
- `GET /api/jobs/{id}/palette`
- `POST /api/jobs/{id}/palette/approve`
- `POST /api/jobs/{id}/palette/suggest/{provider}`
- `POST /api/jobs/{id}/reviews/{provider}/prepare`
- `POST /api/jobs/{id}/reviews/{provider}/run`
- `GET|PUT|DELETE /api/settings/providers/{provider}`
- `GET /api/beat-saber/playtest-maps`
- `POST /api/jobs/import`
- `POST /api/jobs/{id}/playtests`
- `POST /api/jobs/{id}/install`
- `GET /api/jobs/{id}/download`

## WebMCP Challenge edition

BeatForge exposes a human-agent collaboration surface through the browser's
WebMCP API. The human supplies audio and creative intent; an agent can find
catalog metadata and album art from a title/artist query, read the studio
context, apply a typed mapping plan, start a run, inspect the safety-gated
result, and record only headset evidence explicitly supplied by the human.
The finder may import a provider's short preview, but never infers or rips a
full copyrighted recording from metadata. The agent cannot claim that a map is
playable without human evidence.

The implementation is in [`web/webmcp.js`](web/webmcp.js) and uses the
standard `document.modelContext.registerTool()` API. The visible collaboration
panel shows registration status, exposed tool names, and recent tool activity.
When the browser does not provide WebMCP, the rest of BeatForge remains usable
and the panel explains how to enable it.

For a rights-safe browser walkthrough, open the studio and click **Load
collaboration demo**. This loads a synthetic 30-second groove and exercises
the same plan, preview, review, and human-evidence boundary without requiring
a local audio file or Beat Saber installation. The full local pipeline remains
available after uploading a mastered track. The public catalog path only supplies
metadata, artwork, and a permitted short preview. Full generation requires the
user's complete rights-cleared MP3 or another supported local audio file.

The challenge pitch, workflow, testing instructions, and submission checklist
are in [`docs/webmcp-challenge.md`](docs/webmcp-challenge.md). A minimal Render
deployment definition is included in [`render.yaml`](render.yaml).

To test WebMCP itself, use ChatGPT's in-app browser or Chrome 149+ with
`chrome://flags/#enable-webmcp-testing` enabled. Ask the visiting agent to
call `get_studio_context`, `set_mapping_plan`, and `generate_beatmap` while the
activity panel is visible.

The local metadata endpoint caches the matched cover and the generator uses
that exact image to derive and validate the Beat Saber saber/environment color
scheme. A full mastered track is still required for a complete map run.

`release_candidate` is off unless `BEATFORGE_AI_RELEASE_ROUTE=1`. When the Codex review route is on, it still requires verified timing, an approved palette, zero unresolved local and Codex findings, recorded full-speed clears, slow Expert/Expert+ when those maps exist, and a separate tester’s fresh sight-read.

## Portable skills and tests

The portable mapping skill lives in `.codex/skills/beat-saber-mapping` and is kept byte-for-byte aligned with the canonical `skills/beat-saber-mapping` tree. CI verifies the Codex copy against `skill-lock.json`.

```powershell
pip install -e ".[dev]"
python -m playwright install chromium
pytest tests
pytest skills\beat-saber-mapping\tests
```

CI excludes live `network`, `hardware`, `codex`, and `corpus` markers.
Those tests still run locally and skip themselves when the machine cannot
satisfy them. Playwright covers generate-without-audio, needs_anchors
copy, the Codex review flow, the unmodified OpenAI logo, focus, the live
region, and cost/rights gates in headless Chromium.

Windows-specific corpus paths, Codex Credential Manager names, CustomLevels
elevation, and GPU recovery are in `docs/WINDOWS.md`.
