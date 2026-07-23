#!/usr/bin/env python3
"""
Loomtrip — PDF Importer
========================
Extracts text from a PDF itinerary and pipes it into generate-demo.py.

Usage:
  python import-pdf.py mytrip.pdf
  python import-pdf.py mytrip.pdf --color "#1e6b8a" --lang it
  python import-pdf.py mytrip.pdf --preview   # just show extracted text, don't generate

Requires: PyMuPDF (fitz) — already in system Python on this Mac
"""

import sys, os, subprocess, argparse, tempfile
from pathlib import Path

_deps = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deps")
if os.path.isdir(_deps) and _deps not in sys.path:
    sys.path.insert(0, _deps)

parser = argparse.ArgumentParser(description="Loomtrip PDF importer")
parser.add_argument("pdf",           help="Path to PDF file")
parser.add_argument("--color",       default=None)
parser.add_argument("--logo",        default=None)
parser.add_argument("--lang",        default=None)
parser.add_argument("--voice",       default=None)
parser.add_argument("--no-audio",    action="store_true")
parser.add_argument("--preview",     action="store_true", help="Print extracted text only")
parser.add_argument("--model",       default="claude-haiku-4-5")
args = parser.parse_args()

pdf_path = Path(args.pdf)
if not pdf_path.exists():
    print(f"❌ File not found: {pdf_path}"); sys.exit(1)

# ─── Extract text from PDF using PyMuPDF ────────────────────────────────────
try:
    import fitz  # PyMuPDF
except ImportError:
    print("❌ PyMuPDF not found. Install with: pip3 install pymupdf")
    sys.exit(1)

print(f"📄 Reading PDF: {pdf_path.name}")
doc = fitz.open(str(pdf_path))
pages_text = []
for i, page in enumerate(doc):
    text = page.get_text("text")
    if text.strip():
        pages_text.append(text)

doc.close()
full_text = "\n\n".join(pages_text)

if not full_text.strip():
    print("❌ Could not extract text from this PDF (may be image-based/scanned).")
    print("   Try: take a screenshot and paste the text manually into a .txt file.")
    sys.exit(1)

print(f"✅ Extracted {len(full_text)} chars from {len(pages_text)} pages")

if args.preview:
    print("\n" + "─"*60)
    print(full_text[:3000])
    if len(full_text) > 3000:
        print(f"\n... [{len(full_text)-3000} more chars]")
    sys.exit(0)

# ─── Save as temp .txt and call generate-demo.py ─────────────────────────────
tmp_txt = Path(tempfile.mktemp(suffix=".txt"))
tmp_txt.write_text(full_text, encoding="utf-8")

gen_script = Path(__file__).parent / "generate-demo.py"
cmd = [sys.executable, str(gen_script), "--itinerary", str(tmp_txt), "--model", args.model]
if args.color:    cmd += ["--color",   args.color]
if args.logo:     cmd += ["--logo",    args.logo]
if args.lang:     cmd += ["--lang",    args.lang]
if args.voice:    cmd += ["--voice",   args.voice]
if args.no_audio: cmd += ["--no-audio"]

env = os.environ.copy()
env["PYTHONWARNINGS"] = "ignore"

try:
    result = subprocess.run(cmd, env=env, cwd=str(Path(__file__).parent))
finally:
    tmp_txt.unlink(missing_ok=True)

sys.exit(result.returncode)
