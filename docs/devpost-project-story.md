# About the project

## Inspiration

Beat mapping sits between music, interface design, and safety engineering. A mapper has to hear a song, decide how its energy should move through space, and then check hundreds of technical rules. BeatForge started with a question: can an agent handle the repetitive translation work while the mapper keeps creative control?

I built the BeatForge Beat Saber level-generation pipeline alone, with OpenAI Codex as the only AI coding assistant in the development workflow. In my own testing, adding MCP access reduced generation setup and iteration time to roughly one-tenth of my previous manual process. I also estimate a roughly 100x improvement in consistency and usable output quality compared with that earlier workflow. Those are my project observations, not the result of a controlled industry benchmark.

The WebMCP Challenge made the idea concrete. Instead of guessing which button to click, an agent can work with the studio's actual state, typed inputs, timing results, and human feedback. BeatForge gives a mapper and an agent one shared musical workspace.

## What it does

BeatForge is designed for any full-length song that the user owns or has permission to use. The complete mastered track is uploaded locally as an MP3, WAV, OGG, FLAC, M4A, or MP4. The local pipeline analyzes the whole recording and generates a five-difficulty Beat Saber Standard spread.

The public WebMCP connection has a separate, rights-safe preview path. It can find public metadata and album artwork and import a permitted short preview. That preview is limited to about 30 seconds and is used for discovery and the browser demo. It is not a full-recording downloader. A complete level run uses the user's full local audio file.

The person chooses the song, mood, movement, mapper name, seed, and difficulties. The agent can then:

- read the current studio context;
- find song metadata and prepare cover-derived colors;
- apply a typed mapping plan;
- load a synthetic 30-second collaboration demo;
- start a real generation run or browser preview;
- review timing, QA, and safety-gate results; and
- record playtest evidence that a human explicitly supplies.

The agent can explain what still needs to happen, but it cannot claim that it played the map. Human headset judgment remains essential.

## How we built it

FastAPI owns real audio uploads, background jobs, progress events, validation, packaging, and the local Beat Saber workflow. The analyzer decodes the full file onto a 44.1 kHz integer-sample timeline. It can run the enabled beat trackers on the full mix and on separated stems.

Demucs is the source-separation step. BeatForge uses the `htdemucs_6s` model when the optional model dependencies are installed, producing drums, bass, guitar, piano, vocals, and other stems. Stem analysis helps expose percussion, vocal, and harmonic disagreement in difficult passages. The full-mix tracker ensemble still has to meet the timing gate, so stems never hide uncertain timing.

I also ran local reinforcement-learning environments for a full week to improve the mapping search. The PPO policy saw beat-grid features, Demucs stem energy, the current hand kinematics, target difficulty, and a short lookahead window. Its reward favored onset alignment, musical density, readable flow, and safe two-hand coordination. It penalized repeated cuts, insufficient recovery, handclaps, saber-path collisions, vision blocks, and other unsafe transitions. I used the learned behavior to improve candidate generation and iteration. Deterministic validators and human VR testing remained the release gates.

The premium generator builds Easy, Normal, Hard, Expert, and Expert+ independently. It plans notes, arcs, chains, bombs, walls, and lighting around hand reach, saber paths, recovery, vision, and body movement. Validation blocks unsafe or structurally invalid output. A generated chart remains a `playtest_candidate` until a human completes VR and fresh sight-read checks.

The browser layer lives in `web/webmcp.js`. It feature-detects `document.modelContext`, registers eight JSON-Schema tools, returns compact JSON results, and records calls in the page activity log. The main page shows the plan, WebMCP status, exposed tools, album artwork, and review results. The public GitHub Pages build uses a synthetic groove so judges can test the collaboration loop without copyrighted audio, credentials, a local model cache, or a Beat Saber installation.

## Challenges we ran into

WebMCP is still experimental, so the page has to remain useful when the browser does not expose `document.modelContext`. The app registers tools when supported and explains the setup when they are unavailable.

The biggest product challenge was the difference between a public browser preview and a real full-song mapping run. A catalog lookup may provide a short permitted preview, but it cannot provide the full recording for a level. We made that boundary visible in the UI and tool descriptions. Users upload the complete rights-cleared MP3 or another supported audio file for full generation.

Demucs and the other audio models also bring large local dependencies, especially on Windows. BeatForge treats them as optional, records which analyzer ran, and never lowers the timing thresholds when a model is unavailable.

The hardest safety decision was what an agent must never be allowed to say. A chart can pass structural checks and still feel wrong in a headset. Software reports gates and evidence. A human reports playability.

## Accomplishments that we're proud of

I built the complete mapping pipeline and the WebMCP challenge layer as a solo project. The agent can now change a real mapping plan through typed operations instead of fragile UI guesses. The person sees those changes immediately, can keep or reject the direction, and can ask for another run without losing the conversation context.

The week-long local RL runs gave the generator a stronger starting point for musical and physical decisions, while the hard safety contract prevented a learned policy from bypassing constraints.

The activity panel makes the collaboration inspectable. The full-song path preserves local audio control, while the short-preview path gives judges a safe browser demo. The repository includes the source implementation, browser tests, setup instructions, an MIT license, and deployment definitions.

## What we learned

WebMCP tool descriptions are part of the product experience. A useful schema tells an agent what an operation means and tells the person what will change. Application state matters more than a larger page scrape. Once the page exposes a compact context object, the agent can reason about the next step without reconstructing the interface from pixels.

Demucs also changed how I think about mapping analysis. Separating the full mix does not replace timing verification, but it gives the mapper better evidence about which musical layer should drive a phrase. Finally, a demo is most useful when it is honest about what it simulates. A 30-second preview can demonstrate collaboration. It must not masquerade as a validated full release.

## What's next for BeatForge for Beat Saber

Next, I want agents to compare two mapping plans, explain why a generated section failed a gate, and turn human headset notes into structured revision requests. I also want to keep more audio analysis local while sharing only the summaries needed for collaboration.

Longer term, BeatForge should feel like a rehearsal partner. The person brings taste and embodied knowledge. The agent tracks decisions, surfaces tradeoffs, and helps turn a musical idea into a chart that someone can actually play.
