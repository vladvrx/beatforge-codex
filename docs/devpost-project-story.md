# About the project

## Inspiration

Beat mapping sits between music, interface design, and safety engineering. A mapper has to hear a song, decide how its energy should move through space, and then check hundreds of small technical rules. That combination made BeatForge a good test for a different question: what happens when an agent can help with the tedious translation work without taking over the creative decision?

The WebMCP Challenge sharpened that question. Most web automation asks an agent to guess which button or field to use. BeatForge already has meaningful state, meaningful constraints, and a human who needs to trust the result. It felt like the right place to let a person and an agent work on the same musical idea together.

For this Challenge edition, the implementation and submission materials were built with OpenAI Codex only.

## What it does

BeatForge takes mastered audio and turns it into Beat Saber difficulty maps through its existing local pipeline. The person chooses the song, describes the intended movement and mood, and selects the difficulties they want. The agent can then work with the visible plan through six WebMCP tools:

- read the current studio context;
- update the title, artist, mapper, seed, difficulties, and creative brief;
- load a rights-safe synthetic demo;
- start a real generation run or a browser preview;
- inspect timing, QA, and safety-gate results; and
- record playtest evidence that a human explicitly supplies.

The last boundary matters. An agent can explain what still needs to happen, but it cannot pretend that it played the map. The person remains responsible for the headset judgment.

## How we built it

We kept the original BeatForge architecture. FastAPI still owns real audio uploads, background jobs, progress events, validation, packaging, and the existing local Beat Saber workflow. The new browser layer lives in `web/webmcp.js`. It feature-detects `document.modelContext`, registers tools with JSON Schemas, returns compact JSON results, and records calls in the page so the person can see what the agent changed.

The main page adds a creative brief, WebMCP status, exposed-tool list, and activity log. The application exposes its state and actions through `window.BeatForgeApp`, which keeps the tool layer thin and lets the same functions serve human controls and agent calls. The canonical `document.modelContext.registerTool({...})` call is present in the source for inspection.

Because the real mapper expects local audio and a Beat Saber installation, we also built an explicit demo mode. It uses a synthetic 30-second groove, produces a clearly labeled preview, and never creates a copyrighted audio asset or claims to create a downloadable pack. That gives a judge a complete collaboration loop even in a clean browser.

## Challenges we ran into

WebMCP is still experimental, so the app has to work in two worlds. In a supported browser it registers tools. Everywhere else it stays usable and explains how to enable WebMCP. We tested the registration path with a browser-side model-context mock and separately tested the live local page without WebMCP.

The second challenge was the mismatch between a local music tool and a public web demo. The production pipeline relies on local files, model availability, and Beat Saber-specific installation paths. A public judge should not need to configure any of that just to understand the idea. The demo mode became the answer, but only after we made its limits obvious in both the interface and the tool responses.

The hardest product decision was deciding what an agent must never be allowed to say. A generated chart can pass structural checks and still feel wrong in a headset. We kept that distinction visible: software reports gates and evidence, while a human reports playability.

## Accomplishments that we're proud of

The agent can now change a real mapping plan through typed operations instead of fragile UI guesses. The person sees those changes immediately, can keep or reject the direction, and can ask for a new run without losing the context of the conversation. The activity panel makes the collaboration inspectable rather than mysterious.

We are also proud that the WebMCP work did not weaken BeatForge's existing refusal behavior. Timing uncertainty, hard mapping failures, and missing human evidence remain visible. The repository now includes the source implementation, browser tests, setup instructions, an MIT license, and a Render deployment definition.

## What we learned

WebMCP tool descriptions are part of the product experience. A vague tool gives an agent permission without much guidance. A good name, schema, and description tell the agent what the operation means and tell the person what to expect.

We also learned that agents need application state, not a larger scrape of the page. Once the page exposes a compact context object, the agent can reason about the next step without reconstructing the UI from pixels and labels. Finally, a demo is most useful when it is honest about what it simulates. A preview can demonstrate collaboration, but it should never masquerade as a validated Beat Saber release.

## What's next for BeatForge for Beatsaber

The public challenge build is now deployed at `https://vladvrx.github.io/beatforge-codex/` and has been verified in a WebMCP-capable in-app browser. Next, we want agents to compare two mapping plans, explain why a generated section failed a gate, and turn human headset notes into structured revision requests. We also want to keep more audio analysis local while sharing only the summaries needed for collaboration.

Longer term, BeatForge should feel less like a generator and more like a rehearsal partner. The person brings taste and embodied knowledge. The agent keeps track of decisions, surfaces tradeoffs, and helps turn a musical idea into a chart that a person can actually play.
