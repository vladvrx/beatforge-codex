// Unit-level adapter tests: no browser, network, or real credentials.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

for (const scenario of ['missing', 'expired', 'ready', 'unauthorized', 'writing']) {
  test(`cached upload: ${scenario}`, async () => {
    const elements = new Map();
    const element = id => {
      if (!elements.has(id)) elements.set(id, {value:'', checked:false, handlers:{},
        addEventListener(name, fn) {this.handlers[name] = fn;},
        before(){}, setAttribute(){}, replaceChildren(){}});
      return elements.get(id);
    };
    let reservations = 0, puts = 0, phase = 0;
    const response = (body, status=200) => ({ok:status===200,status,json:async()=>body});
    const fakeFetch = async (url, options) => {
      const route = new URL(url).pathname;
      if (route === '/api/capabilities') return response({generationMessage:'Test worker'});
      if (route === '/api/jobs') return response(options.method === 'POST' ? {id:'job',state:'failed'} : {jobs:[]});
      if (route === '/api/jobs/job') return response({id:'job',state:'failed'});
      if (route === '/api/uploads') {
        reservations++;
        return response({id:String(reservations),state:'pending',expires:Date.now()/1000+3600,uploadPath:'/api/upload-bytes'});
      }
      if (route === '/api/upload-bytes') {
        puts++;
        return response({detail:'Stop before job submission'},408);
      }
      assert.equal(phase, 1);
      if (scenario === 'missing') return response({detail:'Missing'},404);
      if (scenario === 'unauthorized') return response({detail:'Rejected'},401);
      return response({state:scenario === 'expired'?'pending':scenario,expires:0});
    };
    class AudioFile {size=4; name='synthetic.wav';}
    const context = {URL, Headers, Event, File:AudioFile, crypto:require('node:crypto').webcrypto,
      fetch:fakeFetch, document:{getElementById:element,createElement:()=>element('panel')},
      window:{dispatchEvent(){},BeatForgeApp:{showRemoteJob(){}}},setTimeout(){}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/connector.js'),'utf8'),context);
    element('gatewayOrigin').value='http://localhost:8013';
    element('gatewayToken').value='a'.repeat(32);
    await element('gatewayConnect').handlers.submit({preventDefault(){}});
    const file = new AudioFile();
    const fields = {audio:file,engine:'premium',difficulties:'Hard',mappingPlan:'{}'};
    const form = {get:key=>fields[key]};
    await assert.rejects(context.window.BeatForgeConnection.generate(form));
    assert.equal(reservations,1);
    phase=1;
    if (scenario === 'ready') {
      // Completed uploads survive reservation expiry and must not be uploaded again.
      await context.window.BeatForgeConnection.generate(form);
    } else await assert.rejects(context.window.BeatForgeConnection.generate(form));
    const renewed = ['missing','expired'].includes(scenario);
    assert.equal(reservations,renewed?2:1);
    assert.equal(puts,renewed?2:1);
  });
}
