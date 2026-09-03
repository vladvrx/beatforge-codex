# BeatForge + WebMCP Challenge

## Short elevator pitch (200 characters or less)

BeatForge uses WebMCP to let people and agents co-create Beat Saber maps: humans set intent, agents configure and review, and only humans certify playability.

The longer Devpost project story is in [`docs/devpost-project-story.md`](devpost-project-story.md).

## 300-word elevator pitch

BeatForge turns a mastered song into a Beat Saber map, but the best chart is not something an agent should invent alone. It is a conversation between musical taste and measurable safety. In BeatForge’s WebMCP edition, a person uploads audio (or opens a rights-safe demo), describes the movement they want, and stays in control of the creative brief. Their browser exposes typed collaboration tools that let an agent read the current studio context, apply a mapping plan, start a generation run, inspect timing and hard-gate results, and record only the playtest evidence the human supplies.

This is a strong WebMCP fit because BeatForge is already a structured workflow with meaningful state, irreversible-looking mistakes, and many small UI decisions. Without WebMCP, an agent must scrape labels, guess which controls matter, and hope a click landed on the right difficulty. With WebMCP, the page declares the operations and schemas directly. The agent can make a precise change such as “keep Expert readable, push the chorus, and use a deterministic seed,” then the person can see the form change before generation begins.

Together they can iterate on musical intent and technical execution in one shared surface: a human supplies taste, consent, and headset judgment; the agent handles translation, configuration, progress interpretation, and comparison. That makes an agent useful without making it authoritative. BeatForge’s safety gates remain local, and no tool can claim that a chart is playable when nobody has played it.

Implementation is transparent. `web/webmcp.js` registers six JSON-Schema tools with `document.modelContext`, returns JSON summaries, and logs calls. The FastAPI pipeline remains the source of truth for audio runs. A synthetic browser demo exercises collaboration without copyrighted audio, credentials, or a local Beat Saber installation. The repository includes an MIT license, setup instructions, Render configuration, and a browser test contract.

## WebMCP workflow

The page registers these tools when the browser exposes WebMCP:

- `get_studio_context` — read the current track, creative brief, mapping plan, run state, QA summary, and next action.
- `set_mapping_plan` — update title, artist, mapper, seed, selected difficulties, and creative brief.
- `load_collaboration_demo` — load a synthetic, rights-safe 30-second session.
- `generate_beatmap` — start either the real local-audio pipeline or the demo preview.
- `review_current_beatmap` — return safety-gated status and QA information.
- `record_human_playtest` — record evidence supplied by a human after they actually play.

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
- Verification: use ChatGPT’s in-app browser or Chrome WebMCP testing and confirm the six tools are discoverable and callable.
