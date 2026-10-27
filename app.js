const $ = id => document.getElementById(id);
const COLORS = {cpu:"#c7f36b",gpu:"#77c6e8",grid:"#2a3131",text:"#758079"};
let lastData = null, alertLatch = {cpu:false,gpu:false}, toastTimer;
const fmt = (v, digits=1) => v === null || v === undefined || !Number.isFinite(Number(v)) ? "N/A" : Number(v).toFixed(digits);
function tempClass(v, threshold) { return v == null ? "tag" : v >= threshold ? "tag hot" : "tag"; }
function showToast(msg) { $("toast").textContent=msg; $("toast").classList.add("show"); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$("toast").classList.remove("show"),6500); }
function checkAlert(kind, value, threshold) {
  if (!$("alerts-enabled").checked || value == null) { alertLatch[kind]=false; return; }
  if (value >= threshold && !alertLatch[kind]) { showToast(`${kind.toUpperCase()} TEMPERATURE WARNING: ${value.toFixed(1)} °C (threshold ${threshold} °C)`); alertLatch[kind]=true; }
  if (value < threshold-3) alertLatch[kind]=false;
}
function setTemp(id, value) { $(id).textContent = value == null ? "N/A" : Number(value).toFixed(1); }
function meter(id, value, max=100) { $(id).style.width = value == null ? "0%" : `${Math.max(0,Math.min(100,Number(value)/max*100))}%`; }
function setTag(id, value, threshold) {
  const el=$(id); el.classList.toggle("hot",value!=null&&value>=threshold);
  el.textContent=value==null?"N/A":value>=threshold?"HOT":"NORMAL";
}
function renderSensors(data) {
  const sensors=data.sensors||[]; $("sensor-count").textContent=String(sensors.length).padStart(2,"0");
  $("sensors").innerHTML=sensors.length?sensors.map(s=>`<div class="sensor-row"><div><div class="sensor-name">${escapeHtml(s.name)}</div><div class="sensor-source">${escapeHtml(s.source||"kernel hwmon")}</div></div><div class="sensor-value">${fmt(s.temperature)}<small>°C</small></div></div>`).join(""):'<div class="empty">No temperature sensors exposed. Check kernel modules and lm-sensors.</div>';
  const fans=data.fans||[]; $("fan-list-count").textContent=String(fans.length).padStart(2,"0");
  $("fans").innerHTML=fans.length?fans.map(f=>`<div class="sensor-row"><div><div class="sensor-name">${escapeHtml(f.name)}</div><div class="sensor-source">TACHOMETER</div></div><div class="sensor-value">${fmt(f.rpm,0)}<small>RPM</small></div></div>`).join(""):'<div class="empty">No fan tachometers exposed. Some controllers do not publish fan RPM to Linux.</div>';
  const drives=data.storage||[]; $("drive-count").textContent=String(drives.length).padStart(2,"0");
  $("storage").innerHTML=drives.length?drives.map(d=>`<div class="drive-card"><div class="drive-name">${escapeHtml(d.name)}</div><div class="drive-meta">${escapeHtml(d.type)} / ${escapeHtml(d.source)}</div><div class="drive-temp">${fmt(d.temperature)}<small> °C</small></div></div>`).join(""):'<div class="empty">No storage temperature readings detected. Install smartmontools / nvme-cli and check device permissions.</div>';
  const caps=data.capabilities||{}; const labels={lm_sensors:"lm-sensors",rocm_smi:"ROCm SMI",smartctl:"smartmontools",nvme_cli:"nvme-cli"};
  $("capabilities").innerHTML=Object.entries(labels).map(([key,label])=>`<div class="capability"><span>${label}</span><strong class="${caps[key]?'ok':'no'}">${caps[key]?'AVAILABLE':'NOT FOUND'}</strong></div>`).join("");
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function renderChart(history) {
  const canvas=$("chart"), rect=canvas.getBoundingClientRect(), dpr=window.devicePixelRatio||1;
  canvas.width=Math.max(1,Math.floor(rect.width*dpr)); canvas.height=Math.max(1,Math.floor(rect.height*dpr));
  const ctx=canvas.getContext("2d");ctx.scale(dpr,dpr);
  const w=rect.width,h=rect.height, pad={l:38,r:12,t:12,b:22}, cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
  ctx.clearRect(0,0,w,h);
  const range=Number($("range").value), now=Date.now()/1000, cutoff=now-range;
  const points=(history||[]).filter(p=>p.timestamp>=cutoff);
  const all=points.flatMap(p=>[p.cpu_temperature,p.gpu_temperature]).filter(v=>v!=null);
  let min=Math.min(20,...all)-3,max=Math.max(80,...all)+3;
  if(max-min<20){max=min+20}
  const x=t=>pad.l+(t-cutoff)/range*cw, y=v=>pad.t+(max-v)/(max-min)*ch;
  ctx.font="9px system-ui";ctx.strokeStyle=COLORS.grid;ctx.fillStyle=COLORS.text;ctx.lineWidth=1;
  for(let i=0;i<=4;i++){let v=min+(max-min)*i/4, yy=y(v);ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();ctx.fillText(`${v.toFixed(0)}°`,2,yy+3)}
  function line(key,color){ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=2;let started=false;for(const p of points){const v=p[key];if(v==null)continue;const xx=x(p.timestamp),yy=y(v);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy)}if(started)ctx.stroke();}
  line("cpu_temperature",COLORS.cpu);line("gpu_temperature",COLORS.gpu);
  $("chart-left").textContent=`NOW - ${range>=3600?(range/3600)+" HOUR":range/60+" MIN"}`;
}
function render(data) {
  lastData=data;
  $("status").textContent="LIVE";$("status").style.color="var(--accent)";
  $("hostname").textContent=(data.system.hostname||"LOCAL HOST").toUpperCase();
  $("updated").textContent=new Date(data.timestamp*1000).toLocaleTimeString();
  $("interval").textContent=`POLL / ${data.system.interval_seconds} SEC`;
  const cpu=data.cpu||{}, cpuT=cpu.temperature, cpuU=cpu.utilization_percent;
  setTemp("cpu-temp",cpuT);$("cpu-util").textContent=cpuU==null?"N/A":`${fmt(cpuU)}%`;meter("cpu-meter",cpuT,110);setTag("cpu-state",cpuT,Number($("cpu-threshold").value));$("cpu-note").textContent=cpuT==null?"NOT EXPOSED":"KERNEL SENSOR";
  const gpu=(data.gpus||[])[0], gpuT=gpu&&gpu.temperatures.length?Math.max(...gpu.temperatures.map(t=>t.temperature)):null;
  setTemp("gpu-temp",gpuT);$("gpu-name").textContent=gpu?gpu.name:"NO AMD GPU FOUND";$("gpu-power").textContent=gpu&&gpu.power_w!=null?`${fmt(gpu.power_w)} W`:"N/A";$("gpu-util").textContent=gpu&&gpu.utilization_percent!=null?`UTIL ${gpu.utilization_percent}%`:"UTIL N/A";meter("gpu-meter",gpuT,110);setTag("gpu-state",gpuT,Number($("gpu-threshold").value));
  const cpuW=data.power.cpu_w;$("cpu-power").textContent=fmt(cpuW);$("power-source").textContent=cpuW==null?"NOT EXPOSED":"LIVE / ESTIMATE";meter("power-meter",cpuW,200);
  const fans=data.fans||[];$("fan-count").textContent=fans.length;$("fan-max").textContent=fans.length?`${Math.max(...fans.map(f=>f.rpm))} RPM`:"N/A";$("fan-state").textContent=fans.length?"DETECTED":"NOT EXPOSED";meter("fan-meter",fans.length?Math.max(...fans.map(f=>f.rpm)):null,2500);
  checkAlert("cpu",cpuT,Number($("cpu-threshold").value));checkAlert("gpu",gpuT,Number($("gpu-threshold").value));
  renderSensors(data);renderChart(data.history||[]);
}
async function poll(){try{const r=await fetch("/api/metrics",{cache:"no-store"});if(!r.ok)throw new Error("HTTP "+r.status);render(await r.json())}catch(e){$("status").textContent="DISCONNECTED";$("status").style.color="var(--red)";console.error(e)}}
$("settings-open").addEventListener("click",()=>$("settings").showModal());
$("settings-form").addEventListener("submit",e=>{if(e.submitter&&e.submitter.value==="save"){e.preventDefault();$("settings").close();if(lastData)render(lastData);showToast("Alert thresholds updated for this browser session.")}});
$("range").addEventListener("change",()=>{if(lastData)renderChart(lastData.history||[])});
window.addEventListener("resize",()=>{if(lastData)renderChart(lastData.history||[])});
poll();setInterval(poll,2000);
