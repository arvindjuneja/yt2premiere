# YT → Premiere

**Download any YouTube video and get a file that just works in Adobe Premiere Pro.**

No codec headaches. No missing audio. No "unsupported compression type" errors.  
Just paste a link → pick quality → get a proper **H.264 + AAC MP4** ready for your timeline.

---

## Why this exists

YouTube serves videos in modern codecs like **AV1** and **Opus** that most browsers handle fine — but Premiere Pro, Final Cut, QuickTime, and many NLEs choke on them:

| What YouTube sends | What Premiere needs |
|---|---|
| AV1 / VP9 video | **H.264 (AVC)** video |
| Opus audio | **AAC** audio |
| WebM container | **MP4** container |

**YT → Premiere** handles all of this automatically. It downloads the best quality available, detects incompatible codecs with `ffprobe`, and re-encodes only what's necessary via `ffmpeg` — so if the video is already H.264 it stays untouched (fast!), and only the audio gets converted if needed.

---

## Features

- **Premiere Pro-compatible output** — H.264 video + AAC audio in an MP4 container, every time
- **Quality options** — Best (up to 4K), 1080p, 720p, 480p, or audio-only MP3
- **Smart conversion** — only re-encodes streams that need it; copies the rest
- **Modern dark UI** — built with [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter), looks native on macOS
- **Progress tracking** — live speed, ETA, and progress bar
- **One-click paste** — grabs the URL from your clipboard

---

## Quick start

### Prerequisites

You need two things installed on your Mac:

1. **Python 3.10+** — check with `python3 --version`
2. **ffmpeg** — install with [Homebrew](https://brew.sh):
   ```bash
   brew install ffmpeg
   ```

### Installation

```bash
# Clone the repo
git clone https://github.com/arvindjuneja/yt2premiere.git
cd yt2premiere

# Create a virtual environment & install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
source venv/bin/activate
python3 app.py
```

Or use the shortcut script:

```bash
./run.sh
```

---

## How to use

1. **Copy** a YouTube URL in your browser
2. **Click "Paste"** in the app (or paste manually into the URL field)
3. **Choose quality** from the dropdown
4. **Click "Download & Convert for Premiere"**
5. Wait for the download + conversion to finish
6. **Import the MP4 into Premiere Pro** — video and audio will just work

---

## How it works under the hood

```
YouTube URL
    │
    ▼
┌─────────┐     best available      ┌──────────┐
│  yt-dlp  │ ──── streams ────────► │ raw file │
└─────────┘                         └────┬─────┘
                                         │
                                    ffprobe checks
                                    video & audio codecs
                                         │
                          ┌──────────────┼──────────────┐
                          │              │              │
                     both H.264     video ok,       video not
                      + AAC        audio not AAC     H.264
                          │              │              │
                       done!        convert audio   re-encode
                     (no re-encode)  (AAC, fast)    video + audio
                          │              │          (H.264 + AAC)
                          ▼              ▼              ▼
                      ┌──────────────────────────────────────┐
                      │  Premiere Pro-ready MP4               │
                      │  H.264 + AAC · movflags +faststart   │
                      └──────────────────────────────────────┘
```

---

## Tech stack

| Component | What it does |
|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Downloads video + audio streams from YouTube |
| [ffmpeg](https://ffmpeg.org/) / ffprobe | Detects codecs & re-encodes to H.264 + AAC |
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) | Modern, dark-mode GUI for Python |
| Python 3.10+ | Glues it all together |

---

## Troubleshooting

**"No module named '\_tkinter'"**  
Install the Tk bindings for your Python version:
```bash
brew install python-tk@3.13    # adjust version to match yours
```
Then recreate the venv.

**SSL certificate errors**  
Make sure you're using Homebrew's Python, not the standalone python.org installer:
```bash
/opt/homebrew/bin/python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Conversion is slow on long videos**  
That's expected — ffmpeg is re-encoding the video to H.264 which is CPU-intensive. The status bar shows progress. For shorter clips it usually takes just a few seconds.

---

## License

MIT — do whatever you want with it.
