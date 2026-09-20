/* Private gateway session. Tokens live only in this closure, never in browser storage. */
(() => {
  'use strict';
  let session = null, generation = 0;
  const revisions = new Map();
  let uploads = new WeakMap(), submissions = new Map(), uploading = false, activeWatch = 0;
  const $ = id => document.getElementById(id);
  function origin(value) {
    const url = new URL(value);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if ((url.protocol !== 'https:' && !(local && url.protocol === 'http:')) ||
        url.username || url.password || url.search || url.hash || url.pathname !== '/') {
      throw new Error('Use an HTTPS gateway origin, without a path or credentials. HTTP is allowed only on localhost.');
    }
    return url.origin;
  }
  async function send(current, route, options = {}) {
    if (!route.startsWith('/api/') || route.includes('\\')) throw new Error('Invalid gateway API path');
    const target = new URL(route, current.origin);
    if (target.origin !== current.origin || !target.pathname.startsWith('/api/')) throw new Error('Invalid gateway API path');
    const headers = new Headers(options.headers);
    headers.set('Authorization', `Bearer ${current.token}`);
    const response = await fetch(target.href, {...options, headers, credentials:'omit', redirect:'error', cache:'no-store'});
    let result;
    try { result = await response.json(); } catch { throw new Error('The gateway returned an unreadable response.'); }
    if (!response.ok) throw new Error(response.status === 401 ? 'Session rejected or expired. Reconnect with a valid token.' :
      typeof result.detail === 'string' ? result.detail : Array.isArray(result.detail) ? result.detail.map(issue=>issue.msg).join(' ') : `Gateway request failed (${response.status}).`);
    return result;
  }
  async function request(route, options) {
    const current = session;
    if (!current) throw new Error('Connect to your gateway first.');
    const result = await send(current, route, options);
    if (session !== current) throw new Error('The connection changed; this result was discarded.');
    return result;
  }
  function downloadUrl(value) {
    if (!session) throw new Error('Connect to your gateway first.');
    const url = new URL(value, session.origin);
    if (url.origin !== session.origin || !url.pathname.startsWith('/downloads/') || url.username || url.password)
      throw new Error('The gateway returned an invalid download URL.');
    return url.href;
  }
  window.BeatForgeConnection = {
    get connected(){return Boolean(session);},
    get origin(){return session?.origin || null;},
    request,
    async generate(form) {
      if (uploading) throw new Error('An upload is already in progress.');
      if (form.get('engine') !== 'premium') throw new Error('Remote generation currently uses the Premium engine.');
      const audio = form.get('audio');
      if (!(audio instanceof File) || !audio.size || audio.size > 64*1024*1024) throw new Error('Choose an audio file up to 64 MiB.');
      uploading = true;
      try {
        let upload = uploads.get(audio);
        if (!upload) {
          upload = await request('/api/uploads', {method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({filename:audio.name, size:audio.size})});
          uploads.set(audio, upload);
        }
        const state = await request(`/api/uploads/${upload.id}`);
        if (state.state !== 'ready') {
          $('gatewayStatus').textContent = 'Uploading audio directly to your gateway…';
          await request(upload.uploadPath, {method:'PUT', headers:{'Content-Type':'application/octet-stream'}, body:audio});
        }
        const payload = {uploadId:upload.id, title:String(form.get('title')||audio.name),
          artist:String(form.get('artist')||'Unknown Artist'), mapper:String(form.get('mapper')||'BeatForge'),
          seed:Number(form.get('seed')||42), difficulties:String(form.get('difficulties')).split(','),
          mappingPlan:JSON.parse(form.get('mappingPlan')||'{}'), allowUnconfirmed:$('gatewayUnconfirmed').checked};
        const key = JSON.stringify(payload);
        if (!submissions.has(key)) submissions.set(key, crypto.randomUUID());
        const job = await request('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({...payload, idempotencyKey:submissions.get(key)})});
        window.BeatForgeApp.showRemoteJob(job);
        window.dispatchEvent(new Event('beatforge:history'));
        await refresh().catch(()=>{}); // The durable job already exists even if history refresh fails.
        const current = session;
        const watch = ++activeWatch;
        const poll = async () => {
          if (!session || session !== current || watch !== activeWatch) return;
          try {
            const latest = await request(`/api/jobs/${job.id}`);
            window.BeatForgeApp.showRemoteJob(latest);
            $('gatewayStatus').textContent = `Generation ${latest.state}. ${latest.result?.message || 'Waiting for your worker. You can cancel from My runs.'}`;
            if (['queued','running'].includes(latest.state)) setTimeout(poll, 2000);
            else {
              window.dispatchEvent(new Event('beatforge:history')); await refresh();
              if (latest.result?.status === 'playtest_candidate') await window.BeatForgePreview.loadRemoteJob(latest.id, latest.request.difficulties[0]);
            }
          } catch(error) { if (session === current) $('gatewayStatus').textContent = `${error.message} Refresh runs to check this job.`; }
        };
        void poll();
        return job;
      } finally { uploading = false; }
    },
    async revise(id, payload) {
      const key = id + JSON.stringify(payload);
      if (!revisions.has(key)) revisions.set(key, crypto.randomUUID());
      const result = await request(`/api/jobs/${encodeURIComponent(id)}/revise`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...payload, idempotencyKey:revisions.get(key)})
      });
      const current = session;
      const poll = async () => {
        if (!session || session !== current) return;
        try {
          const job = await request(`/api/jobs/${result.id}`);
          $('gatewayStatus').textContent = `Section revision ${job.state}. ${job.result?.message || 'The original map is preserved. Your original worker must be online.'}`;
          window.dispatchEvent(new Event('beatforge:history'));
          if (['queued','running'].includes(job.state)) setTimeout(poll, 2000);
          else {
            await refresh();
            if (job.result?.status === 'playtest_candidate' && window.BeatForgePreview.getState()?.remoteJobId === id)
              await window.BeatForgePreview.loadRemoteJob(job.id, payload.difficulty);
          }
        } catch(error) { if (session === current) $('gatewayStatus').textContent = `${error.message} Refresh runs to check the revision.`; }
      };
      void poll();
      return result;
    },
    async preview(id, difficulty) {
      const query = difficulty ? '?difficulty=' + encodeURIComponent(difficulty) : '';
      const [payload, job] = await Promise.all([
        request(`/api/jobs/${encodeURIComponent(id)}/preview${query}`),
        request(`/api/jobs/${encodeURIComponent(id)}`)
      ]);
      window.BeatForgeApp.showRemoteJob(job);
      return {...payload, audioUrl:downloadUrl(payload.audioUrl)};
    }
  };
  const panel = document.createElement('section');
  panel.className = 'panel gateway-panel';
  panel.setAttribute('aria-labelledby', 'gatewayHeading');
  panel.innerHTML = `<details><summary id="gatewayHeading">Connect your assistant workspace</summary>
    <p>Open maps created through your BeatForge connector. Your gateway token stays in this tab until you disconnect or reload.</p>
    <form id="gatewayConnect" class="gateway-connect">
      <label>Gateway URL<input id="gatewayOrigin" type="url" placeholder="https://your-gateway.onrender.com" required autocomplete="off"></label>
      <label>Access token<input id="gatewayToken" type="password" required autocomplete="off" spellcheck="false"></label>
      <button type="submit" id="gatewaySubmit">Connect</button>
    </form>
    <div class="gateway-actions"><button type="button" class="secondary" id="gatewayRefresh" hidden>Refresh runs</button>
      <button type="button" class="secondary" id="gatewayDisconnect" hidden>Disconnect</button></div>
    <label id="gatewayTimingChoice" class="check" hidden><input id="gatewayUnconfirmed" type="checkbox">Allow generation with unconfirmed timing. I will inspect timing before playtesting.</label>
    <p id="gatewayStatus" role="status">Private token connection · OAuth sign-in is not available in this Studio yet.</p>
    <div id="gatewayRuns"></div>
  </details>`;
  $('previewPanel').before(panel);
  async function refresh() {
    const runs = await request('/api/jobs');
    $('gatewayRuns').replaceChildren();
    for (const run of runs.jobs) {
      const row = document.createElement('div'); row.className = 'job-row';
      const info = document.createElement('div'); info.className = 'job-info';
      const title = document.createElement('strong'); title.textContent = run.request.title;
      const detail = document.createElement('small'); detail.textContent = `${run.request.artist} · ${run.result?.status || run.state}${run.request.revision ? ' · section revision' : ''}`;
      info.append(title, detail); row.append(info);
      if (run.state === 'completed' && run.result?.status === 'playtest_candidate') {
        const open = document.createElement('button'); open.className = 'secondary'; open.textContent = 'Open preview';
        open.addEventListener('click', async () => {
          open.disabled = true;
          try {
            window.BeatForgeControls.applyPlan(run.request.mappingPlan);
            const loaded = await window.BeatForgePreview.loadRemoteJob(run.id, run.request.difficulties[0]);
            if (loaded) $('previewPanel').scrollIntoView({behavior:'auto'});
          } catch(error) { $('gatewayStatus').textContent = error.message; }
          finally { open.disabled = false; }
        });
        const save = document.createElement('button'); save.className = 'secondary'; save.textContent = 'Get map ZIP';
        save.addEventListener('click', async () => {
          save.disabled = true;
          try {
            const result = await request(`/api/jobs/${run.id}/artifacts`);
            const artifact = result.artifacts.find(item => item.name === 'map.zip');
            if (!artifact) throw new Error('This run has no published map ZIP.');
            const link = document.createElement('a'); link.href = downloadUrl(artifact.downloadUrl);
            link.textContent = 'Download ZIP · link expires in 5 minutes'; link.referrerPolicy = 'no-referrer';
            link.className = 'secondary'; save.replaceWith(link);
          } catch(error) { $('gatewayStatus').textContent = error.message; save.disabled = false; }
        });
        row.append(open, save);
      }
      $('gatewayRuns').append(row);
    }
    if (!runs.jobs.length) $('gatewayRuns').textContent = 'No assistant runs yet. Create a map through your connected assistant.';
  }
  $('gatewayConnect').addEventListener('submit', async event => {
    event.preventDefault(); const ticket = ++generation;
    $('gatewaySubmit').disabled = true; $('gatewayStatus').textContent = 'Checking gateway…';
    try {
      const candidate = {origin:origin($('gatewayOrigin').value.trim()), token:$('gatewayToken').value.trim()};
      if (candidate.token.length < 32 || /[^\x21-\x7e]/.test(candidate.token)) throw new Error('Enter a valid gateway access token.');
      const capability = await send(candidate, '/api/capabilities');
      if (ticket !== generation) return;
      session = candidate; $('gatewayToken').value = '';
      window.dispatchEvent(new Event('beatforge:connection'));
      $('gatewayConnect').hidden = true; $('gatewayRefresh').hidden = false; $('gatewayDisconnect').hidden = false;
      $('gatewayTimingChoice').hidden = false;
      $('gatewayStatus').textContent = `Connected to ${candidate.origin}. ${capability.generationMessage}`;
      await refresh();
    } catch(error) { if (ticket === generation) $('gatewayStatus').textContent = error.message; }
    finally { $('gatewaySubmit').disabled = false; }
  });
  $('gatewayRefresh').addEventListener('click', () => refresh().catch(error => {$('gatewayStatus').textContent = error.message;}));
  $('gatewayDisconnect').addEventListener('click', () => {
    ++generation; ++activeWatch; session = null; revisions.clear(); uploads = new WeakMap(); submissions.clear();
    window.BeatForgePreview.clearRemote();
    window.dispatchEvent(new Event('beatforge:connection'));
    $('gatewayToken').value = ''; $('gatewayRuns').replaceChildren();
    $('gatewayConnect').hidden = false; $('gatewayRefresh').hidden = true; $('gatewayDisconnect').hidden = true;
    $('gatewayTimingChoice').hidden = true; $('gatewayUnconfirmed').checked = false;
    $('gatewayStatus').textContent = 'Disconnected. Your token has been discarded.';
  });
})();
