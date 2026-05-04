#!/usr/bin/env python3
"""
wfrp_to_tow_gui.py
==================
Graphical front-end for wfrp_to_tow_bulk.py.

Styled after the Warhammer: The Old World Foundry VTT character sheet:
  - Deep-teal outer chrome
  - Parchment content panels
  - Dark-navy section headers with gold lettering
  - Amber accents and borders
  - Georgia serif typography

Run:
    python wfrp_to_tow_gui.py
"""

from __future__ import annotations

import copy
import json
import logging
import queue
import re
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# ── Require converter module (must live in the same directory) ────────────────
try:
    import wfrp_to_tow_bulk as converter
except ImportError:
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        "Missing module",
        "wfrp_to_tow_bulk.py must be in the same folder as this script.",
    )
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# PALETTE — drawn from the TOW character-sheet screenshot
# ─────────────────────────────────────────────────────────────────────────────

C_OUTER   = "#1d4b5a"   # deep teal — outer window chrome
C_PANEL   = "#f0e3bc"   # parchment — all content areas
C_HDR     = "#162b38"   # dark navy — title bar & section header rows
C_SUBHDR  = "#1f566a"   # mid teal — button hover, radio active
C_LOG_BG  = "#16252f"   # near-black teal — log console
C_DARK    = "#251a08"   # dark sepia — body text on parchment
C_GOLD    = "#c9a84c"   # amber/gold — decorative accents
C_BORDER  = "#8b6914"   # dark gold — widget borders / rule lines
C_BTN     = "#1d4b5a"   # button face (idle)
C_BTN_HOV = "#2b8aaa"   # button face (hovered)
C_LOG_FG  = "#9dd4a8"   # log normal text (soft green)
C_LOG_ERR = "#d97060"   # log error text (reddish)
C_LOG_WRN = "#e0b560"   # log warning text (amber)
C_LOG_DBG = "#5d8a96"   # log debug text (muted teal)
C_LOG_SUM = "#c9a84c"   # log summary text (gold — same as C_GOLD)
C_HINT    = "#7a6030"   # hint/italic text on parchment
C_RED     = "#c41e24"   # crimson red  — "WARHAMMER" title text

FT_TITLE  = ("Georgia", 17, "bold")
FT_SECT   = ("Georgia", 12, "bold")
FT_LABEL  = ("Georgia", 12)
FT_ENTRY  = ("Calibri", 12)
FT_LOG    = ("Consolas", 9)
FT_BTN    = ("Georgia", 13, "bold")
FT_HINT   = ("Georgia", 10, "italic")
FT_MINI   = ("Georgia", 9)


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING → QUEUE BRIDGE
# ─────────────────────────────────────────────────────────────────────────────

class _QueueLogHandler(logging.Handler):
    """Puts formatted log records into a queue consumed by the GUI thread."""

    def __init__(self, q: queue.Queue) -> None:
        super().__init__()
        self._q = q

    def emit(self, record: logging.LogRecord) -> None:
        self._q.put(("log", record.levelname, self.format(record)))


# ─────────────────────────────────────────────────────────────────────────────
# MISC HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    """Remove basic HTML tags for card-view display."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(str(val))
    except (ValueError, TypeError):
        return default


def _skill_display_val(v) -> int:
    """Return a displayable integer for a skill value that may be int or {base, modifier}."""
    if isinstance(v, dict):
        return int(v.get("base", 0)) + int(v.get("modifier", 0))
    return int(v) if isinstance(v, (int, float)) else 0


def _iter_skill_entries(skills_raw):
    """Yield (key, value) pairs from skills stored as a dict or list."""
    if isinstance(skills_raw, dict):
        return list(skills_raw.items())

    # Some data sources store skills as an array of objects.
    # Support common forms like:
    #   [{"key": "ath", "value": 2}, ...]
    #   [{"name": "ath", "base": 1, "modifier": 1}, ...]
    if isinstance(skills_raw, list):
        out = []
        for entry in skills_raw:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key") or entry.get("name") or entry.get("id")
            if not isinstance(key, str) or not key.strip():
                continue
            if "value" in entry:
                val = entry.get("value")
            else:
                val = {"base": entry.get("base", 0), "modifier": entry.get("modifier", 0)}
            out.append((key.strip().lower(), val))
        return out

    return []


# ─────────────────────────────────────────────────────────────────────────────
# WIDGET HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _make_section(parent: tk.Widget, title: str) -> tk.Frame:
    """
    Pack a dark header row labelled `title` (gold text) plus a parchment
    content frame into `parent`, then return the content frame.
    """
    # Header row: dark bg + gold label
    hdr = tk.Frame(parent, bg=C_HDR, pady=5)
    hdr.pack(fill="x", pady=(6, 0))
    tk.Label(
        hdr, text=f"  {title.upper()}",
        font=FT_SECT, bg=C_HDR, fg=C_GOLD,
    ).pack(side="left")

    # 1-px gold rule beneath header
    tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x")

    # Parchment body
    body = tk.Frame(parent, bg=C_PANEL, padx=12, pady=8)
    body.pack(fill="x")
    return body


def _browse_button(parent: tk.Widget, command) -> tk.Button:
    """Small 'Browse…' button with teal style and hover effect."""
    btn = tk.Button(
        parent, text="Browse…", command=command,
        bg=C_BTN, fg=C_GOLD, relief="flat",
        font=("Georgia", 9, "bold"), padx=7, pady=2,
        cursor="hand2",
        activebackground=C_BTN_HOV, activeforeground=C_GOLD,
    )
    btn.bind("<Enter>", lambda _e: btn.config(bg=C_BTN_HOV))
    btn.bind("<Leave>", lambda _e: btn.config(bg=C_BTN))
    return btn


def _path_row(
    parent: tk.Widget, label: str, var: tk.StringVar, row: int
) -> tk.Entry:
    """
    Place a label + entry into `parent` grid at the given row.
    `parent` must have columnconfigure(1, weight=1) for the entry to expand.
    Returns the Entry widget (already gridded).
    """
    tk.Label(
        parent, text=label, font=FT_LABEL, bg=C_PANEL, fg=C_DARK,
    ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)

    entry = tk.Entry(
        parent, textvariable=var,
        bg="#f8f0d6", fg=C_DARK, insertbackground=C_DARK,
        font=FT_ENTRY, relief="solid", bd=1,
        highlightbackground=C_BORDER, highlightthickness=1,
        disabledbackground="#e0d4aa", disabledforeground="#7a6030",
    )
    entry.grid(row=row, column=1, sticky="ew", pady=3)
    return entry


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    """Top-level GUI window for the WFRP → TOW bulk converter."""

    def __init__(self) -> None:
        super().__init__()
        self.title("WFRP → TOW Bulk Converter")
        self.configure(bg=C_OUTER)
        self.minsize(820, 720)
        self.geometry("1080x840")
        self.resizable(True, True)

        # ── Application state ──────────────────────────────────────────────
        self.input_mode   = tk.StringVar(value="file")   # "file" | "folder"
        self.input_var    = tk.StringVar()
        self.output_var   = tk.StringVar()
        self.config_var   = tk.StringVar()
        self.opt_backup   = tk.BooleanVar(value=False)
        self.opt_validate = tk.BooleanVar(value=True)
        self.opt_verbose  = tk.BooleanVar(value=False)
        self._progress    = tk.DoubleVar(value=0.0)
        self._log_queue: queue.Queue = queue.Queue()
        self._converting        = False
        self._last_output_path: Path | None = None

        # ── ttk style for progress bar ─────────────────────────────────────
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "TOW.Horizontal.TProgressbar",
            troughcolor=C_HDR,
            background=C_GOLD,
            bordercolor=C_BORDER,
            lightcolor=C_GOLD,
            darkcolor="#9a7828",
            thickness=12,
        )

        self._build_ui()
        self._poll_log()  # start the 100 ms polling loop

    # ── UI CONSTRUCTION ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_title_bar()
        self._build_content()
        self._build_log_panel()

    def _build_title_bar(self) -> None:
        bar = tk.Frame(self, bg=C_HDR)
        bar.pack(fill="x")

        tk.Frame(bar, bg=C_GOLD, height=3).pack(fill="x")           # top gold rule

        inner = tk.Frame(bar, bg=C_HDR, pady=10)
        inner.pack(fill="x")

        # "WARHAMMER" — large crimson bold, mirroring the book cover
        tk.Label(
            inner, text="WARHAMMER",
            font=("Georgia", 26, "bold"), bg=C_HDR, fg=C_RED,
        ).pack()

        # ─── The Old World ─── decorative banner line
        banner = tk.Frame(inner, bg=C_HDR)
        banner.pack(pady=(1, 0))
        tk.Label(banner, text="───  ", font=("Georgia", 8), bg=C_HDR, fg=C_BORDER).pack(side="left")
        tk.Label(banner, text="The Old World", font=("Georgia", 14, "bold italic"), bg=C_HDR, fg=C_GOLD).pack(side="left")
        tk.Label(banner, text="  ───", font=("Georgia", 8), bg=C_HDR, fg=C_BORDER).pack(side="left")

        # "ROLEPLAYING GAME"
        tk.Label(
            inner, text="R O L E P L A Y I N G   G A M E",
            font=("Georgia", 10, "bold"), bg=C_HDR, fg=C_RED,
        ).pack(pady=(2, 0))

        # Subtitle
        tk.Label(
            inner,
            text="WFRP4e  ·  Foundry VTT Compendium Converter",
            font=("Georgia", 9, "italic"), bg=C_HDR, fg="#7ab0c0",
        ).pack(pady=(5, 0))

        tk.Frame(bar, bg=C_GOLD, height=3).pack(fill="x")           # bottom gold rule

    def _build_content(self) -> None:
        # A thin gold border wraps the entire content card
        card_border = tk.Frame(self, bg=C_BORDER, padx=2, pady=2)
        card_border.pack(fill="x", padx=252, pady=8)

        panel = tk.Frame(card_border, bg=C_OUTER)
        panel.pack(fill="x")

        # ── INPUT section ─────────────────────────────────────────────────
        body_in = _make_section(panel, "Input")
        body_in.columnconfigure(1, weight=1)

        # File / Folder radio buttons
        rb_row = tk.Frame(body_in, bg=C_PANEL)
        rb_row.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        for label, value in (("Single File", "file"), ("Folder (batch)", "folder")):
            tk.Radiobutton(
                rb_row, text=label, variable=self.input_mode, value=value,
                command=self._on_mode_change,
                bg=C_PANEL, fg=C_DARK, selectcolor=C_PANEL,
                activebackground=C_PANEL, activeforeground=C_SUBHDR,
                font=FT_LABEL,
            ).pack(side="left", padx=(0, 20))

        # Path entry + browse
        _path_row(body_in, "Path:", self.input_var, row=1)
        _browse_button(body_in, self._browse_input).grid(
            row=1, column=2, padx=(6, 0), pady=3,
        )

        # ── OUTPUT section ────────────────────────────────────────────────
        body_out = _make_section(panel, "Output")
        body_out.columnconfigure(1, weight=1)

        _path_row(body_out, "Path:", self.output_var, row=0)
        _browse_button(body_out, self._browse_output).grid(
            row=0, column=2, padx=(6, 0), pady=3,
        )

        self._out_hint = tk.Label(
            body_out,
            text="Provide a .json file path for single-file input, or a folder for batch.",
            font=FT_HINT, bg=C_PANEL, fg=C_HINT,
        )
        self._out_hint.grid(row=1, column=0, columnspan=3, sticky="w")

        # ── OPTIONS section ───────────────────────────────────────────────
        body_opt = _make_section(panel, "Options")
        body_opt.columnconfigure(1, weight=1)

        # Checkboxes
        ck_row = tk.Frame(body_opt, bg=C_PANEL)
        ck_row.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        for text, var in (
            ("Backup Originals", self.opt_backup),
            ("Validate Output",  self.opt_validate),
            ("Verbose Logging",  self.opt_verbose),
        ):
            tk.Checkbutton(
                ck_row, text=text, variable=var,
                bg=C_PANEL, fg=C_DARK, selectcolor=C_PANEL,
                activebackground=C_PANEL, activeforeground=C_SUBHDR,
                font=FT_LABEL,
            ).pack(side="left", padx=(0, 20))

        # Config-override file
        _path_row(body_opt, "Config:", self.config_var, row=1)
        _browse_button(body_opt, self._browse_config).grid(
            row=1, column=2, padx=(6, 0), pady=3,
        )
        tk.Label(
            body_opt,
            text="Optional JSON file with skill_map / weapon_group_map / quality_map overrides.",
            font=FT_HINT, bg=C_PANEL, fg=C_HINT,
        ).grid(row=2, column=0, columnspan=3, sticky="w")

        # ── CONVERT button (gold-bordered) ────────────────────────────────
        btn_wrap = tk.Frame(panel, bg=C_GOLD, padx=1, pady=1)
        btn_wrap.pack(fill="x", padx=12, pady=10)

        self._convert_btn = tk.Button(
            btn_wrap,
            text="▶▶   CONVERT   ◀◀",
            font=FT_BTN,
            bg=C_HDR, fg=C_GOLD,
            activebackground=C_SUBHDR, activeforeground=C_GOLD,
            relief="flat", bd=0,
            padx=24, pady=9,
            cursor="hand2",
            command=self._start_conversion,
        )
        self._convert_btn.pack(fill="x")
        self._convert_btn.bind("<Enter>", lambda _e: self._convert_btn.config(bg=C_SUBHDR))
        self._convert_btn.bind("<Leave>", lambda _e: self._convert_btn.config(bg=C_HDR))

        # Progress bar (sits below the button, inside the outer panel)
        self._progress_bar = ttk.Progressbar(
            panel, variable=self._progress,
            maximum=100, style="TOW.Horizontal.TProgressbar",
        )
        self._progress_bar.pack(fill="x", padx=12, pady=(0, 6))

        # Preview button wrapper — created here, shown via pack() after a successful conversion
        self._preview_btn_wrap = tk.Frame(panel, bg=C_OUTER)
        self._preview_btn = tk.Button(
            self._preview_btn_wrap,
            text="🔍   Preview Results",
            font=("Georgia", 10, "bold"),
            bg=C_SUBHDR, fg=C_GOLD,
            activebackground=C_BTN_HOV, activeforeground=C_GOLD,
            relief="flat", bd=0, padx=14, pady=5,
            cursor="hand2",
            command=self._open_preview,
        )
        self._preview_btn.pack(side="right")
        # (wrapper is pack()ed in _on_done after a successful single-file conversion)

    def _build_log_panel(self) -> None:
        # Section header row
        log_hdr = tk.Frame(self, bg=C_HDR, pady=5)
        log_hdr.pack(fill="x", padx=12, pady=(0, 0))
        tk.Label(
            log_hdr, text="  CONVERSION LOG",
            font=FT_SECT, bg=C_HDR, fg=C_GOLD,
        ).pack(side="left")

        # Clear button (right-aligned in the header)
        tk.Button(
            log_hdr, text="Clear",
            command=self._clear_log,
            bg=C_HDR, fg="#7ab0c0", relief="flat",
            font=("Calibri", 8), padx=6, cursor="hand2",
            activebackground=C_SUBHDR, activeforeground=C_GOLD,
        ).pack(side="right", padx=8)

        # 1-px gold rule
        tk.Frame(self, bg=C_BORDER, height=1).pack(fill="x", padx=12)

        # Log console frame — expands to fill remaining vertical space
        log_frame = tk.Frame(self, bg=C_LOG_BG, padx=6, pady=6)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        scrollbar = tk.Scrollbar(log_frame, bg=C_HDR, troughcolor=C_HDR)
        scrollbar.pack(side="right", fill="y")

        self._log_text = tk.Text(
            log_frame,
            bg=C_LOG_BG, fg=C_LOG_FG,
            font=FT_LOG, wrap="word",
            relief="flat", bd=0,
            state="disabled",
            insertbackground=C_LOG_FG,
            yscrollcommand=scrollbar.set,
        )
        self._log_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self._log_text.yview)

        # Colour tags for each log level
        self._log_text.tag_configure("DEBUG",   foreground=C_LOG_DBG)
        self._log_text.tag_configure("INFO",    foreground=C_LOG_FG)
        self._log_text.tag_configure("WARNING", foreground=C_LOG_WRN)
        self._log_text.tag_configure("ERROR",   foreground=C_LOG_ERR)
        self._log_text.tag_configure("CRITICAL",foreground=C_LOG_ERR, font=(FT_LOG[0], FT_LOG[1], "bold"))
        self._log_text.tag_configure("SUMMARY", foreground=C_LOG_SUM)

    # ── BROWSE CALLBACKS ──────────────────────────────────────────────────────

    def _browse_input(self) -> None:
        if self.input_mode.get() == "folder":
            path = filedialog.askdirectory(title="Select input folder")
        else:
            path = filedialog.askopenfilename(
                title="Select input JSON file",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            )
        if path:
            self.input_var.set(path)

    def _browse_output(self) -> None:
        if self.input_mode.get() == "folder":
            path = filedialog.askdirectory(title="Select output folder")
        else:
            path = filedialog.asksaveasfilename(
                title="Save converted JSON as…",
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            )
        if path:
            self.output_var.set(path)

    def _browse_config(self) -> None:
        path = filedialog.askopenfilename(
            title="Select config JSON file",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.config_var.set(path)

    def _on_mode_change(self) -> None:
        mode = self.input_mode.get()
        if mode == "folder":
            self._out_hint.config(
                text="Output: select a folder — each converted file is written there with its original name."
            )
        else:
            self._out_hint.config(
                text="Output: select a .json file path for the single converted document."
            )

    # ── LOG HELPERS ───────────────────────────────────────────────────────────

    def _append_log(self, levelname: str, text: str) -> None:
        self._log_text.config(state="normal")
        self._log_text.insert("end", text + "\n", levelname)
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    def _clear_log(self) -> None:
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")

    def _poll_log(self) -> None:
        """
        Drain the log queue in the main thread.  Called every 100 ms via after().
        Queue items are tuples:
          ("log",      levelname, message)
          ("progress", float_0_to_100)
          ("done",     ConversionStats)
        """
        try:
            while True:
                item = self._log_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    _, level, msg = item
                    self._append_log(level, msg)
                elif kind == "progress":
                    self._progress.set(item[1])
                elif kind == "done":
                    self._on_done(item[1])
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    # ── CONVERSION ────────────────────────────────────────────────────────────

    def _start_conversion(self) -> None:
        if self._converting:
            return

        in_str  = self.input_var.get().strip()
        out_str = self.output_var.get().strip()

        if not in_str:
            messagebox.showwarning("Missing Input", "Please select an input file or folder.")
            return
        if not out_str:
            messagebox.showwarning("Missing Output", "Please enter an output path.")
            return

        in_path  = Path(in_str)
        out_path = Path(out_str)
        self._last_output_path = out_path

        if not in_path.exists():
            messagebox.showerror("Not Found", f"Input path does not exist:\n{in_path}")
            return

        cfg_str  = self.config_var.get().strip()
        cfg_path = Path(cfg_str) if cfg_str else None

        self._converting = True
        self._convert_btn.config(state="disabled", text="⏳  Converting…", bg=C_SUBHDR)
        self._progress.set(0)
        self._clear_log()

        threading.Thread(
            target=self._run_conversion,
            args=(in_path, out_path, cfg_path),
            daemon=True,
        ).start()

    def _run_conversion(
        self,
        in_path: Path,
        out_path: Path,
        cfg_path: Path | None,
    ) -> None:
        """
        Worker thread: performs the full conversion and posts results to
        self._log_queue.  Must NOT touch any tk widgets directly.
        """
        # ── Set up logging to route to the GUI queue ──────────────────────
        log = logging.getLogger("wfrp_tow")
        log.setLevel(logging.DEBUG if self.opt_verbose.get() else logging.INFO)
        for h in log.handlers[:]:
            log.removeHandler(h)
        handler = _QueueLogHandler(self._log_queue)
        handler.setFormatter(logging.Formatter("%(levelname)-8s  %(message)s"))
        log.addHandler(handler)
        log.propagate = False

        cfg   = converter.load_config(cfg_path)
        stats = converter.ConversionStats()

        backup   = self.opt_backup.get()
        validate = self.opt_validate.get()

        try:
            if in_path.is_dir():
                files = sorted(in_path.glob("*.json"))
                total = len(files)
                if total == 0:
                    log.warning("No .json files found in '%s'", in_path)
                    self._log_queue.put(("progress", 100.0))
                else:
                    out_path.mkdir(parents=True, exist_ok=True)
                    for i, f in enumerate(files):
                        converter.convert_file(
                            f, out_path / f.name, cfg, stats,
                            backup=backup, validate=validate,
                        )
                        self._log_queue.put(("progress", (i + 1) / total * 100.0))
            else:
                self._log_queue.put(("progress", 10.0))
                converter.convert_file(
                    in_path, out_path, cfg, stats,
                    backup=backup, validate=validate,
                )
                self._log_queue.put(("progress", 100.0))

        except Exception as exc:                        # last-resort catch
            log.error("Unexpected error: %s", exc)

        # ── Emit the summary report as SUMMARY-tagged lines ───────────────
        for line in stats.report().splitlines():
            self._log_queue.put(("log", "SUMMARY", line))

        self._log_queue.put(("done", stats))

    def _on_done(self, stats: converter.ConversionStats) -> None:
        """Called in the main thread once the worker posts a 'done' message."""
        self._converting = False
        self._convert_btn.config(
            state="normal", text="▶▶   CONVERT   ◀◀", bg=C_HDR,
        )
        if stats.errors:
            messagebox.showerror(
                "Conversion completed with errors",
                f"{len(stats.errors)} error(s) encountered.\nSee the Conversion Log for details.",
            )
        else:
            # Reveal the Preview Results button for single .json output files
            if (
                self._last_output_path is not None
                and self._last_output_path.suffix.lower() == ".json"
                and self._last_output_path.exists()
            ):
                self._preview_btn_wrap.pack(fill="x", padx=12, pady=(0, 8))

            # Build completion message — include library summary when present
            msg_lines = [
                f"✓  {stats.documents_converted} document(s) converted successfully.",
            ]
            if stats.library_counts:
                total_lib = sum(stats.library_counts.values())
                buckets_summary = ", ".join(
                    f"{stats.library_counts[b]} {b}"
                    for b in sorted(stats.library_counts)
                )
                msg_lines.append("")
                msg_lines.append(
                    f"📦  {total_lib} unique library items extracted ({buckets_summary})."
                )
                msg_lines.append("")
                msg_lines.append("Import order for Foundry:")
                for fname in stats.library_files:
                    msg_lines.append(f"  1.  {fname}")
                msg_lines.append(
                    f"  2.  {self._last_output_path.name if self._last_output_path else 'actors file'}"
                )
            messagebox.showinfo(
                "Conversion complete",
                "\n".join(msg_lines),
            )

    def _open_preview(self) -> None:
        if self._last_output_path and self._last_output_path.exists():
            PreviewWindow(self, self._last_output_path)


# ─────────────────────────────────────────────────────────────────────────────
# PREVIEW WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class PreviewWindow(tk.Toplevel):
    """
    Two-pane preview / edit window.
      Left  — scrollable list of item names
      Right — card view (for actors) or raw JSON editor
    Toggle between views with the button in the editor header.
    """

    SUBTYPE_OPTIONS = ["minion", "brute", "champion", "monstrosity"]
    SKILL_LABELS = {
        "melee": "Melee", "defence": "Defence", "shooting": "Shooting",
        "throwing": "Throwing", "brawn": "Brawn", "toil": "Toil",
        "survival": "Survival", "endurance": "Endurance",
        "awareness": "Awareness", "dexterity": "Dexterity",
        "athletics": "Athletics", "stealth": "Stealth",
        "willpower": "Willpower", "recall": "Recall",
        "leadership": "Leadership", "charm": "Charm",
    }

    def __init__(self, parent: "App", output_path: Path) -> None:
        super().__init__(parent)
        self.title("Preview · " + output_path.name)
        self.configure(bg=C_OUTER)
        self.minsize(960, 580)
        self.geometry("1240x740")
        self.resizable(True, True)

        self._output_path      = output_path
        self._data             = None
        self._items: list      = []
        self._names: list      = []
        self._view_mode: dict  = {}   # idx → "card" | "json"
        self._json_edits: dict = {}   # idx → buffered JSON str
        self._card_widgets: dict = {} # idx → wvars dict
        self._current_idx: int | None = None
        self._json_text_ref: tk.Text | None = None
        self._editor_frame: tk.Frame   # set in _build_ui
        self._edit_title:   tk.Label
        self._toggle_btn:   tk.Button
        self._status_lbl:   tk.Label
        self._listbox:      tk.Listbox

        self._load_data()
        if self._items:
            self._build_ui()
        else:
            messagebox.showinfo("Empty", "No items found in the output file.", parent=self)
            self.destroy()

    # ── Data ─────────────────────────────────────────────────────────────────

    def _load_data(self) -> None:
        try:
            with open(self._output_path, encoding="utf-8") as fh:
                self._data = json.load(fh)
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not read output file:\n{exc}", parent=self)
            self.destroy()
            return
        if isinstance(self._data, dict) and "items" in self._data:
            self._items = self._data["items"]
        elif isinstance(self._data, list):
            self._items = self._data
        self._names = [item.get("name", f"Item {i}") for i, item in enumerate(self._items)]

    def _is_actor(self, item: dict) -> bool:
        return "skills" in item.get("system", {})

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        bar = tk.Frame(self, bg=C_HDR)
        bar.pack(fill="x")
        tk.Frame(bar, bg=C_GOLD, height=2).pack(fill="x")
        bar_inner = tk.Frame(bar, bg=C_HDR, pady=6)
        bar_inner.pack(fill="x")
        tk.Label(
            bar_inner, text=f"  PREVIEW  ·  {self._output_path.name}",
            font=FT_SECT, bg=C_HDR, fg=C_GOLD,
        ).pack(side="left")
        tk.Label(
            bar_inner,
            text=f"{len(self._items)} items  —  click to inspect & edit",
            font=("Georgia", 9, "italic"), bg=C_HDR, fg="#7ab0c0",
        ).pack(side="left", padx=16)
        tk.Frame(bar, bg=C_GOLD, height=2).pack(fill="x")

        body = tk.Frame(self, bg=C_OUTER)
        body.pack(fill="both", expand=True, padx=10, pady=8)

        # ── Left: item list ──────────────────────────────────────────────
        list_border = tk.Frame(body, bg=C_BORDER, padx=1, pady=1)
        list_border.pack(side="left", fill="y", padx=(0, 6))
        list_inner = tk.Frame(list_border, bg=C_LOG_BG)
        list_inner.pack(fill="both", expand=True)
        list_hdr = tk.Frame(list_inner, bg=C_HDR, pady=4)
        list_hdr.pack(fill="x")
        tk.Label(list_hdr, text="  ITEMS", font=FT_SECT, bg=C_HDR, fg=C_GOLD).pack(side="left")
        list_vsb = tk.Scrollbar(list_inner, bg=C_HDR, troughcolor=C_HDR)
        list_vsb.pack(side="right", fill="y")
        self._listbox = tk.Listbox(
            list_inner,
            bg=C_LOG_BG, fg=C_LOG_FG,
            selectbackground=C_SUBHDR, selectforeground="white",
            font=("Calibri", 11), relief="flat", bd=0,
            width=28, activestyle="none",
            yscrollcommand=list_vsb.set,
        )
        self._listbox.pack(side="left", fill="both", expand=True)
        list_vsb.config(command=self._listbox.yview)
        for name in self._names:
            self._listbox.insert("end", f"  {name}")
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        # ── Right: editor pane ───────────────────────────────────────────
        right = tk.Frame(body, bg=C_OUTER)
        right.pack(side="left", fill="both", expand=True)

        edit_hdr = tk.Frame(right, bg=C_HDR, pady=4)
        edit_hdr.pack(fill="x")
        self._edit_title = tk.Label(
            edit_hdr, text="  Select an item from the list",
            font=FT_SECT, bg=C_HDR, fg=C_GOLD,
        )
        self._edit_title.pack(side="left")
        self._toggle_btn = tk.Button(
            edit_hdr, text="Raw JSON",
            command=self._toggle_view,
            bg=C_BTN, fg="#7ab0c0", relief="flat",
            font=("Georgia", 9, "bold"), padx=10, pady=3,
            cursor="hand2",
            activebackground=C_BTN_HOV, activeforeground=C_GOLD,
        )
        # packed later only when an actor is selected
        tk.Frame(right, bg=C_BORDER, height=1).pack(fill="x")
        self._editor_frame = tk.Frame(right, bg=C_PANEL)
        self._editor_frame.pack(fill="both", expand=True)

        # ── Action bar ───────────────────────────────────────────────────
        btn_bar = tk.Frame(self, bg=C_HDR, pady=7)
        btn_bar.pack(fill="x", padx=10, pady=(0, 8))
        for label, cmd, color in (
            ("Save Item",        self._save_item,  C_GOLD),
            ("Save All to File", self._save_all,   "#5cbf5c"),
            ("Close",            self.destroy,     C_LOG_ERR),
        ):
            b = tk.Button(
                btn_bar, text=label, command=cmd,
                bg=C_BTN, fg=color, relief="flat",
                font=("Georgia", 10, "bold"), padx=14, pady=5,
                cursor="hand2",
                activebackground=C_BTN_HOV, activeforeground=color,
            )
            b.pack(side="left", padx=6)
            b.bind("<Enter>", lambda _e, w=b: w.config(bg=C_BTN_HOV))
            b.bind("<Leave>", lambda _e, w=b: w.config(bg=C_BTN))
        self._status_lbl = tk.Label(
            btn_bar, text="",
            font=("Georgia", 9, "italic"),
            bg=C_HDR, fg=C_HINT,
        )
        self._status_lbl.pack(side="right", padx=12)

    # ── Selection & view management ───────────────────────────────────────────

    def _on_select(self, _event=None) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx == self._current_idx:
            return
        self._flush_current()
        self._current_idx = idx
        self._show_item(idx)

    def _flush_current(self) -> None:
        """Silently persist in-progress edits from the current view."""
        idx = self._current_idx
        if idx is None:
            return
        if self._view_mode.get(idx) == "json" and self._json_text_ref and self._json_text_ref.winfo_exists():
            self._json_edits[idx] = self._json_text_ref.get("1.0", "end-1c")
        # Card edits are read live from widgets; no flush needed here.

    def _show_item(self, idx: int) -> None:
        item  = self._items[idx]
        actor = self._is_actor(item)
        mode  = self._view_mode.get(idx, "card" if actor else "json")
        self._view_mode[idx] = mode
        self._edit_title.config(text=f"  {self._names[idx]}")
        if actor:
            self._toggle_btn.config(text="Raw JSON" if mode == "card" else "Card View")
            self._toggle_btn.pack(side="right", padx=8)
        else:
            self._toggle_btn.pack_forget()
        for w in self._editor_frame.winfo_children():
            w.destroy()
        self._json_text_ref = None
        if mode == "card" and actor:
            self._build_card_view(idx, item)
        else:
            self._build_json_view(idx, item)

    def _toggle_view(self) -> None:
        idx = self._current_idx
        if idx is None:
            return
        mode = self._view_mode.get(idx, "card")
        if mode == "card":
            self._items[idx] = self._read_card_to_item(idx)
            self._card_widgets.pop(idx, None)
            self._view_mode[idx] = "json"
        else:
            if self._json_text_ref and self._json_text_ref.winfo_exists():
                raw = self._json_text_ref.get("1.0", "end-1c")
                try:
                    self._items[idx] = json.loads(raw)
                    self._json_edits.pop(idx, None)
                except json.JSONDecodeError:
                    pass
            self._view_mode[idx] = "card"
        self._show_item(idx)

    # ── JSON view ─────────────────────────────────────────────────────────────

    def _build_json_view(self, idx: int, item: dict) -> None:
        raw = self._json_edits.get(idx) or json.dumps(item, indent=2, ensure_ascii=False)
        fr = tk.Frame(self._editor_frame, bg=C_LOG_BG, padx=4, pady=4)
        fr.pack(fill="both", expand=True)
        vsb = tk.Scrollbar(fr, bg=C_HDR, troughcolor=C_HDR)
        vsb.pack(side="right", fill="y")
        hsb = tk.Scrollbar(fr, orient="horizontal", bg=C_HDR, troughcolor=C_HDR)
        hsb.pack(side="bottom", fill="x")
        txt = tk.Text(
            fr, bg=C_LOG_BG, fg="#ddd8c8",
            font=("Consolas", 10), relief="flat", bd=0,
            insertbackground="#ddd8c8", wrap="none",
            yscrollcommand=vsb.set, xscrollcommand=hsb.set,
        )
        txt.pack(side="left", fill="both", expand=True)
        vsb.config(command=txt.yview)
        hsb.config(command=txt.xview)
        txt.insert("1.0", raw)
        self._json_text_ref = txt

    # ── Card view ─────────────────────────────────────────────────────────────

    def _build_card_view(self, idx: int, item: dict) -> None:  # noqa: C901
        system = item.get("system", {})

        canvas = tk.Canvas(self._editor_frame, bg=C_PANEL, highlightthickness=0)
        vsb = tk.Scrollbar(self._editor_frame, command=canvas.yview, bg=C_HDR, troughcolor=C_HDR)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.config(yscrollcommand=vsb.set)

        inner = tk.Frame(canvas, bg=C_PANEL, padx=20, pady=14)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(_e=None):
            canvas.config(scrollregion=canvas.bbox("all"))
        def _on_canvas_resize(e):
            canvas.itemconfig(win_id, width=e.width)
        def _on_wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_resize)
        canvas.bind("<MouseWheel>", _on_wheel)

        wvars: dict = {}

        def rule():
            tk.Frame(inner, bg=C_BORDER, height=1).pack(fill="x", pady=(6, 3))

        def section_lbl(text):
            tk.Label(inner, text=text, font=FT_SECT, bg=C_PANEL, fg=C_BORDER).pack(anchor="w", pady=(2, 1))

        # ── Name + Subtype ──────────────────────────────────────────────
        hdr_row = tk.Frame(inner, bg=C_PANEL)
        hdr_row.pack(fill="x", pady=(0, 4))
        name_var = tk.StringVar(value=item.get("name", ""))
        tk.Label(hdr_row, text="Name:", font=FT_LABEL, bg=C_PANEL, fg=C_DARK).pack(side="left", padx=(0, 4))
        tk.Entry(
            hdr_row, textvariable=name_var, font=("Georgia", 12, "bold"), width=22,
            bg="#f8f0d6", fg=C_DARK, relief="solid", bd=1,
            highlightbackground=C_BORDER, highlightthickness=1,
        ).pack(side="left", padx=(0, 12))
        npc_type = system.get("type", "minion")
        subtype_var = tk.StringVar(value=npc_type)
        tk.Label(hdr_row, text="—", font=("Georgia", 12), bg=C_PANEL, fg=C_DARK).pack(side="left", padx=4)
        ttk.Combobox(
            hdr_row, textvariable=subtype_var, values=self.SUBTYPE_OPTIONS,
            state="readonly", width=14,
        ).pack(side="left")
        wvars["name"]    = name_var
        wvars["npcType"] = subtype_var

        rule()

        # ── Characteristics ─────────────────────────────────────────────
        CHAR_LABELS = [
            ("ws",  "WS"),  ("bs",  "BS"),  ("s",   "S"),   ("t",   "T"),
            ("i",   "I"),   ("ag",  "Ag"),  ("re",  "Re"),  ("fel", "Fel"),
        ]
        section_lbl("CHARACTERISTICS")
        chars_raw = system.get("characteristics", {})
        char_grid = tk.Frame(inner, bg=C_PANEL)
        char_grid.pack(fill="x", pady=(0, 4))
        char_vars: dict = {}
        for col, (key, label) in enumerate(CHAR_LABELS):
            char_data = chars_raw.get(key, {})
            if isinstance(char_data, dict):
                val = int(char_data.get("base", 0)) + int(char_data.get("modifier", 0))
            else:
                val = int(char_data) if isinstance(char_data, (int, float)) else 0
            cell = tk.Frame(char_grid, bg=C_PANEL)
            cell.grid(row=0, column=col, padx=(0, 16), pady=2, sticky="w")
            tk.Label(cell, text=label + ":", font=FT_LABEL, bg=C_PANEL, fg=C_DARK).pack(side="left")
            cv = tk.StringVar(value=str(val))
            tk.Entry(
                cell, textvariable=cv, width=3,
                font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK,
                relief="solid", bd=1, justify="center",
            ).pack(side="left", padx=(3, 0))
            char_vars[key] = cv
        wvars["chars"]    = char_vars
        wvars["chars_raw"] = chars_raw

        rule()

        # ── Skills ──────────────────────────────────────────────────────
        section_lbl("SKILLS")
        skills_raw = system.get("skills", {})
        skill_entries = _iter_skill_entries(skills_raw)
        ranked = sorted(
            [(k, _skill_display_val(v)) for k, v in skill_entries if _skill_display_val(v) > 0],
            key=lambda x: -x[1],
        )
        skill_grid = tk.Frame(inner, bg=C_PANEL)
        skill_grid.pack(fill="x", pady=(0, 4))
        skill_vars: dict = {}
        for i, (key, val) in enumerate(ranked):
            lbl = self.SKILL_LABELS.get(key, key.title())
            cell = tk.Frame(skill_grid, bg=C_PANEL)
            cell.grid(row=i // 4, column=i % 4, padx=(0, 20), pady=2, sticky="w")
            tk.Label(cell, text=lbl + ":", font=FT_LABEL, bg=C_PANEL, fg=C_DARK).pack(side="left")
            sv = tk.StringVar(value=str(val))
            tk.Entry(
                cell, textvariable=sv, width=3,
                font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK,
                relief="solid", bd=1, justify="center",
            ).pack(side="left", padx=(3, 0))
            skill_vars[key] = sv
        wvars["skills"]    = skill_vars
        wvars["skills_raw"] = skills_raw

        rule()

        # ── Resilience ──────────────────────────────────────────────────
        section_lbl("RESILIENCE")
        res = system.get("resilience", {})
        wounds_var = tk.StringVar(value=str(res.get("value", 0)))
        prot_var   = tk.StringVar(value=str(res.get("modifier", 0)))
        res_row = tk.Frame(inner, bg=C_PANEL)
        res_row.pack(fill="x", pady=(0, 4))
        for lbl, var in (("Wounds:", wounds_var), ("Protection:", prot_var)):
            cell = tk.Frame(res_row, bg=C_PANEL)
            cell.pack(side="left", padx=(0, 24))
            tk.Label(cell, text=lbl, font=FT_LABEL, bg=C_PANEL, fg=C_DARK).pack(side="left")
            tk.Entry(
                cell, textvariable=var, width=4,
                font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK,
                relief="solid", bd=1, justify="center",
            ).pack(side="left", padx=(4, 0))
        wvars["wounds"]     = wounds_var
        wvars["protection"] = prot_var

        rule()

        # ── Weapons ─────────────────────────────────────────────────────
        section_lbl("WEAPONS")
        weapons = [i for i in item.get("items", []) if i.get("type") == "weapon"]
        wpn_frame = tk.Frame(inner, bg=C_PANEL)
        wpn_frame.pack(fill="x")
        weapon_rows: list = []

        def _add_weapon(wpn: dict | None = None) -> None:
            row = tk.Frame(wpn_frame, bg=C_PANEL)
            row.pack(fill="x", pady=1)
            sys_w  = (wpn or {}).get("system", {})
            nv  = tk.StringVar(value=(wpn or {}).get("name", ""))
            dv  = tk.StringVar(value=str(
                (sys_w.get("damage") or {}).get("value") or
                (sys_w.get("damage") or {}).get("formula") or 0
            ))
            dicv = tk.StringVar(value=str(sys_w.get("diceBonus", 4)))
            apv  = tk.StringVar(value=str(sys_w.get("ap", 0)))
            thv  = tk.StringVar(value="2H" if sys_w.get("twoHanded") else "1H")
            qv  = tk.StringVar(value=", ".join(sys_w.get("traits") or sys_w.get("properties", {}).get("qualities") or []))
            tk.Label(row, text="Name:",    font=FT_MINI, bg=C_PANEL, fg=C_DARK).pack(side="left")
            tk.Entry(row, textvariable=nv,   width=16, font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK, relief="solid", bd=1).pack(side="left", padx=(3, 8))
            tk.Label(row, text="Dmg:",     font=FT_MINI, bg=C_PANEL, fg=C_DARK).pack(side="left")
            tk.Entry(row, textvariable=dv,   width=3,  font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK, relief="solid", bd=1, justify="center").pack(side="left", padx=(3, 6))
            tk.Label(row, text="Dice:",    font=FT_MINI, bg=C_PANEL, fg=C_DARK).pack(side="left")
            tk.Entry(row, textvariable=dicv, width=3,  font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK, relief="solid", bd=1, justify="center").pack(side="left", padx=(3, 6))
            tk.Label(row, text="AP:",      font=FT_MINI, bg=C_PANEL, fg=C_DARK).pack(side="left")
            tk.Entry(row, textvariable=apv,  width=2,  font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK, relief="solid", bd=1, justify="center").pack(side="left", padx=(3, 6))
            tk.Label(row, text="Hands:",   font=FT_MINI, bg=C_PANEL, fg=C_DARK).pack(side="left")
            tk.Entry(row, textvariable=thv,  width=3,  font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK, relief="solid", bd=1, justify="center").pack(side="left", padx=(3, 6))
            tk.Label(row, text="Traits:",  font=FT_MINI, bg=C_PANEL, fg=C_DARK).pack(side="left")
            tk.Entry(row, textvariable=qv,   width=18, font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK, relief="solid", bd=1).pack(side="left", padx=(3, 0))
            wr = {"row": row, "name": nv, "dmg": dv, "diceBonus": dicv, "ap": apv, "twoHanded": thv, "qualities": qv, "original": wpn}
            del_b = tk.Button(row, text="✕", bg=C_PANEL, fg=C_LOG_ERR, relief="flat", font=("Calibri", 9), cursor="hand2", bd=0)
            del_b.config(command=lambda: (row.destroy(), weapon_rows.remove(wr) if wr in weapon_rows else None))
            del_b.pack(side="left", padx=(6, 0))
            weapon_rows.append(wr)

        for wpn in weapons:
            _add_weapon(wpn)
        tk.Button(
            wpn_frame, text="+ Weapon", command=_add_weapon,
            bg=C_HDR, fg=C_GOLD, relief="flat",
            font=("Georgia", 9, "bold"), padx=8, pady=2, cursor="hand2",
        ).pack(anchor="w", pady=(4, 0))
        wvars["weapon_rows"] = weapon_rows

        rule()

        # ── Abilities ────────────────────────────────────────────────────
        section_lbl("ABILITIES")
        abilities = [i for i in item.get("items", []) if i.get("type") == "ability"]
        abl_frame = tk.Frame(inner, bg=C_PANEL)
        abl_frame.pack(fill="x")
        ability_rows: list = []

        def _add_ability(abl: dict | None = None) -> None:
            row = tk.Frame(abl_frame, bg=C_PANEL)
            row.pack(fill="x", pady=1)
            nv = tk.StringVar(value=(abl or {}).get("name", ""))
            desc_raw = ""
            if abl:
                desc = abl.get("system", {}).get("description", {})
                if isinstance(desc, dict):
                    desc_raw = desc.get("public", "") or desc.get("value", "")
                elif isinstance(desc, str):
                    desc_raw = desc
            dv = tk.StringVar(value=_strip_html(desc_raw))
            tk.Label(row, text="Name:", font=FT_MINI, bg=C_PANEL, fg=C_DARK).pack(side="left")
            tk.Entry(row, textvariable=nv, width=20, font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK, relief="solid", bd=1).pack(side="left", padx=(3, 8))
            tk.Label(row, text="—", font=FT_MINI, bg=C_PANEL, fg=C_DARK).pack(side="left", padx=(0, 4))
            tk.Entry(row, textvariable=dv, width=44, font=FT_ENTRY, bg="#f8f0d6", fg=C_DARK, relief="solid", bd=1).pack(side="left", padx=(0, 0))
            ar = {"row": row, "name": nv, "desc": dv, "original": abl}
            del_b = tk.Button(row, text="✕", bg=C_PANEL, fg=C_LOG_ERR, relief="flat", font=("Calibri", 9), cursor="hand2", bd=0)
            del_b.config(command=lambda: (row.destroy(), ability_rows.remove(ar) if ar in ability_rows else None))
            del_b.pack(side="left", padx=(6, 0))
            ability_rows.append(ar)

        for abl in abilities:
            _add_ability(abl)
        tk.Button(
            abl_frame, text="+ Ability", command=_add_ability,
            bg=C_HDR, fg=C_GOLD, relief="flat",
            font=("Georgia", 9, "bold"), padx=8, pady=2, cursor="hand2",
        ).pack(anchor="w", pady=(4, 0))
        wvars["ability_rows"] = ability_rows

        self._card_widgets[idx] = wvars

    # ── Card → dict ───────────────────────────────────────────────────────────

    def _read_card_to_item(self, idx: int) -> dict:
        item  = copy.deepcopy(self._items[idx])
        wvars = self._card_widgets.get(idx)
        if not wvars:
            return item

        item["name"] = wvars["name"].get()
        if "prototypeToken" in item:
            item["prototypeToken"]["name"] = item["name"]

        system = item.setdefault("system", {})
        system["type"] = wvars["npcType"].get()

        # Characteristics
        chars_raw = wvars.get("chars_raw", {})
        for key, cv in wvars.get("chars", {}).items():
            new_val = _safe_int(cv.get(), default=0)
            existing = chars_raw.get(key)
            if isinstance(existing, dict):
                system.setdefault("characteristics", {})[key] = {**existing, "base": new_val}
            else:
                system.setdefault("characteristics", {})[key] = {"base": new_val, "modifier": 0}

        # Skills — preserve dict structure if present
        skills_raw = wvars.get("skills_raw", {})
        for key, sv in wvars.get("skills", {}).items():
            new_val = _safe_int(sv.get(), default=0)
            existing = skills_raw.get(key)
            if isinstance(existing, dict):
                system["skills"][key] = {**existing, "base": new_val}
            else:
                system.setdefault("skills", {})[key] = new_val

        res = system.setdefault("resilience", {})
        res["value"]    = _safe_int(wvars["wounds"].get())
        res["modifier"] = _safe_int(wvars["protection"].get())

        new_weapons = []
        for wr in wvars.get("weapon_rows", []):
            if not wr["row"].winfo_exists():
                continue
            w = copy.deepcopy(wr["original"]) if wr["original"] else {"type": "weapon", "system": {}}
            w["name"] = wr["name"].get()
            ws = w.setdefault("system", {})
            ws.setdefault("damage", {})["value"] = _safe_int(wr["dmg"].get())
            ws["diceBonus"]  = _safe_int(wr["diceBonus"].get(), default=4)
            ws["ap"]         = _safe_int(wr["ap"].get(), default=0)
            ws["twoHanded"]  = wr["twoHanded"].get().strip().upper() in ("2H", "TRUE", "YES")
            traits = [q.strip() for q in wr["qualities"].get().split(",") if q.strip()]
            ws["traits"] = traits
            new_weapons.append(w)

        new_abilities = []
        for ar in wvars.get("ability_rows", []):
            if not ar["row"].winfo_exists():
                continue
            a = copy.deepcopy(ar["original"]) if ar["original"] else {"type": "ability", "system": {}}
            a["name"] = ar["name"].get()
            a.setdefault("system", {}).setdefault("description", {})["public"] = ar["desc"].get()
            new_abilities.append(a)

        other = [i for i in item.get("items", []) if i.get("type") not in ("weapon", "ability")]
        item["items"] = other + new_weapons + new_abilities
        return item

    # ── Save callbacks ────────────────────────────────────────────────────────

    def _save_item(self) -> None:
        idx = self._current_idx
        if idx is None:
            return
        mode = self._view_mode.get(idx, "card")
        if mode == "card":
            updated = self._read_card_to_item(idx)
        else:
            raw = self._json_text_ref.get("1.0", "end-1c") if self._json_text_ref else "{}"
            try:
                updated = json.loads(raw)
            except json.JSONDecodeError as exc:
                messagebox.showerror("Invalid JSON", f"Cannot parse JSON:\n{exc}", parent=self)
                return
            self._json_edits.pop(idx, None)
        self._items[idx] = updated
        new_name = updated.get("name", self._names[idx])
        self._names[idx] = new_name
        self._listbox.delete(idx)
        self._listbox.insert(idx, f"  {new_name}")
        self._listbox.selection_set(idx)
        self._status_lbl.config(text=f"✓  Saved '{new_name}'")

    def _save_all(self) -> None:
        # Flush current view
        idx = self._current_idx
        if idx is not None:
            mode = self._view_mode.get(idx, "card")
            if mode == "card":
                self._items[idx] = self._read_card_to_item(idx)
            elif self._json_text_ref and self._json_text_ref.winfo_exists():
                raw = self._json_text_ref.get("1.0", "end-1c")
                try:
                    self._items[idx] = json.loads(raw)
                except json.JSONDecodeError as exc:
                    messagebox.showerror(
                        "Invalid JSON",
                        f"Fix JSON for '{self._names[idx]}' before saving:\n{exc}",
                        parent=self,
                    )
                    return

        if isinstance(self._data, dict) and "items" in self._data:
            self._data["items"] = self._items
            payload = self._data
        else:
            payload = self._items

        try:
            with open(self._output_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            self._status_lbl.config(
                text=f"✓  {len(self._items)} items written to {self._output_path.name}"
            )
        except Exception as exc:
            messagebox.showerror("Write Error", f"Could not save file:\n{exc}", parent=self)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
