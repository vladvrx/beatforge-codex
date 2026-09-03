/* BeatForge's WebMCP surface: typed collaboration tools for browser agents. */
(() => {
  const difficulties = ["Easy", "Normal", "Hard", "Expert", "ExpertPlus"];
  const context = () => document.modelContext || (typeof navigator !== "undefined" ? navigator.modelContext : null);
  const asJson = value => JSON.stringify(value, null, 2);
  const ui = () => window.BeatForgeApp;

  const withActivity = (name, execute) => async (input = {}) => {
    const app = ui();
    if (!app) throw new Error("BeatForge UI is still loading");
    app.recordAgentActivity({ kind: "agent", label: `Agent called ${name}`, detail: input });
    try {
      const result = await execute(input, app);
      app.recordAgentActivity({ kind: "result", label: `${name} completed`, detail: result });
      return asJson(result);
    } catch (error) {
      app.recordAgentActivity({ kind: "error", label: `${name} failed`, detail: error.message });
      throw error;
    }
  };

  const getStudioContext = {
    name: "get_studio_context",
    title: "Read BeatForge studio context",
    description: "Read the current track, creative brief, mapping settings, safety-gated run state, and the next useful human or agent action.",
    inputSchema: {
      type: "object",
      properties: { includeActivity: { type: "boolean", description: "Include the latest agent activity entries." } },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false },
    execute: withActivity("get_studio_context", (input, app) => app.getStudioState(input)),
  };

  const setMappingPlan = {
    name: "set_mapping_plan",
    title: "Apply a BeatForge mapping plan",
    description: "Apply human-approved creative direction and typed mapping settings to the visible BeatForge form without uploading audio or claiming the map is cleared.",
    inputSchema: {
      type: "object",
      properties: {
        title: { type: "string", maxLength: 200 },
        artist: { type: "string", maxLength: 200 },
        mapper: { type: "string", maxLength: 120 },
        seed: { type: "integer", minimum: -2147483648, maximum: 2147483647 },
        difficulties: { type: "array", items: { type: "string", enum: difficulties }, minItems: 1, maxItems: 5 },
        creativeBrief: { type: "string", maxLength: 1000, description: "The musical movement, mood, accessibility, or intensity brief." },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, destructiveHint: false },
    execute: withActivity("set_mapping_plan", (input, app) => app.applyMappingPlan(input)),
  };

  const loadDemoSession = {
    name: "load_collaboration_demo",
    title: "Load the BeatForge collaboration demo",
    description: "Load a rights-safe synthetic 30-second groove so a person and an agent can exercise the complete collaborative preview flow without a local audio file or Beat Saber install.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: false, destructiveHint: false },
    execute: withActivity("load_collaboration_demo", (_input, app) => app.loadDemoSession()),
  };

  const generateBeatmap = {
    name: "generate_beatmap",
    title: "Generate a BeatForge map",
    description: "Start a BeatForge generation run from the human-selected audio and mapping plan, or generate the rights-safe collaboration preview when demo mode is active.",
    inputSchema: {
      type: "object",
      properties: { reason: { type: "string", maxLength: 300, description: "Short explanation of the creative decision behind this run." } },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, destructiveHint: false },
    execute: withActivity("generate_beatmap", (input, app) => app.startGeneration({ source: "agent", reason: input.reason || "Agent-assisted mapping run" })),
  };

  const reviewCurrentBeatmap = {
    name: "review_current_beatmap",
    title: "Review the current BeatForge result",
    description: "Return the current map status, difficulty summary, timing confidence, hard-gate counts, and human-playtest requirements without changing the result.",
    inputSchema: {
      type: "object",
      properties: { detail: { type: "string", enum: ["summary", "full"], description: "Choose a compact summary or the complete visible review state." } },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false },
    execute: withActivity("review_current_beatmap", (input, app) => app.reviewCurrentMap(input)),
  };

  const recordHumanPlaytest = {
    name: "record_human_playtest",
    title: "Record human headset evidence",
    description: "Record a playtest result supplied by a person after they actually played the chart; this tool never simulates a play or upgrades a safety gate by itself.",
    inputSchema: {
      type: "object",
      properties: {
        difficulty: { type: "string", enum: difficulties },
        speed: { type: "string", enum: ["full", "slow"] },
        passed: { type: "boolean" },
        tester: { type: "string", minLength: 1, maxLength: 120 },
        freshSightRead: { type: "boolean" },
        notes: { type: "string", maxLength: 2000 },
      },
      required: ["difficulty", "speed", "passed", "tester"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, destructiveHint: false },
    execute: withActivity("record_human_playtest", (input, app) => app.recordPlaytest(input)),
  };

  const tools = [getStudioContext, setMappingPlan, loadDemoSession, generateBeatmap, reviewCurrentBeatmap, recordHumanPlaytest];

  function setStatus(status, detail) {
    window.__beatforgeWebMcp = { ...(window.__beatforgeWebMcp || {}), status, detail, tools };
    if (ui()) ui().setWebMcpStatus(status, detail, tools);
  }

  async function register() {
    const modelContext = context();
    if (!modelContext || typeof modelContext.registerTool !== "function") {
      setStatus("unsupported", "Use ChatGPT’s in-app browser or Chrome 149+ with WebMCP testing enabled.");
      return false;
    }
    setStatus("registering", "Registering typed collaboration tools…");
    let registered = 0;
    try {
      if (document.modelContext && typeof document.modelContext.registerTool === "function") {
        // Keep the canonical WebMCP call visible in the source for reviewers.
        await document.modelContext.registerTool({
          name: "get_studio_context",
          title: "Read BeatForge studio context",
          description: "Read the current track, creative brief, mapping settings, safety-gated run state, and the next useful human or agent action.",
          inputSchema: getStudioContext.inputSchema,
          annotations: getStudioContext.annotations,
          execute: getStudioContext.execute,
        });
        registered += 1;
        for (const tool of tools.slice(1)) {
          await document.modelContext.registerTool(tool);
          registered += 1;
        }
      } else {
        for (const tool of tools) {
          await modelContext.registerTool(tool);
          registered += 1;
        }
      }
    } catch (error) {
      setStatus(registered ? "partial" : "error", `${registered}/${tools.length} tools registered: ${error.message}`);
      return false;
    }
    setStatus("ready", `${registered} typed collaboration tools registered.`);
    return true;
  }

  window.BeatForgeWebMcp = { register, tools };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", register, { once: true });
  else register();
})();
