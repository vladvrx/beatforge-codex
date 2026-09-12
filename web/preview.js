/* Audio is the preview clock. All displayed objects come from the actual chart. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const canvas = $('chartCanvas'), ctx = canvas.getContext('2d');
  const wave = $('waveformCanvas'), wc = wave.getContext('2d');
  const audio = $('previewAudio');
  let data = null, sample = null, job = null, request = 0, frame = 0;
  let timing = [], width = 1000, height = 420, renderedNotes = 0;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const stamp = n => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
  function buildTiming(chart, bpm, offset) {
    const points = [{b:0, m:bpm}, ...(chart.bpmEvents || []).filter(p => Number.isFinite(p.b) && Number.isFinite(p.m) && p.m > 0)].sort((a,b) => a.b-b.b);
    let seconds = offset || 0, previous = points[0];
    return points.map((point, i) => {
      if (i) seconds += (point.b-previous.b)*60/previous.m;
      previous=point;
      return {...point, seconds};
    });
  }
  function beatSeconds(beat) {
    let point = timing[0] || {b:0,m:120,seconds:0};
    for (const item of timing) { if (item.b > beat) break; point=item; }
    return point.seconds+(beat-point.b)*60/point.m;
  }
  function secondsBeat(seconds) {
    let point = timing[0] || {b:0,m:120,seconds:0};
    for (const item of timing) { if (item.seconds > seconds) break; point=item; }
    return point.b+(seconds-point.seconds)*point.m/60;
  }
  function point(x,y,seconds) {
    const depth = Math.max(.35, 1 + seconds*1.65), scale = 1/depth;
    return {x:width/2+(x-1.5)*width*.16*scale, y:height*.39+(1.0-y)*height*.25*scale, size:width*.09*scale, scale};
  }
  function line(a,b,color,weight=1) {
    ctx.strokeStyle=color; ctx.lineWidth=weight; ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
  }
  function note(item, time) {
    const dt=beatSeconds(item.b)-time;
    if (dt < -.18 || dt > 5) return;
    const p=point(item.x,item.y,dt), size=p.size*.84, color=item.c===0?leftColor():rightColor();
    const back={x:p.x+size*.18,y:p.y-size*.16};
    ctx.globalAlpha=clamp((5-dt)/1.5,0,1)*clamp((dt+.18)*10,0,1);
    ctx.fillStyle=color; ctx.fillRect(p.x-size/2,p.y-size/2,size,size);
    ctx.fillStyle='rgba(255,255,255,.3)'; ctx.beginPath(); ctx.moveTo(p.x-size/2,p.y-size/2); ctx.lineTo(back.x-size/2,back.y-size/2); ctx.lineTo(back.x+size/2,back.y-size/2); ctx.lineTo(p.x+size/2,p.y-size/2); ctx.fill();
    ctx.strokeStyle='rgba(255,255,255,.45)'; ctx.lineWidth=1; ctx.strokeRect(p.x-size/2,p.y-size/2,size,size);
    ctx.save(); ctx.translate(p.x,p.y); ctx.fillStyle='#fff';
    const vectors=[[0,-1],[0,1],[-1,0],[1,0],[-.707,-.707],[.707,-.707],[-.707,.707],[.707,.707]];
    if(item.d===8){ctx.beginPath();ctx.arc(0,0,size*.11,0,Math.PI*2);ctx.fill();}
    else {const v=vectors[item.d] || [0,1];ctx.rotate(Math.atan2(v[1],v[0]));ctx.beginPath();ctx.moveTo(size*.27,0);ctx.lineTo(-size*.05,-size*.22);ctx.lineTo(-size*.05,-size*.09);ctx.lineTo(-size*.25,-size*.09);ctx.lineTo(-size*.25,size*.09);ctx.lineTo(-size*.05,size*.09);ctx.lineTo(-size*.05,size*.22);ctx.closePath();ctx.fill();}
    ctx.restore(); ctx.globalAlpha=1; renderedNotes++;
  }
  function rgb(value,fallback) {return value?`rgb(${Math.round(value.r*255)},${Math.round(value.g*255)},${Math.round(value.b*255)})`:fallback;}
  function leftColor(){return rgb(data?.colors?.left,'#ff526f');}
  function rightColor(){return rgb(data?.colors?.right,'#658dff');}
  function draw() {
    const t=audio.currentTime||0;
    ctx.clearRect(0,0,width,height);
    const gradient=ctx.createLinearGradient(0,0,0,height); gradient.addColorStop(0,'#0a0f1c');gradient.addColorStop(1,'#121526');ctx.fillStyle=gradient;ctx.fillRect(0,0,width,height);
    ctx.fillStyle='#8994ac';ctx.font='12px system-ui';ctx.fillText('CHART PREVIEW',22,28);
    ctx.fillStyle='#566481';ctx.fillText('Audio synchronized · Standard',22,47);
    for(let x=-.5;x<=3.5;x++)line(point(x,-.6,0),point(x,-.6,7),'#26334b');
    const beat=secondsBeat(t);
    for(let b=Math.ceil(beat);b<beat+12;b++){const s=beatSeconds(b)-t;line(point(-.5,-.6,s),point(3.5,-.6,s),b%4===0?'#425374':'#1d293e');}
    for(let x=0;x<4;x++)for(let y=0;y<3;y++){const p=point(x,y,0);ctx.strokeStyle='#30405d';ctx.strokeRect(p.x-p.size*.47,p.y-p.size*.47,p.size*.94,p.size*.94);}
    renderedNotes=0;
    if(data){
      const chart=data.chart;
      for(const wall of chart.obstacles||[]){const s=beatSeconds(wall.b)-t,e=beatSeconds(wall.b+wall.d)-t;if(e<0||s>5)continue;const a=point(wall.x-.5,(wall.y||0)-.5,Math.max(0,s)),b=point(wall.x+wall.w-.5,(wall.y||0)+wall.h-.5,Math.max(0,s));ctx.strokeStyle='#bd89ef';ctx.fillStyle='rgba(142,87,186,.06)';ctx.fillRect(a.x,b.y,b.x-a.x,a.y-b.y);ctx.strokeRect(a.x,b.y,b.x-a.x,a.y-b.y);}
      for(const hold of [...(chart.sliders||[]),...(chart.burstSliders||[])]){const s=beatSeconds(hold.b)-t,e=beatSeconds(hold.tb)-t;if(e<0||s>5)continue;const a=point(hold.x,hold.y,Math.max(-.1,s)),b=point(hold.tx,hold.ty,e);ctx.strokeStyle=hold.c===0?leftColor():rightColor();ctx.lineWidth=hold.sc?7:4;ctx.globalAlpha=.65;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.bezierCurveTo(a.x,a.y-30,b.x,b.y-20,b.x,b.y);ctx.stroke();ctx.globalAlpha=1;}
      for(const bomb of chart.bombNotes||[]){const dt=beatSeconds(bomb.b)-t;if(dt<0||dt>5)continue;const p=point(bomb.x,bomb.y,dt);ctx.fillStyle='#171b23';ctx.strokeStyle='#d4d8e2';ctx.lineWidth=2;ctx.beginPath();ctx.arc(p.x,p.y,p.size*.35,0,Math.PI*2);ctx.fill();ctx.stroke();}
      for(const item of [...(chart.colorNotes||[])].sort((a,b)=>b.b-a.b)) note(item,t);
      ctx.fillStyle='#e7ecfa';ctx.font='600 13px system-ui';ctx.fillText(`${data.difficulty} · ${(chart.colorNotes||[]).length} notes`,22,height-22);
      ctx.fillStyle='#8a98b4';ctx.textAlign='right';ctx.fillText(`Beat ${Math.max(0,beat).toFixed(2)}`,width-22,height-22);ctx.textAlign='left';
    }
    $('previewClock').textContent=`${stamp(t)} / ${stamp(data?.duration||0)}`;
    $('previewSeek').value=String(t);
    $('previewPlay').textContent=audio.paused?'Play':'Pause';
    canvas.dataset.renderedNotes=String(renderedNotes);
    canvas.dataset.time=t.toFixed(3);
    drawWave(t);
  }
  function drawWave(time) {
    const w=wave.clientWidth,h=wave.clientHeight;wc.clearRect(0,0,w,h);wc.fillStyle='#0b101c';wc.fillRect(0,0,w,h);
    if(!data)return;
    const duration=data.duration||1, peaks=data.waveform||[];
    const start=beatSeconds(Number($('revisionStart').value)||0),end=beatSeconds(Number($('revisionEnd').value)||0);
    wc.fillStyle='rgba(103,147,255,.18)';wc.fillRect(start/duration*w,0,(end-start)/duration*w,h);
    wc.strokeStyle='#6980b0';wc.beginPath();peaks.forEach((p,i)=>{const x=i/peaks.length*w;wc.moveTo(x,h/2-p*(h/2-5));wc.lineTo(x,h/2+p*(h/2-5));});wc.stroke();
    wc.strokeStyle='#54668a';for(const section of data.sections||[]){const x=beatSeconds(section.startBeat||0)/duration*w;wc.beginPath();wc.moveTo(x,0);wc.lineTo(x,h);wc.stroke();}
    wc.strokeStyle='#fff';wc.lineWidth=2;wc.beginPath();wc.moveTo(time/duration*w,0);wc.lineTo(time/duration*w,h);wc.stroke();
  }
  function resize(){const dpr=window.devicePixelRatio||1;width=canvas.clientWidth;height=canvas.clientHeight;canvas.width=width*dpr;canvas.height=height*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);wave.width=wave.clientWidth*dpr;wave.height=wave.clientHeight*dpr;wc.setTransform(dpr,0,0,dpr,0,0);draw();}
  function loop(){draw();if(!audio.paused)frame=requestAnimationFrame(loop);}
  function setData(next, source){
    data=next;timing=buildTiming(data.chart,data.bpm,data.offsetSeconds);$('previewPanel').hidden=false;
    const oldTime=audio.currentTime||0;
    if(source && audio.getAttribute('src')!==source){audio.pause();audio.src=source;audio.load();}else audio.currentTime=oldTime;
    $('previewTitle').textContent=`${data.title} · ${data.artist}`;
    $('previewDifficulty').innerHTML=data.difficulties.map(name=>`<option${name===data.difficulty?' selected':''}>${esc(name)}</option>`).join('');
    $('previewSeek').max=String(data.duration);$('previewTiming').textContent=data.timingVerified?'Timing verified':'Timing needs confirmation';
    $('previewSections').innerHTML=(data.sections||[]).map((section,i)=>`<button type="button" class="secondary" data-section="${i}">${esc(section.label||section.type||`Section ${i+1}`)}</button>`).join('');
    $('previewFindings').innerHTML=(data.findings||[]).length?(data.findings||[]).slice(0,30).map((f,i)=>`<button class="finding" data-finding="${i}">${f.beat!=null?`Beat ${Number(f.beat).toFixed(2)} · `:''}${esc(f.code)}: ${esc(f.message)}</button>`).join(''):'<span class="agent-helper">No reported findings for this difficulty. Headset feedback is still needed.</span>';
    $('previewPlay').disabled=false;$('acceptRevision').hidden=!data.provenance?.revision;
    $('reviseSection').disabled=!data.canRevise;$('revisionHelp').textContent=data.canRevise?'Creates a new revision. Notes outside this range stay unchanged; the complete result is validated.':'The sample can be explored here. Section regeneration is available for completed local mapping runs.';
    $('previewDownload').hidden=!sample;$('previewDownload').href=sample?'assets/demo/map.zip':'#';
    $('revisionStart').value='0';$('revisionEnd').value=String(Math.min(16,Math.floor(secondsBeat(data.duration))));
    resize();
  }
  async function json(url){const response=await fetch(url);if(!response.ok){let body;try{body=await response.json();}catch{}throw new Error(body?.detail||`Preview request failed (${response.status})`);}return response.json();}
  async function loadJob(id,difficulty,atTime){
    const ticket=++request;
    try{
      const payload=await json(`/api/jobs/${id}/preview${difficulty?'?difficulty='+encodeURIComponent(difficulty):''}`);
      if(ticket!==request)return false;
      const position=atTime??(job===id?audio.currentTime:0);
      job=id;sample=null;setData(payload,payload.audioUrl);
      const seek=()=>{if(ticket!==request)return;audio.currentTime=clamp(position,0,payload.duration);draw();};
      if(audio.readyState>=1)seek();else audio.addEventListener('loadedmetadata',seek,{once:true});
      $('previewMessage').textContent='';
      window.dispatchEvent(new CustomEvent('beatforge:preview',{detail:{jobId:job,difficulty:data.difficulty}}));
      return true;
    }catch(error){
      if(ticket!==request)return false;
      $('previewPanel').hidden=false;$('previewMessage').textContent=error.message;
      if(!data)$('previewTitle').textContent='Preview unavailable';
      return false;
    }
  }
  async function loadDemo(){const ticket=++request;const base=window.BeatForgeApp?.staticDemo?'assets/demo/':'/assets/demo/';const payload=await json(base+'preview.json');if(ticket!==request)return;sample=payload;job=null;const difficulty=payload.difficulties.includes('Hard')?'Hard':payload.difficulties[0];setData({...payload,chart:payload.charts[difficulty],difficulty,canRevise:false},base+'song.ogg');$('previewDownload').href=base+'map.zip';return payload;}
  $('previewPlay').addEventListener('click',()=>audio.paused?audio.play().catch(error=>{$('previewMessage').textContent=error.message;}):audio.pause());
  audio.addEventListener('play',()=>{cancelAnimationFrame(frame);loop();});audio.addEventListener('pause',()=>{cancelAnimationFrame(frame);draw();});audio.addEventListener('seeked',draw);audio.addEventListener('loadedmetadata',draw);audio.addEventListener('ended',draw);
  $('previewSpeed').addEventListener('change',()=>{audio.playbackRate=Number($('previewSpeed').value);});
  $('previewSeek').addEventListener('input',()=>{audio.currentTime=Number($('previewSeek').value);draw();});
  wave.addEventListener('click',event=>{if(data){audio.currentTime=clamp((event.clientX-wave.getBoundingClientRect().left)/wave.clientWidth,0,1)*data.duration;draw();}});
  $('previewDifficulty').addEventListener('change',()=>{const difficulty=$('previewDifficulty').value;if(sample)setData({...sample,chart:sample.charts[difficulty],difficulty,canRevise:false},null);else if(job)loadJob(job,difficulty);});
  $('previewSections').addEventListener('click',event=>{const target=event.target.closest('[data-section]');if(!target||!data)return;const section=data.sections[Number(target.dataset.section)];$('revisionStart').value=String(section.startBeat);$('revisionEnd').value=String(section.endBeat);audio.currentTime=Math.max(0,beatSeconds(section.startBeat));draw();});
  $('previewFindings').addEventListener('click',event=>{const target=event.target.closest('[data-finding]');if(!target||!data)return;const issue=data.findings[Number(target.dataset.finding)];if(issue.beat!=null)audio.currentTime=Math.max(0,beatSeconds(issue.beat)-1);draw();});
  for(const id of ['revisionStart','revisionEnd'])$(id).addEventListener('input',()=>drawWave(audio.currentTime));
  $('revisionHere').addEventListener('click',()=>{const start=Math.max(0,Math.floor(secondsBeat(audio.currentTime)/4)*4);$('revisionStart').value=String(start);$('revisionEnd').value=String(Math.min(start+32,Math.floor(secondsBeat(data?.duration||0))));draw();});
  $('reviseSection').addEventListener('click',async()=>{
    if(!job||!data?.canRevise)return;
    $('reviseSection').disabled=true;$('previewMessage').textContent='Starting a new section revision…';audio.pause();
    try{const mappingPlan=window.BeatForgeApp.creativePlan();mappingPlan.brief=$('revisionBrief').value.trim()||mappingPlan.brief;mappingPlan.density=Number($('revisionDensity').value);const payload={difficulty:data.difficulty,startBeat:Number($('revisionStart').value),endBeat:Number($('revisionEnd').value),baseHash:data.chartHash,seed:Number($('seed').value||42)+1,mappingPlan};const response=await fetch(`/api/jobs/${job}/revise`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));$('previewMessage').textContent='Revision started. Your original map is preserved.';window.BeatForgeApp.followJob(result.id);}catch(error){$('previewMessage').textContent=error.message;}finally{$('reviseSection').disabled=!data?.canRevise;}
  });
  new ResizeObserver(resize).observe(canvas);
  window.BeatForgePreview={loadJob,loadDemo,getState:()=>data?{jobId:job,difficulty:data.difficulty,chartHash:data.chartHash,time:audio.currentTime,notes:data.chart.colorNotes.length,canRevise:data.canRevise}:null};
})();
