"use strict";
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const state = {
  project: null,
  source: "file",
  file: null,
  view: "mixer",
  transpose: 0,
  capo: 0,
  follow: true,
  activeChord: -1,
  activeLine: -1,
  selectedChord: -1,
  loopA: null,
  loopB: null,
};
const colors = {
  vocals: "#b39cff",
  drums: "#f4b37c",
  bass: "#82bedf",
  guitar: "#b6f56c",
  guitar_lead: "#b6f56c",
  guitar_rhythm: "#5cdbba",
  piano: "#ee95b8",
  other: "#a5b3c9",
};
const icons = {
  vocals: "♩",
  drums: "◉",
  bass: "≋",
  guitar: "♬",
  guitar_lead: "♬",
  guitar_rhythm: "♩",
  piano: "▥",
  other: "∿",
};
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const time = (value) =>
  `${Math.floor(Math.max(0, value || 0) / 60)}:${String(Math.floor(Math.max(0, value || 0) % 60)).padStart(2, "0")}`;
const notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const flats = {
  Db: "C#",
  Eb: "D#",
  Gb: "F#",
  Ab: "G#",
  Bb: "A#",
  Cb: "B",
  Fb: "E",
  "E#": "F",
  "B#": "C",
};
function shifted(label, amount = state.transpose - state.capo) {
  return label.replace(
    /(^|\/)([A-G](?:#|b)?)/g,
    (_, slash, root) =>
      slash + notes[(notes.indexOf(flats[root] || root) + amount + 120) % 12],
  );
}
let toastTimer;
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 4500);
}
async function api(path, options = {}) {
  const result = await (window.faixaLabPages?.request(path, options) ?? fetch(path, options));
  if (result.ok) {
    try {
      return await result.json();
    } catch {
      throw new Error(
        result.redirected
          ? "O servidor redirecionou a solicitação. Confira se sua sessão está ativa."
          : `O servidor enviou uma resposta inválida (HTTP ${result.status}).`,
      );
    }
  }

  const body = (await result.text().catch(() => "")).trim();
  let detail;
  try {
    detail = JSON.parse(body)?.detail;
  } catch {
    if (body && !body.startsWith("<")) detail = body.slice(0, 500);
  }
  if (Array.isArray(detail)) {
    detail = detail.map((issue) => issue?.msg).filter((message) => typeof message === "string").join(" ");
  }
  if (typeof detail === "string" && detail.trim()) throw new Error(detail.trim());

  if (result.status === 401) {
    throw new Error("A sessão não autorizou a solicitação (HTTP 401). Entre novamente e tente outra vez.");
  }
  if ([502, 503, 504].includes(result.status)) {
    throw new Error(`O servidor de processamento está indisponível (HTTP ${result.status}).`);
  }
  throw new Error(`Não foi possível concluir a operação (HTTP ${result.status}).`);
}
function mediaUrl(path) {
  return window.faixaLabPages?.mediaUrl(path) ?? path;
}
function options() {
  const detailed=$('#stem-option').value==='expanded';
  return {
    stems: detailed||Number($("#stem-option").value) === 7 ? 6 : Number($("#stem-option").value),
    split_guitars: detailed||Number($("#stem-option").value) === 7,
    instrument_detail: detailed,
    quality: $("#quality-option").value,
    language: $("#language-option").value,
    lyrics: $("#lyrics-option").checked,
    lyrics_model: $("#lyrics-model-option").value,
    device: "auto",
  };
}

function audioKey(project) {
  return project.id + ":" + (project.audio_revision || "original") + ":" + project.stems.map(s => s.name).join(",");
}

class StemPlayer {
  constructor() {
    this.tracks=[];this.original=null;this.context=null;this.backingAudio=null;
    this.playing=false;this.wantsPlay=false;this.starting=false;this.buffering=false;
    this.comparing=false;this.backingMode=false;this.position=0;this.duration=0;
    this.speed=1;this.key='';this.projectId=null;this.revision='original';
    this.operation=0;this.loadId=0;this.pendingProject=null;this.reference=null;
    this.syncTimer=null;this.waitTimer=null;this.message='';this.recoveries=0;
    this.lastSeek=new WeakMap();this.lastClock=0;this.lastAdvance=0;
  }
  emit() {
    const button=$('#play');
    button.textContent=this.wantsPlay?'Ⅱ':'▶';
    button.setAttribute('aria-label',this.wantsPlay?'Pausar':'Reproduzir');
    this.onState?.();updateMediaSession();
  }
  format() {
    return ['localhost','127.0.0.1','::1','[::1]'].includes(location.hostname)?'wav':'m4a';
  }
  makeAudio(url) {
    const audio=new Audio(),epoch=this.loadId;
    if(window.faixaLabPages)audio.crossOrigin='anonymous';
    audio.preload='none';audio.src=mediaUrl(url);audio.preservesPitch=true;audio.playbackRate=this.speed;
    audio.addEventListener('loadedmetadata',()=>{if(epoch===this.loadId)this.place(audio,this.position);});
    const ready=()=>{if(epoch===this.loadId&&this.wantsPlay&&this.buffering&&!this.starting&&this.canResume())this.play(true);};
    audio.addEventListener('canplay',ready);audio.addEventListener('seeked',ready);
    const wait=()=>{
      if(epoch!==this.loadId||!this.wantsPlay||!this.audios().includes(audio))return;
      clearTimeout(this.waitTimer);
      this.waitTimer=setTimeout(()=>{if(epoch===this.loadId&&this.wantsPlay&&this.audios().includes(audio)&&audio.readyState<3)this.buffer(audio);},400);
    };
    audio.addEventListener('waiting',wait);audio.addEventListener('stalled',wait);
    audio.addEventListener('error',()=>{if(epoch===this.loadId&&this.wantsPlay&&this.audios().includes(audio))this.recover(audio,true);});
    audio.addEventListener('ended',()=>{
      if(epoch!==this.loadId||!this.wantsPlay||audio!==this.clock())return;
      if(audio.currentTime>=this.duration-.3){this.pause();this.seek(0);}else this.recover(audio,false);
    });
    return audio;
  }
  load(project,acceptUpdate=false) {
    const key=audioKey(project);
    if(key===this.key)return false;
    if(this.projectId===project.id&&(this.wantsPlay||this.backingMode)&&!acceptUpdate){this.pendingProject=project;this.emit();return false;}
    const same=this.projectId===project.id,position=same?this.current():0;
    const mix=new Map(this.tracks.map(t=>[t.name,{gain:t.gain,mute:t.mute,solo:t.solo}]));
    const comparing=same&&this.comparing;
    this.dispose();this.projectId=project.id;this.revision=project.audio_revision||'original';
    this.key=key;this.duration=project.duration||0;this.position=Math.min(position,this.duration);this.comparing=comparing;
    for(const stem of project.stems){
      const audio=this.makeAudio(`/api/projects/${project.id}/audio/${stem.name}?format=${this.format()}&v=${encodeURIComponent(this.revision)}`);
      this.tracks.push({...stem,audio,gain:1,mute:false,solo:false,...(same?mix.get(stem.name):{}),node:null,gainNode:null});
    }
    this.original=this.makeAudio(`/api/projects/${project.id}/audio/original?format=${this.format()}`);
    $('#play').disabled=!this.tracks.length;
    this.onLoad?.();this.emit();return true;
  }
  audios() {
    if(this.backingMode)return this.backingAudio?[this.backingAudio]:[];
    if(this.comparing)return this.original?[this.original]:[];
    const solo=this.tracks.some(t=>t.solo);
    const active=this.tracks.filter(t=>!t.mute&&(!solo||t.solo)&&t.gain>0).map(t=>t.audio);
    // One silent stream keeps a useful song clock when all faders are muted.
    return active.length?active:this.tracks[0]?[this.tracks[0].audio]:[];
  }
  clock(){return this.reference||this.audios()[0];}
  bufferedAhead(audio,position=this.position) {
    const ranges=audio?.buffered;if(!ranges)return 0;
    for(let i=0;i<ranges.length;i++)if(ranges.start(i)<=position+.025&&ranges.end(i)>position)return ranges.end(i)-position;
    return 0;
  }
  canResume() {
    const active=this.audios();
    return active.length>0&&active.every(a=>{
      const remaining=Math.max(.05,(Number.isFinite(a.duration)?a.duration:this.duration)-this.position-.05);
      return a.readyState>=3&&!a.seeking&&this.bufferedAhead(a)>=Math.min(2,remaining);
    });
  }
  current() {
    const audio=this.clock();
    return this.wantsPlay&&!this.buffering&&audio?.paused!==true&&Number.isFinite(audio?.currentTime)?audio.currentTime:this.position;
  }
  async ensureContext() {
    if(!this.context){
      const Constructor=typeof AudioContext!=='undefined'?AudioContext:window.webkitAudioContext;
      if(!Constructor)throw new Error('Este navegador não oferece o mixer de áudio.');
      this.context=new Constructor();
      const master=this.context.createDynamicsCompressor();master.threshold.value=-1;master.knee.value=0;master.ratio.value=20;master.connect(this.context.destination);
      for(const t of this.tracks){t.node=this.context.createMediaElementSource(t.audio);t.gainNode=this.context.createGain();t.node.connect(t.gainNode);t.gainNode.connect(master);}
    }
    await this.context.resume();this.applyMix();
  }
  applyMix() {
    const position=this.current(),solo=this.tracks.some(t=>t.solo);
    for(const t of this.tracks){const audible=!t.mute&&(!solo||t.solo);if(t.gainNode)t.gainNode.gain.value=audible?t.gain:0;else t.audio.volume=audible?Math.min(t.gain,1):0;}
    if(!this.wantsPlay||!this.playing||this.starting||this.backingMode||this.comparing)return;
    const active=this.audios(),token=this.operation;
    if(!active.includes(this.reference)){this.position=position;this.reference=active[0];this.place(this.reference,position);}
    for(const t of this.tracks){
      if(!active.includes(t.audio)){t.audio.preload='none';t.audio.pause();continue;}
      t.audio.preload='auto';
      if(t.audio.paused!==false){this.place(t.audio,position);t.audio.play().catch(()=>{if(token===this.operation&&this.wantsPlay)this.recover(t.audio,true);});}
    }
    this.lastClock=position;this.lastAdvance=performance.now();
  }
  place(audio,value,force=false) {
    if(!audio||!Number.isFinite(audio.duration)||audio.duration<=0)return;
    const target=Math.min(Math.max(0,value),Math.max(0,audio.duration-.001));
    if((force||!audio.seeking)&&Math.abs(audio.currentTime-target)>.025){try{audio.currentTime=target;this.lastSeek.set(audio,performance.now());}catch{}}
    audio.playbackRate=this.speed;
  }
  async play(recovery=false) {
    if(this.wantsPlay&&this.playing&&!recovery)return;
    if(this.pendingProject&&!this.wantsPlay&&!this.backingMode)this.load(this.pendingProject,true);
    const active=this.audios();if(!active.length)return;
    const token=++this.operation;
    this.wantsPlay=true;this.starting=true;this.buffering=!!recovery;this.message='Carregando áudio…';this.reference=active.includes(this.reference)?this.reference:active[0];this.emit();
    clearInterval(this.syncTimer);this.syncTimer=setInterval(()=>this.synchronize(),350);
    let deadline;
    try{
      // Pausing an active preload=none stream can suspend its download. Keep
      // active streams fetching so a short underrun can refill without reload.
      for(const a of active)a.preload='auto';
      if(!this.backingMode&&!this.comparing)await this.ensureContext();
      if(token!==this.operation||!this.wantsPlay)return;
      for(const t of this.tracks)if(!active.includes(t.audio)){t.audio.preload='none';t.audio.pause();}
      for(const a of active)this.place(a,this.position);
      await Promise.race([Promise.all(active.map(a=>a.play())),new Promise((_,reject)=>{deadline=setTimeout(()=>reject(new Error('O áudio não respondeu. Tente reproduzir novamente.')),20000);})]);
      if(token!==this.operation||!this.wantsPlay)return;
      this.playing=true;this.starting=false;this.buffering=false;this.message='';this.lastClock=this.current();this.lastAdvance=performance.now();this.emit();
    }catch(error){if(token===this.operation&&this.wantsPlay)this.fail(error?.name==='NotAllowedError'?'Toque em Reproduzir para retomar o áudio.':error?.message||'Não foi possível iniciar o áudio.');}
    finally{clearTimeout(deadline);}
  }
  pause() {
    this.position=this.current();++this.operation;this.wantsPlay=false;this.playing=false;this.starting=false;this.buffering=false;
    clearInterval(this.syncTimer);clearTimeout(this.waitTimer);
    for(const t of this.tracks)t.audio.pause();this.original?.pause();this.backingAudio?.pause();this.message='';this.emit();
  }
  seek(value) {
    this.position=Math.max(0,Math.min(Number(value)||0,this.duration));
    for(const a of this.audios())this.place(a,this.position,true);
    this.lastClock=this.position;this.lastAdvance=performance.now();updateMediaPosition();
  }
  buffer(audio) {
    if(!this.wantsPlay||this.buffering)return;
    this.position=this.current();++this.operation;this.playing=false;this.starting=false;this.buffering=true;
    this.waitingAudio=audio;this.waitSince=performance.now();
    this.bufferProgress=this.audios().reduce((sum,a)=>sum+this.bufferedAhead(a),0);this.bufferProgressAt=this.waitSince;
    for(const a of this.audios()){a.preload='auto';a.pause();}
    this.message='Carregando áudio…';this.emit();
  }
  synchronize() {
    if(!this.wantsPlay)return;
    const active=this.audios(),now=performance.now();
    const context=this.context;
    if(!this.backingMode&&!this.comparing&&context?.state&&context.state!=='running'&&context.state!=='closed'){
      if(this.contextResuming)return;
      if(this.recoveries>=2){this.fail('Toque em Reproduzir para retomar a saída de áudio.');return;}
      ++this.recoveries;this.lastRecovery=now;this.contextResuming=true;this.buffer(this.clock());
      Promise.resolve(context.resume()).then(()=>{
        if(!this.wantsPlay||this.context!==context)return;
        if(context.state==='running')return this.play(true);
        this.fail('Toque em Reproduzir para retomar a saída de áudio.');
      }).catch(()=>{if(this.wantsPlay&&this.context===context)this.fail('Toque em Reproduzir para retomar a saída de áudio.');}).finally(()=>{this.contextResuming=false;});
      return;
    }
    if(this.buffering&&!this.starting){
      if(this.canResume()){this.play(true);return;}
      const progress=active.reduce((sum,a)=>sum+this.bufferedAhead(a),0);
      if(progress>this.bufferProgress+.05){this.bufferProgress=progress;this.bufferProgressAt=now;}
      // load() discards the bytes already received and can create a repeating
      // play/pause/download loop. Let a progressing download finish instead.
      if(now-this.bufferProgressAt>20000||now-this.waitSince>60000)this.fail('O áudio não terminou de carregar. Toque em Reproduzir para tentar novamente; a separação está salva.');
      return;
    }
    if(!this.playing||this.starting)return;
    const master=this.clock(),reference=this.current();
    if(reference>this.lastClock+.02){this.lastClock=reference;this.lastAdvance=now;if(now-(this.lastRecovery||0)>15000)this.recoveries=0;}
    else if(!master?.seeking&&now-this.lastAdvance>4000&&reference<this.duration-.25){this.recover(master,false);return;}
    for(const audio of active){
      if(audio===master||audio.seeking||audio.readyState<3||now-(this.lastSeek.get(audio)||-Infinity)<1500)continue;
      if(Math.abs(audio.currentTime-reference)<=.12)continue;
      const ranges=audio.buffered;
      if(ranges&&ranges.length&&!Array.from({length:ranges.length},(_,i)=>i).some(i=>reference>=ranges.start(i)&&reference<ranges.end(i)-.05))continue;
      this.place(audio,reference);
    }
  }
  async recover(audio,formatError=false) {
    if(!this.wantsPlay||this.recovering)return;
    if(this.recoveries>=2){this.fail('O áudio foi interrompido. Toque em Reproduzir para tentar novamente.');return;}
    this.recovering=true;++this.recoveries;this.lastRecovery=performance.now();
    this.position=this.current();++this.operation;this.playing=false;this.starting=false;this.buffering=true;
    for(const a of this.audios())a.pause();
    this.message='Retomando áudio…';this.emit();
    try{
      if(audio){
        if(formatError&&!audio.formatFallback){
          const url=new URL(audio.src,location.href||'http://localhost');
          if(url.searchParams.has('format')){url.searchParams.set('format',url.searchParams.get('format')==='m4a'?'wav':'m4a');audio.formatFallback=true;audio.src=url.href;}
        }
        if(formatError||audio.error||audio.readyState===0)audio.load();
      }
      await this.play(true);
    }finally{this.recovering=false;}
  }
  fail(message){this.pause();this.message=message;this.emit();toast(message);}
  setSpeed(speed){this.position=this.current();this.speed=speed;for(const t of this.tracks)t.audio.playbackRate=speed;if(this.original)this.original.playbackRate=speed;if(this.backingAudio)this.backingAudio.playbackRate=speed;updateMediaPosition();}
  async compare(on){const was=this.wantsPlay;this.pause();this.backingMode=false;this.comparing=on;this.reference=null;this.seek(this.position);if(was)await this.play();}
  async useBacking(url){this.pause();this.backingAudio?.removeAttribute('src');this.backingAudio=this.makeAudio(url);this.backingAudio.preload='metadata';this.backingMode=true;this.comparing=false;this.reference=null;$('#compare-original').checked=false;this.backingAudio.addEventListener('timeupdate',updateMediaPosition);$('#play').disabled=false;this.emit();}
  useStems(){if(!this.backingMode)return;const was=this.wantsPlay;this.pause();this.backingMode=false;this.reference=null;if(was)this.play();}
  async applyPending(){if(!this.pendingProject)return;const p=this.pendingProject,was=this.wantsPlay;this.pause();this.load(p,true);if(was)await this.play();}
  dispose(){
    this.pause();++this.loadId;this.pendingProject=null;
    this.backingAudio?.removeAttribute('src');this.backingAudio=null;this.backingMode=false;
    for(const t of this.tracks){t.audio.removeAttribute('src');t.audio.load();t.node?.disconnect();t.gainNode?.disconnect();}
    this.original?.removeAttribute('src');this.tracks=[];this.original=null;this.reference=null;
    if(this.context){try{this.context.close()?.catch?.(()=>{});}catch{}this.context=null;}
    this.key='';this.lastSeek=new WeakMap();this.recoveries=0;this.recovering=false;
  }
}
const player = new StemPlayer();
player.onLoad=()=>{renderTracks();$('#total-time').textContent=time(player.duration);drawWave();};
player.onState=()=>{
  const status=$('#playback-status');if(status){status.textContent=player.message;status.hidden=!player.message;}
  const notice=$('#audio-update-notice');if(notice)notice.hidden=!player.pendingProject;
};

function openImport() {
  $("#import-error").hidden = true;
  $("#import-dialog").showModal();
}
$("#new-project").onclick = openImport;
$("#start-import").onclick = openImport;
$("#about").onclick = () => $("#about-dialog").showModal();
$$("[data-close]").forEach(
  (b) => (b.onclick = () => $("#" + b.dataset.close).close()),
);
$$("[data-source]").forEach(
  (b) =>
    (b.onclick = () => {
      state.source = b.dataset.source;
      $$("[data-source]").forEach((x) => x.classList.toggle("active", x === b));
      $("#file-source").hidden = state.source !== "file";
      $("#youtube-source").hidden = state.source !== "youtube";
      $("#youtube-url").disabled = state.source !== "youtube";
      $("#file-youtube-url").disabled = state.source !== "file";
    }),
);
function setFile(file) {
  if (!file) return;
  state.file = file;
  $("#file-title").textContent = file.name;
}
$("#audio-file").onchange = (e) => setFile(e.target.files[0]);
$("#dropzone").ondragover = (e) => {
  e.preventDefault();
  $("#dropzone").classList.add("dragging");
};
$("#dropzone").ondragleave = () => $("#dropzone").classList.remove("dragging");
$("#dropzone").ondrop = (e) => {
  e.preventDefault();
  $("#dropzone").classList.remove("dragging");
  setFile(e.dataTransfer.files[0]);
};
$("#import-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = $("#import-submit");
  button.disabled = true;
  button.textContent = "Importando…";
  $("#import-error").hidden = true;
  try {
    let p;
    if (state.source === "file") {
      if (!state.file)
        throw new Error("Escolha ou arraste um arquivo de áudio.");
      const data = new FormData();
      data.append("file", state.file);
      data.append("options", JSON.stringify(options()));
      const video = $("#file-youtube-url").value.trim();
      if (video) data.append("youtube_url", video);
      p = await api("/api/import/file", { method: "POST", body: data });
      $("#file-youtube-url").value = "";
    } else {
      p = await api("/api/import/youtube", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: $("#youtube-url").value,
          options: options(),
        }),
      });
    }
    $("#import-dialog").close();
    await selectProject(p.id);
    await refreshLibrary();
  } catch (err) {
    $("#import-error").textContent = err.message;
    $("#import-error").hidden = false;
  } finally {
    button.disabled = false;
    button.innerHTML = "Separar e analisar música <span>→</span>";
  }
};
async function refreshLibrary() {
  const list = await api("/api/projects");
  $("#project-count").textContent = list.length;
  $("#project-list").innerHTML = list.length
    ? list
        .map((p) => {
          const label = {
            processing: "Processando…",
            queued: "Na fila",
            failed: "Falha na importação",
            cancelled: "Importação cancelada",
            interrupted: "Importação interrompida",
          };
          const subtitle = p.duration && !["processing", "queued"].includes(p.status)
            ? time(p.duration) : label[p.status] || "Importação";
          const remove = p.can_delete_import
            ? `<button type="button" class="project-delete" data-delete-import="${p.id}" aria-label="Excluir tentativa: ${escapeHTML(p.title)}">Excluir</button>` : "";
          return `<div class="project-row"><button type="button" class="project-item ${p.id === state.project?.id ? "current" : ""}" data-id="${p.id}"><span class="project-icon">♫</span><span><b>${escapeHTML(p.title)}</b><small>${subtitle}</small></span></button>${remove}</div>`;
        })
        .join("")
    : '<p class="muted small">Suas músicas ficam salvas neste computador.</p>';
  $$(".project-item").forEach(
    (b) => (b.onclick = () => selectProject(b.dataset.id)),
  );
  $$("[data-delete-import]").forEach((button) => {
    button.onclick = () => deleteImport(button);
  });
}
let projectSelection=0;
async function deleteImport(button) {
  if (!window.confirm("Excluir esta tentativa de importação da biblioteca?")) return;
  const id = button.dataset.deleteImport;
  const selection = projectSelection;
  button.disabled = true;
  try {
    await api("/api/projects/" + id, { method: "DELETE" });
    if (state.project?.id === id && projectSelection === selection) {
      ++projectSelection;
      state.project = null;
      player.dispose();
      $("#workspace").hidden = true;
      $("#welcome").hidden = false;
    }
    await refreshLibrary();
    toast("Tentativa de importação excluída.");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}
async function selectProject(id) {
  const selection=++projectSelection;
  player.pause();
  document.body.classList.remove("library-open");
  const p = await api("/api/projects/" + id);
  if(selection!==projectSelection)return;
  state.project = p;
  state.activeChord = -1;
  state.activeLine = -1;
  state.loopA = null;
  state.loopB = null;
  state.selectedChord = -1;
  state.selectedTrack = null;
  $("#loop-a").classList.remove("active");
  $("#loop-b").classList.remove("active");
  $("#compare-original").checked = false;
  player.comparing = false;
  $("#welcome").hidden = true;
  $("#workspace").hidden = false;
  renderProject(p, true);
  await refreshLibrary();
}
function renderProject(p, force = false) {
  const previous = state.project;
  state.project = p;
  $("#breadcrumb-name").textContent = p.title;
  $("#song-title").textContent = p.title;
  $("#song-source").textContent =
    p.source.type === "youtube" ? "IMPORTADO DO YOUTUBE" : "ARQUIVO LOCAL";
  $("#song-duration").textContent = p.duration
    ? time(p.duration)
    : "Preparando";
  $("#song-key").textContent = p.key?.label || "—";
  $("#song-bpm").textContent = p.bpm || "—";
  const busy = ["queued", "processing"].includes(p.status);
  $("#song-status").textContent = busy
    ? "Processando"
    : p.status === "ready"
      ? "Pronto para tocar"
      : p.status === "partial"
        ? "Faixas prontas"
        : p.status === "failed"
          ? "Falha na análise"
          : "Interrompido";
  $("#processing").hidden = !busy;
  $("#processing-stage").textContent = p.stage;
  $("#processing-percent").textContent = (p.progress || 0) + "%";
  $("#processing-bar").value = p.progress || 0;
  const sinceUpdate=busy && p.updated_at?Math.max(0,(Date.now()-Date.parse(p.updated_at))/1000):0;
  const elapsed = (p.elapsed_seconds || 0)+sinceUpdate;
  const waiting = (p.worker_runtime?.last_output_seconds || 0)+sinceUpdate;
  $("#processing-detail").textContent = busy
    ? `Tempo decorrido: ${time(elapsed)}${p.worker_runtime ? ` · ${waiting >= 30 ? 'Aguardando o próximo trecho; última saída há '+time(waiting) : 'Progresso atualizado por trecho'}` : ''}. Os modelos são baixados somente no primeiro uso.`
    : '';
  const oldAnalysis = p.chords?.length && (p.chord_analysis_pending || busy && p.progress < 85 || p.chord_analysis?.version !== '0.3.0');
  $('#previous-analysis').hidden = !oldAnalysis;
  $('#previous-analysis-text').textContent = busy
    ? 'Cifra da análise anterior. Os acordes serão atualizados após o processamento.'
    : 'Cifra de uma análise anterior. Recalcule com o novo modo estável.';
  $('#update-existing-chords').textContent = busy ? 'Parar separação e atualizar cifra' : 'Atualizar somente a cifra';
  $('#update-existing-chords').disabled = !p.stems?.length;
  const warnings = [...(p.warnings || [])];
  if (p.error) warnings.push({ message: p.error });
  $("#warnings").hidden = !warnings.length;
  $("#warnings").innerHTML = warnings
    .map(
      (w) =>
        `<div>${escapeHTML(w.message)} ${w.stage ? `<button class="text-button" data-retry="${w.stage}">Tentar novamente</button>` : ""}</div>`,
    )
    .join("");
  $$("[data-retry]").forEach(
    (b) =>
      (b.onclick = async () => {
        await api(`/api/projects/${p.id}/retry/${b.dataset.retry}`, {
          method: "POST",
        });
        await poll();
      }),
  );
  $("#export-project").disabled = !p.stems?.length;
  const canRetrySeparation = ["failed", "cancelled"].includes(p.status) && p.duration > 0;
  $("#refine-audio").disabled = busy || (!p.stems?.length && !canRetrySeparation);
  $("#stem-settings").disabled = !p.stems?.length;
  $("#split-guitars").hidden = !p.stems?.some(s => s.name === "guitar");
  $("#split-guitars").disabled = busy;
  $("#reanalyze-chords").disabled = !p.stems?.length;
  $("#stems-empty").hidden = !!p.stems?.length;
  if (p.stems?.length) {
    const changed =
      player.key !== audioKey(p);
    const loaded=changed&&player.load(p);
    const revisionChanged=!previous||previous.audio_revision!==p.audio_revision;
    const contentChanged = !previous || JSON.stringify([previous.chords,previous.lyrics,previous.key,previous.bpm,previous.chord_analysis]) !== JSON.stringify([p.chords,p.lyrics,p.key,p.bpm,p.chord_analysis]);
    if (force || loaded || revisionChanged || contentChanged) {
      renderStudy();
      renderDetails();
      renderStudioPanels();
      studioLayout();
    }
  }
  renderTabUI();
}
function trackWave(track) {
  const values = track.waveform || [];
  if (!values.length) return '<svg class="track-wave" viewBox="0 0 1000 64"></svg>';
  const top = values.map((v,i) => `${i/(values.length-1 || 1)*1000},${32-Math.max(.4,Math.sqrt(Math.max(0,v))*30)}`);
  const bottom = values.map((v,i) => `${i/(values.length-1 || 1)*1000},${32+Math.max(.4,Math.sqrt(Math.max(0,v))*30)}`).reverse();
  return `<svg class="track-wave" viewBox="0 0 1000 64" preserveAspectRatio="none" aria-hidden="true"><path d="M ${top.join(' L ')} L ${bottom.join(' L ')} Z"/></svg>`;
}
function renderTracks() {
  $("#track-list").innerHTML = player.tracks
    .map(
      (t, i) =>
        `<div class="track ${t.name === (state.selectedTrack || (player.tracks.some(s=>s.name==='guitar_rhythm')?'guitar_rhythm':player.tracks.some(s=>s.name==='guitar')?'guitar':'other')) ? 'selected' : ''}" data-track="${i}"><div class="track-controls"><div class="track-name-line"><button class="track-select" data-track-name="${t.name}" title="${t.name==='guitar_lead'?'Abrir tablatura da guitarra solo':'Selecionar faixa'}">${escapeHTML(t.label)}${t.name==='guitar_lead'?'<small>TAB</small>':''}</button><a href="${escapeHTML(mediaUrl(`/api/projects/${state.project.id}/audio/${t.name}?download=true&v=${encodeURIComponent(player.revision)}`))}" title="Baixar faixa" aria-label="Baixar ${escapeHTML(t.label)}">↓</a></div><div class="track-controls-bottom"><input class="volume" type="range" min="0" max="150" value="100" aria-label="Volume de ${escapeHTML(t.label)}"><button class="square mute" aria-label="Silenciar ${escapeHTML(t.label)}" aria-pressed="false">M</button><button class="square solo" aria-label="Ouvir somente ${escapeHTML(t.label)}" aria-pressed="false">S</button><span class="track-db">0 dB</span></div></div><button class="track-audio" aria-label="Posição na faixa ${escapeHTML(t.label)}">${trackWave(t)}</button></div>`,
    )
    .join("");
  $$(".track").forEach((row) => {
    const t = player.tracks[+row.dataset.track];
    const select=()=>{
      state.selectedTrack=t.name;
      $$(".track").forEach(r=>r.classList.toggle("selected",+r.dataset.track===+row.dataset.track));
      if(t.name==='guitar_lead')$('[data-view="tab"]').click();
    };
    $('.track-select',row).onclick=select;
    $(".track-audio", row).onclick = (e) => {
      const rect=e.currentTarget.getBoundingClientRect();
      player.seek((e.clientX-rect.left)/rect.width*player.duration);
      select();
    };
    $(".mute", row).onclick = (e) => {
      player.useStems();
      t.mute = !t.mute;
      e.currentTarget.classList.toggle("active", t.mute);
      e.currentTarget.setAttribute("aria-pressed", String(t.mute));
      row.classList.toggle("silenced", t.mute);
      player.applyMix();
    };
    $(".solo", row).onclick = (e) => {
      player.useStems();
      t.solo = !t.solo;
      e.currentTarget.classList.toggle("active", t.solo);
      e.currentTarget.setAttribute("aria-pressed", String(t.solo));
      player.applyMix();
      if(t.solo&&t.name==='guitar_lead')select();
    };
    $(".volume", row).oninput = (e) => {
      player.useStems();
      t.gain = +e.target.value / 100;
      $(".track-db", row).textContent = t.gain
        ? `${(20 * Math.log10(t.gain)).toFixed(1)} dB`
        : "−∞ dB";
      player.applyMix();
    };
  });
}
$$("[data-view]").forEach(
  (button) =>
    (button.onclick = () => {
      state.view = button.dataset.view;
      $$("[data-view]").forEach((b) =>
        b.setAttribute("aria-selected", String(b === button)),
      );
      ["mixer", "study", "chords", "forms", "tab", "details"].forEach(
        (v) => ($("#" + v + "-view").hidden = v !== state.view),
      );
      document.body.dataset.studioView=state.view;
      renderStudioPanels();
      studioLayout();
      if(state.view==='tab')renderTabUI(true);
    }),
);
$("#play").onclick = () => (player.wantsPlay ? player.pause() : player.play());
$("#skip-back").onclick = () => player.seek(player.current() - 10);
$("#skip-forward").onclick = () => player.seek(player.current() + 10);
$("#seek").oninput = (e) =>
  player.seek((+e.target.value / 1000) * player.duration);
$("#speed").onchange = (e) => player.setSpeed(+e.target.value);
$("#compare-original").onchange = (e) => player.compare(e.target.checked);
$("#loop-a").onclick = () => {
  state.loopA = player.current();
  state.loopB = null;
  $("#loop-a").classList.add("active");
  $("#loop-b").classList.remove("active");
  toast("Início do trecho: " + time(state.loopA));
};
$("#loop-b").onclick = () => {
  const end = player.current();
  if (state.loopA === null || end < state.loopA + 0.3) {
    toast("Marque o ponto A e escolha um ponto B posterior.");
    return;
  }
  state.loopB = end;
  $("#loop-b").classList.add("active");
  toast("Repetição: " + time(state.loopA) + " → " + time(state.loopB));
};
$("#clear-loop").onclick = () => {
  state.loopA = null;
  state.loopB = null;
  $("#loop-a").classList.remove("active");
  $("#loop-b").classList.remove("active");
};
$("#cancel-job").onclick = async () => {
  if (state.project)
    await api(`/api/projects/${state.project.id}/cancel`, { method: "POST" });
};
$("#refine-audio").onclick = async () => {
  if (!state.project) return;
  const hasStems = Boolean(state.project.stems?.length);
  player.pause();
  try {
    await api(`/api/projects/${state.project.id}/refine`, { method: "POST", headers:{'Content-Type':'application/json'},body:JSON.stringify({quality:$('#refine-quality-option').value}) });
    await poll();
    toast(hasStems ? "Refinando as faixas e a cifra. Sua letra e suas correções serão preservadas."
      : "Separando as faixas a partir do áudio salvo.");
  } catch (error) { toast(error.message); }
};
$("#reanalyze-chords").onclick = async () => {
  if (!state.project) return;
  try {
    const mode=$('#chord-mode-option').value;
    const queued=await api(`/api/projects/${state.project.id}/analyze-chords`, { method: "POST", headers:{'Content-Type':'application/json'},body:JSON.stringify({stability:mode}) });
    await poll();
    toast(queued.stopping_separation ? 'Parando a separação para analisar os acordes nas faixas já salvas.' : 'Recalculando a cifra e preservando os acordes corrigidos por você.');
  } catch (error) { toast(error.message); }
};
$("#split-guitars").onclick = async () => {
  if (!state.project) return;
  player.pause();
  try {
    await api(`/api/projects/${state.project.id}/split-guitars`, { method: "POST" });
    await poll();
    toast("Separando solo e base com um modelo próprio para guitarras.");
  } catch (error) { toast(error.message); }
};
function drawWave() {
  const canvas = $("#wave-canvas"),
    box = canvas.getBoundingClientRect(),
    ratio = devicePixelRatio || 1;
  canvas.width = Math.max(1, box.width * ratio);
  canvas.height = Math.max(1, box.height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  const peaks = state.project?.waveform || [],
    n = Math.min(Math.floor(box.width / 3), peaks.length);
  ctx.strokeStyle = "#5d6b7f";
  ctx.lineWidth = 1.5;
  for (let i = 0; i < n; i++) {
    const v = peaks[Math.floor((i / n) * peaks.length)] || 0,
      h = Math.max(2, Math.sqrt(v) * box.height * 0.85);
    ctx.beginPath();
    ctx.moveTo((i * box.width) / n, box.height / 2 - h / 2);
    ctx.lineTo((i * box.width) / n, box.height / 2 + h / 2);
    ctx.stroke();
  }
}
new ResizeObserver(drawWave).observe($("#wave"));

function chordSVG(label, position=null) {
  if (label === "N") return "<p>Trecho sem acorde definido</p>";
  const shape = position?.frets || GuitarVoicings.find(label);
  if (!shape) return "<p>Nenhuma posição confortável encontrada para este acorde.</p>";
  const positives = shape.filter((x) => x > 0),
    min = Math.min(...positives),
    max = Math.max(...positives),
    start = max > 5 ? Math.max(1, min) : 1,
    fretCount=Math.max(5,max-start+1),fretStep=120/fretCount;
  let svg =
    '<svg viewBox="0 0 150 190" role="img" aria-label="Diagrama do acorde ' +
    escapeHTML(label) +
    '"><g stroke="#77849a" stroke-width="1">';
  for (let s = 0; s < 6; s++)
    svg += `<line x1="${25 + s * 20}" x2="${25 + s * 20}" y1="32" y2="152"/>`;
  for (let f = 0; f <= fretCount; f++)
    svg += `<line x1="25" x2="125" y1="${32 + f * fretStep}" y2="${32 + f * fretStep}" stroke-width="${f === 0 && start === 1 ? 4 : 1}"/>`;
  svg += "</g>";
  if (start > 1)
    svg += `<text x="11" y="48" fill="#a1aec2" font-size="10">${start}</text>`;
  const {barres,fingers}=position || GuitarVoicings.details(label,shape);
  for(const barre of barres||[]) {
    const y = 32 + (barre.fret - start + 0.5) * fretStep;
    svg += `<rect class="guitar-barre" x="${20+barre.from*20}" y="${y-6}" width="${(barre.to-barre.from)*20+10}" height="12" rx="6" fill="#b6f56c"/>`;
  }
  shape.forEach((f, s) => {
    const x = 25 + s * 20;
    if (f === -1)
      svg += `<text x="${x}" y="21" text-anchor="middle" fill="#8291a7" font-size="12">×</text>`;
    else if (f === 0)
      svg += `<circle cx="${x}" cy="17" r="4" fill="none" stroke="#b6f56c"/>`;
    else {
      const y=32 + (f - start + 0.5) * fretStep;
      svg += `<circle cx="${x}" cy="${y}" r="8" fill="#b6f56c"/>`;
      if(fingers[s])svg+=`<text class="finger-number" x="${x}" y="${y+3}" text-anchor="middle" font-size="9">${fingers[s]}</text>`;
    }
    svg += `<text x="${x}" y="174" text-anchor="middle" fill="#7889a1" font-size="10">${["E", "A", "D", "G", "B", "e"][s]}</text>`;
  });
  return svg + "</svg>";
}
function displayChord(index) {
  const c = state.project?.chords?.[index];
  if (!c) return;
  const label = shifted(c.label);
  $("#current-chord").textContent = label === "N" ? "—" : label;
  $("#chord-diagram").innerHTML = chordSVG(label);
}
function renderStudy() {
  const p = state.project,
    chords = p.chords || [];
  $("#transpose-value").textContent =
    (state.transpose > 0 ? "+" : "") + state.transpose;
  $("#chord-timeline").innerHTML = chords.length
    ? chords
        .map(
          (c, i) =>
            `<button class="chord-chip" data-chord="${i}" title="${c.edited ? "Corrigido por você" : "Confiança estimada: " + Math.round(Math.max(0, Math.min(1, c.confidence || 0)) * 100) + "%"}"><b>${c.label === "N" ? "—" : escapeHTML(shifted(c.label))}</b><small>${time(c.start)}</small></button>`,
        )
        .join("")
    : '<p class="muted small">Nenhum acorde disponível para esta música.</p>';
  $$("[data-chord]").forEach((b) => {
    b.onclick = () => {
      const i = +b.dataset.chord;
      player.seek(chords[i].start);
      state.selectedChord = i;
      displayChord(i);
    };
    b.ondblclick = () => openDictionary(chords[+b.dataset.chord].label,+b.dataset.chord);
  });
  const practice = practiceLines(p);
  const intro=(p.chords||[]).filter(c=>c.label!=="N" && c.start<(practice[0]?.start||0));
  $("#intro-chords").hidden=!intro.length;
  $("#intro-chords").innerHTML='<small>INTRO</small><div>'+intro.map(c=>`<button data-intro-start="${c.start}">${escapeHTML(shifted(c.label))}</button>`).join('')+'</div>';
  $$("[data-intro-start]").forEach(b=>b.onclick=()=>player.seek(+b.dataset.introStart));
  if (practice.length) {
    $("#lyrics").innerHTML = practice
      .map((line, index) => {
        let words = line.words?.length
          ? line.words
          : line.text
              .split(/\s+/)
              .map((text, j, all) => ({
                text,
                start: line.start + ((line.end - line.start) * j) / all.length,
                end:
                  line.start + ((line.end - line.start) * (j + 1)) / all.length,
              }));
        const mapped = new Map();
        const active = chords
          .filter((c) => c.start <= line.start && c.label !== "N")
          .at(-1);
        if (active) mapped.set(0, shifted(active.label));
        for (const c of chords.filter(
          (c) => c.start >= line.start && c.start < line.end && c.label !== "N",
        )) {
          const j = words.findIndex((w) => w.end > c.start);
          if (j >= 0) mapped.set(j, shifted(c.label));
        }
        return `<div class="lyric-line" data-line="${index}" data-start="${line.start}" data-end="${line.end}"><span class="lyric-time">${time(line.start)}</span><div class="lyric-words">${words.map((w, j) => `<span class="lyric-word" data-start="${w.start}" data-end="${w.end}"><span class="chord-over">${escapeHTML(mapped.get(j) || "")}</span><span>${escapeHTML(w.text)}</span></span>`).join("")}</div></div>`;
      })
      .join("");
    $$("[data-line]").forEach(
      (b) => (b.onclick = () => player.seek(practice[+b.dataset.line].start)),
    );
  } else
    $("#lyrics").innerHTML =
      '<div class="empty-state">Nenhuma letra transcrita.<small>Músicas instrumentais, voz muito baixa ou idiomas não reconhecidos podem não gerar texto. As faixas e cifras continuam disponíveis.</small></div>';
  displayChord(Math.max(0, state.selectedChord));
  state.activeChord = -1;
  state.activeLine = -1;
}
$("#transpose-down").onclick = () => {
  state.transpose = Math.max(-12, state.transpose - 1);
  if (state.project) renderStudy();
};
$("#transpose-up").onclick = () => {
  state.transpose = Math.min(12, state.transpose + 1);
  if (state.project) renderStudy();
};
$("#capo").onchange = (e) => {
  state.capo = +e.target.value;
  if (state.project) renderStudy();
};
$("#follow-lyrics").onclick = (e) => {
  state.follow = !state.follow;
  e.currentTarget.classList.toggle("active", state.follow);
  e.currentTarget.setAttribute("aria-pressed", String(state.follow));
};
$("#edit-lyrics").onclick = () => {
  const lines = state.project?.lyrics || [];
  if (!lines.length) {
    toast("Ainda não há trechos de letra para editar.");
    return;
  }
  $("#lyrics-editor").innerHTML = lines
    .map(
      (l, i) =>
        `<label>${time(l.start)} → ${time(l.end)}<textarea data-edit-line="${i}">${escapeHTML(l.text)}</textarea></label>`,
    )
    .join("");
  $("#lyrics-dialog").showModal();
};
$("#save-lyrics").onclick = async () => {
  const lyrics = state.project.lyrics.map((l, i) => ({
    ...l,
    text: $(`[data-edit-line="${i}"]`).value,
    words: undefined,
  }));
  try {
    state.project = await api(`/api/projects/${state.project.id}/annotations`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lyrics }),
    });
    $("#lyrics-dialog").close();
    renderStudy();
    toast("Letra corrigida.");
  } catch (e) {
    toast(e.message);
  }
};
function renderDetails() {
  const p = state.project;
  const fields = [
    ["Motor de separação", p.engine?.engine || "—"],
    ["Modelo", p.engine?.model || "—"],
    ["Processamento", p.engine?.device === "cuda" ? "GPU NVIDIA" : "CPU local"],
    ["Faixas separadas", p.stems?.length || 0],
    ["Guitarra solo e base", p.engine?.guitar_split?.model || "Guitarra única"],
    ["Reconhecimento de acordes", p.chord_analysis?.engine || "CQT · versão anterior"],
    [
      "Transcrição",
      p.transcription?.model
        ? "Whisper " + p.transcription.model
        : "Não gerada",
    ],
    ["Idioma detectado", p.transcription?.language || "—"],
  ];
  $("#analysis-details").innerHTML = fields
    .map(
      ([a, b]) =>
        `<div class="detail-card"><small>${escapeHTML(a)}</small><b>${escapeHTML(b)}</b></div>`,
    )
    .join("");
}
function download(kind) {
  if (!state.project) return;
  const a = document.createElement("a");
  a.href = mediaUrl(`/api/projects/${state.project.id}/export/${kind}?transpose=${state.transpose - state.capo}`);
  a.click();
}
$("#download-lrc").onclick = () => download("lrc");
$("#download-chords").onclick = () => download("chords");
$("#export-project").onclick = () => download("zip");

let lastFrame = 0;
function frame(timestamp) {
  if (state.project && timestamp - lastFrame > 80) {
    lastFrame = timestamp;
    const t = player.current(),
      duration = player.duration || 1;
    if (player.playing && state.loopB !== null && t >= state.loopB)
      player.seek(state.loopA);
    $("#current-time").textContent = time(t);
    const controls=parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--control-width")) || 288;
    const lane=$("#stem-timeline");
    const marker=$("#timeline-playhead");
    marker.style.left=(controls+Math.max(0,lane.clientWidth-controls)*t/duration)+"px";
    followStudioTime(t);
    if(state.view==='tab')updateTabCursor(t);
    $("#seek").value = Math.floor((t / duration) * 1000);
    $("#playhead").style.left = Math.min(100, (t / duration) * 100) + "%";
    const chords = state.project.chords || [],
      ci = chords.findIndex((c) => t >= c.start && t < c.end);
    if (ci !== state.activeChord) {
      $$(".chord-chip.active").forEach((b) => b.classList.remove("active"));
      const el = $(`[data-chord="${ci}"]`);
      el?.classList.add("active");
      if (state.view === "study" && player.playing)
        el?.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
          inline: "center",
        });
      if (ci >= 0) displayChord(ci);
      $$("[data-rail-chord]").forEach(b=>b.classList.toggle("active",+b.dataset.railChord===ci));
      $$(".chord-chip").forEach(b=>b.classList.toggle("active",+b.dataset.chord===ci));
      renderForms(ci);
      state.activeChord = ci;
    }
    const li = practiceLines(state.project).findIndex(
      (l) => t >= l.start && t < l.end,
    );
    if (li !== state.activeLine) {
      $$(".lyric-line.current").forEach((l) => l.classList.remove("current"));
      const line = $(`[data-line="${li}"]`);
      line?.classList.add("current");
      if (state.follow && player.playing && state.view === "study")
        line?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      state.activeLine = li;
      updateFormsLyrics(li);
    }
    $$(".lyric-line.current .lyric-word").forEach((w) =>
      w.classList.toggle("sung", t >= +w.dataset.start && t < +w.dataset.end),
    );
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
document.addEventListener("keydown", (e) => {
  if (
    e.code === "Space" &&
    !["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(
      document.activeElement.tagName,
    ) &&
    !$("dialog[open]")
  ) {
    e.preventDefault();
    if (player.tracks.length) player.wantsPlay ? player.pause() : player.play();
  }
});
let pollInFlight=false;
async function poll() {
  if(pollInFlight||!state.project)return;
  pollInFlight=true;
  const id=state.project.id,selection=projectSelection,controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),12000);
  try{
    const p=await api('/api/projects/'+id,{signal:controller.signal});
    if(state.project?.id!==id||selection!==projectSelection)return;
    if(p.updated_at!==state.project.updated_at){
      const statusChanged=p.status!==state.project.status;
      renderProject(p);
      if(statusChanged)await refreshLibrary();
    }
  }catch(error){if(error.name!=='AbortError'&&state.project?.id===id&&selection===projectSelection)toast(error.message);}
  finally{clearTimeout(timeout);pollInFlight=false;}
}
setInterval(()=>{
  const p=state.project,tab=p?.tablature;
  if(document.visibilityState==='visible' && p &&
     (['queued','processing'].includes(p.status)||['queued','processing'].includes(tab?.status)))poll();
},1500);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')poll();});
Promise.all([refreshLibrary(),api('/api/chord-dictionary').then(data=>{
  GuitarVoicings.install(data);
  try{for(const [label,shape] of Object.entries(JSON.parse(localStorage.getItem('faixalabVoicings')||'{}')))GuitarVoicings.prefer(label,shape);}catch{}
  prepareDictionary();
})])
  .then(async () => {
    const list = await api("/api/projects");
    if (list.length) await selectProject(list[0].id);
  })
  .catch((e) => toast("Não foi possível conectar ao aplicativo: " + e.message));

// Shared transport, chord rail, lyric sheet and moving guitar shapes.
let studioWindowSeconds = null, studioProjectId = null, studioPixelsPerSecond = 1;
let studioViewport = 700, lastStudioTime = 0, studioPanUntil = 0;
let cachedPractice = null, cachedPracticeLines = [];
function practiceLines(project) {
  const source=project?.lyrics || [];
  if(source===cachedPractice)return cachedPracticeLines;
  cachedPractice=source;
  cachedPracticeLines=[];
  for(const original of source){
    const words=original.words?.length?original.words:original.text.split(/\s+/).filter(Boolean).map((text,i,all)=>({text,start:original.start+i*(original.end-original.start)/all.length,end:original.start+(i+1)*(original.end-original.start)/all.length}));
    let chunk=[];
    for(const word of words){
      const previous=chunk.at(-1);
      if(chunk.length && (word.start-previous.end>.75 || chunk.map(w=>w.text).join(' ').length+word.text.length>48 || chunk.length>=9)){
        cachedPracticeLines.push({start:chunk[0].start,end:chunk.at(-1).end,text:chunk.map(w=>w.text).join(' '),words:chunk});chunk=[];
      }
      chunk.push(word);
    }
    if(chunk.length)cachedPracticeLines.push({start:chunk[0].start,end:Math.max(chunk.at(-1).end,original.end),text:chunk.map(w=>w.text).join(' '),words:chunk});
  }
  return cachedPracticeLines;
}
function studioLayout(preservePosition=true){
  const controls=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--control-width'))||288;
  const p=state.project, duration=p?.duration||1, scroll=$('.stem-scroll');
  const previousStart=scroll.scrollLeft/studioPixelsPerSecond;
  studioViewport=Math.max(200,$('.timeline-heading-right').clientWidth||window.innerWidth-64-controls);
  if(p && p.id!==studioProjectId){
    studioProjectId=p.id;
    studioWindowSeconds=Math.min(duration,8*4*60/(p.bpm||120));
    preservePosition=false;
  }
  const seconds=Math.min(duration,studioWindowSeconds||duration);
  studioPixelsPerSecond=studioViewport/Math.max(seconds,.1);
  const width=duration*studioPixelsPerSecond;
  $('#stem-timeline').style.width=(controls+width)+'px';
  $('#studio-chord-rail').style.width=width+'px';
  $('#studio-ruler').style.width=width+'px';
  const bar=4*60/(p?.bpm||120), tickStep=bar* Math.max(1,Math.ceil(78/(bar*studioPixelsPerSecond)));
  const ticks=[];
  for(let t=0;t<duration;t+=tickStep){
    ticks.push(`<div class="ruler-tick" style="left:${t*studioPixelsPerSecond}px"><span>${time(t)}<small>comp. ${Math.round(t/bar)+1}</small></span></div>`);
  }
  $('#studio-ruler').innerHTML=ticks.join('');
  scroll.scrollLeft=preservePosition?Math.min(Math.max(0,width-studioViewport),previousStart*studioPixelsPerSecond):0;
  updateStudioScroll();
}
function updateStudioScroll(){
  const scroll=$('.stem-scroll'), duration=state.project?.duration||1;
  const shift=`translateX(${-scroll.scrollLeft}px)`;
  $('#studio-chord-rail').style.transform=shift;$('#studio-ruler').style.transform=shift;
  const start=scroll.scrollLeft/studioPixelsPerSecond,end=Math.min(duration,start+studioViewport/studioPixelsPerSecond);
  $('#timeline-range').textContent=`${time(start)} – ${time(end)} · compassos`;
  $('#overview-window').style.left=(start/duration*100)+'%';
  $('#overview-window').style.width=((end-start)/duration*100)+'%';
}
function followStudioTime(t){
  const jump=Math.abs(t-lastStudioTime)>.6;
  lastStudioTime=t;
  if(state.view!=='mixer' || !studioWindowSeconds || (!player.playing && !jump) || performance.now()<studioPanUntil)return;
  const scroll=$('.stem-scroll'), x=t*studioPixelsPerSecond;
  if(x<scroll.scrollLeft || x>scroll.scrollLeft+studioViewport*.9){
    scroll.scrollLeft=Math.max(0,Math.min((state.project.duration*studioPixelsPerSecond)-studioViewport,x-studioViewport*.15));
    updateStudioScroll();
  }
}
function renderStudioPanels(){
  const p=state.project;if(!p)return;
  $('#transport-song-title').textContent=p.title;
  $('#transport-meta').textContent=[p.bpm?p.bpm+' bpm':null,p.key?.label].filter(Boolean).join(' / ')||'Analisando áudio';
  const duration=p.duration||1;
  $('#studio-chord-rail').innerHTML=(p.chords||[]).map((c,i)=>`<button class="rail-chord ${i===state.activeChord?'active':''}" data-rail-chord="${i}" style="left:${c.start/duration*100}%;width:${(c.end-c.start)/duration*100}%" title="${escapeHTML(shifted(c.label))} · ${time(c.start)} – ${time(c.end)}">${c.label==='N'?'—':escapeHTML(shifted(c.label))}</button>`).join('');
  $$('[data-rail-chord]').forEach(b=>b.onclick=()=>player.seek(p.chords[+b.dataset.railChord].start));
  renderForms(Math.max(0,state.activeChord));
  updateFormsLyrics(Math.max(0,state.activeLine));
}
function renderForms(index){
  const chords=state.project?.chords||[];
  if(!chords.length){$('#forms-cards').innerHTML='<p class="muted">Os diagramas aparecerão após a análise dos acordes.</p>';return;}
  index=Math.max(0,index);
  let start=Math.max(0,index-2);start=Math.min(start,Math.max(0,chords.length-5));
  $('#forms-cards').innerHTML=chords.slice(start,start+5).map((c,j)=>`<button class="form-card ${start+j<index?'past':''} ${start+j===index?'current':''}" data-form-chord="${start+j}"><strong>${c.label==='N'?'—':escapeHTML(shifted(c.label))}</strong><span class="diagram-surface">${chordSVG(shifted(c.label))}</span></button>`).join('');
  $$('[data-form-chord]').forEach(b=>b.onclick=()=>player.seek(chords[+b.dataset.formChord].start));
  const first=Math.max(0,index-4);
  $('#forms-progress').innerHTML=chords.slice(first,first+9).map((c,j)=>`<button class="${first+j===index?'active':''}" data-form-step="${first+j}" aria-label="Ir ao acorde ${escapeHTML(c.label)}"><small>${Math.abs(first+j-index)<=1?escapeHTML(shifted(c.label)):''}</small></button>`).join('');
  $$('[data-form-step]').forEach(b=>b.onclick=()=>player.seek(chords[+b.dataset.formStep].start));
}
function updateFormsLyrics(index){
  const lines=practiceLines(state.project);
  if(index<0)index=Math.max(0,lines.findLastIndex(line=>line.start<=player.current()));
  $('#forms-current-lyric').textContent=lines[index]?.text || (state.project?.lyrics?.length?'':'Música instrumental');
  $('#forms-next-lyric').textContent=lines[index+1]?.text||'';
}
let instrumentCatalog=null;
async function loadInstrumentChoices(){
  if(!instrumentCatalog)instrumentCatalog=await api('/api/instruments');
  const selected=state.project?.instrument_settings?.selection||[];
  $('#instrument-selection-mode').value=selected.length?'manual':'auto';
  $('#instrument-choices').hidden=!selected.length;
  $('#instrument-choices').innerHTML=instrumentCatalog.instruments.map(item=>`<label><input type="checkbox" value="${escapeHTML(item.name)}" ${selected.includes(item.name)?'checked':''}>${escapeHTML(item.label)}</label>`).join('');
  $$('#instrument-choices input').forEach(input=>input.onchange=()=>{
    if(!input.checked)return;
    for(const group of instrumentCatalog.exclusive_groups||[]){
      if(!group.includes(input.value))continue;
      const excludes=input.value===group[0]?group.slice(1):[group[0]];
      for(const other of $$('#instrument-choices input'))if(excludes.includes(other.value))other.checked=false;
    }
  });
}
function studioInitialize(){
  $('#library-toggle').onclick=()=>document.body.classList.toggle('library-open');
  $('#transport-export').append($('#export-project'));
  $('#chords-page').append($('#chord-timeline'));
  $('#stem-settings').onclick=()=>{
    if(state.project){
      const busy=['queued','processing'].includes(state.project.status);
      $('#settings-refine').disabled=busy;
      $('#settings-instruments').disabled=busy||!state.project.stems?.some(s=>s.name==='drums');
      $('#settings-guitars').disabled=busy||!state.project.stems?.some(s=>s.name==='guitar');
      $('#refine-quality-option').value=state.project.options?.quality||'balanced';
      $('#chord-mode-option').value=state.project.options?.chord_stability||'stable';
      $('#settings-chords').textContent=busy?'Parar separação e recalcular a cifra':'Recalcular somente a cifra';
      $('#separation-dialog').showModal();
      loadInstrumentChoices().catch(error=>toast(error.message));
    }
    else openImport();
  };
  for(const [settings,action] of [['settings-refine','refine-audio'],['settings-guitars','split-guitars'],['settings-chords','reanalyze-chords']]){
    $('#'+settings).onclick=()=>{$('#separation-dialog').close();$('#'+action).click();};
  }
  $('#instrument-selection-mode').onchange=()=>{
    $('#instrument-choices').hidden=$('#instrument-selection-mode').value!=='manual';
  };
  $('#settings-instruments').onclick=async()=>{
    if(!state.project)return;
    const manual=$('#instrument-selection-mode').value==='manual';
    const selection=manual?$$('#instrument-choices input:checked').map(input=>input.value):[];
    if(manual&&!selection.length){toast('Escolha ao menos uma categoria.');return;}
    try{
      await api(`/api/projects/${state.project.id}/refine-instruments`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selection})});
      player.pause();$('#separation-dialog').close();await poll();toast('Separando os candidatos e verificando os instrumentos por IA.');
    }catch(error){toast(error.message);}
  };
  const scroll=$('.stem-scroll');
  scroll.onscroll=updateStudioScroll;
  scroll.addEventListener('wheel',()=>{studioPanUntil=performance.now()+4000;},{passive:true});
  scroll.addEventListener('pointerdown',()=>{studioPanUntil=performance.now()+4000;});
  $('#zoom-in').onclick=()=>{studioWindowSeconds=Math.max(2,(studioWindowSeconds||16)/1.5);studioLayout();};
  $('#zoom-out').onclick=()=>{studioWindowSeconds=Math.min(state.project?.duration||16,(studioWindowSeconds||16)*1.5);studioLayout();};
  $('#overview-seek').oninput=e=>{
    studioPanUntil=0;
    player.seek(+e.target.value/1000*(state.project?.duration||0));
    const x=player.current()*studioPixelsPerSecond;
    scroll.scrollLeft=Math.max(0,Math.min((state.project?.duration||1)*studioPixelsPerSecond-studioViewport,x-studioViewport*.15));
    updateStudioScroll();
  };
  $('#update-existing-chords').onclick=()=>{
    $('#chord-mode-option').value='stable';
    $('#reanalyze-chords').click();
  };
  window.addEventListener('resize',()=>studioLayout());
  studioLayout();
}
studioInitialize();

// Offline chord reference and audio-derived lead-guitar tablature.
let dictionarySelection=null, dictionaryPosition=0, dictionaryTarget=null, dictionaryPositions=[];
let tabScoreKey='', tabRequestedKey='', tabEditIndex=-1, currentTabBlock=-1, tabControlsProject=null;
const SOLO_TAB_VERSION='0.6.0';
const pitchNames=['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
const guitarTuning=[40,45,50,55,59,64];
const pitchName=midi=>pitchNames[midi%12]+(Math.floor(midi/12)-1);
const qualityNames={'':'Maior',m:'Menor',dim:'Diminuto',dim7:'Diminuto com sétima',aug:'Aumentado','5':'Power chord','6':'Sexta',m6:'Menor com sexta','7':'Sétima dominante',maj7:'Sétima maior',m7:'Menor com sétima',mMaj7:'Menor com sétima maior',sus2:'Suspenso 2',sus4:'Suspenso 4',m7b5:'Meio diminuto'};
function prepareDictionary(){
  $('#dictionary-root').innerHTML='<option value="">Todas</option>'+pitchNames.map((root,i)=>`<option value="${i}">${root}${[1,3,6,8,10].includes(i)?' / '+['','Db','','Eb','','','Gb','','Ab','','Bb'][i]:''}</option>`).join('');
  $('#dictionary-quality').innerHTML='<option value="*">Todas</option>'+GuitarVoicings.qualities().map(q=>`<option value="${escapeHTML(q)}">${escapeHTML(qualityNames[q]||q)}</option>`).join('');
  const meta=GuitarVoicings.summary();
  $('#dictionary-summary').textContent=`${meta.chords.toLocaleString('pt-BR')} cifras · ${meta.positions.toLocaleString('pt-BR')} posições verificadas · ${meta.families} famílias · afinação padrão`;
}
function openDictionary(label='C',target=null){
  dictionaryTarget=target===null?null:{project:state.project?.id,index:target};
  $('#dictionary-root').value='';$('#dictionary-quality').value='*';$('#dictionary-search').value='';
  renderDictionaryResults();
  chooseDictionaryChord(label==='N'?'C':label);
  $('#dictionary-apply').hidden=!dictionaryTarget;
  $('#dictionary-silence').hidden=!dictionaryTarget;
  $('#dictionary-dialog').showModal();
  $('#dictionary-search').focus();
}
function renderDictionaryResults(){
  let query=$('#dictionary-search').value.trim();
  query=query.replace(/dó/ig,'C').replace(/ré/ig,'D').replace(/mi\b/ig,'E').replace(/fá/ig,'F').replace(/sol/ig,'G').replace(/lá/ig,'A').replace(/si\b/ig,'B').replace(/\s*menor/ig,'m').replace(/\s*maior/ig,'');
  const parsed=GuitarVoicings.parse(query), canonical=parsed?.label||query;
  const root=$('#dictionary-root').value,quality=$('#dictionary-quality').value;
  const matches=GuitarVoicings.entries().filter(c=>(root===''||c.root===+root)&&(quality==='*'||c.quality===quality)&&(!query||c.label.toLowerCase().includes(canonical.toLowerCase())));
  matches.sort((a,b)=>Number(b.label===canonical)-Number(a.label===canonical)||a.root-b.root||a.label.length-b.label.length||a.label.localeCompare(b.label));
  $('#dictionary-count').textContent=`${matches.length} acordes encontrados`;
  $('#dictionary-results').innerHTML=matches.map(c=>`<button class="dictionary-result ${c.label===dictionarySelection?'active':''}" data-dictionary-chord="${escapeHTML(c.label)}"><b>${escapeHTML(c.label)}</b><small>${c.positions.length} posições</small></button>`).join('')||'<p class="muted">Nenhuma cifra encontrada. Use uma fundamental e uma família.</p>';
  $$('[data-dictionary-chord]').forEach(button=>button.onclick=()=>chooseDictionaryChord(button.dataset.dictionaryChord));
}
function chooseDictionaryChord(label){
  const parsed=GuitarVoicings.parse(label);
  dictionarySelection=parsed?.label||label;dictionaryPosition=0;
  const positions=GuitarVoicings.positions(dictionarySelection);
  dictionaryPositions=positions;
  $('#dictionary-name').textContent=dictionarySelection;
  updateDictionaryNotes();
  $('#dictionary-positions').innerHTML=positions.map((position,i)=>`<button class="dictionary-position ${i===0?'active':''}" data-dictionary-position="${i}"><span>Posição ${i+1}</span><span class="diagram-surface">${chordSVG(dictionarySelection,position)}</span><small>${position.frets.map(f=>f<0?'x':f).join(' · ')}</small></button>`).join('')||'<p class="muted">Não há posição disponível nessa região.</p>';
  $$('[data-dictionary-position]').forEach(button=>button.onclick=()=>{
    dictionaryPosition=+button.dataset.dictionaryPosition;
    $$('[data-dictionary-position]').forEach(b=>b.classList.toggle('active',b===button));
    updateDictionaryNotes();
  });
  $('#dictionary-prefer').disabled=!positions.length;
  $$('[data-dictionary-chord]').forEach(button=>button.classList.toggle('active',button.dataset.dictionaryChord===dictionarySelection));
}
function updateDictionaryNotes(){
  const parsed=GuitarVoicings.parse(dictionarySelection),position=dictionaryPositions[dictionaryPosition];
  if(!parsed){$('#dictionary-notes').textContent='Escolha uma cifra do catálogo.';return;}
  const sounding=position?.frets.flatMap((f,i)=>f<0?[]:[pitchName(guitarTuning[i]+f)])||[];
  $('#dictionary-notes').textContent=`Família: ${parsed.notes.map(n=>pitchNames[n]).join(' · ')}. Baixo: ${pitchNames[parsed.bass]}.\nNesta posição: ${sounding.join(' · ')}. Notas opcionais podem ser omitidas nas extensões.`;
}
async function applyDictionaryChord(label){
  if(!dictionaryTarget||dictionaryTarget.project!==state.project?.id)return;
  const chords=state.project.chords.map((c,i)=>i===dictionaryTarget.index?{...c,label}:c);
  try{
    const p=await api(`/api/projects/${state.project.id}/annotations`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({chords})});
    $('#dictionary-dialog').close();renderProject(p,true);toast('Acorde corrigido e preservado para as próximas análises.');
  }catch(error){toast(error.message);}
}
function currentTabValid(){
  const p=state.project,tab=p?.tablature;
  return !!tab&&tab.status==='ready'&&tab.version===SOLO_TAB_VERSION&&(tab.source_revision||'original')===(p.audio_revision||'original');
}
async function generateSoloTab(automatic=false){
  const p=state.project;if(!p)return;
  const key=p.id+':'+(p.audio_revision||'original')+':'+SOLO_TAB_VERSION;
  tabRequestedKey=key;
  const [min_fret,max_fret]=$('#tab-region').value.split(',').map(Number);
  try{
    await api(`/api/projects/${p.id}/solo-tabs`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({min_fret,max_fret,voices:+$('#tab-voices').value,scope:$('#tab-scope').value,preserve_edits:$('#tab-keep-edits').checked})});
    await poll();
  }catch(error){
    if(!automatic)toast(error.message);
    $('#tab-status').textContent=error.message;
  }
}
function renderTabUI(automatic=false){
  const p=state.project;if(!p)return;
  const tab=p.tablature||{},lead=p.stems?.some(s=>s.name==='guitar_lead');
  if(tabControlsProject!==p.id){
    tabControlsProject=p.id;
    const region=[tab.settings?.min_fret??0,tab.settings?.max_fret??24].join(',');
    $('#tab-region').value=$$('#tab-region option').some(o=>o.value===region)?region:'0,24';
    $('#tab-voices').value=String(tab.settings?.voices||1);
    $('#tab-scope').value=tab.version===SOLO_TAB_VERSION?(tab.settings?.scope||'solos'):'solos';
    $('#tab-keep-edits').checked=tab.settings?.preserve_edits!==false;
  }
  const busy=['queued','processing'].includes(tab.status),separating=['queued','processing'].includes(p.status);
  const stale=(tab.source_revision||'original')!==(p.audio_revision||'original')||tab.version!==SOLO_TAB_VERSION;
  const usable=currentTabValid();
  $('#generate-solo-tab').disabled=busy||separating||!lead;
  $('#generate-solo-tab').textContent=usable?'Recalcular tablatura':'Gerar tablatura';
  $('#cancel-solo-tab').hidden=!busy;
  $('#tab-progress').hidden=!busy;$('#tab-progress').value=tab.progress||0;
  $('#tab-scope').disabled=busy;
  $('#download-solo-tab').disabled=!usable;$('#download-solo-midi').disabled=!usable;
  $('#tab-split-guitars').hidden=lead||!p.stems?.some(s=>s.name==='guitar');
  $('#tab-split-guitars').disabled=separating;
  const sectionText=tab.settings?.scope==='full'?' · faixa inteira':` · ${tab.sections?.length||0} trechos de solo`;
  $('#tab-status').textContent=busy?`${tab.stage} · ${tab.progress||0}%`:tab.error?tab.error:separating?'Aguardando a separação terminar para usar a faixa atual.':!lead?'Este projeto precisa ter uma faixa de guitarra solo.':usable?`${tab.notes?.length||0} notas${sectionText}${tab.unmapped?' · '+tab.unmapped+' fora da região escolhida':''}. ${tab.message||''}`:tab.status==='cancelled'||tab.status==='interrupted'?'A transcrição foi interrompida. Gere novamente para continuar.':stale&&tab.notes?.length?'Esta tablatura usa uma análise anterior. Recalcule para detectar os solos automaticamente.':'A análise detectará os trechos de solo antes de transcrever as notas.';
  const sections=usable&&tab.settings?.scope!=='full'?tab.sections||[]:[];
  $('#solo-sections').hidden=!sections.length;
  $('#solo-sections').innerHTML=sections.map((s,i)=>`<button type="button" data-solo-start="${Number(s.start)}">Solo ${i+1} <span>${time(s.start)}–${time(s.end)}</span></button>`).join('');
  $$('[data-solo-start]').forEach(button=>button.onclick=()=>player.seek(+button.dataset.soloStart));
  const notes=usable?tab.notes||[]:[];
  $('#tab-empty').hidden=!!notes.length;
  $('#tab-empty').textContent=busy?'Reconhecendo as notas do solo…':tab.message||(!lead?'Separe guitarra solo e base para habilitar a tablatura.':'As notas e os tempos aparecerão aqui após a transcrição.');
  const scoreKey=JSON.stringify([p.id,p.audio_revision,usable,notes,p.bpm]);
  if(scoreKey!==tabScoreKey){tabScoreKey=scoreKey;currentTabBlock=-1;renderTabScore(notes);}
  if(state.view==='tab'&&lead&&!separating&&!busy&&!usable&&!['failed','cancelled','interrupted'].includes(tab.status)){
    const key=p.id+':'+(p.audio_revision||'original')+':'+SOLO_TAB_VERSION;
    if(tabRequestedKey!==key)generateSoloTab(true);
  }
}
function renderTabScore(notes){
  if(!notes.length){$('#solo-tab-score').innerHTML='';return;}
  const p=state.project,secondsPerBlock=4*4*60/(p.bpm||120);
  const blocks=new Map();
  notes.forEach((note,index)=>{
    const first=Math.floor(note.start/secondsPerBlock),last=Math.floor((note.end-.0001)/secondsPerBlock);
    for(let block=first;block<=last;block++){
      if(!blocks.has(block))blocks.set(block,[]);
      blocks.get(block).push({...note,index,continued:block>first});
    }
  });
  const parts=[];
  for(const [block,items] of [...blocks].sort((a,b)=>a[0]-b[0])){
    const start=block*secondsPerBlock,end=Math.min(p.duration,start+secondsPerBlock);
    const width=Math.max(1000,items.length*32),left=45,right=width-25;
    let svg=`<svg viewBox="0 0 ${width} 190" style="min-width:${Math.min(width,1800)}px" role="img" aria-label="Tablatura de ${time(start)} a ${time(end)}"><g class="tab-strings">`;
    for(let string=5;string>=0;string--){
      const y=35+(5-string)*25;
      svg+=`<text x="12" y="${y+4}">${['E','A','D','G','B','e'][string]}</text><line x1="${left}" x2="${right}" y1="${y}" y2="${y}"/>`;
    }
    const bar=4*60/(p.bpm||120);
    for(let n=0;n<=4;n++){
      const x=left+n/4*(right-left);
      svg+=`<line class="tab-barline" x1="${x}" x2="${x}" y1="26" y2="168"/><text class="tab-measure" x="${x+5}" y="17">${block*4+n+1}</text>`;
    }
    svg+='</g>';
    for(const note of items){
      const visibleStart=Math.max(start,note.start),x=left+(visibleStart-start)/secondsPerBlock*(right-left), y=35+(5-(note.string??5))*25;
      const durationWidth=Math.max(3,(Math.min(note.end,end)-visibleStart)/secondsPerBlock*(right-left));
      const label=note.fret===null?'?':note.continued?'('+note.fret+')':note.fret;
      svg+=`<g tabindex="0" role="button" aria-label="${escapeHTML(note.note)}, ${note.string===null?'sem posição':'corda '+(6-note.string)+', casa '+note.fret}, ${note.start.toFixed(2)} segundos" class="tab-note ${note.edited?'edited':''}" data-tab-note="${note.index}"><title>${escapeHTML(note.note)} · ${note.start.toFixed(2)}–${note.end.toFixed(2)} s${note.edited?' · corrigida':''}${note.continued?' · nota sustentada':''}</title><line class="tab-duration" x1="${x}" x2="${x+durationWidth}" y1="${y+10}" y2="${y+10}"/><rect x="${x-(note.continued?22:13)}" y="${y-12}" width="${note.continued?45:27}" height="24" rx="5"/><text x="${x}" y="${y+6}" text-anchor="middle">${label}</text></g>`;
    }
    svg+='<line class="tab-cursor" x1="45" x2="45" y1="23" y2="170"/></svg>';
    parts.push(`<section class="tab-block" data-tab-block="${block}" data-start="${start}" data-end="${end}" data-width="${width}"><header><span>Compassos ${block*4+1}–${block*4+4}</span><small>${time(start)} – ${time(end)}</small></header><div class="tab-staff-scroll">${svg}</div></section>`);
  }
  $('#solo-tab-score').innerHTML=parts.join('');
  $$('[data-tab-note]').forEach(element=>{
    element.onclick=()=>player.seek(notes[+element.dataset.tabNote].start);
    element.ondblclick=()=>editSoloNote(+element.dataset.tabNote);
    element.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();player.seek(notes[+element.dataset.tabNote].start);}};
  });
  updateTabCursor(player.current());
}
function updateTabCursor(t){
  const notes=currentTabValid()?state.project.tablature.notes:[];
  $$('.tab-note.active').forEach(element=>element.classList.remove('active'));
  for(let i=0;i<notes.length;i++)if(notes[i].start<=t&&notes[i].end>t)$$(`[data-tab-note="${i}"]`).forEach(element=>element.classList.add('active'));
  const block=$$('.tab-block').find(element=>t>=+element.dataset.start&&t<+element.dataset.end);
  if(!block)return;
  const index=+block.dataset.tabBlock,step=16*60/(state.project.bpm||120),width=+block.dataset.width;
  const x=45+(t-(+block.dataset.start))/step*(width-70);
  const cursor=$('.tab-cursor',block);cursor.setAttribute('x1',x);cursor.setAttribute('x2',x);
  if(index!==currentTabBlock){
    $$('.tab-block.current').forEach(element=>element.classList.remove('current'));
    block.classList.add('current');currentTabBlock=index;
    if(player.playing&&state.view==='tab')block.scrollIntoView({behavior:'smooth',block:'center'});
  }
}
function editSoloNote(index){
  const note=state.project?.tablature?.notes?.[index];if(!note)return;
  tabEditIndex=index;
  $('#solo-note-start').value=note.start;$('#solo-note-end').value=note.end;
  $('#solo-note-string').value=note.string??5;$('#solo-note-fret').value=note.fret??0;
  $('#solo-note-name').textContent=note.note;
  $('#solo-note-dialog').showModal();
}
function initializeLearning(){
  $('#dictionary-toggle').onclick=()=>openDictionary(state.project?.chords?.[state.activeChord]?.label||'C');
  for(const id of ['dictionary-search','dictionary-root','dictionary-quality'])$('#'+id).addEventListener(id==='dictionary-search'?'input':'change',renderDictionaryResults);
  $('#dictionary-prefer').onclick=()=>{
    const shape=dictionaryPositions[dictionaryPosition]?.frets;
    if(!shape||!GuitarVoicings.prefer(dictionarySelection,shape))return;
    try{const saved=JSON.parse(localStorage.getItem('faixalabVoicings')||'{}');saved[dictionarySelection]=shape;localStorage.setItem('faixalabVoicings',JSON.stringify(saved));}catch{}
    if(state.project){renderForms(Math.max(0,state.activeChord));renderStudy();renderStudioPanels();}
    toast('Posição escolhida para os diagramas deste acorde.');
  };
  $('#dictionary-apply').onclick=()=>applyDictionaryChord(dictionarySelection);
  $('#dictionary-silence').onclick=()=>applyDictionaryChord('N');
  $('#generate-solo-tab').onclick=()=>generateSoloTab();
  $('#cancel-solo-tab').onclick=async()=>{if(state.project)await api(`/api/projects/${state.project.id}/solo-tabs/cancel`,{method:'POST'});};
  $('#download-solo-tab').onclick=()=>download('solo-tab');$('#download-solo-midi').onclick=()=>download('solo-midi');
  $('#tab-split-guitars').onclick=()=>$('#split-guitars').click();
  const preview=()=>{$('#solo-note-name').textContent=pitchName(guitarTuning[+$('#solo-note-string').value]+ +$('#solo-note-fret').value);};
  $('#solo-note-string').onchange=preview;$('#solo-note-fret').oninput=preview;
  $('#solo-note-form').onsubmit=async event=>{
    event.preventDefault();if(!state.project)return;
    const body={index:tabEditIndex,start:+$('#solo-note-start').value,end:+$('#solo-note-end').value,string:+$('#solo-note-string').value,fret:+$('#solo-note-fret').value};
    try{const p=await api(`/api/projects/${state.project.id}/solo-tabs`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});$('#solo-note-dialog').close();renderProject(p,true);toast('Nota corrigida.');}catch(error){toast(error.message);}
  };
  $('#delete-solo-note').onclick=async()=>{
    if(!state.project||tabEditIndex<0)return;
    try{const p=await api(`/api/projects/${state.project.id}/solo-tabs/${tabEditIndex}`,{method:'DELETE'});$('#solo-note-dialog').close();renderProject(p,true);toast('Nota removida.');}catch(error){toast(error.message);}
  };
}
initializeLearning();


// Native media controls route the backing track through the phone's audio output.
function updateMediaPosition(){
  if(!navigator.mediaSession?.setPositionState || !(player.duration>0))return;
  try{navigator.mediaSession.setPositionState({duration:player.duration,playbackRate:player.speed||1,position:Math.min(player.duration,Math.max(0,player.current()))});}catch{}
}
function updateMediaSession(){
  if(!navigator.mediaSession)return;
  const p=state.project;
  if(p&&typeof MediaMetadata!=='undefined')navigator.mediaSession.metadata=new MediaMetadata({title:p.title||'Backing track',artist:'FaixaLab',album:player.backingMode?'Sua backing track':'Estúdio de estudo'});
  navigator.mediaSession.playbackState=player.wantsPlay?'playing':'paused';
  updateMediaPosition();
}
function initializeBacking(){
  const button=document.createElement('button');button.id='open-backing';button.className='button secondary';button.textContent='Backing track';
  $('#transport-export').append(button);
  button.onclick=()=>{if(!state.project?.stems?.length){toast('Abra uma música com faixas separadas.');return;}$('#play-backing').hidden=true;$('#download-backing').hidden=true;$('#backing-stage').textContent='';$('#backing-dialog').showModal();};
  const playButton=document.createElement('button');playButton.id='play-backing';playButton.className='button primary wide';playButton.textContent='Ouvir backing track';playButton.hidden=true;
  $('#download-backing').before(playButton);
  playButton.onclick=()=>{$('#backing-dialog').close();player.play().catch(error=>toast(error.message));};
  $('#make-backing').textContent='Preparar backing track';
  $('#make-backing').onclick=async()=>{
    if(!state.project)return;
    const p=state.project,preset=$('#backing-preset').value;
    const solo=player.tracks.some(t=>t.solo);
    const gains=Object.fromEntries(player.tracks.map(t=>[t.name,t.mute||solo&&!t.solo?0:t.gain]));
    $('#make-backing').disabled=true;playButton.hidden=true;
    $('#backing-stage').textContent='Preparando sua mixagem…';
    try{
      const result=await api(`/api/projects/${p.id}/backing`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preset,gains:preset==='mixer'?gains:null})});
      if(state.project?.id!==p.id)return;
      await player.useBacking(result.url);
      $('#download-backing').href=mediaUrl(result.url+'?download=true');$('#download-backing').hidden=false;
      $('#backing-stage').textContent='Pronta. Toque em Ouvir ou baixe para usar sem conexão.';
      playButton.hidden=false;
    }catch(error){$('#backing-stage').textContent=error.message;}
    finally{$('#make-backing').disabled=false;}
  };
  if(navigator.mediaSession){
    const handlers={play:()=>player.play(),pause:()=>player.pause(),seekbackward:e=>player.seek(player.current()-(e.seekOffset||10)),seekforward:e=>player.seek(player.current()+(e.seekOffset||10)),seekto:e=>player.seek(e.seekTime||0),stop:()=>{player.pause();player.seek(0);}};
    for(const [name,handler] of Object.entries(handlers))try{navigator.mediaSession.setActionHandler(name,handler);}catch{}
  }
  if(!window.faixaLabPages&&'serviceWorker' in navigator&&window.isSecureContext)navigator.serviceWorker.register('/service-worker.js').catch(()=>{});
}
initializeBacking();

$('#apply-audio-update').onclick=()=>player.applyPending();
