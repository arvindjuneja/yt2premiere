#!/usr/bin/env python3
"""YT → Premiere — Download YouTube clips as Premiere Pro-ready MP4s."""

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox

import yt_dlp

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
MONO_FONT = "Consolas" if IS_WINDOWS else "Menlo"

# Hide console windows when spawning ffmpeg/ffprobe on Windows
_SP_KWARGS: dict = {}
if IS_WINDOWS:
    _SP_KWARGS["creationflags"] = subprocess.CREATE_NO_WINDOW

# Secondary/label text colours (light, dark) — dark side brightened for contrast
MUTED = ("gray45", "gray70")


class _Cancelled(Exception):
    """Raised internally to abort an in-flight download/conversion."""


def _resource_dir() -> str:
    """Where bundled binaries live (PyInstaller temp dir when frozen)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _find_binary(name: str) -> str:
    """Prefer a bundled ffmpeg/ffprobe; fall back to one on PATH."""
    exe = name + ".exe" if IS_WINDOWS else name
    bundled = os.path.join(_resource_dir(), exe)
    if os.path.isfile(bundled):
        return bundled
    return shutil.which(name) or name


FFMPEG = _find_binary("ffmpeg")
FFPROBE = _find_binary("ffprobe")
FFMPEG_DIR = os.path.dirname(FFMPEG)

DEFAULT_OUTPUT_DIR = str(Path.home() / "Downloads")

QUALITY_OPTIONS = [
    "Best available (up to 4K)",
    "1080p Full HD",
    "720p HD",
    "480p SD",
    "Audio only (MP3)",
]

AUDIO_ONLY = "Audio only (MP3)"

QUALITY_HEIGHTS = {
    "Best available (up to 4K)": None,
    "1080p Full HD": 1080,
    "720p HD": 720,
    "480p SD": 480,
}

URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/\S+",
    re.IGNORECASE,
)

# YouTube needs a JS runtime to hand out working stream URLs. Without one yt-dlp
# falls back to a client that 403s on some videos and throttles the rest.
JS_RUNTIMES = ("deno", "node", "bun", "quickjs")


def detect_js_runtimes() -> dict:
    return {name: {} for name in JS_RUNTIMES if shutil.which(name)}


class _Tooltip:
    """Lightweight hover tooltip (used for the full output path)."""

    def __init__(self, widget, text_func):
        self.widget = widget
        self.text_func = text_func
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None):
        text = self.text_func()
        if self.tip or not text:
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tw, text=text, justify="left",
            bg="#2b2b2b", fg="#dddddd", relief="solid", borderwidth=1,
            font=(MONO_FONT, 9), padx=6, pady=3,
        ).pack()

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class App(ctk.CTk):
    IDLE_COLOR = ("#3a7ebf", "#1f538d")
    IDLE_HOVER = ("#325882", "#14375e")
    CANCEL_COLOR = ("#d9534f", "#a93226")
    CANCEL_HOVER = ("#c9302c", "#922b21")

    def __init__(self):
        super().__init__()

        self.title("YT → Premiere")
        self.geometry("720x860")
        self.minsize(640, 760)

        icon_path = os.path.join(_resource_dir(), "assets", "icon.ico")
        if IS_WINDOWS and os.path.isfile(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        self.output_dir = DEFAULT_OUTPUT_DIR
        self.downloading = False
        self.cancel_requested = False
        self._proc = None  # currently running ffmpeg process, if any
        self.queue_index = 0
        self.queue_total = 0
        self.current_title = ""
        self.js_runtimes = detect_js_runtimes()

        self._build_ui()

        # Start ready to type; Cmd/Ctrl+Enter starts the queue.
        self.after(100, self.url_box.focus_set)

        if not self.js_runtimes:
            self._set_status(
                "No JavaScript runtime found — downloads may fail or crawl. "
                f"Install one with: {'winget install DenoLand.Deno' if IS_WINDOWS else 'brew install deno'}"
            )

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ghost = dict(
            corner_radius=8, fg_color="transparent",
            border_width=1, border_color=("gray70", "gray30"),
            hover_color=("gray85", "gray25"),
            text_color=("gray30", "gray70"),
        )

        # ── Header ───────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=32, pady=(28, 0))

        ctk.CTkLabel(
            header, text="YT  →  Premiere",
            font=ctk.CTkFont(size=32, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="Paste one or many YouTube links, pick quality, and get Premiere Pro-ready MP4s (H.264 + AAC).",
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
            wraplength=620, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # ── URLs ─────────────────────────────────────────────────────────
        url_frame = ctk.CTkFrame(self, corner_radius=12)
        url_frame.grid(row=1, column=0, sticky="nsew", padx=32, pady=(20, 0))
        url_frame.grid_columnconfigure(0, weight=1)
        url_frame.grid_rowconfigure(1, weight=1)

        url_head = ctk.CTkFrame(url_frame, fg_color="transparent")
        url_head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        url_head.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            url_head, text="YOUTUBE URLS  ·  ONE PER LINE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED,
        ).grid(row=0, column=0, sticky="w")

        self.count_label = ctk.CTkLabel(
            url_head, text="no links yet",
            font=ctk.CTkFont(size=11), text_color=MUTED,
        )
        self.count_label.grid(row=0, column=1, sticky="e")

        self.url_box = ctk.CTkTextbox(
            url_frame, height=140, wrap="none",
            font=ctk.CTkFont(family=MONO_FONT, size=12),
            corner_radius=8,
        )
        self.url_box.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        self.url_box.bind("<KeyRelease>", lambda _e: self._refresh_count())
        for seq in ("<Command-Return>", "<Control-Return>"):
            self.url_box.bind(seq, lambda _e: (self._start_download(), "break")[1])

        btn_row = ctk.CTkFrame(url_frame, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 14))

        ctk.CTkButton(
            btn_row, text="Paste", width=80, height=34,
            command=self._paste_urls, **ghost,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row, text="Clear", width=80, height=34,
            command=self._clear_urls, **ghost,
        ).pack(side="left")

        # ── Options ──────────────────────────────────────────────────────
        opt_frame = ctk.CTkFrame(self, corner_radius=12)
        opt_frame.grid(row=2, column=0, sticky="ew", padx=32, pady=(14, 0))
        opt_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            opt_frame, text="OPTIONS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 8))

        ctk.CTkLabel(
            opt_frame, text="Quality",
            font=ctk.CTkFont(size=13),
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

        self.quality_var = ctk.StringVar(value="1080p Full HD")
        self.quality_menu = ctk.CTkOptionMenu(
            opt_frame, variable=self.quality_var, values=QUALITY_OPTIONS,
            width=240, height=36, corner_radius=8,
            font=ctk.CTkFont(size=13),
            dropdown_font=ctk.CTkFont(size=13),
        )
        self.quality_menu.grid(row=1, column=1, sticky="e", padx=16, pady=(0, 10))

        self.prefer_h264_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            opt_frame,
            text="Prefer YouTube's H.264 streams — no re-encoding, far faster (max 1080p)",
            variable=self.prefer_h264_var,
            font=ctk.CTkFont(size=12),
            checkbox_width=20, checkbox_height=20, corner_radius=5,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))

        ctk.CTkLabel(
            opt_frame, text="Save to",
            font=ctk.CTkFont(size=13),
        ).grid(row=3, column=0, sticky="w", padx=16, pady=(0, 10))

        dir_inner = ctk.CTkFrame(opt_frame, fg_color="transparent")
        dir_inner.grid(row=3, column=1, sticky="e", padx=16, pady=(0, 10))

        self.dir_label = ctk.CTkLabel(
            dir_inner, text=self._short_path(self.output_dir),
            font=ctk.CTkFont(family=MONO_FONT, size=12),
            text_color=("gray30", "gray70"),
        )
        self.dir_label.pack(side="left", padx=(0, 10))
        _Tooltip(self.dir_label, lambda: self.output_dir)

        ctk.CTkButton(
            dir_inner, text="Browse…", width=80, height=32,
            command=self._browse_dir, **ghost,
        ).pack(side="left")

        ctk.CTkLabel(
            opt_frame, text="Appearance",
            font=ctk.CTkFont(size=13),
        ).grid(row=4, column=0, sticky="w", padx=16, pady=(0, 14))

        self.appearance_seg = ctk.CTkSegmentedButton(
            opt_frame, values=["Light", "Dark"],
            command=self._set_appearance,
            font=ctk.CTkFont(size=12),
        )
        self.appearance_seg.set("Dark")
        self.appearance_seg.grid(row=4, column=1, sticky="e", padx=16, pady=(0, 14))

        # ── Progress ─────────────────────────────────────────────────────
        prog_frame = ctk.CTkFrame(self, corner_radius=12)
        prog_frame.grid(row=3, column=0, sticky="ew", padx=32, pady=(14, 0))
        prog_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            prog_frame, text="PROGRESS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))

        self.queue_bar = ctk.CTkProgressBar(prog_frame, height=10, corner_radius=5)
        self.queue_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        self.queue_bar.set(0)

        self.queue_label = ctk.CTkLabel(
            prog_frame, text="Ready",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.queue_label.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 10))

        self.progress_bar = ctk.CTkProgressBar(prog_frame, height=6, corner_radius=3)
        self.progress_bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 4))
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(
            prog_frame, text="Paste your links above to begin",
            font=ctk.CTkFont(size=12), text_color=MUTED,
            wraplength=620, justify="left",
        )
        self.status_label.grid(row=4, column=0, sticky="w", padx=16, pady=(0, 14))

        # ── Download button ──────────────────────────────────────────────
        self.download_btn = ctk.CTkButton(
            self, text="Download & Convert for Premiere",
            height=48, corner_radius=10,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._start_download,
        )
        self.download_btn.grid(row=4, column=0, sticky="ew", padx=32, pady=(20, 28))

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _short_path(p: str) -> str:
        home = str(Path.home())
        disp = "~" + p[len(home):] if p.startswith(home) else p
        if len(disp) > 40:
            disp = disp[:18] + "…" + disp[-19:]
        return disp

    def _ui(self, fn, *args):
        """Schedule a UI update on the Tk main thread."""
        self.after(0, fn, *args)

    def _set_status(self, text: str):
        self.status_label.configure(text=text)

    def _set_queue_label(self, text: str):
        self.queue_label.configure(text=text)

    def _set_appearance(self, choice: str):
        ctk.set_appearance_mode(choice.lower())

    def _open_folder(self, path: str):
        try:
            if IS_WINDOWS:
                os.startfile(path)  # noqa: S606
            elif IS_MAC:
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _progress_indeterminate(self, on: bool):
        if on:
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start()
        else:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")

    def _set_button_busy(self):
        self.download_btn.configure(
            text="Cancel", command=self._cancel, state="normal",
            fg_color=self.CANCEL_COLOR, hover_color=self.CANCEL_HOVER,
        )

    def _set_button_idle(self):
        self.download_btn.configure(
            text="Download & Convert for Premiere",
            command=self._start_download, state="normal",
            fg_color=self.IDLE_COLOR, hover_color=self.IDLE_HOVER,
        )

    @staticmethod
    def _humanize_error(msg: str) -> str:
        low = msg.lower()
        if "unavailable" in low or "not available" in low:
            return ("This video is unavailable — it may be private, deleted, "
                    "age-restricted, or blocked in your region.")
        if "sign in" in low or "confirm you" in low or "bot" in low:
            return ("YouTube is asking to verify this request. Try again later, "
                    "or pick a different video.")
        if "private video" in low:
            return "This is a private video and can't be downloaded."
        if "unsupported url" in low or "is not a valid url" in low:
            return "That link isn't a supported YouTube URL."
        if "ffmpeg" in low and ("not found" in low or "no such" in low):
            return "ffmpeg was not found. Please install ffmpeg and try again."
        if "http error 429" in low or "too many requests" in low:
            return "Too many requests to YouTube. Please wait a bit and try again."
        if "http error 403" in low or "forbidden" in low:
            return ("YouTube refused the download. This usually means no JavaScript "
                    "runtime is installed (install deno) or yt-dlp is out of date "
                    "(pip install --upgrade yt-dlp).")
        if ("getaddrinfo" in low or "failed to resolve" in low
                or "connection" in low or "timed out" in low or "urlopen" in low):
            return "Network problem — check your internet connection and try again."
        # Fallback: keep it short, drop the noisy "ERROR:" prefix.
        clean = msg.replace("ERROR:", "").strip()
        if len(clean) > 200:
            clean = clean[:200] + "…"
        return clean or "Something went wrong. Please try again."

    def _collect_urls(self) -> list[str]:
        """Pull every YouTube link out of the box, in order, without duplicates."""
        raw = self.url_box.get("1.0", "end")
        seen, urls = set(), []
        for match in URL_RE.findall(raw):
            url = match.rstrip(".,;)]}\"'")
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _refresh_count(self):
        n = len(self._collect_urls())
        self.count_label.configure(
            text="no links yet" if n == 0 else f"{n} link{'s' if n != 1 else ''} detected"
        )

    def _label(self) -> str:
        title = self.current_title or "…"
        if len(title) > 70:
            title = title[:67] + "…"
        return f"[{self.queue_index}/{self.queue_total}] {title}"

    # ── Actions ───────────────────────────────────────────────────────────

    def _browse_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.output_dir)
        if chosen:
            self.output_dir = chosen
            self.dir_label.configure(text=self._short_path(chosen))

    def _paste_urls(self):
        try:
            clipboard = self.clipboard_get()
        except Exception:
            return
        if not clipboard.strip():
            return
        existing = self.url_box.get("1.0", "end").strip()
        self.url_box.insert("end", ("\n" if existing else "") + clipboard.strip())
        self._refresh_count()

    def _clear_urls(self):
        self.url_box.delete("1.0", "end")
        self._refresh_count()

    def _start_download(self):
        if self.downloading:
            return

        urls = self._collect_urls()
        if not urls:
            messagebox.showwarning(
                "No URLs", "Paste at least one YouTube link — one per line.",
            )
            return

        self.downloading = True
        self.cancel_requested = False
        self.queue_total = len(urls)
        self.queue_index = 0

        self._set_button_busy()
        self.url_box.configure(state="disabled")
        self._progress_indeterminate(False)
        self.progress_bar.set(0)
        self.queue_bar.set(0)
        self._set_queue_label(f"0 / {len(urls)} done")
        self._set_status("Starting…")

        # Tk variables must be read on the main thread, so resolve the options
        # here and hand the worker plain values.
        threading.Thread(
            target=self._run_queue,
            args=(urls, self.quality_var.get(), self.prefer_h264_var.get()),
            daemon=True,
        ).start()

    def _cancel(self):
        if not self.downloading or self.cancel_requested:
            return
        self.cancel_requested = True
        self._set_status("Cancelling…")
        self.download_btn.configure(state="disabled")
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _progress_hook(self, d: dict):
        if self.cancel_requested:
            raise _Cancelled()

        title = (d.get("info_dict") or {}).get("title")
        if title:
            self.current_title = title

        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                self._ui(self.progress_bar.set, downloaded / total)
            speed = (d.get("_speed_str") or "").strip()
            eta = (d.get("_eta_str") or "").strip()
            self._ui(self._set_status, f"{self._label()}  ·  {speed}  ETA {eta}")
        elif d["status"] == "finished":
            # Download done; what follows (merge / audio extract / re-encode)
            # has no byte-level progress, so switch to an animated bar.
            self._ui(self._set_status, f"{self._label()}  ·  merging & preparing…")
            self._ui(self._progress_indeterminate, True)

    # ── Format selection ─────────────────────────────────────────────────

    def _build_format(self, quality: str, prefer_h264: bool) -> str:
        if quality == AUDIO_ONLY:
            return "bestaudio[acodec^=mp4a]/bestaudio/best"

        height = QUALITY_HEIGHTS.get(quality)
        cap = f"[height<={height}]" if height else ""

        chains = []
        if prefer_h264:
            # YouTube ships avc1 + mp4a next to its AV1/Opus streams; picking
            # those turns the conversion step into a remux instead of a
            # CPU-bound re-encode of every file.
            chains.append(f"bestvideo[vcodec^=avc1]{cap}+bestaudio[acodec^=mp4a]")
            chains.append(f"best[vcodec^=avc1][acodec^=mp4a]{cap}")
        chains.append(f"bestvideo{cap}+bestaudio")
        chains.append(f"best{cap}")
        chains.append("best")
        return "/".join(chains)

    # ── Codec detection & conversion ─────────────────────────────────────

    def _get_codecs(self, filepath: str) -> tuple[str, str]:
        try:
            vr = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                capture_output=True, text=True, timeout=30, **_SP_KWARGS,
            )
            ar = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_name",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                capture_output=True, text=True, timeout=30, **_SP_KWARGS,
            )
            return (vr.stdout.strip().lower(), ar.stdout.strip().lower())
        except Exception:
            return ("", "")

    def _ensure_compatible(self, filepath: str, duration: float) -> str:
        vcodec, acodec = self._get_codecs(filepath)
        video_ok = vcodec in ("h264", "")
        audio_ok = acodec in ("aac", "")

        if video_ok and audio_ok:
            return filepath

        base, _ = os.path.splitext(filepath)
        out_path = base + "_compat.mp4"

        parts = ["video" if not video_ok else "", "audio" if not audio_ok else ""]
        label = " & ".join(p for p in parts if p)
        self._ui(self._set_status, f"{self._label()}  ·  converting {label} for Premiere Pro…")

        cmd = [FFMPEG, "-y", "-i", filepath]
        if video_ok:
            cmd += ["-c:v", "copy"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-pix_fmt", "yuv420p"]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]

        # Re-encoding runs slower than real time, so scale the limit with the
        # clip length rather than using a ceiling long videos always blow past.
        timeout = max(900, int(duration * 10)) if duration else 3600

        try:
            self._run_cancellable(cmd, timeout=timeout)
        except Exception:
            if os.path.isfile(out_path):
                os.remove(out_path)
            raise

        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("ffmpeg produced no output")

        os.remove(filepath)
        os.rename(out_path, filepath)
        return filepath

    def _run_cancellable(self, cmd: list, timeout: int = 600):
        """Run a subprocess so it can be killed when the user hits Cancel."""
        # stderr goes to a file rather than a pipe: ffmpeg is chatty enough to
        # fill a pipe buffer and deadlock while we poll.
        with tempfile.TemporaryFile() as errfile:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=errfile, **_SP_KWARGS,
            )
            deadline = time.time() + timeout
            try:
                while self._proc.poll() is None:
                    if self.cancel_requested:
                        self._proc.terminate()
                        raise _Cancelled()
                    if time.time() > deadline:
                        self._proc.terminate()
                        raise TimeoutError("Conversion timed out.")
                    time.sleep(0.2)
                if self._proc.returncode != 0:
                    errfile.seek(0)
                    lines = errfile.read().decode("utf-8", "replace").strip().splitlines()
                    raise RuntimeError(
                        "ffmpeg failed: " + (lines[-1] if lines else "unknown error")
                    )
            finally:
                self._proc = None

    # ── Download ─────────────────────────────────────────────────────────

    def _run_queue(self, urls: list, quality: str, prefer_h264: bool):
        fmt = self._build_format(quality, prefer_h264)
        is_audio = quality == AUDIO_ONLY

        done, failures, cancelled = 0, [], False

        for idx, url in enumerate(urls, start=1):
            if self.cancel_requested:
                cancelled = True
                break

            self.queue_index = idx
            self.current_title = ""
            self._ui(self._progress_indeterminate, False)
            self._ui(self.progress_bar.set, 0)
            self._ui(self._set_status, f"[{idx}/{len(urls)}] fetching video info…")

            try:
                self._download_one(url, fmt, is_audio)
                done += 1
            except _Cancelled:
                cancelled = True
                break
            except Exception as exc:
                # A cancel during the download surfaces wrapped in DownloadError.
                if self.cancel_requested:
                    cancelled = True
                    break
                failures.append((url, self._humanize_error(str(exc))))

            self._ui(self.queue_bar.set, idx / len(urls))
            self._ui(self._set_queue_label, f"{done} / {len(urls)} done")

        self._ui(self._on_queue_done, done, failures, cancelled, len(urls), is_audio)

    def _download_one(self, url: str, fmt: str, is_audio: bool):
        outtmpl = os.path.join(self.output_dir, "%(title)s.%(ext)s")

        ydl_opts: dict = {
            "format": fmt,
            "outtmpl": outtmpl,
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "retries": 5,
            "fragment_retries": 5,
            "extractor_retries": 3,
        }

        if self.js_runtimes:
            ydl_opts["js_runtimes"] = self.js_runtimes

        if os.path.isdir(FFMPEG_DIR):
            ydl_opts["ffmpeg_location"] = FFMPEG_DIR

        if is_audio:
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        else:
            ydl_opts["merge_output_format"] = "mp4"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            if not info:
                raise RuntimeError("no video information returned")
            if "entries" in info:
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise RuntimeError("nothing to download at this URL")
                info = entries[0]

            if info.get("title"):
                self.current_title = info["title"]

            if is_audio:
                return

            path = next(
                (d.get("filepath") for d in (info.get("requested_downloads") or [])
                 if d.get("filepath")),
                None,
            )
            if not path:
                path = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp4"

        if os.path.isfile(path):
            self._ensure_compatible(path, info.get("duration") or 0)

    def _on_queue_done(self, done: int, failures: list, cancelled: bool,
                       total: int, is_audio: bool):
        self.downloading = False
        self.cancel_requested = False
        self._progress_indeterminate(False)
        self._set_button_idle()
        self.url_box.configure(state="normal")
        self.progress_bar.set(0)
        self._set_queue_label(f"{done} / {total} done")

        kind = "MP3" if is_audio else "Premiere Pro-ready MP4"

        if cancelled:
            self._set_status(f"Cancelled — {done} of {total} finished")
            messagebox.showinfo("Cancelled", f"Stopped after {done} of {total} downloads.")
            return

        if failures:
            self._set_status(f"{done} of {total} succeeded · {len(failures)} failed")
            preview = "\n\n".join(f"{u}\n{err}" for u, err in failures[:5])
            if len(failures) > 5:
                preview += f"\n\n…and {len(failures) - 5} more."
            messagebox.showwarning(
                "Finished with errors",
                f"Saved {done} of {total} to:\n{self.output_dir}\n\n"
                f"Failed ({len(failures)}):\n\n{preview}",
            )
            return

        self.queue_bar.set(1.0)
        self._set_status(f"Done — {done} file{'s' if done != 1 else ''} ready!")
        body = (f"{done} {kind} file{'s' if done != 1 else ''} saved to:\n"
                f"{self.output_dir}")
        if messagebox.askyesno("Complete", body + "\n\nOpen the folder now?"):
            self._open_folder(self.output_dir)


if __name__ == "__main__":
    App().mainloop()
