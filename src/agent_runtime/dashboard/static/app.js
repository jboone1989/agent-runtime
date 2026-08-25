"use strict";
// ===== 常量：子系统与连线（与 digest.py 的 SUBSYSTEMS 一致） =====
const SUBS = [
  ["认知",       "#ffb347", -0.75, 0.70, 0.55, 0.38],
  ["行动",       "#ff5f56",  0.75, 0.50, 0.65, 0.36],
  ["感知",       "#ab7bff",  0.40, 0.25,-1.05, 0.36],
  ["记忆",       "#66d9a0", -1.05,-0.25, 0.45, 0.34],
  ["学习",       "#4dd0e1",  1.05,-0.35, 0.25, 0.34],
  ["情感/社会",  "#ff6b9d", -0.30, 0.10, 0.50, 0.42],
  ["动机/存活",  "#ffd166",  0.00,-1.05,-0.45, 0.36],
  ["自我模型",   "#b0b7ff", -0.45, 1.05,-0.10, 0.34],
  ["监管",       "#7fd4ff",  0.50, 1.05,-0.20, 0.34],
];
const LINKS = {0:[1,3,7], 1:[2,8], 2:[0,5], 3:[4,7], 4:[7,8], 5:[0,8], 6:[8,0], 7:[8,0], 8:[1,2,5]};
const SUBS_COLOR = SUBS.map(s => new THREE.Color(s[1]));
const ENVELOPE = [
  [ 0.62, 0.35, 0.15, 1.05, 1.15, 1.05], [-0.62, 0.35, 0.15, 1.05, 1.15, 1.05],
  [ 0.95,-0.05, 0.55, 0.62, 0.62, 0.75], [-0.95,-0.05, 0.55, 0.62, 0.62, 0.75],
  [ 0.35, 0.30,-1.15, 0.62, 0.60, 0.55], [-0.35, 0.30,-1.15, 0.62, 0.60, 0.55],
  [ 0.00,-0.62,-1.05, 0.85, 0.45, 0.48], [ 0.00,-1.30,-0.25, 0.26, 0.55, 0.32],
];

// ===== 3D 大脑 =====
const canvas = document.getElementById("brain");
const renderer = new THREE.WebGLRenderer({canvas, antialias:true});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
const scene = new THREE.Scene();
scene.add(new THREE.AmbientLight(0x99aacc, 1.5));
const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 80);
camera.position.set(0, 0.3, 6.2);
const orbit = new THREE.Group(); scene.add(orbit);
const brain = new THREE.Group(); orbit.add(brain);

function shellPos(env, N, b0, b1) {
  const pos = new Float32Array(N*3);
  const [cx,cy,cz,rx,ry,rz] = env;
  for (let i=0;i<N;i++){
    const th = Math.random()*Math.PI*2, ph = Math.acos(2*Math.random()-1);
    const s = b0+(b1-b0)*Math.random();
    pos[i*3]=cx+rx*s*Math.sin(ph)*Math.cos(th);
    pos[i*3+1]=cy+ry*s*Math.sin(ph)*Math.sin(th);
    pos[i*3+2]=cz+rz*s*Math.cos(ph);
  }
  return pos;
}
function glowTexture(hex) {
  const cv = document.createElement("canvas"); cv.width = cv.height = 128;
  const g = cv.getContext("2d");
  const grad = g.createRadialGradient(64,64,0,64,64,64);
  grad.addColorStop(0, hex); grad.addColorStop(0.25, hex+"88"); grad.addColorStop(1, hex+"00");
  g.fillStyle = grad; g.fillRect(0,0,128,128);
  return new THREE.CanvasTexture(cv);
}
// 星空
{
  const pos = new Float32Array(500*3);
  for (let i=0;i<500;i++){ pos[i*3]=(Math.random()-0.5)*30; pos[i*3+1]=(Math.random()-0.5)*20; pos[i*3+2]=-8-Math.random()*12; }
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos,3));
  scene.add(new THREE.Points(g, new THREE.PointsMaterial({color:0x8fa0c0, size:0.02, transparent:true, opacity:0.7})));
}
// 脑组织基底
{
  const counts=[560,560,240,240,260,260,320,170];
  let total=0; counts.forEach(n=>total+=n*3);
  const merged=new Float32Array(total); let off=0;
  ENVELOPE.forEach((env,i)=>{ const p=shellPos(env,counts[i],0.90,1.0); merged.set(p,off); off+=p.length; });
  const g=new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(merged,3));
  brain.add(new THREE.Points(g, new THREE.PointsMaterial({color:0x5f8fd8, size:0.04, transparent:true, opacity:0.32, blending:THREE.AdditiveBlending, depthWrite:false})));
}
// 子系统星团 + 光晕
const subGroups = SUBS.map(s => {
  const pos = shellPos([s[2],s[3],s[4],s[5],s[5]*0.9,s[5]*0.95], 260, 0, 1);
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos,3));
  const m = new THREE.PointsMaterial({color:s[1], size:0.07, transparent:true, opacity:0.92, blending:THREE.AdditiveBlending, depthWrite:false});
  m._base=0.92;
  const pts = new THREE.Points(g,m); brain.add(pts);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({map:glowTexture(s[1]), transparent:true, opacity:0.35, blending:THREE.AdditiveBlending, depthWrite:false}));
  halo.scale.setScalar(s[5]*4.2); halo.position.set(s[2],s[3],s[4]);
  halo.material._base=0.35;
  brain.add(halo);
  return {points:pts, material:m, halo, level:0};
});
// 扫描环
const rings = [];
[[1.95,0.9,0.2],[1.95,-0.4,0.5],[1.6,1.0,-0.6]].forEach(([r,ix,iz])=>{
  const ring = new THREE.Mesh(new THREE.TorusGeometry(r,0.006,8,120),
    new THREE.MeshBasicMaterial({color:0x4d7fd8, transparent:true, opacity:0.4, blending:THREE.AdditiveBlending}));
  ring.rotation.x = Math.PI/2*ix; ring.rotation.z = Math.PI/2*iz;
  orbit.add(ring); rings.push(ring);
});
// 信息流
const FLOW_N=900;
const flow={t:new Float32Array(FLOW_N),dur:new Float32Array(FLOW_N),from:new Int32Array(FLOW_N),to:new Int32Array(FLOW_N),mid:new Float32Array(FLOW_N*3)};
for(let i=0;i<FLOW_N;i++)flow.t[i]=-Math.random();
const fGeo=new THREE.BufferGeometry();
const fPos=new Float32Array(FLOW_N*3),fCol=new Float32Array(FLOW_N*3);
fGeo.setAttribute("position",new THREE.BufferAttribute(fPos,3));
fGeo.setAttribute("color",new THREE.BufferAttribute(fCol,3));
const flowPts = new THREE.Points(fGeo, new THREE.PointsMaterial({size:0.06,vertexColors:true,transparent:true,opacity:1,blending:THREE.AdditiveBlending,depthWrite:false}));
scene.add(flowPts);
const centers = SUBS.map(s => new THREE.Vector3(s[2],s[3],s[4]));
const WHITE = new THREE.Color(0xffffff);
let acc = 0;

function emitFlow(fromIdx, count) {
  const targets = LINKS[fromIdx] || [];
  if (!targets.length) return;
  for (let n=0;n<count;n++){
    const idx = flow.t.findIndex(v=>v<0);
    if (idx<0) return;
    flow.from[idx]=fromIdx; flow.to[idx]=targets[Math.floor(Math.random()*targets.length)];
    const a=centers[fromIdx], b=centers[flow.to[idx]];
    flow.mid[idx*3]=(a.x+b.x)/2; flow.mid[idx*3+1]=(a.y+b.y)/2+0.9; flow.mid[idx*3+2]=(a.z+b.z)/2+0.2;
    flow.t[idx]=0; flow.dur[idx]=0.8+Math.random()*0.7;
  }
}
function setActivation(levels) {
  SUBS.forEach((s, i) => { subGroups[i].level = Math.max(0, Math.min(1, levels[s[0]] || 0)); });
}
// 拖动
let dragging=false, px=0, py=0, velX=0, velY=0, rotX=0, rotY=0;
canvas.addEventListener("pointerdown", e=>{dragging=true;px=e.clientX;py=e.clientY;velX=velY=0;canvas.classList.add("dragging");canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener("pointermove", e=>{if(!dragging)return;const dx=e.clientX-px,dy=e.clientY-py;velX=dx*0.005;velY=dy*0.005;rotY+=velX;rotX+=velY;px=e.clientX;py=e.clientY;});
canvas.addEventListener("pointerup", ()=>{dragging=false;canvas.classList.remove("dragging");});
canvas.addEventListener("wheel", e=>{e.preventDefault();camera.position.z=Math.min(11,Math.max(3.4,camera.position.z+e.deltaY*0.005));},{passive:false});
canvas.addEventListener("dblclick", ()=>{rotX=rotY=0;camera.position.z=6.2;});
function resize(){ renderer.setSize(canvas.clientWidth, canvas.clientHeight, false); camera.aspect=canvas.clientWidth/canvas.clientHeight; camera.updateProjectionMatrix(); }
window.addEventListener("resize", resize); resize();
function bez(p0,p1,p2,t){const u=1-t;return[u*u*p0[0]+2*u*t*p1[0]+t*t*p2[0],u*u*p0[1]+2*u*t*p1[1]+t*t*p2[1],u*u*p0[2]+2*u*t*p1[2]+t*t*p2[2]];}
function tick(){
  requestAnimationFrame(tick);
  const t=performance.now()/1000;
  if(!dragging){rotY+=0.0016;velX*=0.94;velY*=0.94;}else{velX*=0.85;velY*=0.85;}
  rotY+=velX; rotX+=velY;
  orbit.rotation.y=rotY; orbit.rotation.x=rotX;
  brain.scale.setScalar(1+0.01*Math.sin(t*0.8));
  subGroups.forEach((sg,i)=>{
    const level=sg.level;
    sg.material.opacity=sg.material._base*(0.3+0.7*level);
    sg.material.size=0.06+0.04*level;
    sg.halo.material.opacity=sg.halo.material._base*(0.25+0.75*level);
    sg.halo.scale.setScalar(SUBS[i][5]*4.2*(1+0.25*level));
  });
  rings.forEach((r,i)=>{r.rotation.z+=0.003*(i%2?1:-1);r.rotation.x+=0.0015*(i%2?-1:1);});
  acc += 1.5;
  while(acc>=1){
    acc-=1;
    const act = subGroups.reduce((best,sg,i)=> sg.level>subGroups[best].level? i:best, 0);
    if (subGroups[act].level > 0.05) emitFlow(act, 3);
  }
  for(let i=0;i<FLOW_N;i++){
    if(flow.t[i]<0){fCol[i*3]=fCol[i*3+1]=fCol[i*3+2]=0;fPos[i*3]=99;continue;}
    flow.t[i]+=0.016/flow.dur[i];
    if(flow.t[i]>=1){flow.t[i]=-1;fCol[i*3]=fCol[i*3+1]=fCol[i*3+2]=0;continue;}
    const a=centers[flow.from[i]],b=centers[flow.to[i]];
    const p=bez([a.x,a.y,a.z],[flow.mid[i*3],flow.mid[i*3+1],flow.mid[i*3+2]],[b.x,b.y,b.z],flow.t[i]);
    fPos[i*3]=p[0];fPos[i*3+1]=p[1];fPos[i*3+2]=p[2];
    const c=SUBS_COLOR[flow.from[i]].clone().lerp(WHITE,0.3);
    const fade=Math.sin(Math.PI*Math.min(1,flow.t[i]));
    fCol[i*3]=c.r*fade;fCol[i*3+1]=c.g*fade;fCol[i*3+2]=c.b*fade;
  }
  fGeo.attributes.position.needsUpdate=true;
  fGeo.attributes.color.needsUpdate=true;
  renderer.render(scene,camera);
}
tick();

// ===== 图例 =====
{
  const lg = document.getElementById("legend");
  SUBS.forEach(s => {
    const el = document.createElement("span");
    el.innerHTML = '<i style="background:'+s[1]+';color:'+s[1]+'"></i>'+s[0];
    lg.appendChild(el);
  });
}

// ===== 思考流（只显示"思考过程"，技术事件仅驱动大脑） =====
const FILTERS = ["全部","反思","对话","小结","动作"];
const CHIP_TYPES = {"反思":["thought"],"对话":["dialogue"],"小结":["reflection"],"动作":["action"]};
const DISPLAY_TYPES = ["thought","dialogue","reflection","action"];
const KIND_ZH = {noop:"空闲",introspect:"自省",diagnose:"诊断",maintain:"维护",record:"记录",observe:"观察",conserve:"节能",contain:"遏制",restore_config:"恢复配置",switch_provider:"切换供应商",disable_capability:"禁用能力",probe_provider:"探测供应商"};
const stream = document.getElementById("stream");
const known = new Set();
let filter = "全部";

function relTime(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (isNaN(t)) return "";
  const diff = Date.now() - t;
  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return Math.floor(diff/60000) + " 分钟前";
  if (diff < 86400000) return Math.floor(diff/3600000) + " 小时前";
  return Math.floor(diff/86400000) + " 天前";
}
function parseSummary(text) {
  const m = (re) => { const x = String(text).match(re); return x ? parseInt(x[1], 10) : null; };
  const done = m(/actuator_completed=(\d+)/), failed = m(/failed=(\d+)/),
        unverifiable = m(/evidence_unverifiable=(\d+)/);
  if (done === null) return null;
  if (done === 0 && (failed === null || failed === 0)) return "平静的一轮：没有动作，没有失败。";
  let s = "完成 " + done + " 个动作" + (failed ? "，失败 " + failed + " 个" : "");
  if (unverifiable) s += "，其中 " + unverifiable + " 个结果证据不可核验";
  return s + "。";
}
function renderEvent(e) {
  if (known.has(e.id)) return;
  known.add(e.id);
  if (!DISPLAY_TYPES.includes(e.type)) return; // 技术事件只驱动大脑，不展示
  const div = document.createElement("div");
  div.className = "event flash " + e.type;
  div.dataset.types = e.type;
  const time = relTime(e.ts);
  let head = "", body = "";
  if (e.type === "thought") {
    head = "🧠 反思";
    body = String(e.text);
  } else if (e.type === "dialogue") {
    const idx = String(e.text).indexOf(": ");
    const partner = idx > 0 ? String(e.text).slice(0, idx) : "对话";
    head = "💬 与 " + partner + " 的对话";
    body = idx > 0 ? String(e.text).slice(idx + 2) : String(e.text);
  } else if (e.type === "reflection") {
    head = "📋 小结";
    body = parseSummary(e.text) || String(e.text);
  } else { // action
    const parts = String(e.text).split(" — ");
    const kindRaw = parts[0] ? parts[0].split(" ")[0] : "";
    const kindZh = KIND_ZH[kindRaw] || kindRaw;
    const reason = parts[1] ? parts[1].split(" → ")[0] : "";
    head = "⚙️ 动作：" + kindZh;
    body = reason;
  }
  div.innerHTML = '<div class="meta">' + head +
    (time ? '<span class="time">' + time + '</span>' : "") +
    '</div><div class="body">' + escapeHtml(body) + '</div>';
  stream.prepend(div);
  while (stream.children.length > 240) stream.removeChild(stream.lastChild);
  if (document.getElementById("autoscroll").checked) stream.scrollTop = 0;
  setTimeout(()=>div.classList.remove("flash"), 1200);
}
function escapeHtml(s) { const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }
function applyFilter() {
  const types = CHIP_TYPES[filter] || null;
  [...stream.children].forEach(div => {
    div.style.display = (!types || types.includes(div.dataset.types)) ? "" : "none";
  });
}
{
  const chips = document.getElementById("chips");
  FILTERS.forEach(name => {
    const c = document.createElement("span");
    c.className = "chip" + (name==="全部" ? " active" : "");
    c.textContent = name;
    c.onclick = () => {
      filter = name;
      [...chips.children].forEach(x => x.classList.toggle("active", x.textContent === name));
      applyFilter();
    };
    chips.appendChild(c);
  });
}

// ===== 轮询 =====
let lastSeq = 0;
async function poll() {
  try {
    const resp = await fetch("/api/digest?since=" + lastSeq, {cache: "no-store"});
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const d = await resp.json();
    document.getElementById("reconnect").hidden = true;
    const dot = document.getElementById("hud-dot");
    const statusEl = document.getElementById("hud-status");
    if (d.consecutive_errors > 0) {
      dot.className = "hud-dot error";
      statusEl.textContent = "异常 ×" + d.consecutive_errors + " · 第 " + d.cycle + " 周期";
    } else if (d.status === "RUNNING") {
      dot.className = "hud-dot running";
      statusEl.textContent = "运行中 · 第 " + d.cycle + " 周期";
    } else if (d.status === "STOPPED") {
      dot.className = "hud-dot stopped";
      statusEl.textContent = "已停止 · 最后第 " + d.cycle + " 周期";
    } else {
      dot.className = "hud-dot";
      statusEl.textContent = "未发现状态";
    }
    setActivation(d.activations);
    d.events.forEach(renderEvent);
    if (d.events.length) applyFilter();
    lastSeq = Math.max(lastSeq, d.seq);
  } catch (err) {
    document.getElementById("reconnect").hidden = false;
  }
}
poll();
setInterval(poll, 1500);
