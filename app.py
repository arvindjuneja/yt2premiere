#!/usr/bin/env python3
"""YT → Premiere — Download YouTube clips as Premiere Pro-ready MP4s."""

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

import yt_dlp

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

IS_WINDOWS = platform.system() == "Windows"
MONO_FONT = "Consolas" if IS_WINDOWS else "Menlo"

# Hide console windows when spawning ffmpeg/ffprobe on Windows
_SP_KWARGS: dict = {}
if IS_WINDOWS:
    _SP_KWARGS["creationflags"] = subprocess.CREATE_NO_WINDOW

DEFAULT_OUTPUT_DIR = str(Path.home() / "Downloads")

QUALITY_OPTIONS = [
    "Best available (up to 4K)",
    "1080p Full HD",
    "720p HD",
    "480p SD",
    "Audio only (MP3)",
]

QUALITY_FORMATS = {
    "Best available (up to 4K)": "bestvideo+bestaudio/best",
    "1080p Full HD":             "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "720p HD":                   "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "480p SD":                   "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "Audio only (MP3)":          "bestaudio/best",
}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("YT → Premiere")
        self.geometry("700x580")
        self.minsize(600, 540)

        self.output_dir = DEFAULT_OUTPUT_DIR
        self.downloading = False

        self._build_ui()

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
            text_color=("gray50", "gray60"),
            wraplength=600, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # ── URL ──────────────────────────────────────────────────────────
        url_frame = ctk.CTkFrame(self, corner_radius=12)
        url_frame.grid(row=1, column=0, sticky="ew", padx=32, pady=(20, 0))
        url_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            url_frame, text="YOUTUBE URL",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray50", "gray60"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 4))

        self.url_entry = ctk.CTkEntry(
            url_frame, placeholder_text="https://www.youtube.com/watch?v=…",
            height=42, font=ctk.CTkFont(family=MONO_FONT, size=13),
            corner_radius=8,
        )
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(16, 8), pady=(0, 14))

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
            text_color=("gray50", "gray60"),
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
        ).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 14))

        dir_inner = ctk.CTkFrame(opt_frame, fg_color="transparent")
        dir_inner.grid(row=2, column=1, sticky="e", padx=16, pady=(0, 14))

        self.dir_label = ctk.CTkLabel(
            dir_inner, text=self._short_path(self.output_dir),
            font=ctk.CTkFont(family=MONO_FONT, size=12),
            text_color=("gray30", "gray70"),
        )
        self.dir_label.pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            dir_inner, text="Browse…", width=80, height=32,
            corner_radius=8, fg_color="transparent",
            border_width=1, border_color=("gray70", "gray30"),
            hover_color=("gray85", "gray25"),
            text_color=("gray30", "gray70"),
            command=self._browse_dir,
        ).pack(side="left")

        # ── Progress ─────────────────────────────────────────────────────
        prog_frame = ctk.CTkFrame(self, corner_radius=12)
        prog_frame.grid(row=3, column=0, sticky="ew", padx=32, pady=(14, 0))
        prog_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            prog_frame, text="PROGRESS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray50", "gray60"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 6))

        self.progress_bar = ctk.CTkProgressBar(
            prog_frame, height=10, corner_radius=5,
        )
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4))
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(
            prog_frame, text="Ready",
            font=ctk.CTkFont(size=12),
            text_color=("gray50", "gray60"),
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
        return p.replace(home, "~") if p.startswith(home) else p

    def _set_status(self, text: str):
        self.status_label.configure(text=text)

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
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please enter a YouTube URL.")
            return
        if not self._validate_url(url):
            messagebox.showwarning("Invalid URL", "That doesn't look like a valid YouTube URL.")
            return
        if self.downloading:
            return

        self.downloading = True
        self.download_btn.configure(state="disabled", fg_color="gray40")
        self.progress_bar.set(0)
        self._set_status("Starting download…")

        threading.Thread(target=self._download, args=(url,), daemon=True).start()

    def _progress_hook(self, d: dict):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                self.progress_bar.set(downloaded / total)
            speed = d.get("_speed_str", "")
            eta = d.get("_eta_str", "")
            self._set_status(f"Downloading… {speed}  ETA {eta}")
        elif d["status"] == "finished":
            self._set_status("Merging streams…")
            self.progress_bar.set(1.0)

    # ── Codec detection & conversion ─────────────────────────────────────

    def _get_codecs(self, filepath: str) -> tuple[str, str]:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return ("", "")
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
        self._set_status(f"Converting {label} for Premiere Pro…")

        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
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

        subprocess.run(cmd, capture_output=True, timeout=600, **_SP_KWARGS)

        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            os.remove(filepath)
            os.rename(out_path, filepath)

        return filepath

    # ── Download ─────────────────────────────────────────────────────────

    def _download(self, url: str):
        quality_key = self.quality_var.get()
        fmt = QUALITY_FORMATS[quality_key]
        is_audio = quality_key == "Audio only (MP3)"

        outtmpl = os.path.join(self.output_dir, "%(title)s.%(ext)s")

        ydl_opts: dict = {
            "format": fmt,
            "outtmpl": outtmpl,
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
        }

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

            self.after(0, self._on_success)
        except Exception as exc:
            self.after(0, self._on_error, str(exc))

    def _on_success(self):
        self.downloading = False
        self.download_btn.configure(state="normal", fg_color=("#3a7ebf", "#1f538d"))
        self._set_status("Done — Premiere Pro ready!")
        self.progress_bar.set(1.0)
        messagebox.showinfo(
            "Complete",
            f"Premiere Pro-ready MP4 saved to:\n{self.output_dir}",
        )

    def _on_error(self, msg: str):
        self.downloading = False
        self.download_btn.configure(state="normal", fg_color=("#3a7ebf", "#1f538d"))
        self._set_status("Error")
        self.progress_bar.set(0)
        messagebox.showerror("Download Error", msg)


if __name__ == "__main__":
    App().mainloop()
