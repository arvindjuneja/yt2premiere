#!/usr/bin/env python3
"""YT → Premiere — Download YouTube clips as Premiere Pro-ready MP4s."""

import os
import platform
import re
import shutil
import subprocess
import sys
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

QUALITY_FORMATS = {
    "Best available (up to 4K)": "bestvideo+bestaudio/best",
    "1080p Full HD":             "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "720p HD":                   "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "480p SD":                   "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "Audio only (MP3)":          "bestaudio/best",
}


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
        self.geometry("700x600")
        self.minsize(600, 560)

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

        self._build_ui()

        # Start ready to type, and let Enter trigger a download.
        self.after(100, self.url_entry.focus_set)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)

        # ── Header ───────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=32, pady=(28, 0))

        ctk.CTkLabel(
            header, text="YT  →  Premiere",
            font=ctk.CTkFont(size=32, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="Paste a YouTube link, pick quality, and get a Premiere Pro-ready MP4 (H.264 + AAC).",
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
            wraplength=600, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # ── URL ──────────────────────────────────────────────────────────
        url_frame = ctk.CTkFrame(self, corner_radius=12)
        url_frame.grid(row=1, column=0, sticky="ew", padx=32, pady=(20, 0))
        url_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            url_frame, text="YOUTUBE URL",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 4))

        self.url_entry = ctk.CTkEntry(
            url_frame, placeholder_text="https://www.youtube.com/watch?v=…",
            height=42, font=ctk.CTkFont(family=MONO_FONT, size=13),
            corner_radius=8,
        )
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(16, 8), pady=(0, 14))
        self.url_entry.bind("<Return>", lambda _e: self._start_download())

        ctk.CTkButton(
            url_frame, text="Paste", width=70, height=42,
            corner_radius=8, fg_color="transparent",
            border_width=1, border_color=("gray70", "gray30"),
            hover_color=("gray85", "gray25"),
            text_color=("gray30", "gray70"),
            command=self._paste_url,
        ).grid(row=1, column=1, sticky="e", padx=(0, 16), pady=(0, 14))

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

        self.quality_var = ctk.StringVar(value=QUALITY_OPTIONS[0])
        self.quality_menu = ctk.CTkOptionMenu(
            opt_frame, variable=self.quality_var, values=QUALITY_OPTIONS,
            width=240, height=36, corner_radius=8,
            font=ctk.CTkFont(size=13),
            dropdown_font=ctk.CTkFont(size=13),
        )
        self.quality_menu.grid(row=1, column=1, sticky="e", padx=16, pady=(0, 10))

        ctk.CTkLabel(
            opt_frame, text="Save to",
            font=ctk.CTkFont(size=13),
        ).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 10))

        dir_inner = ctk.CTkFrame(opt_frame, fg_color="transparent")
        dir_inner.grid(row=2, column=1, sticky="e", padx=16, pady=(0, 10))

        self.dir_label = ctk.CTkLabel(
            dir_inner, text=self._short_path(self.output_dir),
            font=ctk.CTkFont(family=MONO_FONT, size=12),
            text_color=("gray30", "gray70"),
        )
        self.dir_label.pack(side="left", padx=(0, 10))
        _Tooltip(self.dir_label, lambda: self.output_dir)

        ctk.CTkButton(
            dir_inner, text="Browse…", width=80, height=32,
            corner_radius=8, fg_color="transparent",
            border_width=1, border_color=("gray70", "gray30"),
            hover_color=("gray85", "gray25"),
            text_color=("gray30", "gray70"),
            command=self._browse_dir,
        ).pack(side="left")

        ctk.CTkLabel(
            opt_frame, text="Appearance",
            font=ctk.CTkFont(size=13),
        ).grid(row=3, column=0, sticky="w", padx=16, pady=(0, 14))

        self.appearance_seg = ctk.CTkSegmentedButton(
            opt_frame, values=["Light", "Dark"],
            command=self._set_appearance,
            font=ctk.CTkFont(size=12),
        )
        self.appearance_seg.set("Dark")
        self.appearance_seg.grid(row=3, column=1, sticky="e", padx=16, pady=(0, 14))

        # ── Progress ─────────────────────────────────────────────────────
        prog_frame = ctk.CTkFrame(self, corner_radius=12)
        prog_frame.grid(row=3, column=0, sticky="ew", padx=32, pady=(14, 0))
        prog_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            prog_frame, text="PROGRESS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))

        self.progress_bar = ctk.CTkProgressBar(
            prog_frame, height=10, corner_radius=5,
        )
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4))
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(
            prog_frame, text="Ready",
            font=ctk.CTkFont(size=12),
            text_color=MUTED,
        )
        self.status_label.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 14))

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

    def _set_status(self, text: str):
        self.status_label.configure(text=text)

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
        if "video unavailable" in low or "not available" in low:
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
        if ("getaddrinfo" in low or "failed to resolve" in low
                or "connection" in low or "timed out" in low or "urlopen" in low):
            return "Network problem — check your internet connection and try again."
        # Fallback: keep it short, drop the noisy "ERROR:" prefix.
        clean = msg.replace("ERROR:", "").strip()
        if len(clean) > 200:
            clean = clean[:200] + "…"
        return clean or "Something went wrong. Please try again."

    # ── Actions ───────────────────────────────────────────────────────────

    def _browse_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.output_dir)
        if chosen:
            self.output_dir = chosen
            self.dir_label.configure(text=self._short_path(chosen))

    def _paste_url(self):
        try:
            clipboard = self.clipboard_get()
            if clipboard:
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, clipboard.strip())
        except Exception:
            pass

    def _validate_url(self, url: str) -> bool:
        pattern = r"(https?://)?(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/.+"
        return bool(re.match(pattern, url.strip()))

    def _start_download(self):
        if self.downloading:
            return
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please enter a YouTube URL.")
            return
        if not self._validate_url(url):
            messagebox.showwarning("Invalid URL", "That doesn't look like a valid YouTube URL.")
            return

        self.downloading = True
        self.cancel_requested = False
        self._set_button_busy()
        self._progress_indeterminate(False)
        self.progress_bar.set(0)
        self._set_status("Starting download…")

        threading.Thread(target=self._download, args=(url,), daemon=True).start()

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
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                self.progress_bar.set(downloaded / total)
            speed = d.get("_speed_str", "")
            eta = d.get("_eta_str", "")
            self._set_status(f"Downloading… {speed}  ETA {eta}")
        elif d["status"] == "finished":
            # Download done; what follows (merge / audio extract / re-encode)
            # has no byte-level progress, so switch to an animated bar.
            self.after(0, lambda: self._set_status("Merging & preparing…"))
            self.after(0, lambda: self._progress_indeterminate(True))

    # ── Codec detection & conversion ─────────────────────────────────────

    def _get_codecs(self, filepath: str) -> tuple[str, str]:
        ffprobe = FFPROBE
        try:
            vr = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                capture_output=True, text=True, timeout=10, **_SP_KWARGS,
            )
            ar = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_name",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                capture_output=True, text=True, timeout=10, **_SP_KWARGS,
            )
            return (vr.stdout.strip().lower(), ar.stdout.strip().lower())
        except Exception:
            return ("", "")

    def _ensure_compatible(self, filepath: str) -> str:
        vcodec, acodec = self._get_codecs(filepath)
        video_ok = vcodec in ("h264", "")
        audio_ok = acodec in ("aac", "")

        if video_ok and audio_ok:
            return filepath

        base, _ = os.path.splitext(filepath)
        out_path = base + "_compat.mp4"

        parts = ["video" if not video_ok else "", "audio" if not audio_ok else ""]
        label = " & ".join(p for p in parts if p)
        self.after(0, self._set_status, f"Converting {label} for Premiere Pro…")

        ffmpeg = FFMPEG
        cmd = [
            ffmpeg, "-y", "-i", filepath,
            "-c:v", "libx264" if not video_ok else "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
        ]
        if not video_ok:
            cmd += ["-preset", "medium", "-crf", "20"]
        cmd.append(out_path)

        self._run_cancellable(cmd, timeout=600)

        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            os.remove(filepath)
            os.rename(out_path, filepath)

        return filepath

    def _run_cancellable(self, cmd: list, timeout: int = 600):
        """Run a subprocess so it can be killed when the user hits Cancel."""
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_SP_KWARGS,
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
        finally:
            self._proc = None

    # ── Download ─────────────────────────────────────────────────────────

    def _download(self, url: str):
        quality_key = self.quality_var.get()
        fmt = QUALITY_FORMATS[quality_key]
        is_audio = quality_key == AUDIO_ONLY

        outtmpl = os.path.join(self.output_dir, "%(title)s.%(ext)s")

        ydl_opts: dict = {
            "format": fmt,
            "outtmpl": outtmpl,
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
        }

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

        try:
            downloaded_file = None
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    downloaded_file = ydl.prepare_filename(info)
                    base, _ = os.path.splitext(downloaded_file)
                    mp4_path = base + ".mp4"
                    if os.path.isfile(mp4_path):
                        downloaded_file = mp4_path

            if not is_audio and downloaded_file and os.path.isfile(downloaded_file):
                self._ensure_compatible(downloaded_file)

            self.after(0, lambda: self._on_success(is_audio))
        except _Cancelled:
            self.after(0, self._on_cancelled)
        except Exception as exc:
            self.after(0, self._on_error, str(exc))

    def _on_success(self, is_audio: bool):
        self.downloading = False
        self._progress_indeterminate(False)
        self._set_button_idle()
        self.progress_bar.set(1.0)

        if is_audio:
            self._set_status("Done — MP3 saved!")
            body = f"MP3 audio saved to:\n{self.output_dir}"
        else:
            self._set_status("Done — Premiere Pro ready!")
            body = f"Premiere Pro-ready MP4 saved to:\n{self.output_dir}"

        if messagebox.askyesno("Complete", body + "\n\nOpen the folder now?"):
            self._open_folder(self.output_dir)

    def _on_cancelled(self):
        self.downloading = False
        self.cancel_requested = False
        self._progress_indeterminate(False)
        self._set_button_idle()
        self.progress_bar.set(0)
        self._set_status("Cancelled")

    def _on_error(self, msg: str):
        self.downloading = False
        self._progress_indeterminate(False)
        self._set_button_idle()
        self.progress_bar.set(0)
        self._set_status("Error")
        messagebox.showerror("Download Error", self._humanize_error(msg))


if __name__ == "__main__":
    App().mainloop()
