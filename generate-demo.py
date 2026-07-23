#!/usr/bin/env python3
"""
Loomtrip — Trojan Loom Demo Generator (with Audio Guide)
=========================================================
Takes any DMC itinerary text → generates a polished branded travel app
with day-by-day audio guide narration.

Output: a folder   demo-{slug}/
           index.html       ← full app (Days · Hotels · 🎙️ Audio · Info · SOS)
           audio-01.mp3     ← narration per day
           audio-02.mp3
           ...

Usage:
  python generate-demo.py --itinerary sample-itinerary-tuscany.txt
  python generate-demo.py --itinerary mytrip.txt --color "#8B4513" --lang it
  python generate-demo.py --itinerary mytrip.txt --voice en-US-AriaNeural

Voices (edge-tts):
  English : en-US-AriaNeural (default) | en-US-GuyNeural
  Italian : it-IT-DiegoNeural | it-IT-ElsaNeural
  French  : fr-FR-DeniseNeural | fr-FR-HenriNeural

Requirements: deps/ folder with anthropic + edge-tts installed
API key     : ANTHROPIC_API_KEY in .env or environment
"""

import sys, os, asyncio
_deps = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deps")
if os.path.isdir(_deps) and _deps not in sys.path:
    sys.path.insert(0, _deps)

import anthropic, httpx, json, re, argparse
from pathlib import Path

# ─── load .env ───────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ─── CLI ─────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Loomtrip demo generator")
parser.add_argument("--itinerary", required=True)
parser.add_argument("--color",       default=None, help="Brand hex e.g. #8B4513")
parser.add_argument("--logo",        default=None, help="Logo URL")
parser.add_argument("--lang",        default=None, help="Override language: en | it | fr")
parser.add_argument("--voice",       default=None, help="edge-tts voice name")
parser.add_argument("--rate",        default="-8%", help="Speech rate e.g. -8pct")
parser.add_argument("--model",       default="claude-haiku-4-5")
parser.add_argument("--no-audio",    action="store_true", help="Skip audio generation")
parser.add_argument("--trip-name",   default=None, help="Override trip title")
parser.add_argument("--client-name", default=None, help="Client/traveler name for personalisation")
parser.add_argument("--agency-name", default=None, help="Override DMC/agency name")
args = parser.parse_args()

# ─── read itinerary ──────────────────────────────────────────────────────────
ipath = Path(args.itinerary)
if not ipath.exists():
    print(f"❌ File not found: {ipath}"); sys.exit(1)
itinerary_text = ipath.read_text(encoding="utf-8")
print(f"📄 Loaded: {ipath.name} ({len(itinerary_text)} chars)")

# ─── Claude client (SSL bypass for macOS Python 3.9 + corporate proxy) ───────
api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("❌ ANTHROPIC_API_KEY not set. Add it to .env"); sys.exit(1)

ai = anthropic.Anthropic(api_key=api_key, http_client=httpx.Client(verify=False))

# ─── STEP 1 — Parse itinerary → structured JSON ──────────────────────────────
PARSE_PROMPT = """Extract structured travel data from this itinerary for a luxury DMC app.
Return ONLY a valid JSON object — no markdown fences, no explanation.

{
  "dmc_name": "DMC or travel company name",
  "destination": "main destination e.g. Tuscany, Italy",
  "trip_title": "short catchy title e.g. Tuscany: Art, Wine & La Dolce Vita",
  "trip_subtitle": "e.g. 7 Days · 6 Nights · May 2026",
  "brand_color": "hex color fitting destination palette (Tuscany=#8B4513, coast=#1e6b8a, jungle=#2d5a27, desert=#c17f3b, alpine=#2c5f2e, city-luxury=#1a1a2e)",
  "language": "client language: it | en | fr",
  "emergency_number": "local country emergency number e.g. 112",
  "flights": [
    {
      "type": "outbound|return",
      "date": "Fri 14 Jun 2026",
      "flight": "EasyJet U2 3997",
      "from": "Milan Malpensa MXP",
      "to": "Tbilisi TBS",
      "depart": "07:10",
      "arrive": "13:25",
      "duration": "4h 15m",
      "notes": "e.g. baggage allowance, check-in info or null"
    }
  ],
  "contacts": [
    {
      "group": "group label e.g. IL NOSTRO UFFICIO IN ITALIA or Your Italian Office",
      "org": "organisation name",
      "addr": "full address or null",
      "entries": [
        {"label": "phone label e.g. Ufficio / Office / Emergency / WhatsApp / Guide", "phone": "+39...", "phone_display": "formatted for display"}
      ]
    }
  ],
  "hotels": [
    {
      "n": 1,
      "name": "hotel name",
      "loc": "city/area",
      "dates": "Mon 4 → Tue 5 May",
      "rating": "score string or null",
      "addr": "full address",
      "phone": "+39... or null",
      "phone_display": "+39 055 ... or null",
      "web": "domain.com or null",
      "meta": "1-2 sentence highlights. ⚠️ prefix warnings.",
      "map_query": "Full hotel name + street address + city, e.g. 'Hotel Museum Via Museumstrasse 1 Tbilisi'"
    }
  ],
  "days": [
    {
      "n": 1,
      "date": "Mon 4 May",
      "title": "short evocative day title",
      "route": "A → B",
      "stats": "~50 km · Easy",
      "hotel_n": 1,
      "timeline": [["HH:MM or label", "activity — use <strong> for key names, emoji ok"]],
      "boxes": [{"type": "success|warn|gold", "title": "emoji Title", "text": "tip text"}]
    }
  ]
}

Rules: hotel_n = 1-based index in hotels array (or null). Mark booked = (booked), recommended = (recommended).
Extract ALL days. Extract ALL contacts groups (Italian office, local office, guides, drivers, etc.) — include every phone number listed. Phone numbers must include country code with + prefix. Extract ALL flights (outbound and return) including flight number, airports with IATA codes, departure/arrival times, and duration.
For timeline times: use exact times from the itinerary (e.g. "09:00"). If no time is given, use a natural label like "Morning", "Afternoon", "Evening", "Lunch", "Dinner" — NEVER invent or estimate times that are not in the source text.
IMPORTANT: Write "trip_subtitle" in the same language as the "language" field you detect. Examples: EN → "7 Days · 6 Nights · May 2026", IT → "7 Giorni · 6 Notti · Maggio 2026", FR → "7 Jours · 6 Nuits · Mai 2026".

ITINERARY:
"""

print(f"🤖 Parsing itinerary ({args.model})...")
msg = ai.messages.create(
    model=args.model, max_tokens=8192,
    messages=[{"role":"user","content": PARSE_PROMPT + itinerary_text}]
)
raw = re.sub(r'^```(?:json)?\s*','', msg.content[0].text.strip())
raw = re.sub(r'\s*```$','', raw)
try:
    data = json.loads(raw)
    print(f"✅ Parsed: {len(data['days'])} days, {len(data['hotels'])} hotels")
except json.JSONDecodeError as e:
    print(f"❌ JSON error: {e}\n{raw[:400]}"); sys.exit(1)

# ─── apply overrides ─────────────────────────────────────────────────────────
if args.color: data["brand_color"] = args.color
if args.lang:  data["language"]    = args.lang

brand_color  = data.get("brand_color") or "#1e3a5f"
dmc_name     = args.agency_name or data.get("dmc_name") or "Your Travel Company"
destination  = data.get("destination")  or "Your Destination"
trip_title   = args.trip_name or data.get("trip_title") or destination
trip_subtitle= data.get("trip_subtitle") or ""
client_name  = args.client_name or ""
lang         = data.get("language")         or "en"
emerg_num    = data.get("emergency_number") or "112"
dmc_phone    = data.get("dmc_emergency_phone") or ""
contacts     = data.get("contacts") or []
flights      = data.get("flights")  or []
hotels       = data.get("hotels",[])
days         = data.get("days",[])
logo_url     = args.logo or ""

def luminance(h):
    try:
        h=h.lstrip('#'); r,g,b=int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        return 0.299*r+0.587*g+0.114*b
    except: return 128
brand_text = "#ffffff" if luminance(brand_color)<160 else "#000000"

def slugify(s):
    s=s.lower(); s=re.sub(r'[^a-z0-9\s-]','',s); s=re.sub(r'\s+','-',s.strip())
    return s

# ─── STEP 2 — Generate audio narration scripts ───────────────────────────────
VOICE_MAP = {"en":"en-US-AriaNeural","it":"it-IT-DiegoNeural","fr":"fr-FR-DeniseNeural"}
voice = args.voice or VOICE_MAP.get(lang,"en-US-AriaNeural")

NARRATION_PROMPT = """You are a warm, knowledgeable luxury travel guide narrating a personalised audio guide for a client.
Write short audio narrations — one per day — for a {n_days}-day trip to {destination}.
Each narration should be 60-90 seconds when read aloud (roughly 140-200 words).
Tone: intimate, evocative, practical. Like a trusted friend who knows the destination deeply.
Do NOT read out times or logistics — focus on atmosphere, what makes this place special, and 1-2 insider tips.
Start each narration naturally, as if speaking directly to the traveller. No titles, no "Day 1:" labels.

IMPORTANT: Write ALL narration text in {lang_name}. The "title" field can also be in {lang_name}.

Return ONLY a JSON array — no markdown, no explanation:
[
  {{"day": 1, "title": "Day title for the player UI", "script": "Full narration text..."}},
  ...
]

Trip data:
{trip_json}
"""

audio_chapters = []
if not args.no_audio:
    print(f"✍️  Generating {len(days)}-day narration scripts...")
    trip_summary = json.dumps([{"n":d["n"],"date":d["date"],"title":d["title"],
                                "route":d.get("route",""),"timeline":d.get("timeline",[])} for d in days],
                              ensure_ascii=False)
    lang_names = {"en": "English", "it": "Italian / Italiano", "fr": "French / Français"}
    lang_name  = lang_names.get(lang, "English")
    narr_msg = ai.messages.create(
        model=args.model, max_tokens=8192,
        messages=[{"role":"user","content":
            NARRATION_PROMPT.format(n_days=len(days), destination=destination,
                                   trip_json=trip_summary, lang_name=lang_name)}]
    )
    raw_narr = re.sub(r'^```(?:json)?\s*','', narr_msg.content[0].text.strip())
    raw_narr = re.sub(r'\s*```$','', raw_narr)
    try:
        audio_chapters = json.loads(raw_narr)
        print(f"✅ Scripts ready: {len(audio_chapters)} chapters")
    except json.JSONDecodeError as e:
        print(f"⚠️  Narration JSON error ({e}) — skipping audio"); audio_chapters = []

# ─── STEP 3 — Generate MP3 files with edge-tts ───────────────────────────────
output_slug = slugify(dmc_name)
output_dir  = Path(f"demo-{output_slug}")
output_dir.mkdir(exist_ok=True)

mp3_files = []
if audio_chapters and not args.no_audio:
    print(f"🎙️  Generating audio ({voice}, rate={args.rate})...")
    # Save scripts so they're never lost regardless of TTS outcome
    scripts_path = output_dir / "narration-scripts.json"
    scripts_path.write_text(json.dumps(audio_chapters, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   💾 Scripts saved → {scripts_path.name}")

    import subprocess, tempfile
    for ch in audio_chapters:
        n   = ch["day"]
        mp3 = output_dir / f"audio-{n:02d}.mp3"
        if mp3.exists() and mp3.stat().st_size > 0:
            print(f"   ↩️  audio-{n:02d}.mp3 already exists, skipping")
            mp3_files.append(mp3.name); continue
        script_text = ch.get("script", "")
        if not script_text.strip():
            print(f"   ⚠️  audio-{n:02d}.mp3 skipped — empty script")
            continue
        print(f"   🔊 Generating audio-{n:02d}.mp3 ({len(script_text)} chars)...")
        try:
            # Run edge-tts as a subprocess — avoids asyncio/threading conflicts entirely
            result = subprocess.run(
                [sys.executable, "-m", "edge_tts",
                 "--voice", voice, "--rate", args.rate,
                 "--text", script_text,
                 "--write-media", str(mp3)],
                capture_output=True, text=True, timeout=120,
                cwd=str(BASE_DIR if 'BASE_DIR' in dir() else Path(__file__).parent)
            )
            if mp3.exists() and mp3.stat().st_size > 0:
                size_kb = mp3.stat().st_size // 1024
                print(f"   ✓ audio-{n:02d}.mp3 ({size_kb} KB)")
                mp3_files.append(mp3.name)
            else:
                stderr_snippet = (result.stderr or "")[:300]
                print(f"   ⚠️  audio-{n:02d}.mp3 empty/missing. stderr: {stderr_snippet}")
        except subprocess.TimeoutExpired:
            print(f"   ⚠️  audio-{n:02d}.mp3 TIMEOUT after 120s")
        except Exception as e:
            print(f"   ⚠️  audio-{n:02d}.mp3 FAILED: {type(e).__name__}: {e}")

# ─── i18n labels ─────────────────────────────────────────────────────────────
LABELS = {
    "en":{"days":"Days","hotels":"Hotels","audio":"Audio","info":"Info","sos":"SOS",
          "itinerary":"Itinerary","map":"📍 Map","call":"📞 Call","web":"🌐 Website",
          "powered":"Powered by Loomtrip","select_ch":"Select a chapter",
          "ch_avail":f"{len(audio_chapters)} chapters available",
          "emergency":"🚨 CALL","welcome":"welcome to your trip",
          "tonight":"Tonight","crafted":"Crafted by",
          "flights":"Flights","outbound":"✈️ Outbound","return_f":"🏠 Return",
          "your_team":"Your dedicated team","this_app":"This App",
          "app_desc":"Your complete trip — programme, hotels, audio guide and emergency contacts."},
    "it":{"days":"Giorni","hotels":"Hotel","audio":"Audio","info":"Info","sos":"SOS",
          "itinerary":"Programma","map":"📍 Mappa","call":"📞 Chiama","web":"🌐 Sito",
          "powered":"Powered by Loomtrip","select_ch":"Seleziona un capitolo",
          "ch_avail":f"{len(audio_chapters)} capitoli disponibili",
          "emergency":"🚨 CHIAMA","welcome":"benvenuti nel vostro viaggio",
          "tonight":"Stanotte","crafted":"A cura di",
          "flights":"Voli","outbound":"✈️ Andata","return_f":"🏠 Ritorno",
          "your_team":"Il vostro team dedicato","this_app":"Questa App",
          "app_desc":"Il vostro viaggio completo — programma, hotel, audio guida e contatti d'emergenza."},
    "fr":{"days":"Jours","hotels":"Hôtels","audio":"Audio","info":"Infos","sos":"SOS",
          "itinerary":"Programme","map":"📍 Carte","call":"📞 Appeler","web":"🌐 Site",
          "powered":"Powered by Loomtrip","select_ch":"Choisissez un chapitre",
          "ch_avail":f"{len(audio_chapters)} chapitres disponibles",
          "emergency":"🚨 APPELER","welcome":"bienvenue dans votre voyage",
          "tonight":"Ce soir","crafted":"Créé par",
          "flights":"Vols","outbound":"✈️ Aller","return_f":"🏠 Retour",
          "your_team":"Votre équipe dédiée","this_app":"Cette App",
          "app_desc":"Votre voyage complet — programme, hôtels, guide audio et contacts d'urgence."},
}
t = LABELS.get(lang, LABELS["en"])

# ─── serialise data ──────────────────────────────────────────────────────────
hotels_js   = json.dumps(hotels,   ensure_ascii=False, indent=2)
days_js     = json.dumps(days,     ensure_ascii=False, indent=2)
contacts_js = json.dumps(contacts, ensure_ascii=False, indent=2)
flights_js  = json.dumps(flights,  ensure_ascii=False, indent=2)
n_tabs    = 5 if audio_chapters else 4  # show Audio tab only if audio was generated
grid_cols = f"repeat({n_tabs},1fr)"

# Build AP_CHAPTERS JS array from audio_chapters
ap_chapters_js = "[]"
if audio_chapters:
    ch_list = []
    for i, ch in enumerate(audio_chapters):
        d = days[i] if i < len(days) else {}
        ch_list.append({
            "n": ch["day"],
            "title": ch.get("title", d.get("title",f"Day {ch['day']}")),
            "date":  d.get("date",""),
            "mp3":   f"audio-{ch['day']:02d}.mp3"
        })
    ap_chapters_js = json.dumps(ch_list, ensure_ascii=False)

audio_tab_html = ""
audio_tab_btn  = ""
audio_js       = ""

if audio_chapters:
    audio_tab_html = f"""
  <!-- AUDIO TAB -->
  <section class="tab-panel no-padding" id="tab-audio">
    <div class="ap-bar-wrap">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:4px;">
        <div style="flex:1;min-width:0;">
          <div class="ap-title" id="ap-title">{t["select_ch"]}</div>
          <div class="ap-sub" id="ap-sub">{t["ch_avail"]}</div>
        </div>
        <button onclick="apCycleSpeed()" id="ap-speed" class="ap-speed-btn">1×</button>
      </div>
      <div class="ap-progress-wrap">
        <span class="ap-time" id="ap-elapsed">0:00</span>
        <div class="ap-progress" onclick="apSeek(event)">
          <div class="ap-progress-fill" id="ap-bar"></div>
        </div>
        <span class="ap-time" id="ap-duration" style="text-align:right;">0:00</span>
      </div>
      <div class="ap-controls">
        <button onclick="apPrev()" class="ap-ctrl-btn" aria-label="Previous">⏮</button>
        <button onclick="apBack()" class="ap-ctrl-btn" aria-label="Back 15s">⏪</button>
        <button onclick="apToggle()" id="ap-play" class="ap-play-btn" aria-label="Play/Pause">▶</button>
        <button onclick="apFwd()"  class="ap-ctrl-btn" aria-label="Forward 15s">⏩</button>
        <button onclick="apNext()" class="ap-ctrl-btn" aria-label="Next">⏭</button>
      </div>
    </div>
    <div id="audio-chapter-list" style="padding:12px 16px 24px;"></div>
  </section>
"""
    audio_tab_btn = f"""
  <button class="tab-btn" onclick="switchTab('audio')" id="btn-audio">
    <span class="tab-icon">🎙️</span><span>{t["audio"]}</span>
  </button>"""

    audio_js = f"""
// ─── AUDIO PLAYER ────────────────────────────────────────────────────────────
const AP_CHAPTERS = {ap_chapters_js};
let apCurrent = null, apRate = 1.0;
const AP_RATES = [0.85, 1.0, 1.15, 1.3];
const apAudio = new Audio();

function apFmt(s) {{
  const m = Math.floor((s||0)/60), sec = Math.floor((s||0)%60);
  return m + ':' + String(sec).padStart(2,'0');
}}

function apRenderList() {{
  document.getElementById('audio-chapter-list').innerHTML = AP_CHAPTERS.map(ch => `
    <div class="ap-chapter-item" onclick="apPlay(${{ch.n}})">
      <div class="ap-chapter-icon ${{apCurrent===ch.n?'playing':''}}">${{apCurrent===ch.n?'▶':'▷'}}</div>
      <div style="flex:1;min-width:0;">
        <div class="ap-chapter-title ${{apCurrent===ch.n?'playing':''}}">${{ch.title}}</div>
        <div class="ap-chapter-date">${{ch.date}}</div>
      </div>
    </div>`).join('');
}}

function apPlay(n) {{
  const ch = AP_CHAPTERS.find(c => c.n === n);
  if (!ch) return;
  apCurrent = n;
  apAudio.src = ch.mp3;
  apAudio.playbackRate = apRate;
  apAudio.play();
  document.getElementById('ap-title').textContent = ch.title;
  document.getElementById('ap-sub').textContent   = ch.date;
  document.getElementById('ap-play').textContent  = '⏸';
  apRenderList();
}}

function apToggle() {{
  if (apCurrent === null) {{ apPlay(AP_CHAPTERS[0].n); return; }}
  if (apAudio.paused) {{ apAudio.play(); document.getElementById('ap-play').textContent='⏸'; }}
  else {{ apAudio.pause(); document.getElementById('ap-play').textContent='▶'; }}
}}

function apPrev() {{ if (apCurrent > 1) apPlay(apCurrent - 1); }}
function apNext() {{ const idx = AP_CHAPTERS.findIndex(c => c.n === apCurrent); if (idx < AP_CHAPTERS.length-1) apPlay(AP_CHAPTERS[idx+1].n); }}
function apFwd()  {{ apAudio.currentTime = Math.min(apAudio.currentTime+15, apAudio.duration||0); }}
function apBack() {{ apAudio.currentTime = Math.max(apAudio.currentTime-15, 0); }}
function apCycleSpeed() {{
  const idx = AP_RATES.indexOf(apRate);
  apRate = AP_RATES[(idx+1) % AP_RATES.length];
  apAudio.playbackRate = apRate;
  document.getElementById('ap-speed').textContent = apRate.toFixed(2).replace('.00','').replace(/\\.?0+$/,'') + '×';
}}
function apSeek(e) {{
  if (!apAudio.duration) return;
  const bar = e.currentTarget;
  apAudio.currentTime = (e.offsetX / bar.offsetWidth) * apAudio.duration;
}}
apAudio.addEventListener('timeupdate', () => {{
  const p = apAudio.duration ? apAudio.currentTime/apAudio.duration*100 : 0;
  document.getElementById('ap-bar').style.width = p + '%';
  document.getElementById('ap-elapsed').textContent  = apFmt(apAudio.currentTime);
  document.getElementById('ap-duration').textContent = apFmt(apAudio.duration);
}});
apAudio.addEventListener('ended', () => {{
  document.getElementById('ap-play').textContent = '▶';
  const idx = AP_CHAPTERS.findIndex(c => c.n === apCurrent);
  if (idx < AP_CHAPTERS.length-1) apPlay(AP_CHAPTERS[idx+1].n);
}});
apAudio.addEventListener('play',  () => {{ document.getElementById('ap-play').textContent = '⏸'; }});
apAudio.addEventListener('pause', () => {{ document.getElementById('ap-play').textContent = '▶'; }});
apRenderList();
"""

# ─── STEP 4 — Build HTML ─────────────────────────────────────────────────────
HTML = f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,viewport-fit=cover,maximum-scale=1.0,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{destination}">
<meta name="theme-color" content="{brand_color}">
<title>{trip_title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
/* ── LUXURY EDITORIAL DESIGN SYSTEM ────────────────────────────────────────
   Style: Warm editorial — Georgia serif titles, liquid-glass nav, ink on cream.
   References: Monocle Travel, Net-a-Porter Mobile, Soho House App.
   ───────────────────────────────────────────────────────────────────────── */
:root{{
  --brand:{brand_color};
  --brand-text:{brand_text};
  --brand-rgb:28,25,23;
  /* surfaces */
  --bg:#FAF8F5;
  --bg-card:#FFFFFF;
  --bg-nav:rgba(250,248,245,0.92);
  --bg-glass:rgba(255,255,255,0.72);
  /* typography */
  --text:#1C1917;
  --text-secondary:#57534E;
  --text-muted:#A8A29E;
  --text-serif:'Cormorant Garamond',Georgia,"Times New Roman",serif;
  --text-sans:'Inter',-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Helvetica,Arial,sans-serif;
  /* semantic */
  --accent:{brand_color};
  --danger:#C0392B;
  --warn:#B7791F;
  --success:#276749;
  --gold-text:#92400E;
  /* structure */
  --border:rgba(28,25,23,0.08);
  --border-strong:rgba(28,25,23,0.14);
  --divider:rgba(28,25,23,0.06);
  --shadow-xs:0 1px 2px rgba(28,25,23,0.04);
  --shadow-sm:0 2px 8px rgba(28,25,23,0.06),0 1px 2px rgba(28,25,23,0.04);
  --shadow-md:0 4px 20px rgba(28,25,23,0.08),0 1px 4px rgba(28,25,23,0.04);
  --shadow-brand:0 4px 16px rgba(28,25,23,0.18);
  /* layout */
  --tab-h:64px;
  --header-h:70px;
  --radius-sm:10px;
  --radius-md:16px;
  --radius-lg:20px;
}}
@media(prefers-color-scheme:dark){{
  :root{{
    --bg:#0C0A09;
    --bg-card:#1C1917;
    --bg-nav:rgba(12,10,9,0.92);
    --bg-glass:rgba(28,25,23,0.72);
    --text:#FAFAF9;
    --text-secondary:#D6D3D1;
    --text-muted:#78716C;
    --border:rgba(250,250,249,0.08);
    --border-strong:rgba(250,250,249,0.14);
    --divider:rgba(250,250,249,0.06);
    --shadow-xs:0 1px 2px rgba(0,0,0,0.3);
    --shadow-sm:0 2px 8px rgba(0,0,0,0.3),0 1px 2px rgba(0,0,0,0.2);
    --shadow-md:0 4px 20px rgba(0,0,0,0.4),0 1px 4px rgba(0,0,0,0.2);
    --shadow-brand:0 4px 20px rgba(0,0,0,0.5);
    --warn:#D97706;
    --success:#34D399;
    --danger:#F87171;
    --gold-text:#D97706;
  }}
}}
*{{box-sizing:border-box;-webkit-tap-highlight-color:transparent;margin:0;padding:0}}
html,body{{height:100%;overflow:hidden;overscroll-behavior:none}}
body{{
  font-family:var(--text-sans);
  font-size:15px;line-height:1.5;
  color:var(--text);background:var(--bg);
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
}}
h1,h2,h3{{font-family:var(--text-serif);letter-spacing:-0.3px;color:var(--text)}}
h2{{font-size:22px;font-weight:400;margin:0 0 8px}}
h3{{font-size:17px;font-weight:400;margin:0 0 6px}}
p{{margin:0 0 8px;color:var(--text-secondary);line-height:1.6}}
a{{color:var(--accent);text-decoration:none}}
strong{{font-weight:600;color:var(--text)}}

/* ── APP SHELL ─────────────────────────────────────────────────────────── */
.app-header{{
  position:fixed;top:0;left:0;right:0;
  height:var(--header-h);
  background:var(--brand);
  color:var(--brand-text);
  display:flex;align-items:center;justify-content:space-between;
  padding:0 18px;
  padding-top:env(safe-area-inset-top);
  z-index:100;
  gap:12px;
  background-image:
    linear-gradient(135deg,rgba(255,255,255,0.10) 0%,transparent 50%),
    linear-gradient(to bottom,rgba(0,0,0,0.08) 0%,transparent 100%);
  box-shadow:0 1px 0 rgba(0,0,0,0.12),0 2px 12px rgba(0,0,0,0.15);
}}
.app-header-brand{{
  font-family:var(--text-serif);
  font-size:17px;font-weight:500;
  letter-spacing:0.2px;
  opacity:0.96;
  flex:1;
  min-width:0;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  line-height:1.2;
}}
.app-header-trip{{
  font-family:var(--text-sans);
  font-size:10px;
  letter-spacing:0.5px;
  text-transform:uppercase;
  opacity:0.6;
  text-align:right;
  line-height:1.5;
  flex-shrink:0;
  max-width:130px;
}}
.app-header-trip strong{{
  display:block;
  font-family:var(--text-serif);
  font-size:13px;font-weight:400;
  letter-spacing:0;text-transform:none;
  opacity:1;color:var(--brand-text);
  margin-bottom:1px;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  max-width:130px;
  line-height:1.2;
}}

/* ── TAB BAR ───────────────────────────────────────────────────────────── */
.tab-bar{{
  position:fixed;bottom:0;left:0;right:0;
  height:var(--tab-h);
  padding-bottom:env(safe-area-inset-bottom);
  background:var(--bg-nav);
  backdrop-filter:blur(28px) saturate(200%);
  -webkit-backdrop-filter:blur(28px) saturate(200%);
  border-top:1px solid var(--border);
  display:grid;grid-template-columns:{grid_cols};
  z-index:100;
}}
.tab-btn{{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:3px;border:none;background:none;cursor:pointer;
  color:var(--text-muted);
  font-family:var(--text-sans);
  font-size:10px;font-weight:500;
  letter-spacing:0.2px;
  padding:10px 0 8px;
  transition:color 0.2s;
  touch-action:manipulation;
  position:relative;
}}
.tab-btn::before{{
  content:'';position:absolute;top:0;left:25%;right:25%;
  height:2px;border-radius:0 0 2px 2px;
  background:var(--brand);
  transform:scaleX(0);transition:transform 0.2s cubic-bezier(0.4,0,0.2,1);
}}
.tab-btn.active::before{{transform:scaleX(1)}}
.tab-btn .tab-icon{{font-size:20px;line-height:1;transition:transform 0.18s}}
.tab-btn.active{{color:var(--brand)}}
.tab-btn.active .tab-icon{{transform:translateY(-1px)}}
.tab-btn:active .tab-icon{{transform:scale(0.88)}}

/* ── BODY / PANELS ─────────────────────────────────────────────────────── */
.app-body{{position:fixed;top:var(--header-h);bottom:var(--tab-h);left:0;right:0;overflow:hidden}}
.tab-panel{{
  position:absolute;inset:0;
  overflow-y:auto;-webkit-overflow-scrolling:touch;
  padding:16px 14px 28px;
  display:none;
  scrollbar-width:none;
}}
.tab-panel::-webkit-scrollbar{{display:none}}
.tab-panel.active{{display:block}}
.tab-panel.no-padding{{padding:0}}

/* ── CARDS ─────────────────────────────────────────────────────────────── */
.card{{
  background:var(--bg-card);
  border-radius:var(--radius-md);
  padding:18px;margin-bottom:12px;
  border:1px solid var(--border);
  box-shadow:var(--shadow-sm);
}}

/* ── CALENDAR STRIP ────────────────────────────────────────────────────── */
.cal-strip{{
  display:flex;gap:8px;overflow-x:auto;
  padding:14px 16px 12px;
  scrollbar-width:none;
  position:sticky;top:0;z-index:20;
  background:var(--bg);
  border-bottom:1px solid var(--border);
  box-shadow:0 2px 12px rgba(0,0,0,0.05);
}}
.cal-strip::-webkit-scrollbar{{display:none}}
.cal-day{{
  min-width:50px;height:66px;
  border-radius:14px;border:1.5px solid var(--border);
  cursor:pointer;background:var(--bg-card);
  color:var(--text-muted);
  font-family:var(--text-sans);
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;
  box-shadow:var(--shadow-xs);
  touch-action:manipulation;
  transition:all 0.2s cubic-bezier(0.4,0,0.2,1);
  flex-shrink:0;
}}
.cal-day:active{{transform:scale(0.91)}}
.cal-day.active{{
  background:var(--brand);color:var(--brand-text);
  border-color:transparent;
  box-shadow:0 4px 16px rgba(0,0,0,0.18);
  transform:translateY(-1px);
}}
.cal-day-num{{font-size:18px;font-weight:700;line-height:1;font-family:var(--text-serif)}}
.cal-day-mon{{font-size:9.5px;font-weight:600;letter-spacing:0.5px;opacity:0.7;text-transform:uppercase}}

/* ── DAY CARDS ─────────────────────────────────────────────────────────── */
.day-card{{
  background:var(--bg-card);
  border-radius:var(--radius-md);
  margin:0 0 10px;
  border:1px solid var(--border);
  box-shadow:var(--shadow-sm);
  overflow:hidden;
  transition:box-shadow 0.2s;
}}
.day-card.open{{box-shadow:var(--shadow-md)}}
.day-card-summary{{
  display:flex;align-items:center;gap:14px;
  padding:16px 16px;
  cursor:pointer;background:none;border:none;
  width:100%;text-align:left;
  touch-action:manipulation;
}}
.day-card-summary:active{{background:var(--divider)}}

/* Large editorial day number */
.day-num-badge{{
  width:46px;height:46px;
  border-radius:12px;
  border:1.5px solid var(--border-strong);
  display:flex;align-items:center;justify-content:center;
  flex-shrink:0;
  position:relative;overflow:hidden;
  transition:all 0.2s;
}}
.day-num-badge-inner{{
  font-family:var(--text-serif);
  font-size:20px;font-weight:500;
  color:var(--brand);
  line-height:1;
}}
.day-card.open .day-num-badge{{
  background:var(--brand);
  border-color:transparent;
  box-shadow:0 2px 10px rgba(0,0,0,0.15);
}}
.day-card.open .day-num-badge-inner{{color:var(--brand-text)}}

.day-info{{flex:1;min-width:0}}
.day-info-title{{
  font-family:var(--text-serif);
  font-size:17px;font-weight:400;
  color:var(--text);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  letter-spacing:-0.1px;
}}
.day-info-meta{{
  font-size:10.5px;
  color:var(--text-muted);
  letter-spacing:0.4px;
  text-transform:uppercase;
  margin-top:3px;
}}
.day-chevron{{
  font-size:18px;color:var(--text-muted);
  transition:transform 0.25s cubic-bezier(0.4,0,0.2,1);
  flex-shrink:0;line-height:1;
}}
.day-card.open .day-chevron{{transform:rotate(90deg)}}

.day-card-body{{
  display:none;
  padding:0 16px 16px;
  border-top:1px solid var(--divider);
}}
.day-card.open .day-card-body{{display:block}}

/* ── TIMELINE ──────────────────────────────────────────────────────────── */
.tl-label{{
  font-size:10px;font-weight:600;
  letter-spacing:1px;text-transform:uppercase;
  color:var(--text-muted);
  margin:16px 0 10px;
  display:flex;align-items:center;gap:8px;
}}
.tl-label::after{{content:'';flex:1;height:1px;background:var(--divider)}}
.timeline{{list-style:none;padding:0;margin:0;position:relative}}
.timeline::before{{
  content:'';
  position:absolute;left:51px;top:8px;bottom:8px;
  width:1px;background:var(--divider);
  pointer-events:none;
}}
.timeline li{{
  display:flex;gap:0;
  padding:7px 0;
  font-size:14px;
  line-height:1.5;
  align-items:flex-start;
  position:relative;
}}
.timeline-time{{
  color:var(--brand);
  font-family:var(--text-sans);
  font-size:11.5px;font-weight:600;
  letter-spacing:0.2px;
  min-width:52px;
  padding-top:2px;
  flex-shrink:0;
  white-space:nowrap;
}}
.timeline-dot{{
  width:7px;height:7px;border-radius:50%;
  background:var(--border-strong);
  flex-shrink:0;margin:5px 12px 0 0;
  position:relative;z-index:1;
}}
.timeline-content{{flex:1;color:var(--text-secondary);padding-top:1px}}
.timeline-content strong{{color:var(--text)}}
.timeline-content a.attr-link{{
  color:var(--text);
  text-decoration:none;
  border-bottom:1px solid rgba(0,0,0,0.2);
  padding-bottom:0;
  transition:color 0.15s,border-color 0.15s;
}}
.timeline-content a.attr-link:hover{{color:var(--accent);border-color:var(--accent)}}

/* ── INFO BOXES ────────────────────────────────────────────────────────── */
.box{{
  border-radius:12px;
  padding:13px 15px;
  margin:10px 0;
  border-left:3px solid;
}}
.box-title{{font-size:12px;font-weight:700;letter-spacing:0.2px;margin-bottom:4px}}
.box-body{{font-size:13px;line-height:1.55;color:var(--text-secondary)}}
.box.success{{
  background:rgba(39,103,73,0.07);
  border-color:var(--success);
}}
.box.success .box-title{{color:var(--success)}}
.box.warn{{
  background:rgba(183,121,31,0.08);
  border-color:var(--warn);
}}
.box.warn .box-title{{color:var(--warn)}}
.box.gold{{
  background:rgba(146,64,14,0.07);
  border-color:var(--gold-text);
}}
.box.gold .box-title{{color:var(--gold-text)}}
@media(prefers-color-scheme:dark){{
  .box.success{{background:rgba(52,211,153,0.08)}}
  .box.warn{{background:rgba(217,119,6,0.1)}}
  .box.gold{{background:rgba(217,119,6,0.1)}}
}}

/* ── HOTEL CARDS ───────────────────────────────────────────────────────── */
.hotel-card{{
  background:var(--bg-card);
  border-radius:var(--radius-md);
  padding:0;margin-bottom:12px;
  border:1px solid var(--border);
  box-shadow:var(--shadow-sm);
  overflow:hidden;
}}
.hotel-card-header{{
  padding:14px 16px 12px;
  border-bottom:1px solid var(--divider);
  background:linear-gradient(to right,rgba(0,0,0,0.015),transparent);
}}
.hotel-card-eyebrow{{
  font-size:9.5px;font-weight:700;
  letter-spacing:1px;text-transform:uppercase;
  color:var(--brand);
  margin-bottom:6px;
  display:flex;align-items:center;gap:8px;
}}
.hotel-card-eyebrow-line{{flex:1;height:1px;background:var(--divider)}}
.hotel-card-name{{
  font-family:var(--text-serif);
  font-size:20px;font-weight:500;
  color:var(--text);
  margin-bottom:2px;
  letter-spacing:-0.2px;
  line-height:1.2;
}}
.hotel-card-loc{{
  font-size:11.5px;
  letter-spacing:0.4px;
  text-transform:uppercase;
  color:var(--text-muted);
  margin-bottom:0;
}}
.hotel-card-rating{{
  display:inline-flex;align-items:center;gap:4px;
  background:var(--brand);color:var(--brand-text);
  font-size:11px;font-weight:700;
  padding:2px 9px;border-radius:20px;
  margin-top:8px;
  letter-spacing:0.2px;
}}
.hotel-card-body{{padding:14px 16px}}
.hotel-card-meta{{
  font-size:13.5px;
  color:var(--text-secondary);
  line-height:1.6;
  margin-bottom:14px;
}}
.hotel-card-actions{{display:flex;gap:8px;flex-wrap:wrap}}

/* ── BUTTONS ───────────────────────────────────────────────────────────── */
.btn{{
  display:inline-flex;align-items:center;gap:5px;
  padding:8px 15px;
  border-radius:9px;
  font-family:var(--text-sans);
  font-size:12px;font-weight:600;
  letter-spacing:0.2px;
  border:1px solid var(--border-strong);
  cursor:pointer;text-decoration:none;
  background:var(--bg-card);color:var(--text);
  transition:all 0.15s;
  touch-action:manipulation;
}}
.btn:active{{transform:scale(0.96);opacity:0.85}}
.btn-brand{{
  background:var(--brand);color:var(--brand-text);
  border-color:transparent;
  box-shadow:var(--shadow-brand);
}}
.btn-success{{
  background:rgba(39,103,73,0.08);
  color:var(--success);
  border-color:rgba(39,103,73,0.2);
}}
@media(prefers-color-scheme:dark){{.btn-success{{color:#34D399;border-color:rgba(52,211,153,0.2)}}}}

/* ── AUDIO PLAYER ──────────────────────────────────────────────────────── */
.ap-bar-wrap{{
  position:sticky;top:0;z-index:10;
  background:var(--bg-glass);
  backdrop-filter:blur(24px) saturate(180%);
  -webkit-backdrop-filter:blur(24px) saturate(180%);
  border-bottom:1px solid var(--border);
  padding:16px 18px 18px;
}}
.ap-title{{
  font-family:var(--text-serif);
  font-size:16px;font-weight:400;
  color:var(--text);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.ap-sub{{
  font-size:11px;
  letter-spacing:0.3px;text-transform:uppercase;
  color:var(--text-muted);
  margin-top:2px;margin-bottom:10px;
}}
.ap-progress-wrap{{
  display:flex;align-items:center;gap:8px;
  margin-bottom:14px;
}}
.ap-time{{
  font-size:11px;font-weight:600;
  color:var(--text-muted);
  font-variant-numeric:tabular-nums;
  min-width:32px;
}}
.ap-progress{{
  flex:1;height:3px;
  background:var(--border);
  border-radius:2px;
  cursor:pointer;
  position:relative;
}}
.ap-progress-fill{{
  height:100%;background:var(--brand);
  border-radius:2px;width:0%;
  pointer-events:none;
  transition:width 0.1s linear;
}}
.ap-controls{{display:flex;justify-content:center;align-items:center;gap:20px}}
.ap-ctrl-btn{{
  background:none;border:none;cursor:pointer;
  padding:6px;touch-action:manipulation;
  transition:transform 0.15s,opacity 0.15s;
  color:var(--text-secondary);
  font-size:20px;line-height:1;
}}
.ap-ctrl-btn:active{{transform:scale(0.88);opacity:0.7}}
.ap-play-btn{{
  width:56px;height:56px;border-radius:28px;
  background:var(--brand);color:var(--brand-text);
  border:none;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  font-size:22px;
  box-shadow:var(--shadow-brand);
  transition:transform 0.15s,box-shadow 0.15s;
  touch-action:manipulation;
}}
.ap-play-btn:active{{transform:scale(0.93);box-shadow:none}}
.ap-speed-btn{{
  background:var(--bg);border:1px solid var(--border);
  border-radius:8px;padding:4px 9px;
  font-size:12px;font-weight:700;cursor:pointer;
  color:var(--text);letter-spacing:0.2px;
  transition:all 0.15s;
}}
.ap-chapter-item{{
  display:flex;align-items:center;gap:14px;
  padding:13px 2px;
  border-bottom:1px solid var(--divider);
  cursor:pointer;
  touch-action:manipulation;
  transition:opacity 0.15s;
}}
.ap-chapter-item:last-child{{border-bottom:none}}
.ap-chapter-item:active{{opacity:0.65}}
.ap-chapter-icon{{
  width:38px;height:38px;
  border-radius:10px;
  border:1.5px solid var(--border-strong);
  display:flex;align-items:center;justify-content:center;
  flex-shrink:0;
  font-size:16px;
  transition:all 0.2s;
}}
.ap-chapter-icon.playing{{
  background:var(--brand);
  border-color:transparent;
  color:var(--brand-text);
}}
.ap-chapter-title{{
  font-family:var(--text-serif);
  font-size:15px;font-weight:400;
  color:var(--text);
  transition:color 0.2s;
}}
.ap-chapter-title.playing{{color:var(--brand)}}
.ap-chapter-date{{
  font-size:11px;
  letter-spacing:0.2px;text-transform:uppercase;
  color:var(--text-muted);
  margin-top:2px;
}}

/* ── FLIGHTS ───────────────────────────────────────────────────────────── */
.flight-card{{
  background:var(--bg-card);border:1px solid var(--border);
  border-radius:var(--radius-md);padding:16px;
  margin-bottom:10px;box-shadow:var(--shadow-sm);
}}
.flight-card-type{{
  font-size:10px;font-weight:700;letter-spacing:0.8px;text-transform:uppercase;
  color:var(--brand);margin-bottom:10px;
}}
.flight-route{{
  display:flex;align-items:center;gap:10px;margin-bottom:10px;
}}
.flight-iata{{
  font-size:22px;font-weight:700;font-family:var(--text-sans);color:var(--text);line-height:1;
}}
.flight-iata-label{{font-size:11px;color:var(--text-muted);margin-top:2px}}
.flight-arrow{{
  flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;
}}
.flight-arrow-line{{height:1px;width:100%;background:var(--border);position:relative}}
.flight-arrow-line::after{{content:'›';position:absolute;right:-4px;top:-8px;font-size:14px;color:var(--text-muted)}}
.flight-duration{{font-size:10px;color:var(--text-muted);letter-spacing:0.3px}}
.flight-meta{{
  display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary);
  border-top:1px solid var(--border);padding-top:10px;margin-top:2px;
}}
.flight-meta span{{display:flex;align-items:center;gap:4px}}
.flight-notes{{font-size:12px;color:var(--text-muted);margin-top:8px;font-style:italic}}

/* ── SOS ───────────────────────────────────────────────────────────────── */
.sos-btn-huge{{
  width:100%;padding:16px 20px;
  border-radius:var(--radius-md);
  background:var(--brand);color:var(--brand-text);
  font-family:var(--text-sans);
  font-size:15px;font-weight:700;
  letter-spacing:0.3px;
  border:none;cursor:pointer;margin-bottom:16px;
  box-shadow:var(--shadow-brand);
  touch-action:manipulation;
  display:flex;align-items:center;justify-content:center;gap:8px;
}}
.sos-btn-huge:active{{transform:scale(0.98)}}
.list-group{{
  background:var(--bg-card);
  border-radius:var(--radius-md);
  overflow:hidden;
  margin-bottom:12px;
  border:1px solid var(--border);
  box-shadow:var(--shadow-xs);
}}
.phone-row{{
  display:flex;align-items:center;
  padding:10px 14px;
  border-bottom:1px solid var(--divider);
  gap:10px;
}}
.phone-row:last-child{{border-bottom:none}}
.phone-info{{flex:1;min-width:0}}
.phone-name{{font-weight:600;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.phone-desc{{font-size:11.5px;color:var(--text-muted);margin-top:1px}}
.call-btn{{
  background:transparent;
  color:var(--accent);
  border:1.5px solid var(--accent);
  padding:6px 14px;border-radius:20px;
  display:inline-flex;align-items:center;gap:4px;
  font-size:12.5px;font-weight:600;text-decoration:none;
  white-space:nowrap;
  transition:background 0.15s,color 0.15s;
  touch-action:manipulation;
  flex-shrink:0;
}}
.call-btn:active,.call-btn:hover{{
  background:var(--accent);color:#fff;
}}
.list-header{{
  font-size:10.5px;font-weight:600;
  letter-spacing:0.8px;text-transform:uppercase;
  color:var(--text-muted);
  padding:14px 2px 5px;
}}
.list-header:first-child{{padding-top:2px}}

/* ── LOOMTRIP BADGE ────────────────────────────────────────────────────── */
.loomtrip-badge{{
  position:fixed;
  bottom:calc(var(--tab-h) + 10px);right:14px;
  background:rgba(12,10,9,0.5);color:rgba(255,255,255,0.8);
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  font-size:10px;font-weight:500;
  letter-spacing:0.3px;
  padding:4px 10px;border-radius:20px;
  z-index:99;pointer-events:none;
}}

/* ── MISC ──────────────────────────────────────────────────────────────── */
.small{{font-size:13px}}.muted{{color:var(--text-muted)}}.mb-0{{margin-bottom:0}}
@media(prefers-reduced-motion:reduce){{
  *{{transition-duration:0.01ms !important;animation-duration:0.01ms !important}}
}}
</style>
</head>
<body>
<header class="app-header">
  <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1;overflow:hidden;">
    {"<img src='" + logo_url + "' style='height:26px;border-radius:5px;flex-shrink:0;' alt=''>" if logo_url else ""}
    <div class="app-header-brand">{dmc_name}</div>
  </div>
  <div class="app-header-trip"><strong>{destination}</strong><br>{trip_subtitle}</div>
</header>
<div class="app-body">

  <!-- DAYS TAB -->
  <section class="tab-panel active no-padding" id="tab-days">
    <div class="cal-strip" id="calendar-strip"></div>
    {"<div style='padding:14px 16px 0'><div class='box gold' style='margin-bottom:0;padding:12px 16px;'><span style='font-size:13px;'>👋 </span><strong>" + client_name + "</strong> — " + t.get('welcome','welcome to your trip') + "</div></div>" if client_name else ""}
    <div id="days-list"></div>
  </section>

  <!-- HOTELS TAB -->
  <section class="tab-panel" id="tab-hotels">
    <div id="hotels-list"></div>
  </section>
{audio_tab_html}
  <!-- INFO TAB -->
  <section class="tab-panel" id="tab-info">
    <div class="card"><h3>✈️ {trip_title}</h3><p>{trip_subtitle}</p>{"<p class='small' style='margin-top:4px;'><strong>" + client_name + "</strong></p>" if client_name else ""}<p class="small muted mb-0">{t["crafted"]} {dmc_name}</p></div>
    <div id="flights-section"></div>
    <div class="box gold" style="margin-top:0;"><div class="box-title">🌟 {t["your_team"]}</div>{dmc_name} is with you 24/7.{"<br><br><a href='tel:" + dmc_phone + "' class='btn btn-brand' style='margin-top:8px;'>📞 " + t["call"] + "</a>" if dmc_phone else ""}</div>
    <div class="card"><h3>📱 {t["this_app"]}</h3><p class="small">{t["app_desc"]}</p></div>
  </section>

  <!-- SOS TAB -->
  <section class="tab-panel" id="tab-sos">
    <div id="sos-content"></div>
  </section>

</div>
<nav class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('days')"   id="btn-days"><span class="tab-icon">📅</span><span>{t["days"]}</span></button>
  <button class="tab-btn"        onclick="switchTab('hotels')" id="btn-hotels"><span class="tab-icon">🏨</span><span>{t["hotels"]}</span></button>
{audio_tab_btn}
  <button class="tab-btn"        onclick="switchTab('info')"   id="btn-info"><span class="tab-icon">ℹ️</span><span>{t["info"]}</span></button>
  <button class="tab-btn"        onclick="switchTab('sos')"    id="btn-sos"><span class="tab-icon">🚨</span><span>{t["sos"]}</span></button>
</nav>
<div class="loomtrip-badge">✨ {t["powered"]}</div>

<script>
const HOTELS   = {hotels_js};
const DAYS     = {days_js};
const CONTACTS = {contacts_js};
const FLIGHTS  = {flights_js};
const M = q => 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(q);

function switchTab(id) {{
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  document.getElementById('btn-'+id).classList.add('active');
}}
function openDay(n) {{
  const c = document.querySelector('[data-n="'+n+'"]');
  if (!c) return;
  c.classList.add('open');
  // highlight calendar button
  document.querySelectorAll('.cal-day').forEach(b=>b.classList.remove('active'));
  const cb=document.getElementById('cal-'+n);
  if(cb){{cb.classList.add('active');cb.scrollIntoView({{behavior:'smooth',block:'nearest',inline:'center'}});}}
  setTimeout(()=>c.scrollIntoView({{behavior:'smooth',block:'nearest'}}),50);
}}
function renderDays() {{
  const MONTHS={{'jan':'JAN','feb':'FEB','mar':'MAR','apr':'APR','may':'MAY','jun':'JUN','jul':'JUL','aug':'AUG','sep':'SEP','oct':'OCT','nov':'NOV','dec':'DEC',
    'gennaio':'GEN','febbraio':'FEB','marzo':'MAR','aprile':'APR','maggio':'MAG','giugno':'GIU','luglio':'LUG','agosto':'AGO','settembre':'SET','ottobre':'OTT','novembre':'NOV','dicembre':'DIC',
    'janvier':'JAN','février':'FÉV','mars':'MAR','avril':'AVR','mai':'MAI','juin':'JUI','juillet':'JUI','août':'AOÛ','septembre':'SEP','octobre':'OCT','novembre':'NOV','décembre':'DÉC'}};
  let strip='';
  DAYS.forEach((d,i)=>{{
    const parts=(d.date||'').split(/\\s+/);
    const num=parts.find(p=>/^\\d+$/.test(p))||(i+1);
    const monRaw=(parts.find(p=>/^[a-zA-Zéû]+$/.test(p)&&p.length>2&&!/^(mon|tue|wed|thu|fri|sat|sun|lun|mar|mer|jeu|ven|sam|dim)$/i.test(p))||'').toLowerCase();
    const mon=MONTHS[monRaw]||monRaw.slice(0,3).toUpperCase()||'';
    strip+=`<button class="cal-day" id="cal-${{d.n}}" onclick="openDay(${{d.n}})"><span class="cal-day-num">${{num}}</span>${{mon?`<span class="cal-day-mon">${{mon}}</span>`:''}}</button>`;
  }});
  document.getElementById('calendar-strip').innerHTML=strip;
  let html='';
  DAYS.forEach(day=>{{
    const hotel=day.hotel_n?HOTELS.find(h=>h.n===day.hotel_n):null;
    const hb=hotel?`
      <div class="hotel-card" style="margin-top:14px;">
        <div class="hotel-card-header">
          <div class="hotel-card-eyebrow">{t["tonight"]}<span class="hotel-card-eyebrow-line"></span></div>
          <div class="hotel-card-name">${{hotel.name}}</div>
          <div class="hotel-card-loc">${{hotel.loc}}</div>
          ${{hotel.rating?`<div class="hotel-card-rating">★ ${{hotel.rating}}</div>`:''}}
        </div>
        <div class="hotel-card-body">
          <div class="hotel-card-meta">${{hotel.meta}}</div>
          <div class="hotel-card-actions">
            <a class="btn" href="${{M(hotel.map_query)}}" target="_blank">{t["map"]}</a>
            ${{hotel.phone?`<a class="btn btn-success" href="tel:${{hotel.phone}}">{t["call"]}</a>`:''}}
          </div>
        </div>
      </div>`:'';
    const tl=(day.timeline||[]).map(([tm,c])=>`
      <li><span class="timeline-time">${{tm}}</span><span class="timeline-dot"></span><span class="timeline-content">${{c}}</span></li>`
    ).join('');
    const bx=(day.boxes||[]).map(b=>`
      <div class="box ${{b.type}}"><div class="box-title">${{b.title}}</div><div class="box-body">${{b.text}}</div></div>`
    ).join('');
    html+=`
    <div class="day-card" data-n="${{day.n}}">
      <button class="day-card-summary" onclick="const c=this.parentElement;c.classList.toggle('open');">
        <div class="day-num-badge"><span class="day-num-badge-inner">${{String(day.n).padStart(2,'0')}}</span></div>
        <div class="day-info">
          <div class="day-info-title">${{day.title}}</div>
          <div class="day-info-meta">${{day.date}} · ${{day.stats}}</div>
        </div>
        <span class="day-chevron">›</span>
      </button>
      <div class="day-card-body">
        ${{hb}}
        <div class="tl-label">{t["itinerary"]}</div>
        <ul class="timeline">${{tl}}</ul>
        ${{bx}}
      </div>
    </div>`;
  }});
  document.getElementById('days-list').innerHTML=html;
  linkAttractions();
  setTimeout(()=>openDay(1),100);
}}

function linkAttractions() {{
  // Auto-wrap <strong> tags in timeline items with Google Maps search links
  const dest = encodeURIComponent("{destination}");
  document.querySelectorAll('.timeline-content strong').forEach(el => {{
    const name = el.textContent.trim();
    if (!name || name.length < 3) return;
    const query = encodeURIComponent(name + ', ' + "{destination}");
    const a = document.createElement('a');
    a.href = `https://www.google.com/maps/search/?api=1&query=${{query}}`;
    a.target = '_blank';
    a.rel = 'noopener';
    a.className = 'attr-link';
    a.innerHTML = el.innerHTML;
    el.parentNode.replaceChild(a, el);
  }});
}}
function renderHotels() {{
  document.getElementById('hotels-list').innerHTML=HOTELS.map((h,i)=>`
    <div class="hotel-card">
      <div class="hotel-card-header">
        <div class="hotel-card-eyebrow">Night ${{h.n}}<span class="hotel-card-eyebrow-line"></span>${{h.dates}}</div>
        <div class="hotel-card-name">${{h.name}}</div>
        <div class="hotel-card-loc">${{h.loc}}</div>
        ${{h.rating?`<div class="hotel-card-rating">★ ${{h.rating}}</div>`:''}}
      </div>
      <div class="hotel-card-body">
        <div class="hotel-card-meta">${{h.meta}}<br><span style="font-size:11.5px;color:var(--text-muted);display:block;margin-top:6px;letter-spacing:0.1px;">📍 ${{h.addr}}</span></div>
        <div class="hotel-card-actions">
          <a class="btn" href="${{M(h.map_query)}}" target="_blank">{t["map"]}</a>
          ${{h.phone?`<a class="btn btn-success" href="tel:${{h.phone}}">{t["call"]}</a>`:''}}
          ${{h.web?`<a class="btn" href="https://${{h.web}}" target="_blank">{t["web"]}</a>`:''}}
        </div>
      </div>
    </div>`).join('');
}}
function renderSOS() {{
  document.getElementById('sos-content').innerHTML=`
    <button class="sos-btn-huge" onclick="if(confirm('Call {emerg_num}?'))window.location.href='tel:{emerg_num}';">{t["emergency"]} {emerg_num}</button>
    ${{CONTACTS.length?CONTACTS.map(g=>`
      <div class="list-header">${{g.group}}</div>
      <div style="font-size:12px;color:var(--text-muted);padding:0 16px 6px">${{g.org}}${{g.addr?` · ${{g.addr}}`:''}}</div>
      <div class="list-group">${{g.entries.map(e=>{{
        const isWA = e.label && /whatsapp/i.test(e.label);
        const waNum = e.phone ? e.phone.replace(/[^0-9]/g,'') : '';
        return `<div class="phone-row">
          <div class="phone-info">
            <div class="phone-name">${{e.label}}</div>
            <div class="phone-desc">${{e.phone_display}}</div>
          </div>
          <div style="display:flex;gap:6px;flex-shrink:0;">
            ${{isWA ? `<a class="call-btn" href="https://wa.me/${{waNum}}" target="_blank" style="background:rgba(37,211,102,0.12);border-color:rgba(37,211,102,0.4);color:#1a9e50;">💬 WhatsApp</a>` : ''}}
            <a class="call-btn" href="tel:${{e.phone}}">📞 {t["call"].replace("📞 ","")}</a>
          </div>
        </div>`;
      }}).join('')}}
      </div>`).join(''):`<div class="list-header">{dmc_name}</div><div class="list-group"><div class="phone-row"><div class="phone-info"><div class="phone-name">{dmc_name} — 24h</div></div>${{'{dmc_phone}'?`<a class="call-btn" href="tel:{dmc_phone}">📞 {t["call"].replace("📞 ","")}</a>`:''}}
</div></div>`}}
    <div class="list-header">Hotels</div>
    <div class="list-group">${{HOTELS.filter(h=>h.phone).map(h=>`<div class="phone-row"><div class="phone-info"><div class="phone-name">${{h.name}}</div><div class="phone-desc">Night ${{h.n}} · ${{h.loc}}</div></div><a class="call-btn" href="tel:${{h.phone}}">📞 {t["call"].replace("📞 ","")}</a></div>`).join('')}}</div>
    <div class="box warn" style="margin-top:16px"><div class="box-title">⚠️ Emergency</div><div class="box-body">{emerg_num} works without SIM credit. English-speaking operators 24/7.</div></div>`;
}}

function renderFlights() {{
  if(!FLIGHTS||!FLIGHTS.length) return;
  const labels={{'outbound':'{t["outbound"]} Flight','return':'{t["return_f"]} Flight'}};
  document.getElementById('flights-section').innerHTML=
    `<div style="margin-bottom:10px"><div class="list-header" style="padding:0 0 8px">{t["flights"]}</div>`+
    FLIGHTS.map(f=>`
      <div class="flight-card">
        <div class="flight-card-type">${{labels[f.type]||'✈️ Flight'}}</div>
        <div class="flight-route">
          <div style="text-align:center">
            <div class="flight-iata">${{(f.from||'').match(/[A-Z]{{3}}/)?.[0]||f.from||'—'}}</div>
            <div class="flight-iata-label">${{f.depart||''}}</div>
          </div>
          <div class="flight-arrow">
            <div class="flight-duration">${{f.duration||''}}</div>
            <div class="flight-arrow-line"></div>
            <div class="flight-duration">${{f.flight||''}}</div>
          </div>
          <div style="text-align:center">
            <div class="flight-iata">${{(f.to||'').match(/[A-Z]{{3}}/)?.[0]||f.to||'—'}}</div>
            <div class="flight-iata-label">${{f.arrive||''}}</div>
          </div>
        </div>
        <div class="flight-meta">
          <span>📅 ${{f.date||''}}</span>
          <span>🛫 ${{f.from||''}}</span>
          <span>🛬 ${{f.to||''}}</span>
        </div>
        ${{f.notes?`<div class="flight-notes">ℹ️ ${{f.notes}}</div>`:''}}
      </div>`).join('')+`</div>`;
}}

renderDays();
renderHotels();
renderFlights();
renderSOS();

{audio_js}
</script>
</body>
</html>"""

# ─── STEP 5 — Write index.html ────────────────────────────────────────────────
index_path = output_dir / "index.html"
index_path.write_text(HTML, encoding="utf-8")
size_kb = index_path.stat().st_size // 1024

print(f"\n{'─'*50}")
print(f"✅  Demo ready: {output_dir}/")
print(f"    index.html  ({size_kb} KB)")
for mp3 in mp3_files:
    sz = (output_dir/mp3).stat().st_size//1024
    print(f"    {mp3}  ({sz} KB)")
print(f"{'─'*50}")
print(f"    DMC    : {dmc_name}")
print(f"    Trip   : {trip_title}")
print(f"    Colour : {brand_color}")
print(f"    Days   : {len(days)}  |  Hotels : {len(hotels)}  |  Audio : {len(mp3_files)} chapters")
print(f"    Voice  : {voice}")
if audio_chapters:
    print(f"\n🎙️  Open {output_dir}/index.html → tap 🎙️ Audio tab to test")
print(f"\n🎬  Screen-record your Loom and send to {dmc_name}!")
