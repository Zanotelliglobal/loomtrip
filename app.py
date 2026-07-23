#!/usr/bin/env python3
"""
Loomtrip Admin Dashboard
=========================
Local web server — upload a PDF or paste an itinerary → get a branded demo app.

Usage:
  python app.py
  Open http://localhost:5099

Requires: Flask, PyMuPDF, anthropic (all in deps/)
"""

import sys, os, json, re, subprocess, threading, time, shutil, uuid
from pathlib import Path

_deps = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deps")
if os.path.isdir(_deps) and _deps not in sys.path:
    sys.path.insert(0, _deps)

from flask import Flask, request, jsonify, send_from_directory, render_template_string, Response
import anthropic, httpx

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload limit

BASE_DIR   = Path(__file__).parent
BUILDS_DIR = BASE_DIR / "builds"
BUILDS_DIR.mkdir(exist_ok=True)

# ─── in-memory job store ─────────────────────────────────────────────────────
jobs = {}  # job_id → {status, log, output_dir}

# ─── HTML template ───────────────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loomtrip — Admin</title>
<style>
:root {
  --brand: #1C1917;
  --accent: #8B4513;
  --bg: #FAF8F5;
  --card: #fff;
  --border: rgba(28,25,23,0.1);
  --text: #1C1917;
  --muted: #78716C;
  --success: #276749;
  --danger: #C0392B;
  --serif: Georgia,"Times New Roman",serif;
  --sans: -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif;
  --radius: 14px;
  --shadow: 0 2px 12px rgba(28,25,23,0.07);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: var(--sans); background: var(--bg); color: var(--text); font-size: 15px; line-height: 1.5; -webkit-font-smoothing: antialiased; }
.top-bar { background: var(--brand); color: #fff; padding: 0 32px; height: 56px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
.top-bar-brand { font-family: var(--serif); font-size: 17px; font-weight: 400; letter-spacing: 0.2px; }
.top-bar-sub { font-size: 12px; opacity: 0.55; letter-spacing: 0.4px; text-transform: uppercase; }
.layout { display: grid; grid-template-columns: 380px 1fr; gap: 0; min-height: calc(100vh - 56px); }
.sidebar { border-right: 1px solid var(--border); padding: 28px 24px; }
.main { padding: 28px 32px; }
h2 { font-family: var(--serif); font-size: 20px; font-weight: 400; margin-bottom: 20px; }
h3 { font-family: var(--serif); font-size: 16px; font-weight: 400; margin-bottom: 12px; color: var(--muted); }
.section { margin-bottom: 28px; }
label { display: block; font-size: 12px; font-weight: 600; letter-spacing: 0.4px; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }
input[type=text], input[type=color], select, textarea {
  width: 100%; padding: 10px 13px;
  border: 1px solid var(--border); border-radius: 9px;
  font-family: var(--sans); font-size: 14px; color: var(--text);
  background: var(--card); outline: none;
  transition: border-color 0.15s;
}
input[type=text]:focus, select:focus, textarea:focus { border-color: var(--accent); }
textarea { resize: vertical; min-height: 140px; }
.color-row { display: flex; gap: 8px; align-items: center; }
.color-row input[type=color] { width: 44px; height: 38px; padding: 2px; cursor: pointer; border-radius: 8px; }
.color-row input[type=text] { flex: 1; }
.drop-zone {
  border: 2px dashed var(--border); border-radius: var(--radius);
  padding: 28px 20px; text-align: center; cursor: pointer;
  transition: all 0.2s; background: var(--card);
  margin-bottom: 14px;
}
.drop-zone:hover, .drop-zone.over { border-color: var(--accent); background: rgba(139,69,19,0.04); }
.drop-zone-icon { font-size: 32px; margin-bottom: 8px; }
.drop-zone-text { font-size: 13px; color: var(--muted); }
.drop-zone-text strong { color: var(--accent); cursor: pointer; }
#pdf-input { display: none; }
.pill { display: inline-flex; align-items: center; gap: 6px; background: rgba(139,69,19,0.1); color: var(--accent); font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 20px; }
.pill-remove { cursor: pointer; opacity: 0.6; font-size: 14px; }
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 10px 20px; border-radius: 10px; font-size: 14px; font-weight: 600; border: none; cursor: pointer; transition: all 0.15s; font-family: var(--sans); }
.btn-primary { background: var(--accent); color: #fff; width: 100%; justify-content: center; font-size: 15px; padding: 13px; }
.btn-primary:hover { filter: brightness(1.1); }
.btn-primary:active { transform: scale(0.97); }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { background: var(--card); color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--accent); color: var(--accent); }
.divider { display: flex; align-items: center; gap: 10px; margin: 14px 0; }
.divider-line { flex: 1; height: 1px; background: var(--border); }
.divider-text { font-size: 11px; color: var(--muted); letter-spacing: 0.3px; text-transform: uppercase; }
/* Job log */
.log-box { background: #0C0A09; color: #D6D3D1; border-radius: var(--radius); padding: 16px 18px; font-family: "SF Mono","Fira Code",monospace; font-size: 12px; line-height: 1.7; min-height: 200px; max-height: 380px; overflow-y: auto; }
.log-line { display: block; }
.log-ok  { color: #34D399; }
.log-err { color: #F87171; }
.log-info { color: #FCD34D; }
/* Builds list */
.build-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; margin-bottom: 12px; display: flex; align-items: center; gap: 16px; box-shadow: var(--shadow); }
.build-card-icon { width: 44px; height: 44px; border-radius: 11px; background: rgba(139,69,19,0.1); display: flex; align-items: center; justify-content: center; font-size: 22px; flex-shrink: 0; }
.build-card-info { flex: 1; min-width: 0; }
.build-card-name { font-family: var(--serif); font-size: 15px; font-weight: 400; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.build-card-meta { font-size: 11px; color: var(--muted); letter-spacing: 0.3px; text-transform: uppercase; margin-top: 2px; }
.build-card-actions { display: flex; gap: 8px; flex-shrink: 0; }
.badge { display: inline-block; font-size: 10px; font-weight: 600; letter-spacing: 0.3px; padding: 3px 8px; border-radius: 20px; }
.badge-ok { background: rgba(39,103,73,0.1); color: var(--success); }
.badge-running { background: rgba(139,69,19,0.1); color: var(--accent); }
.badge-error { background: rgba(192,57,43,0.1); color: var(--danger); }
.empty-state { text-align: center; padding: 60px 20px; color: var(--muted); }
.empty-icon { font-size: 40px; margin-bottom: 12px; opacity: 0.4; }
.progress-bar { height: 3px; background: var(--border); border-radius: 2px; overflow: hidden; margin-top: 8px; }
.progress-fill { height: 100%; background: var(--accent); border-radius: 2px; width: 0%; transition: width 0.4s; }
.color-swatch { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.swatch { width: 28px; height: 28px; border-radius: 7px; cursor: pointer; border: 2px solid transparent; transition: transform 0.15s; }
.swatch:hover { transform: scale(1.15); }
.swatch.active { border-color: var(--text); }
@media(max-width:768px) { .layout { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="top-bar">
  <div class="top-bar-brand">Loomtrip</div>
  <div class="top-bar-sub">Demo Generator</div>
</div>

<div class="layout">
  <!-- SIDEBAR — INPUT FORM -->
  <aside class="sidebar">
    <h2>New Demo</h2>

    <!-- PDF Upload -->
    <div class="section">
      <label>Upload Itinerary PDF</label>
      <div class="drop-zone" id="drop-zone" onclick="document.getElementById('pdf-input').click()">
        <div class="drop-zone-icon">📄</div>
        <div class="drop-zone-text">Drop PDF here or <strong>browse</strong></div>
      </div>
      <input type="file" id="pdf-input" accept=".pdf" onchange="handleFile(this.files[0])">
      <div id="pdf-pill" style="display:none;margin-bottom:6px;"></div>
    </div>

    <div class="divider"><div class="divider-line"></div><div class="divider-text">or paste text</div><div class="divider-line"></div></div>

    <!-- Text paste -->
    <div class="section">
      <label>Itinerary Text</label>
      <textarea id="itin-text" placeholder="Paste the DMC's itinerary here — any format works..."></textarea>
    </div>

    <!-- Brand options -->
    <div class="section">
      <label>Brand Colour</label>
      <div class="color-row">
        <input type="color" id="color-picker" value="#8B4513" oninput="syncColor(this.value)">
        <input type="text" id="color-hex" value="#8B4513" placeholder="#8B4513" oninput="syncColorFromText(this.value)" maxlength="7">
      </div>
      <div class="color-swatch" id="swatches"></div>
    </div>

    <div class="section">
      <label>Language</label>
      <select id="lang-select">
        <option value="en" selected>English</option>
        <option value="it">Italian</option>
        <option value="fr">French</option>
        <option value="">Auto-detect from PDF</option>
      </select>
    </div>

    <div class="section">
      <label>Voice</label>
      <select id="voice-select">
        <option value="">Auto (matches language)</option>
        <option value="en-US-AriaNeural">English — Aria (female, warm)</option>
        <option value="en-US-GuyNeural">English — Guy (male)</option>
        <option value="it-IT-DiegoNeural">Italian — Diego (male)</option>
        <option value="it-IT-ElsaNeural">Italian — Elsa (female)</option>
        <option value="fr-FR-DeniseNeural">French — Denise (female)</option>
      </select>
    </div>

    <div class="section">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
        <input type="checkbox" id="no-audio" style="width:auto">
        Skip audio generation (faster)
      </label>
    </div>

    <button class="btn btn-primary" id="generate-btn" onclick="startGenerate()">
      ✨ Generate Demo
    </button>
  </aside>

  <!-- MAIN — LOG + BUILDS -->
  <main class="main">
    <div id="log-section" style="display:none;margin-bottom:32px;">
      <h2>Generating…</h2>
      <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
      <div style="height:10px;"></div>
      <div class="log-box" id="log-box"></div>
    </div>

    <div id="builds-section">
      <h2>Your Demos</h2>
      <div id="builds-list">
        <div class="empty-state">
          <div class="empty-icon">🗺️</div>
          <p>No demos yet.<br>Upload an itinerary to get started.</p>
        </div>
      </div>
    </div>
  </main>
</div>

<script>
// ─── Colour presets ──────────────────────────────────────────────────────────
const PRESETS = [
  {c:'#8B4513',label:'Tuscany'},
  {c:'#1e3a5f',label:'Coastal'},
  {c:'#2c5f2e',label:'Alpine'},
  {c:'#6B3FA0',label:'Exotic'},
  {c:'#1a1a2e',label:'City'},
  {c:'#c17f3b',label:'Desert'},
  {c:'#1e6b8a',label:'Island'},
  {c:'#5C4033',label:'Safari'},
];
const sw = document.getElementById('swatches');
PRESETS.forEach(p => {
  const el = document.createElement('div');
  el.className = 'swatch' + (p.c==='#8B4513'?' active':'');
  el.style.background = p.c;
  el.title = p.label;
  el.onclick = () => { syncColor(p.c); document.getElementById('color-picker').value=p.c; document.querySelectorAll('.swatch').forEach(s=>s.classList.remove('active')); el.classList.add('active'); };
  sw.appendChild(el);
});

function syncColor(v) { document.getElementById('color-hex').value=v; }
function syncColorFromText(v) { if(/^#[0-9a-fA-F]{6}$/.test(v)) document.getElementById('color-picker').value=v; }

// ─── PDF drag & drop ─────────────────────────────────────────────────────────
let pdfFile = null;
const dz = document.getElementById('drop-zone');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
dz.addEventListener('dragleave', () => dz.classList.remove('over'));
dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('over'); handleFile(e.dataTransfer.files[0]); });

function handleFile(file) {
  if (!file || !file.name.endsWith('.pdf')) return;
  pdfFile = file;
  document.getElementById('pdf-pill').style.display='block';
  document.getElementById('pdf-pill').innerHTML=`<span class="pill">📄 ${file.name} <span class="pill-remove" onclick="clearPdf()">×</span></span>`;
  document.getElementById('drop-zone').style.display='none';
}
function clearPdf() {
  pdfFile=null;
  document.getElementById('pdf-pill').style.display='none';
  document.getElementById('drop-zone').style.display='block';
  document.getElementById('pdf-input').value='';
}

// ─── Generate ────────────────────────────────────────────────────────────────
let currentJobId = null;
function startGenerate() {
  const text = document.getElementById('itin-text').value.trim();
  if (!pdfFile && !text) { alert('Please upload a PDF or paste an itinerary.'); return; }

  const btn = document.getElementById('generate-btn');
  btn.disabled = true; btn.textContent = '⏳ Working…';

  document.getElementById('log-section').style.display='block';
  document.getElementById('log-box').innerHTML='';
  document.getElementById('progress-fill').style.width='5%';

  const fd = new FormData();
  if (pdfFile) fd.append('pdf', pdfFile);
  if (text)    fd.append('text', text);
  fd.append('color',    document.getElementById('color-hex').value);
  fd.append('lang',     document.getElementById('lang-select').value);
  fd.append('voice',    document.getElementById('voice-select').value);
  fd.append('no_audio', document.getElementById('no-audio').checked ? '1' : '0');

  fetch('/api/generate', { method:'POST', body:fd })
    .then(r=>r.json())
    .then(d => { currentJobId = d.job_id; pollLog(d.job_id); })
    .catch(e => { appendLog('error', '❌ '+e); btn.disabled=false; btn.textContent='✨ Generate Demo'; });
}

function appendLog(type, text) {
  const box = document.getElementById('log-box');
  const line = document.createElement('span');
  line.className = 'log-line' + (type==='ok'?' log-ok':type==='error'?' log-err':type==='info'?' log-info':'');
  line.textContent = text;
  box.appendChild(line);
  box.appendChild(document.createElement('br'));
  box.scrollTop = box.scrollHeight;
}

function pollLog(jobId) {
  const es = new EventSource(`/api/log/${jobId}`);
  let progress = 5;
  es.onmessage = e => {
    const msg = e.data;
    if (msg === '__DONE__') {
      es.close();
      document.getElementById('progress-fill').style.width='100%';
      document.getElementById('generate-btn').disabled=false;
      document.getElementById('generate-btn').textContent='✨ Generate Demo';
      loadBuilds();
      return;
    }
    if (msg === '__ERROR__') {
      es.close();
      document.getElementById('generate-btn').disabled=false;
      document.getElementById('generate-btn').textContent='✨ Generate Demo';
      return;
    }
    const type = msg.startsWith('✅')||msg.startsWith('✓') ? 'ok' : msg.startsWith('❌')||msg.startsWith('⚠️') ? 'error' : msg.startsWith('🤖')||msg.startsWith('✍️')||msg.startsWith('🎙️') ? 'info' : '';
    appendLog(type, msg);
    progress = Math.min(progress + 8, 90);
    document.getElementById('progress-fill').style.width = progress + '%';
  };
  es.onerror = () => { es.close(); };
}

// ─── Builds list ─────────────────────────────────────────────────────────────
function loadBuilds() {
  fetch('/api/builds').then(r=>r.json()).then(builds => {
    const el = document.getElementById('builds-list');
    if (!builds.length) {
      el.innerHTML='<div class="empty-state"><div class="empty-icon">🗺️</div><p>No demos yet.<br>Upload an itinerary to get started.</p></div>';
      return;
    }
    el.innerHTML = builds.map(b => `
      <div class="build-card">
        <div class="build-card-icon">🗺️</div>
        <div class="build-card-info">
          <div class="build-card-name">${b.name}</div>
          <div class="build-card-meta">${b.days} days · ${b.hotels} hotels · ${b.audio} audio · ${b.size}${b.code ? ` · 🔑 <strong style="letter-spacing:2px;color:var(--accent)">${b.code}</strong>` : ''}</div>
        </div>
        <div class="build-card-actions">
          <a class="btn btn-secondary" href="/preview/${b.id}" target="_blank">👁 Preview</a>
          <a class="btn btn-secondary" href="/download/${b.id}" download>↓ Download</a>
        </div>
      </div>`).join('');
  });
}

loadBuilds();
setInterval(loadBuilds, 10000);
</script>
</body>
</html>"""

# ─── Flask routes ────────────────────────────────────────────────────────────
@app.route("/api/generate", methods=["POST"])
def generate():
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "running", "log": [], "output_dir": None}

    # Save uploaded PDF or text
    pdf_file = request.files.get("pdf")
    text     = request.form.get("text", "").strip()
    color    = request.form.get("color", "")
    lang     = request.form.get("lang", "")
    voice    = request.form.get("voice", "")
    no_audio = request.form.get("no_audio", "0") == "1"

    # Write itinerary to temp file
    import tempfile
    if pdf_file and pdf_file.filename:
        tmp_pdf = Path(tempfile.mktemp(suffix=".pdf"))
        pdf_file.save(str(tmp_pdf))
        entry_script = BASE_DIR / "import-pdf.py"
        itin_arg = str(tmp_pdf)
        is_pdf = True
    elif text:
        tmp_txt = Path(tempfile.mktemp(suffix=".txt"))
        tmp_txt.write_text(text, encoding="utf-8")
        entry_script = BASE_DIR / "generate-demo.py"
        itin_arg = str(tmp_txt)
        is_pdf = False
    else:
        return jsonify({"error": "No input"}), 400

    def run_job():
        import subprocess, os
        # import-pdf.py takes pdf path as positional arg
        # generate-demo.py takes --itinerary flag
        if is_pdf:
            cmd = [sys.executable, str(entry_script), itin_arg]
        else:
            cmd = [sys.executable, str(entry_script), "--itinerary", itin_arg]
        if color:    cmd += ["--color", color]
        if lang:     cmd += ["--lang", lang]
        if voice:    cmd += ["--voice", voice]
        if no_audio: cmd += ["--no-audio"]

        env = os.environ.copy()
        env["PYTHONWARNINGS"] = "ignore"
        # Run from loomtrip dir so output folders land there
        proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, bufsize=1
        )
        output_dir = None
        for line in proc.stdout:
            line = line.rstrip()
            jobs[job_id]["log"].append(line)
            # detect output dir from success line
            if "Demo ready:" in line:
                m = re.search(r"Demo ready: (.+?)/?$", line)
                if m: output_dir = BASE_DIR / m.group(1).strip()
        proc.wait()
        # Clean up temp input
        Path(itin_arg).unlink(missing_ok=True)
        if proc.returncode == 0 and output_dir:
            # Move to builds/
            dest = BUILDS_DIR / job_id
            if output_dir.exists():
                shutil.move(str(output_dir), str(dest))
            jobs[job_id]["output_dir"] = str(dest)
            jobs[job_id]["status"] = "done"
            # Auto-generate access code
            try:
                html_path = dest / "index.html"
                html_content = html_path.read_text(errors="ignore") if html_path.exists() else ""
                m2 = re.search(r'class="app-header-brand">([^<]+)', html_content)
                bname = m2.group(1) if m2 else ""
                m3 = re.search(r'<title>([^<:]+)', html_content)
                dest_name = m3.group(1).strip() if m3 else bname
                access_code = register_code(job_id, bname, dest_name)
                jobs[job_id]["code"] = access_code
                jobs[job_id]["log"].append(f"🔑 Access code: {access_code}  →  localhost:5099")
            except Exception as ex:
                jobs[job_id]["log"].append(f"⚠️  Code generation failed: {ex}")
        else:
            jobs[job_id]["status"] = "error"
        jobs[job_id]["log"].append("__DONE__" if jobs[job_id]["status"]=="done" else "__ERROR__")

    threading.Thread(target=run_job, daemon=True).start()
    return jsonify({"job_id": job_id})

@app.route("/api/log/<job_id>")
def log_stream(job_id):
    def stream():
        seen = 0
        for _ in range(600):  # max ~5 min
            log = jobs.get(job_id, {}).get("log", [])
            while seen < len(log):
                line = log[seen]; seen += 1
                yield f"data: {line}\n\n"
                if line in ("__DONE__", "__ERROR__"):
                    return
            time.sleep(0.5)
    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/builds")
def list_builds():
    result = []
    for d in sorted(BUILDS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not d.is_dir(): continue
        html = d / "index.html"
        if not html.exists(): continue
        # Parse metadata
        content = html.read_text(errors="ignore")
        audio = len(list(d.glob("audio-*.mp3")))
        # Day count: prefer narration-scripts.json (exact count), fall back to HTML
        nj = d / "narration-scripts.json"
        if nj.exists():
            try:
                days = len(json.loads(nj.read_text()))
            except Exception:
                days = 0
        else:
            days = len(re.findall(r'class="cal-day-num"', content))
        hotels = len(re.findall(r'class="hotel-card"', content))
        # Extract DMC name
        m = re.search(r'class="app-header-brand">([^<]+)', content)
        name = m.group(1) if m else d.name
        size_kb = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) // 1024
        # look up code
        all_codes = load_codes()
        code = next((k for k,v in all_codes.items() if v == d.name), None)
        result.append({"id": d.name, "name": name, "days": days,
                        "hotels": hotels, "audio": audio,
                        "size": f"{size_kb} KB", "code": code})
    return jsonify(result)

@app.route("/preview/<build_id>")
def preview(build_id):
    build_dir = BUILDS_DIR / build_id
    return send_from_directory(str(build_dir), "index.html")

@app.route("/preview/<build_id>/<path:filename>")
def preview_asset(build_id, filename):
    return send_from_directory(str(BUILDS_DIR / build_id), filename)

@app.route("/download/<build_id>")
def download(build_id):
    import zipfile, io
    build_dir = BUILDS_DIR / build_id
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in build_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(build_dir))
    buf.seek(0)
    return Response(buf.read(), mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=loomtrip-demo-{build_id}.zip"})

# ─── code → build mapping ────────────────────────────────────────────────────
CODES_FILE = BASE_DIR / "codes.json"

def load_codes():
    if CODES_FILE.exists():
        try: return json.loads(CODES_FILE.read_text())
        except: pass
    return {}

def save_codes(codes):
    CODES_FILE.write_text(json.dumps(codes, indent=2))

def register_code(build_id, name, destination):
    """Auto-generate a memorable 6-char code from destination + name."""
    import re as _re
    dest = _re.sub(r'[^a-zA-Z]', '', destination or name or build_id).upper()[:4]
    suffix = build_id[:2].upper()
    code = (dest + suffix)[:6].ljust(6, 'X')
    codes = load_codes()
    # avoid collision
    base, i = code, 1
    while code in codes and codes[code] != build_id:
        code = (base[:5] + str(i))[:6]; i += 1
    codes[code] = build_id
    save_codes(codes)
    return code

LANDING_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loomtrip — Your Journey Awaits</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Helvetica Neue',sans-serif}

/* ── BACKGROUND SLIDESHOW ── */
.bg-stage{position:fixed;inset:0;z-index:0}
.bg-slide{
  position:absolute;inset:0;
  background-size:cover;background-position:center;
  opacity:0;transition:opacity 2s ease-in-out;
}
.bg-slide.active{opacity:1}
.bg-overlay{
  position:absolute;inset:0;
  background:linear-gradient(
    160deg,
    rgba(0,0,0,0.55) 0%,
    rgba(0,0,0,0.35) 50%,
    rgba(0,0,0,0.65) 100%
  );
}

/* ── LAYOUT ── */
.stage{
  position:relative;z-index:1;
  height:100vh;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  padding:24px;
}

/* ── WORDMARK ── */
.wordmark{
  font-size:13px;font-weight:600;letter-spacing:3px;text-transform:uppercase;
  color:rgba(255,255,255,0.7);margin-bottom:48px;
  display:flex;align-items:center;gap:10px;
}
.wordmark::before,.wordmark::after{
  content:'';flex:1;height:1px;width:32px;
  background:rgba(255,255,255,0.3);
}

/* ── CARD ── */
.card{
  width:100%;max-width:400px;
  background:rgba(255,255,255,0.10);
  backdrop-filter:blur(32px) saturate(180%);
  -webkit-backdrop-filter:blur(32px) saturate(180%);
  border:1px solid rgba(255,255,255,0.18);
  border-radius:24px;
  padding:40px 36px 36px;
  box-shadow:0 32px 80px rgba(0,0,0,0.4),0 0 0 1px rgba(255,255,255,0.06) inset;
}
.card-title{
  font-size:26px;font-weight:700;
  color:#fff;letter-spacing:-0.5px;
  margin-bottom:6px;line-height:1.2;
}
.card-sub{
  font-size:14px;color:rgba(255,255,255,0.55);
  margin-bottom:32px;line-height:1.5;
}

/* ── INPUT ── */
.input-wrap{position:relative;margin-bottom:14px}
.code-input{
  width:100%;
  padding:18px 20px;
  font-size:22px;font-weight:700;letter-spacing:6px;text-align:center;
  text-transform:uppercase;
  background:rgba(255,255,255,0.12);
  border:1.5px solid rgba(255,255,255,0.25);
  border-radius:14px;
  color:#fff;
  outline:none;
  transition:all 0.2s;
  font-family:inherit;
  caret-color:rgba(255,255,255,0.8);
}
.code-input::placeholder{
  color:rgba(255,255,255,0.25);letter-spacing:4px;font-size:16px;font-weight:400;
}
.code-input:focus{
  border-color:rgba(255,255,255,0.55);
  background:rgba(255,255,255,0.18);
  box-shadow:0 0 0 4px rgba(255,255,255,0.08);
}
.code-input.error{
  border-color:rgba(255,100,100,0.7);
  animation:shake 0.4s ease;
}
@keyframes shake{
  0%,100%{transform:translateX(0)}
  20%{transform:translateX(-8px)}
  40%{transform:translateX(8px)}
  60%{transform:translateX(-5px)}
  80%{transform:translateX(5px)}
}

/* ── BUTTON ── */
.btn-enter{
  width:100%;padding:17px;
  font-size:15px;font-weight:600;letter-spacing:0.3px;
  background:rgba(255,255,255,0.95);
  color:#111;border:none;border-radius:14px;
  cursor:pointer;
  transition:all 0.2s;
  display:flex;align-items:center;justify-content:center;gap:8px;
}
.btn-enter:hover{background:#fff;transform:translateY(-1px);box-shadow:0 8px 24px rgba(0,0,0,0.25)}
.btn-enter:active{transform:translateY(0);box-shadow:none}
.btn-enter:disabled{opacity:0.6;transform:none;cursor:default}
.btn-arrow{font-size:18px;transition:transform 0.2s}
.btn-enter:hover .btn-arrow{transform:translateX(3px)}

/* ── ERROR MSG ── */
.err-msg{
  font-size:13px;color:rgba(255,150,150,0.9);
  text-align:center;margin-top:12px;min-height:20px;
  transition:opacity 0.2s;
}

/* ── DESTINATION LABEL ── */
.dest-label{
  position:fixed;bottom:32px;left:50%;transform:translateX(-50%);
  font-size:11px;letter-spacing:2px;text-transform:uppercase;
  color:rgba(255,255,255,0.45);
  background:rgba(0,0,0,0.25);
  backdrop-filter:blur(8px);
  padding:6px 16px;border-radius:20px;
  transition:opacity 0.8s ease;
  white-space:nowrap;
}

/* ── SPINNER ── */
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{
  width:18px;height:18px;
  border:2px solid rgba(0,0,0,0.2);
  border-top-color:#111;
  border-radius:50%;
  animation:spin 0.7s linear infinite;
}
</style>
</head>
<body>

<div class="bg-stage" id="bgStage"></div>

<div class="stage">
  <div class="wordmark">Loomtrip</div>

  <div class="card">
    <div class="card-title">Your journey<br>awaits.</div>
    <div class="card-sub">Enter the access code sent by your travel specialist.</div>

    <form onsubmit="submit(event)">
      <div class="input-wrap">
        <input class="code-input" id="codeInput"
          type="text" maxlength="6"
          placeholder="• • • • • •"
          autocomplete="off" autocorrect="off" autocapitalize="characters"
          spellcheck="false" autofocus>
      </div>
      <button class="btn-enter" id="submitBtn" type="submit">
        <span id="btnText">Enter</span>
        <span class="btn-arrow" id="btnArrow">→</span>
      </button>
    </form>
    <div class="err-msg" id="errMsg"></div>
  </div>
</div>

<div class="dest-label" id="destLabel"></div>

<script>
const SCENES = [
  {url:'https://images.unsplash.com/photo-1565008887967-af5ab3adcbe9?w=1920&q=80&fit=crop',label:'Tbilisi, Georgia'},
  {url:'https://images.unsplash.com/photo-1533105079780-92b9be482077?w=1920&q=80&fit=crop',label:'Santorini, Greece'},
  {url:'https://images.unsplash.com/photo-1467269204594-9661b134dd2b?w=1920&q=80&fit=crop',label:'Tuscany, Italy'},
  {url:'https://images.unsplash.com/photo-1476610182048-b716b8518aae?w=1920&q=80&fit=crop',label:'Iceland'},
  {url:'https://images.unsplash.com/photo-1514282401047-d79a71a590e8?w=1920&q=80&fit=crop',label:'Maldives'},
  {url:'https://images.unsplash.com/photo-1512100356356-de1b84283e18?w=1920&q=80&fit=crop',label:'Cinque Terre, Italy'},
  {url:'https://images.unsplash.com/photo-1542293787938-c9e299b880cc?w=1920&q=80&fit=crop',label:'Cappadocia, Turkey'},
  {url:'https://images.unsplash.com/photo-1528360983277-13d401cdc186?w=1920&q=80&fit=crop',label:'Amalfi Coast, Italy'},
  {url:'https://images.unsplash.com/photo-1548013146-72479768bada?w=1920&q=80&fit=crop',label:'Taj Mahal, India'},
  {url:'https://images.unsplash.com/photo-1553913861-c0fddf2619ee?w=1920&q=80&fit=crop',label:'Machu Picchu, Peru'},
];

// Shuffle
for(let i=SCENES.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[SCENES[i],SCENES[j]]=[SCENES[j],SCENES[i]];}

const stage = document.getElementById('bgStage');
let slides = [], cur = 0;

// Create overlay once
const ov = document.createElement('div');
ov.className='bg-overlay'; stage.appendChild(ov);

// Preload & create slide divs
SCENES.forEach((s,i)=>{
  const img = new Image();
  img.src = s.url;
  const div = document.createElement('div');
  div.className='bg-slide';
  div.style.backgroundImage=`url(${s.url})`;
  stage.appendChild(div);
  slides.push(div);
});

function showSlide(n){
  slides.forEach(s=>s.classList.remove('active'));
  slides[n].classList.add('active');
  document.getElementById('destLabel').textContent=SCENES[n].label;
}
// Pick one random scene and stay on it — changes only on fresh page load (new login)
const chosen = Math.floor(Math.random() * slides.length);
showSlide(chosen);

// Input auto-uppercase
const inp = document.getElementById('codeInput');
inp.addEventListener('input',()=>{
  inp.value=inp.value.toUpperCase().replace(/[^A-Z0-9]/g,'');
  inp.classList.remove('error');
  document.getElementById('errMsg').textContent='';
});

// Submit
async function submit(e){
  e.preventDefault();
  const code = inp.value.trim();
  if(code.length<4){shake();return;}
  const btn=document.getElementById('submitBtn');
  btn.disabled=true;
  btn.innerHTML='<div class="spinner"></div>';
  try{
    const r = await fetch('/enter',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
    const d = await r.json();
    if(d.url){
      window.location.href=d.url;
    } else {
      shake();
      document.getElementById('errMsg').textContent=d.error||'Invalid code. Please try again.';
      btn.disabled=false;
      btn.innerHTML='<span id="btnText">Enter</span><span class="btn-arrow" id="btnArrow">→</span>';
    }
  }catch{
    shake();
    document.getElementById('errMsg').textContent='Connection error. Try again.';
    btn.disabled=false;
    btn.innerHTML='<span id="btnText">Enter</span><span class="btn-arrow" id="btnArrow">→</span>';
  }
}
function shake(){inp.classList.add('error');setTimeout(()=>inp.classList.remove('error'),600);}
</script>
</body>
</html>"""

@app.route("/")
def landing():
    return render_template_string(LANDING_HTML)

@app.route("/enter", methods=["POST"])
def enter_code():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip().upper()[:6]
    if not code:
        return jsonify({"error": "Enter a code"}), 400
    codes = load_codes()
    build_id = codes.get(code)
    if not build_id:
        return jsonify({"error": "Code not found. Check with your travel specialist."}), 404
    build_dir = BUILDS_DIR / build_id
    if not build_dir.exists():
        return jsonify({"error": "This trip has been archived."}), 404
    return jsonify({"url": f"/preview/{build_id}"})

@app.route("/admin")
def admin():
    """Admin dashboard — internal use only."""
    return render_template_string(DASHBOARD_HTML)

# ─── run ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # try loading from .env
        env_file = BASE_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    os.environ["ANTHROPIC_API_KEY"] = line.split("=",1)[1].strip()
                    break
    print("\n" + "─"*50)
    print("  🗺️  Loomtrip Admin Dashboard")
    print("  http://localhost:5099")
    print("─"*50 + "\n")
    port = int(os.environ.get("PORT", 5099))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
