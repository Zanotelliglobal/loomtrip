# Loomtrip — Trojan Loom Demo Generator

Turn any itinerary text into a polished branded travel app in 30 seconds.

## Setup (one-time)

1. Copy `.env.example` to `.env`
2. Add your Anthropic API key:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

## Run

```bash
cd /tmp/claude-501/loomtrip

# Generate from the sample Tuscany itinerary
python3 generate-demo.py --itinerary sample-itinerary-tuscany.txt

# With a custom brand colour
python3 generate-demo.py --itinerary mytrip.txt --color "#8B4513"

# With a logo
python3 generate-demo.py --itinerary mytrip.txt --logo "https://yourdmc.com/logo.png"

# Custom output filename
python3 generate-demo.py --itinerary mytrip.txt --output demo-client-name.html
```

## Workflow

1. Find a DMC on LinkedIn
2. Grab their public itinerary (from website, PDF, or copy-paste)
3. Save as `.txt` file
4. Run: `python3 generate-demo.py --itinerary their-itinerary.txt`
5. Open the generated HTML in browser
6. Start Loom recording → scroll through the app (30 sec)
7. Send to DMC: "I built your [Destination] trip as an app — here's what your clients would see"

## Output

Single self-contained HTML file (~80-120 KB). No dependencies. Works offline.
Open in any browser, add to home screen as PWA.
