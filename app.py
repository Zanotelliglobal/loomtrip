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
from functools import wraps
import anthropic, httpx

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload limit

BASE_DIR   = Path(__file__).parent
BUILDS_DIR = BASE_DIR / "builds"
BUILDS_DIR.mkdir(exist_ok=True)

# ─── Admin auth ───────────────────────────────────────────────────────────────
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "")

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not ADMIN_PASS:
            return f(*args, **kwargs)  # no password set → open (local dev)
        auth = request.authorization
        if not auth or auth.username != ADMIN_USER or auth.password != ADMIN_PASS:
            return Response("Unauthorised", 401,
                            {"WWW-Authenticate": 'Basic realm="Loomtrip Admin"'})
        return f(*args, **kwargs)
    return decorated

# ─── in-memory job store ─────────────────────────────────────────────────────
jobs = {}  # job_id → {status, log, output_dir}

# ─── HTML template ───────────────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loomtrip — Studio</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Playfair+Display:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --sidebar-bg: #0F0E0D;
  --sidebar-border: rgba(255,255,255,0.07);
  --sidebar-text: rgba(255,255,255,0.88);
  --sidebar-muted: rgba(255,255,255,0.38);
  --sidebar-input-bg: rgba(255,255,255,0.06);
  --sidebar-input-border: rgba(255,255,255,0.10);
  --sidebar-input-focus: rgba(255,255,255,0.22);
  --accent: #C4873A;
  --accent-dim: rgba(196,135,58,0.15);
  --main-bg: #F7F5F2;
  --card: #FFFFFF;
  --border: rgba(0,0,0,0.08);
  --text: #18160F;
  --muted: #8A8278;
  --success: #2A7A4E;
  --danger: #C0392B;
  --serif: 'Playfair Display', Georgia, serif;
  --sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --radius: 12px;
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  --shadow-md: 0 4px 16px rgba(0,0,0,0.08), 0 1px 4px rgba(0,0,0,0.04);
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 15px; }
body { font-family: var(--sans); background: var(--main-bg); color: var(--text); line-height: 1.55; -webkit-font-smoothing: antialiased; display: flex; min-height: 100vh; }

/* ── SIDEBAR ── */
.sidebar {
  width: 380px; min-width: 380px; background: var(--sidebar-bg);
  display: flex; flex-direction: column;
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
  border-right: 1px solid var(--sidebar-border);
}
.sidebar-header {
  padding: 28px 28px 20px;
  border-bottom: 1px solid var(--sidebar-border);
}
.wordmark {
  font-family: var(--serif); font-size: 22px; font-weight: 400;
  color: #fff; letter-spacing: -0.3px; margin-bottom: 2px;
}
.wordmark-sub {
  font-size: 10.5px; letter-spacing: 2px; text-transform: uppercase;
  color: var(--sidebar-muted); font-weight: 500;
}
.sidebar-body { padding: 24px 28px; flex: 1; }
.sidebar-footer { padding: 20px 28px; border-top: 1px solid var(--sidebar-border); }

.section { margin-bottom: 22px; }
.section:last-child { margin-bottom: 0; }

label {
  display: block; font-size: 10.5px; font-weight: 600;
  letter-spacing: 1.2px; text-transform: uppercase;
  color: var(--sidebar-muted); margin-bottom: 7px;
}

/* inputs inside sidebar */
.sidebar input[type=text],
.sidebar select,
.sidebar textarea {
  width: 100%; padding: 10px 12px;
  background: var(--sidebar-input-bg);
  border: 1px solid var(--sidebar-input-border);
  border-radius: 8px;
  font-family: var(--sans); font-size: 13.5px;
  color: var(--sidebar-text); outline: none;
  transition: border-color 0.15s, background 0.15s;
  -webkit-appearance: none;
}
.sidebar input[type=text]::placeholder,
.sidebar textarea::placeholder { color: var(--sidebar-muted); }
.sidebar input[type=text]:focus,
.sidebar select:focus,
.sidebar textarea:focus {
  border-color: var(--sidebar-input-focus);
  background: rgba(255,255,255,0.09);
}
.sidebar select option { background: #1a1814; color: #fff; }
.sidebar textarea { resize: vertical; min-height: 110px; line-height: 1.5; }

/* drop zone */
.drop-zone {
  border: 1.5px dashed rgba(255,255,255,0.14);
  border-radius: var(--radius); padding: 22px 16px;
  text-align: center; cursor: pointer;
  transition: all 0.18s; background: rgba(255,255,255,0.03);
}
.drop-zone:hover, .drop-zone.over {
  border-color: var(--accent);
  background: var(--accent-dim);
}
.drop-zone-icon { font-size: 26px; margin-bottom: 6px; opacity: 0.7; }
.drop-zone-text { font-size: 12.5px; color: var(--sidebar-muted); }
.drop-zone-text strong { color: var(--accent); font-weight: 500; }
#pdf-input { display: none; }

/* pill */
.pill {
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--accent-dim); color: var(--accent);
  font-size: 11.5px; font-weight: 500; padding: 5px 11px;
  border-radius: 20px; border: 1px solid rgba(196,135,58,0.25);
}
.pill-remove { cursor: pointer; opacity: 0.55; font-size: 15px; line-height: 1; }
.pill-remove:hover { opacity: 1; }

/* divider */
.divider {
  display: flex; align-items: center; gap: 10px;
  margin: 18px 0;
}
.divider-line { flex: 1; height: 1px; background: var(--sidebar-border); }
.divider-text {
  font-size: 10px; color: var(--sidebar-muted);
  letter-spacing: 1px; text-transform: uppercase;
}

/* color row */
.color-row { display: flex; gap: 8px; align-items: center; }
.color-row input[type=color] {
  width: 40px; height: 38px; padding: 3px;
  border: 1px solid var(--sidebar-input-border);
  border-radius: 8px; background: var(--sidebar-input-bg);
  cursor: pointer;
}
.color-swatch { display: flex; gap: 7px; flex-wrap: wrap; margin-top: 9px; }
.swatch {
  width: 26px; height: 26px; border-radius: 6px;
  cursor: pointer; border: 2px solid transparent;
  transition: transform 0.12s, border-color 0.12s;
}
.swatch:hover { transform: scale(1.18); }
.swatch.active { border-color: rgba(255,255,255,0.7); }

/* toggle row */
.toggle-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 11px 13px; border-radius: 8px;
  background: var(--sidebar-input-bg);
  border: 1px solid var(--sidebar-input-border);
  cursor: pointer; user-select: none;
}
.toggle-row-label { font-size: 13px; color: var(--sidebar-text); }
.toggle {
  width: 36px; height: 20px; background: rgba(255,255,255,0.15);
  border-radius: 10px; position: relative;
  transition: background 0.2s; flex-shrink: 0;
}
.toggle.on { background: var(--accent); }
.toggle::after {
  content: ''; position: absolute; top: 2px; left: 2px;
  width: 16px; height: 16px; border-radius: 50%;
  background: #fff; transition: transform 0.2s;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
}
.toggle.on::after { transform: translateX(16px); }
#no-audio { display: none; }

/* generate button */
.btn-generate {
  width: 100%; padding: 13px 20px;
  background: var(--accent); color: #fff;
  border: none; border-radius: 10px;
  font-family: var(--sans); font-size: 14px; font-weight: 600;
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  gap: 8px; transition: filter 0.15s, transform 0.12s;
  letter-spacing: 0.1px;
}
.btn-generate:hover { filter: brightness(1.08); }
.btn-generate:active { transform: scale(0.98); }
.btn-generate:disabled { opacity: 0.45; cursor: not-allowed; filter: none; transform: none; }

/* ── MAIN ── */
.main { flex: 1; padding: 36px 40px; overflow-y: auto; }

.page-header {
  display: flex; align-items: baseline; justify-content: space-between;
  margin-bottom: 28px;
}
.page-title { font-family: var(--serif); font-size: 26px; font-weight: 400; color: var(--text); }
.page-count { font-size: 12px; color: var(--muted); letter-spacing: 0.3px; }

/* log section */
.log-section { margin-bottom: 36px; }
.log-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px;
}
.log-title { font-size: 12px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase; color: var(--muted); }
.progress-bar { height: 2px; background: var(--border); border-radius: 2px; overflow: hidden; margin-bottom: 12px; }
.progress-fill { height: 100%; background: var(--accent); border-radius: 2px; width: 0%; transition: width 0.5s ease; }
.log-box {
  background: #111009; color: #C9C6C1;
  border-radius: var(--radius); padding: 16px 18px;
  font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
  font-size: 11.5px; line-height: 1.75;
  min-height: 180px; max-height: 340px; overflow-y: auto;
  border: 1px solid rgba(255,255,255,0.06);
  position: relative;
}
.log-line { display: block; }
.log-ok   { color: #4ADE80; }
.log-err  { color: #F87171; }
.log-info { color: #FCD34D; }

/* waiting indicator */
.log-waiting {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 14px;
}
.log-spinner {
  width: 28px; height: 28px;
  border: 2.5px solid rgba(255,255,255,0.08);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.75s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.log-waiting-text {
  font-family: var(--sans); font-size: 12px;
  color: rgba(255,255,255,0.3); letter-spacing: 0.3px;
}
.log-dots::after {
  content: '';
  animation: dots 1.4s steps(4, end) infinite;
}
@keyframes dots {
  0%   { content: ''; }
  25%  { content: '.'; }
  50%  { content: '..'; }
  75%  { content: '...'; }
  100% { content: ''; }
}

/* ── GMAIL / EXCEL TABLE ── */
.trip-table { width: 100%; border-collapse: collapse; }
.trip-table-head {
  display: grid;
  grid-template-columns: 32px 1fr 160px 56px 80px 90px 32px;
  padding: 0 12px 0 8px;
  border-bottom: 2px solid var(--border);
  margin-bottom: 0;
}
.trip-table-head-cell {
  font-size: 10.5px; font-weight: 600; letter-spacing: 0.4px;
  text-transform: uppercase; color: var(--muted);
  padding: 6px 8px 6px 0;
  white-space: nowrap; overflow: hidden;
}
.trip-row {
  display: grid;
  grid-template-columns: 32px 1fr 160px 56px 80px 90px 32px;
  align-items: center;
  padding: 0 12px 0 8px;
  border-bottom: 1px solid var(--border);
  cursor: default;
  transition: background 0.1s;
  position: relative;
  min-height: 48px;
}
.trip-row:hover { background: rgba(196,135,58,0.04); }
.trip-row:hover .row-actions { opacity: 1; }
.trip-row:hover .trip-col-date { opacity: 0; }
.trip-row-cb { display: flex; align-items: center; }
.trip-row-cb input[type=checkbox] {
  width: 16px; height: 16px; cursor: pointer; accent-color: var(--accent);
}
.trip-col { padding: 10px 8px 10px 0; overflow: hidden; }
.trip-col-name {
  font-size: 13.5px; font-weight: 500; color: var(--text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.trip-col-sub {
  font-size: 11px; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  margin-top: 1px;
}
.trip-col-client {
  font-size: 12.5px; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.lang-chip {
  display: inline-block;
  font-size: 10px; font-weight: 700; letter-spacing: 0.5px;
  text-transform: uppercase;
  padding: 2px 7px; border-radius: 4px;
  background: rgba(0,0,0,0.05); color: var(--muted);
}
.trip-col-date { font-size: 11.5px; color: var(--muted); white-space: nowrap; }
.code-badge {
  display: inline-flex; align-items: center; gap: 4px;
  background: var(--accent-dim); color: var(--accent);
  font-size: 10px; font-weight: 700; letter-spacing: 1.2px;
  padding: 2px 7px; border-radius: 4px;
  cursor: pointer; white-space: nowrap;
  transition: background 0.12s;
}
.code-badge:hover { background: rgba(196,135,58,0.28); }
/* Action icons appear on row hover */
.row-actions {
  position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  display: flex; gap: 2px; align-items: center;
  opacity: 0; transition: opacity 0.12s;
  background: var(--main-bg);
  padding: 0 4px 0 12px;
}
.row-action-btn {
  display: flex; align-items: center; justify-content: center;
  width: 30px; height: 30px; border-radius: 6px;
  border: none; background: none; cursor: pointer; color: var(--muted);
  text-decoration: none;
  transition: background 0.12s, color 0.12s;
}
.row-action-btn:hover { background: var(--border); color: var(--text); }
.row-action-btn.danger:hover { background: rgba(192,57,43,0.1); color: var(--danger); }
.row-action-btn svg { width: 15px; height: 15px; stroke: currentColor; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.row-action-btn[title]:hover::after {
  content: attr(title);
  position: absolute; bottom: calc(100% + 4px); left: 50%; transform: translateX(-50%);
  background: #222; color: #fff; font-size: 11px; padding: 3px 8px;
  border-radius: 5px; white-space: nowrap; pointer-events: none; z-index: 10;
}
.row-action-btn { position: relative; }

/* filter toolbar — spreadsheet style */
.filter-toolbar {
  display: flex; align-items: center; gap: 0;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--card); overflow: hidden;
  margin-bottom: 16px; box-shadow: var(--shadow-sm);
}
.filter-search-wrap { flex: 1; position: relative; }
.filter-search-icon {
  position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
  width: 14px; height: 14px; color: var(--muted); pointer-events: none;
}
.filter-search {
  width: 100%; padding: 9px 12px 9px 34px;
  border: none; border-right: 1px solid var(--border);
  font-family: var(--sans); font-size: 13px; color: var(--text);
  background: transparent; outline: none;
}
.filter-search::placeholder { color: var(--muted); }
.filter-divider { width: 1px; background: var(--border); align-self: stretch; }
.filter-select {
  padding: 9px 28px 9px 10px; border: none; border-right: 1px solid var(--border);
  font-family: var(--sans); font-size: 12.5px; color: var(--muted);
  background: transparent; outline: none; cursor: pointer;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='11' viewBox='0 0 24 24' fill='none' stroke='%238A8278' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 8px center;
}
.filter-select:last-child { border-right: none; }

/* empty state */
.empty-state { text-align: center; padding: 80px 20px; color: var(--muted); }
.empty-icon { font-size: 44px; margin-bottom: 14px; opacity: 0.25; }
.empty-title { font-family: var(--serif); font-size: 20px; font-weight: 400; color: var(--text); margin-bottom: 6px; opacity: 0.5; }
.meta-dot { opacity: 0.35; }

/* filter bar */
.filter-bar {
  display: flex; gap: 10px; align-items: center;
  margin-bottom: 20px; flex-wrap: wrap;
}
.filter-search-wrap {
  flex: 1; min-width: 200px; position: relative;
}
.filter-search-icon {
  position: absolute; left: 11px; top: 50%; transform: translateY(-50%);
  width: 15px; height: 15px; color: var(--muted); pointer-events: none;
}
.filter-search {
  width: 100%; padding: 9px 12px 9px 34px;
  border: 1px solid var(--border); border-radius: 8px;
  font-family: var(--sans); font-size: 13.5px; color: var(--text);
  background: var(--card); outline: none;
  transition: border-color 0.15s;
}
.filter-search:focus { border-color: var(--accent); }
.filter-select {
  padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px;
  font-family: var(--sans); font-size: 13px; color: var(--text);
  background: var(--card); outline: none; cursor: pointer;
  transition: border-color 0.15s; -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238A8278' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 10px center;
  padding-right: 30px;
}
.filter-select:focus { border-color: var(--accent); }

/* empty state */
.empty-state {
  text-align: center; padding: 80px 20px; color: var(--muted);
}
.empty-icon { font-size: 44px; margin-bottom: 14px; opacity: 0.25; }
.empty-title { font-family: var(--serif); font-size: 20px; font-weight: 400; color: var(--text); margin-bottom: 6px; opacity: 0.5; }
.empty-sub { font-size: 13px; opacity: 0.7; }

@media(max-width: 860px) {
  body { flex-direction: column; }
  .sidebar { width: 100%; min-width: unset; height: auto; position: static; }
  .main { padding: 24px 20px; }
}
</style>
</head>
<body>

<!-- ── SIDEBAR ── -->
<aside class="sidebar">
  <div class="sidebar-header">
    <div class="wordmark">Loomtrip</div>
    <div style="display:flex;align-items:center;justify-content:space-between;">
      <div class="wordmark-sub" data-i18n="studio">Trip Studio</div>
      <div id="admin-lang-switcher" style="display:flex;gap:2px;background:rgba(255,255,255,0.06);border-radius:6px;padding:2px;">
        <button onclick="setAdminLang('en')" data-al="en" style="font-size:10px;font-weight:700;letter-spacing:0.5px;padding:3px 7px;border:none;border-radius:4px;cursor:pointer;background:transparent;color:rgba(255,255,255,0.4);transition:all 0.15s;font-family:inherit;">EN</button>
        <button onclick="setAdminLang('it')" data-al="it" style="font-size:10px;font-weight:700;letter-spacing:0.5px;padding:3px 7px;border:none;border-radius:4px;cursor:pointer;background:transparent;color:rgba(255,255,255,0.4);transition:all 0.15s;font-family:inherit;">IT</button>
        <button onclick="setAdminLang('fr')" data-al="fr" style="font-size:10px;font-weight:700;letter-spacing:0.5px;padding:3px 7px;border:none;border-radius:4px;cursor:pointer;background:transparent;color:rgba(255,255,255,0.4);transition:all 0.15s;font-family:inherit;">FR</button>
      </div>
    </div>
  </div>

  <div id="edit-banner" style="display:none;padding:10px 28px;background:rgba(196,135,58,0.12);border-bottom:1px solid rgba(196,135,58,0.2);">
    <div style="font-size:11px;color:var(--accent);font-weight:600;letter-spacing:0.5px;margin-bottom:4px;" data-i18n="editing_trip">EDITING TRIP</div>
    <div style="font-size:12px;color:var(--sidebar-muted);margin-bottom:8px;" id="edit-banner-name"></div>
    <button onclick="clearEdit()" style="font-size:11px;color:var(--sidebar-muted);background:none;border:1px solid rgba(255,255,255,0.12);border-radius:6px;padding:4px 10px;cursor:pointer;font-family:var(--sans);" data-i18n="new_trip_instead">✕ New trip instead</button>
  </div>

  <div class="sidebar-body">

    <div class="section">
      <label data-i18n="lbl_pdf">Itinerary PDF</label>
      <div class="drop-zone" id="drop-zone" onclick="document.getElementById('pdf-input').click()">
        <div class="drop-zone-icon">📄</div>
        <div class="drop-zone-text" data-i18n="drop_pdf">Drop PDF here or <strong>browse</strong></div>
      </div>
      <input type="file" id="pdf-input" accept=".pdf" onchange="handleFile(this.files[0])">
      <div id="pdf-pill" style="display:none;margin-top:6px;"></div>
    </div>

    <div class="divider">
      <div class="divider-line"></div>
      <div class="divider-text" data-i18n="or_paste">or paste text</div>
      <div class="divider-line"></div>
    </div>

    <div class="section">
      <label data-i18n="lbl_text">Itinerary Text</label>
      <textarea id="itin-text" data-i18n-placeholder="placeholder_text" placeholder="Paste the DMC's itinerary here…"></textarea>
    </div>

    <div class="section">
      <label data-i18n="lbl_color">Brand Colour</label>
      <div class="color-row">
        <input type="color" id="color-picker" value="#C4873A" oninput="syncColor(this.value)">
        <input type="text" id="color-hex" value="#C4873A" placeholder="#C4873A" oninput="syncColorFromText(this.value)" maxlength="7">
      </div>
      <div class="color-swatch" id="swatches"></div>
    </div>

    <div class="section">
      <label data-i18n="lbl_trip_name">Trip Name</label>
      <input type="text" id="trip-name" data-i18n-placeholder="placeholder_trip_name" placeholder="e.g. Georgia: Caucasus Highlights">
    </div>

    <div class="section">
      <label data-i18n="lbl_client">Client Name</label>
      <input type="text" id="client-name" data-i18n-placeholder="placeholder_client" placeholder="e.g. The Zanotelli Family">
    </div>

    <div class="section">
      <label data-i18n="lbl_agency">Agency / DMC Name</label>
      <input type="text" id="agency-name" data-i18n-placeholder="placeholder_agency" placeholder="Leave blank to auto-detect from PDF">
    </div>

    <div class="section">
      <label data-i18n="lbl_logo">Agency Logo URL</label>
      <input type="text" id="logo-url" placeholder="https://…/logo.png">
    </div>

    <div class="section">
      <label data-i18n="lbl_lang">Language</label>
      <select id="lang-select">
        <option value="en" selected data-i18n="lang_en">English</option>
        <option value="it" data-i18n="lang_it">Italian</option>
        <option value="fr" data-i18n="lang_fr">French</option>
        <option value="" data-i18n="lang_auto">Auto-detect from PDF</option>
      </select>
    </div>

    <div class="section">
      <label data-i18n="lbl_voice">Voice</label>
      <select id="voice-select">
        <option value="" data-i18n="voice_auto">Auto (matches language)</option>
        <option value="en-US-AriaNeural">English — Aria (female, warm)</option>
        <option value="en-US-GuyNeural">English — Guy (male)</option>
        <option value="it-IT-DiegoNeural">Italian — Diego (male)</option>
        <option value="it-IT-ElsaNeural">Italian — Elsa (female)</option>
        <option value="fr-FR-DeniseNeural">French — Denise (female)</option>
      </select>
    </div>

    <div class="section">
      <input type="checkbox" id="no-audio">
      <div class="toggle-row" onclick="toggleAudio()">
        <span class="toggle-row-label" data-i18n="skip_audio">Skip audio</span> <span style="color:var(--sidebar-muted);font-size:12px;" data-i18n="skip_audio_hint">(faster)</span>
        <div class="toggle" id="toggle-audio"></div>
      </div>
    </div>

  </div>

  <div class="sidebar-footer">
    <button class="btn-generate" id="generate-btn" onclick="startGenerate()">
      <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1v14M1 8h14"/></svg>
      <span data-i18n="btn_generate">Generate Trip</span>
    </button>
  </div>
</aside>

<!-- ── MAIN ── -->
<main class="main">

  <div id="log-section" class="log-section" style="display:none;">
    <div class="log-header">
      <span class="log-title">Build Log</span>
    </div>
    <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
    <div class="log-box" id="log-box">
      <div class="log-waiting" id="log-waiting">
        <div class="log-spinner"></div>
        <div class="log-waiting-text">Starting up<span class="log-dots"></span></div>
      </div>
    </div>
  </div>

  <div id="builds-section">
    <div class="page-header">
      <div class="page-title" data-i18n="page_title">Your Trips</div>
      <div class="page-count" id="builds-count"></div>
    </div>

    <div class="filter-toolbar">
      <div class="filter-search-wrap">
        <svg class="filter-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input class="filter-search" id="filter-search" type="text" data-i18n-placeholder="search_placeholder" placeholder="Search trips, clients, destinations…" oninput="applyFilters()">
      </div>
      <select class="filter-select" id="filter-lang" onchange="applyFilters()">
        <option value="" data-i18n="all_langs">All languages</option>
        <option value="en">🇬🇧 English</option>
        <option value="it">🇮🇹 Italian</option>
        <option value="fr">🇫🇷 French</option>
      </select>
      <select class="filter-select" id="filter-sort" onchange="applyFilters()">
        <option value="date-desc" data-i18n="sort_newest">↓ Newest</option>
        <option value="date-asc" data-i18n="sort_oldest">↑ Oldest</option>
        <option value="name-asc" data-i18n="sort_agency">A→Z Agency</option>
        <option value="dest-asc" data-i18n="sort_dest">A→Z Destination</option>
        <option value="client-asc" data-i18n="sort_client">A→Z Client</option>
        <option value="days-desc" data-i18n="sort_days">Most days</option>
      </select>
    </div>

    <div id="builds-list">
      <div class="empty-state">
        <div class="empty-icon">🗺️</div>
        <div class="empty-title" data-i18n="empty_title">No trips yet</div>
        <div class="empty-sub" data-i18n="empty_sub">Upload an itinerary to generate your first trip.</div>
      </div>
    </div>
  </div>

</main>

<script>
// ─── Admin i18n ───────────────────────────────────────────────────────────────
const ADMIN_I18N = {
  en: {
    studio:'Trip Studio', editing_trip:'EDITING TRIP', new_trip_instead:'✕ New trip instead',
    lbl_pdf:'Itinerary PDF', drop_pdf:'Drop PDF here or <strong>browse</strong>', or_paste:'or paste text',
    lbl_text:'Itinerary Text', placeholder_text:'Paste the DMC\'s itinerary here…',
    lbl_color:'Brand Colour', lbl_trip_name:'Trip Name', placeholder_trip_name:'e.g. Georgia: Caucasus Highlights',
    lbl_client:'Client Name', placeholder_client:'e.g. The Zanotelli Family',
    lbl_agency:'Agency / DMC Name', placeholder_agency:'Leave blank to auto-detect from PDF',
    lbl_logo:'Agency Logo URL', lbl_lang:'Language', lbl_voice:'Voice',
    lang_en:'English', lang_it:'Italian', lang_fr:'French', lang_auto:'Auto-detect from PDF',
    voice_auto:'Auto (matches language)',
    skip_audio:'Skip audio', skip_audio_hint:'(faster)',
    btn_generate:'Generate Trip',
    page_title:'Your Trips',
    search_placeholder:'Search trips, clients, destinations…',
    all_langs:'All languages',
    sort_newest:'↓ Newest', sort_oldest:'↑ Oldest', sort_agency:'A→Z Agency',
    sort_dest:'A→Z Destination', sort_client:'A→Z Client', sort_days:'Most days',
    empty_title:'No trips yet', empty_sub:'Upload an itinerary to generate your first trip.',
    col_trip:'Trip / Agency', col_client:'Client', col_lang:'Lang', col_date:'Date', col_code:'Code',
    days_label:'days', hotels_label:'hotels',
  },
  it: {
    studio:'Trip Studio', editing_trip:'MODIFICA VIAGGIO', new_trip_instead:'✕ Nuovo viaggio',
    lbl_pdf:'PDF Itinerario', drop_pdf:'Trascina il PDF qui o <strong>sfoglia</strong>', or_paste:'o incolla il testo',
    lbl_text:'Testo Itinerario', placeholder_text:'Incolla qui l\'itinerario del DMC…',
    lbl_color:'Colore Brand', lbl_trip_name:'Nome Viaggio', placeholder_trip_name:'es. Georgia: Highlights del Caucaso',
    lbl_client:'Nome Cliente', placeholder_client:'es. Famiglia Zanotelli',
    lbl_agency:'Nome Agenzia / DMC', placeholder_agency:'Lascia vuoto per rilevamento automatico',
    lbl_logo:'URL Logo Agenzia', lbl_lang:'Lingua', lbl_voice:'Voce',
    lang_en:'Inglese', lang_it:'Italiano', lang_fr:'Francese', lang_auto:'Rileva automaticamente',
    voice_auto:'Auto (segue la lingua)',
    skip_audio:'Salta audio', skip_audio_hint:'(più veloce)',
    btn_generate:'Genera Viaggio',
    page_title:'I Tuoi Viaggi',
    search_placeholder:'Cerca viaggi, clienti, destinazioni…',
    all_langs:'Tutte le lingue',
    sort_newest:'↓ Più recenti', sort_oldest:'↑ Meno recenti', sort_agency:'A→Z Agenzia',
    sort_dest:'A→Z Destinazione', sort_client:'A→Z Cliente', sort_days:'Più giorni',
    empty_title:'Nessun viaggio', empty_sub:'Carica un itinerario per generare il tuo primo viaggio.',
    col_trip:'Viaggio / Agenzia', col_client:'Cliente', col_lang:'Lingua', col_date:'Data', col_code:'Codice',
    days_label:'giorni', hotels_label:'hotel',
  },
  fr: {
    studio:'Trip Studio', editing_trip:'MODIFIER LE VOYAGE', new_trip_instead:'✕ Nouveau voyage',
    lbl_pdf:'PDF Itinéraire', drop_pdf:'Déposez le PDF ici ou <strong>parcourir</strong>', or_paste:'ou coller le texte',
    lbl_text:'Texte Itinéraire', placeholder_text:'Collez ici l\'itinéraire du DMC…',
    lbl_color:'Couleur Marque', lbl_trip_name:'Nom du Voyage', placeholder_trip_name:'ex. Géorgie: Highlights du Caucase',
    lbl_client:'Nom du Client', placeholder_client:'ex. Famille Zanotelli',
    lbl_agency:'Nom Agence / DMC', placeholder_agency:'Laisser vide pour détection automatique',
    lbl_logo:'URL Logo Agence', lbl_lang:'Langue', lbl_voice:'Voix',
    lang_en:'Anglais', lang_it:'Italien', lang_fr:'Français', lang_auto:'Détection automatique',
    voice_auto:'Auto (suit la langue)',
    skip_audio:'Ignorer audio', skip_audio_hint:'(plus rapide)',
    btn_generate:'Générer le Voyage',
    page_title:'Vos Voyages',
    search_placeholder:'Rechercher voyages, clients, destinations…',
    all_langs:'Toutes les langues',
    sort_newest:'↓ Plus récents', sort_oldest:'↑ Plus anciens', sort_agency:'A→Z Agence',
    sort_dest:'A→Z Destination', sort_client:'A→Z Client', sort_days:'Plus de jours',
    empty_title:'Aucun voyage', empty_sub:'Chargez un itinéraire pour générer votre premier voyage.',
    col_trip:'Voyage / Agence', col_client:'Client', col_lang:'Langue', col_date:'Date', col_code:'Code',
    days_label:'jours', hotels_label:'hôtels',
  }
};

let adminLang = localStorage.getItem('loomtrip_admin_lang') || 'en';

function setAdminLang(l) {
  adminLang = l;
  localStorage.setItem('loomtrip_admin_lang', l);
  applyAdminLang();
}

function applyAdminLang() {
  const d = ADMIN_I18N[adminLang] || ADMIN_I18N.en;
  // Update text content
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (d[key] !== undefined) el.innerHTML = d[key];
  });
  // Update placeholders
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    if (d[key] !== undefined) el.placeholder = d[key];
  });
  // Highlight active lang button
  document.querySelectorAll('#admin-lang-switcher button').forEach(btn => {
    const active = btn.getAttribute('data-al') === adminLang;
    btn.style.background = active ? 'rgba(196,135,58,0.8)' : 'transparent';
    btn.style.color = active ? '#fff' : 'rgba(255,255,255,0.4)';
  });
  // Re-render table with new labels if data loaded
  if (allBuilds.length) applyFilters();
}

// ─── Toggle ───────────────────────────────────────────────────────────────────
function toggleAudio() {
  const cb = document.getElementById('no-audio');
  cb.checked = !cb.checked;
  document.getElementById('toggle-audio').classList.toggle('on', cb.checked);
}

// ─── Colour presets ───────────────────────────────────────────────────────────
const PRESETS = [
  {c:'#C4873A',label:'Desert Gold'},
  {c:'#1e3a5f',label:'Coastal'},
  {c:'#2c5f2e',label:'Alpine'},
  {c:'#6B3FA0',label:'Exotic'},
  {c:'#1a1a2e',label:'City Night'},
  {c:'#8B4513',label:'Tuscany'},
  {c:'#1e6b8a',label:'Island'},
  {c:'#5C4033',label:'Safari'},
];
const sw = document.getElementById('swatches');
PRESETS.forEach(p => {
  const el = document.createElement('div');
  el.className = 'swatch' + (p.c==='#C4873A'?' active':'');
  el.style.background = p.c;
  el.title = p.label;
  el.onclick = () => {
    syncColor(p.c);
    document.getElementById('color-picker').value = p.c;
    document.querySelectorAll('.swatch').forEach(s => s.classList.remove('active'));
    el.classList.add('active');
  };
  sw.appendChild(el);
});

function syncColor(v) { document.getElementById('color-hex').value = v; }
function syncColorFromText(v) { if(/^#[0-9a-fA-F]{6}$/.test(v)) document.getElementById('color-picker').value = v; }

// ─── PDF drag & drop ──────────────────────────────────────────────────────────
let pdfFile = null;
const dz = document.getElementById('drop-zone');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
dz.addEventListener('dragleave', () => dz.classList.remove('over'));
dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('over'); handleFile(e.dataTransfer.files[0]); });

function handleFile(file) {
  if (!file || !file.name.endsWith('.pdf')) return;
  pdfFile = file;
  document.getElementById('pdf-pill').style.display = 'block';
  document.getElementById('pdf-pill').innerHTML = `<span class="pill">📄 ${file.name} <span class="pill-remove" onclick="clearPdf()">×</span></span>`;
  document.getElementById('drop-zone').style.display = 'none';
}
function clearPdf() {
  pdfFile = null;
  document.getElementById('pdf-pill').style.display = 'none';
  document.getElementById('drop-zone').style.display = 'block';
  document.getElementById('pdf-input').value = '';
}

// ─── Generate ─────────────────────────────────────────────────────────────────
let currentJobId = null;
function startGenerate() {
  const text = document.getElementById('itin-text').value.trim();
  if (!pdfFile && !text) { alert('Please upload a PDF or paste an itinerary.'); return; }

  const btn = document.getElementById('generate-btn');
  btn.disabled = true;
  btn.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> Working…';

  document.getElementById('log-section').style.display = 'block';
  document.getElementById('log-box').innerHTML = '<div class="log-waiting" id="log-waiting"><div class="log-spinner"></div><div class="log-waiting-text">Starting up<span class="log-dots"></span></div></div>';
  document.getElementById('progress-fill').style.width = '4%';

  const fd = new FormData();
  if (pdfFile) fd.append('pdf', pdfFile);
  if (text)    fd.append('text', text);
  fd.append('color',       document.getElementById('color-hex').value);
  fd.append('lang',        document.getElementById('lang-select').value);
  fd.append('voice',       document.getElementById('voice-select').value);
  fd.append('no_audio',    document.getElementById('no-audio').checked ? '1' : '0');
  fd.append('trip_name',   document.getElementById('trip-name').value.trim());
  fd.append('client_name', document.getElementById('client-name').value.trim());
  fd.append('agency_name', document.getElementById('agency-name').value.trim());
  fd.append('logo_url',    document.getElementById('logo-url').value.trim());

  fetch('/api/generate', { method:'POST', body:fd })
    .then(r=>r.json())
    .then(d => { currentJobId = d.job_id; pollLog(d.job_id); })
    .catch(e => { appendLog('error', '❌ '+e); btn.disabled=false; btn.innerHTML='<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1v14M1 8h14"/></svg> Generate Trip'; });
}

function appendLog(type, text) {
  const box = document.getElementById('log-box');
  // Hide waiting indicator on first real line
  const waiting = document.getElementById('log-waiting');
  if (waiting) waiting.remove();
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
    const RESET_BTN = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1v14M1 8h14"/></svg> Generate Trip';
    if (msg === '__DONE__') {
      es.close();
      document.getElementById('progress-fill').style.width='100%';
      document.getElementById('generate-btn').disabled=false;
      document.getElementById('generate-btn').innerHTML=RESET_BTN;
      loadBuilds();
      return;
    }
    if (msg === '__ERROR__') {
      es.close();
      document.getElementById('generate-btn').disabled=false;
      document.getElementById('generate-btn').innerHTML=RESET_BTN;
      return;
    }
    const type = msg.startsWith('✅')||msg.startsWith('✓') ? 'ok' : msg.startsWith('❌')||msg.startsWith('⚠️') ? 'error' : msg.startsWith('🤖')||msg.startsWith('✍️')||msg.startsWith('🎙️') ? 'info' : '';
    appendLog(type, msg);
    progress = Math.min(progress + 8, 90);
    document.getElementById('progress-fill').style.width = progress + '%';
  };
  es.onerror = () => { es.close(); };
}

// ─── Builds list (Gmail/Excel table) ────────────────────────────────────────────
let allBuilds = [];
const LANG_FLAGS = {en:'\u{1F1EC}\u{1F1E7}', it:'\u{1F1EE}\u{1F1F9}', fr:'\u{1F1EB}\u{1F1F7}'};

function loadBuilds() {
  fetch('/api/builds').then(r=>r.json()).then(builds => {
    allBuilds = builds;
    applyFilters();
  });
}

function applyFilters() {
  const q    = (document.getElementById('filter-search').value || '').toLowerCase();
  const lang = document.getElementById('filter-lang').value;
  const sort = document.getElementById('filter-sort').value;
  let list = allBuilds.filter(b => {
    if (lang && b.lang !== lang) return false;
    if (!q) return true;
    return [b.name, b.destination, b.client, b.trip_name].some(f => (f||'').toLowerCase().includes(q));
  });
  list.sort((a,b) => {
    if (sort==='date-asc')   return new Date(a.date)-new Date(b.date);
    if (sort==='name-asc')   return (a.name||'').localeCompare(b.name||'');
    if (sort==='dest-asc')   return (a.destination||'').localeCompare(b.destination||'');
    if (sort==='client-asc') return (a.client||'').localeCompare(b.client||'');
    if (sort==='days-desc')  return (b.days_count||0)-(a.days_count||0);
    return new Date(b.date)-new Date(a.date);
  });
  const el = document.getElementById('builds-list');
  const cnt = document.getElementById('builds-count');
  if (!allBuilds.length) {
    cnt.textContent='';
    const d2=ADMIN_I18N[adminLang]||ADMIN_I18N.en;
    el.innerHTML='<div class="empty-state"><div class="empty-icon">\uD83D\uDDFA\uFE0F</div><div class="empty-title">'+d2.empty_title+'</div><div class="empty-sub">'+d2.empty_sub+'</div></div>';
    return;
  }
  cnt.textContent = list.length+' of '+allBuilds.length+' trip'+(allBuilds.length!==1?'s':'');
  if (!list.length) {
    el.innerHTML='<div class="empty-state"><div class="empty-icon">\uD83D\uDD0D</div><div class="empty-title">No results</div><div class="empty-sub">Try a different search or filter.</div></div>';
    return;
  }
  const dateStr = iso => { try { return iso ? new Date(iso).toLocaleDateString('en-GB',{day:'numeric',month:'short'}) : '\u2014'; } catch { return '\u2014'; } };
  const svgEdit  = '<svg viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
  const svgEye   = '<svg viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  const svgDown  = '<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  const svgTrash = '<svg viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg>';
  el.innerHTML =
    '<div class="trip-table-head">' +
    '<div class="trip-table-head-cell"></div>' +
    '<div class="trip-table-head-cell">'+((ADMIN_I18N[adminLang]||ADMIN_I18N.en).col_trip)+'</div>' +
    '<div class="trip-table-head-cell">'+((ADMIN_I18N[adminLang]||ADMIN_I18N.en).col_client)+'</div>' +
    '<div class="trip-table-head-cell">'+((ADMIN_I18N[adminLang]||ADMIN_I18N.en).col_lang)+'</div>' +
    '<div class="trip-table-head-cell">'+((ADMIN_I18N[adminLang]||ADMIN_I18N.en).col_date)+'</div>' +
    '<div class="trip-table-head-cell">'+((ADMIN_I18N[adminLang]||ADMIN_I18N.en).col_code)+'</div>' +
    '<div class="trip-table-head-cell"></div></div>' +
    list.map(b =>
      '<div class="trip-row">' +
      '<div class="trip-row-cb"><input type="checkbox"></div>' +
      '<div class="trip-col"><div class="trip-col-name">'+( b.destination || b.name )+'</div>' +
      '<div class="trip-col-sub">'+b.name+' · '+b.days+' '+((ADMIN_I18N[adminLang]||ADMIN_I18N.en).days_label)+' · '+b.hotels+' '+((ADMIN_I18N[adminLang]||ADMIN_I18N.en).hotels_label)+' · '+b.size+'</div></div>' +
      '<div class="trip-col"><div class="trip-col-client">'+(b.client||'—')+'</div></div>' +
      '<div class="trip-col"><span class="lang-chip">'+( (b.lang==='en'?'\uD83C\uDDEC\uD83C\uDDE7':b.lang==='it'?'\uD83C\uDDEE\uD83C\uDDF9':b.lang==='fr'?'\uD83C\uDDEB\uD83C\uDDF7':'')+' '+(b.lang||'').toUpperCase() )+'</span></div>' +
      '<div class="trip-col trip-col-date">'+dateStr(b.date)+'</div>' +
      '<div class="trip-col"><span class="code-badge" id="code-badge-'+b.id+'" onclick="editCode(\''+b.id+'\')">' +
      '\uD83D\uDD11 '+(b.code||'+ code')+'</span></div>' +
      '<div class="trip-col"></div>' +
      '<div class="row-actions">' +
      '<button class="row-action-btn" onclick="editBuild(\''+b.id+'\')" title="Edit">'+svgEdit+'</button>' +
      '<a class="row-action-btn" href="/preview/'+b.id+'" target="_blank" title="Preview">'+svgEye+'</a>' +
      '<a class="row-action-btn" href="/download/'+b.id+'" download title="Download">'+svgDown+'</a>' +
      '<button class="row-action-btn danger" onclick="deleteBuild(\''+b.id+'\',\''+b.name+'\')" title="Delete">'+svgTrash+'</button>' +
      '</div></div>'
    ).join('');
}


// ─── Access code editor ───────────────────────────────────────────────────────
function editCode(id) {
  const badge = document.getElementById('code-badge-' + id);
  const current = badge.textContent.replace('🔑','').trim();
  const input = prompt('Access code (4–6 letters/numbers):', current === '+ Add code' ? '' : current);
  if (input === null) return;
  const code = input.trim().toUpperCase().replace(/[^A-Z0-9]/g,'').slice(0,6);
  if (!code || code.length < 4) { alert('Code must be 4–6 characters (letters and numbers only).'); return; }
  fetch(`/api/build/${id}/code`, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({code})
  }).then(r=>r.json()).then(d => {
    if (d.error) { alert(d.error); return; }
    badge.textContent = '🔑 ' + d.code;
  }).catch(() => alert('Failed to update code.'));
}

// ─── Delete build ─────────────────────────────────────────────────────────────
function deleteBuild(id, name) {
  if (!confirm(`Delete "${name}"?\n\nThis cannot be undone.`)) return;
  fetch(`/api/build/${id}`, { method: 'DELETE' })
    .then(r => r.json())
    .then(() => loadBuilds())
    .catch(() => alert('Delete failed.'));
}

// ─── Edit mode ────────────────────────────────────────────────────────────────
let editingBuildId = null;

function editBuild(id) {
  fetch(`/api/build/${id}/meta`)
    .then(r => r.json())
    .then(meta => {
      if (meta.error) { alert('No saved settings for this trip — regenerate it first.'); return; }
      editingBuildId = id;

      // Populate form
      if (meta.color) { syncColor(meta.color); document.getElementById('color-picker').value = meta.color; }
      document.getElementById('lang-select').value  = meta.lang  || 'en';
      document.getElementById('voice-select').value = meta.voice || '';
      document.getElementById('trip-name').value    = meta.trip_name   || '';
      document.getElementById('client-name').value  = meta.client_name || '';
      document.getElementById('agency-name').value  = meta.agency_name || '';
      document.getElementById('logo-url').value     = meta.logo_url    || '';
      if (meta.source_text) {
        document.getElementById('itin-text').value = meta.source_text;
        clearPdf();
      }

      // Show banner
      document.getElementById('edit-banner').style.display = 'block';
      document.getElementById('edit-banner-name').textContent = `Changes will regenerate this trip`;

      // Scroll sidebar to top
      document.querySelector('.sidebar').scrollTo({ top: 0, behavior: 'smooth' });
    })
    .catch(() => alert('Could not load trip settings.'));
}

function clearEdit() {
  editingBuildId = null;
  document.getElementById('edit-banner').style.display = 'none';
  document.getElementById('itin-text').value = '';
  clearPdf();
}

applyAdminLang();
loadBuilds();
setInterval(loadBuilds, 10000);
</script>
</body>
</html>"""

# ─── Flask routes ────────────────────────────────────────────────────────────
@app.route("/api/generate", methods=["POST"])
@require_admin
def generate():
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "running", "log": [], "output_dir": None}

    # Save uploaded PDF or text
    pdf_file = request.files.get("pdf")
    text     = request.form.get("text", "").strip()
    color       = request.form.get("color", "")
    lang        = request.form.get("lang", "")
    voice       = request.form.get("voice", "")
    no_audio    = request.form.get("no_audio", "0") == "1"
    trip_name   = request.form.get("trip_name", "").strip()
    client_name = request.form.get("client_name", "").strip()
    agency_name = request.form.get("agency_name", "").strip()
    logo_url    = request.form.get("logo_url", "").strip()

    # Write itinerary to temp file
    import tempfile
    source_text_for_meta = text  # will be updated after PDF extraction if needed
    if pdf_file and pdf_file.filename:
        tmp_pdf = Path(tempfile.mktemp(suffix=".pdf"))
        pdf_file.save(str(tmp_pdf))
        # Extract text now so we can save it in meta.json for re-editing
        try:
            import fitz
            doc = fitz.open(str(tmp_pdf))
            source_text_for_meta = "\n\n".join(p.get_text("text") for p in doc if p.get_text("text").strip())
            doc.close()
        except Exception:
            source_text_for_meta = ""
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
        if color:       cmd += ["--color",       color]
        if lang:        cmd += ["--lang",        lang]
        if voice:       cmd += ["--voice",       voice]
        if no_audio:    cmd += ["--no-audio"]
        if trip_name:   cmd += ["--trip-name",   trip_name]
        if client_name: cmd += ["--client-name", client_name]
        if agency_name: cmd += ["--agency-name", agency_name]
        if logo_url:    cmd += ["--logo",        logo_url]

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
            # Save build metadata for edit-and-regenerate
            try:
                meta = {
                    "color": color, "lang": lang, "voice": voice,
                    "trip_name": trip_name, "client_name": client_name,
                    "agency_name": agency_name, "logo_url": logo_url,
                    "source_text": source_text_for_meta,
                }
                (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            except Exception: pass
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
        heartbeat = 0
        for _ in range(1200):  # max ~10 min
            log = jobs.get(job_id, {}).get("log", [])
            while seen < len(log):
                line = log[seen]; seen += 1
                yield f"data: {line}\n\n"
                if line in ("__DONE__", "__ERROR__"):
                    return
                heartbeat = 0
            heartbeat += 1
            if heartbeat % 4 == 0:  # every 2s send a keepalive comment
                yield ": keep-alive\n\n"
            time.sleep(0.5)
    return Response(stream(), mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                    })

@app.route("/api/builds")
@require_admin
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
        # Read saved meta for filter fields
        meta = {}
        meta_file = d / "meta.json"
        if meta_file.exists():
            try: meta = json.loads(meta_file.read_text())
            except: pass
        # Extract destination from HTML title
        mt = re.search(r'<title>([^<]+)</title>', content)
        destination = mt.group(1).strip() if mt else ""
        # mtime as ISO date string
        mtime = d.stat().st_mtime
        import datetime
        date_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        result.append({
            "id": d.name, "name": name, "days": days, "days_count": days,
            "hotels": hotels, "audio": audio,
            "size": f"{size_kb} KB", "code": code,
            "destination": destination,
            "client": meta.get("client_name", ""),
            "lang": meta.get("lang", ""),
            "trip_name": meta.get("trip_name", ""),
            "date": date_str,
        })
    return jsonify(result)

@app.route("/api/build/<build_id>", methods=["DELETE"])
@require_admin
def delete_build(build_id):
    build_dir = BUILDS_DIR / build_id
    if not build_dir.exists():
        return jsonify({"error": "not found"}), 404
    shutil.rmtree(str(build_dir))
    # Remove code mapping
    codes = load_codes()
    codes = {k: v for k, v in codes.items() if v != build_id}
    save_codes(codes)
    return jsonify({"ok": True})

@app.route("/api/build/<build_id>/code", methods=["PUT"])
@require_admin
def set_code(build_id):
    data = request.get_json(force=True)
    new_code = (data.get("code") or "").strip().upper()[:6]
    if not new_code or len(new_code) < 4:
        return jsonify({"error": "Code must be 4-6 characters"}), 400
    import re as _re
    if not _re.match(r'^[A-Z0-9]+$', new_code):
        return jsonify({"error": "Only letters and numbers allowed"}), 400
    codes = load_codes()
    if new_code in codes and codes[new_code] != build_id:
        return jsonify({"error": "Code already in use"}), 409
    # Remove old code for this build
    codes = {k: v for k, v in codes.items() if v != build_id}
    codes[new_code] = build_id
    save_codes(codes)
    return jsonify({"code": new_code})

@app.route("/api/build/<build_id>/meta")
@require_admin
def build_meta(build_id):
    meta_file = BUILDS_DIR / build_id / "meta.json"
    if not meta_file.exists():
        return jsonify({"error": "no meta"}), 404
    return jsonify(json.loads(meta_file.read_text()))

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
@require_admin
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
