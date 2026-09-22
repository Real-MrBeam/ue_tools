#!/usr/bin/env python3
"""
poolview.py - graphical texture-pool viewer for UE5 memreports.

Scans a folder (recursively) for .memreport files, draws resident texture
memory per capture as stacked columns, and lets you step between captures to
see what grew and shrank compared with the previous one.

Needs pooldump.py in the same folder; it does all the parsing. Otherwise
standard library only: Python 3.10+ with tkinter, which the python.org
Windows installer includes.

    python poolview.py                      scan the folder this file is in
    python poolview.py D:\\path\\MemReports   scan a specific folder

Keys: Left / Right step between captures, Home / End jump to first / last.
Click a column to select that capture. Click a legend entry to hide or show a
category. Select a texture row to see its size across every capture;
double-click it to copy the asset path.

Parsed reports are cached in %LOCALAPPDATA%\\poolview_cache, keyed on file
size, modification time and the pooldump.py version, so reopening is instant
and a pooldump change re-parses automatically.
"""

import glob
import hashlib
import json
import math
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import pooldump
except Exception:  # reported in the UI
    pooldump = None

CACHE_VERSION = 3
TRANSIENT = ("/Engine/Transient", "/Engine/EditorResources")
VIEW_RE = re.compile(r"View Location:\s*X=(-?[\d.]+)\s*Y=(-?[\d.]+)\s*Z=(-?[\d.]+)")
CAMERA_WARN_METRES = 5.0

# ---------------------------------------------------------------- palette ---
BG = "#1B2230"
PANEL = "#222A38"
PANEL2 = "#2A3344"
GRID = "#343E52"
SEL = "#3B4763"
TEXT = "#DCE3EE"
DIM = "#8D98AC"
FAINT = "#5C667A"
GROW = "#E27D6F"
SHRINK = "#66B8A6"
WARN = "#E3B35C"

GROUP_COLORS = {
    "World": "#7FA36B", "Character": "#B08BC4", "UI": "#E3A857",
    "Effects": "#5E9ACB", "Skybox": "#9FC3D9", "Props": "#C98A6B",
    "Lighting": "#D8C77A", "RenderTarget": "#7D8AA3", "Other": "#6E7A8F",
    "Transient": "#4E586C",
}
GROUP_ORDER = list(GROUP_COLORS)
BINARY_COLORS = {"Streaming": "#5E9ACB", "Pinned": "#E3A857",
                 "Compressed": "#7FA36B", "Uncompressed": "#E27D6F"}
FOLDER_PALETTE = ["#7FA36B", "#E3A857", "#B08BC4", "#5E9ACB", "#C98A6B",
                  "#9FC3D9", "#D8C77A", "#8FB3A0", "#C47C9A"]
OTHER_FOLDERS = "Other folders"


# ------------------------------------------------------------- categories ---
def group_bucket(name, t):
    if name.startswith(TRANSIENT):
        return "Transient"
    g = t.get("lod_group", "").upper().replace("TEXTUREGROUP_", "")
    if g == "UI":
        return "UI"
    if g.startswith(("WORLD", "TERRAIN", "HIERARCHICALLOD")):
        return "World"
    if g.startswith("CHARACTER"):
        return "Character"
    if g.startswith("EFFECTS"):
        return "Effects"
    if g == "SKYBOX":
        return "Skybox"
    if "LIGHTMAP" in g or "SHADOWMAP" in g or g in ("COLORLOOKUPTABLE", "IESLIGHTPROFILE"):
        return "Lighting"
    if g.startswith(("WEAPON", "VEHICLE")):
        return "Props"
    if "RENDERTARGET" in g:
        return "RenderTarget"
    return "Other"


def folder_bucket(name, t):
    if name.startswith(TRANSIENT):
        return "/Engine/Transient"
    parts = name.split(".")[0].split("/")
    if len(parts) >= 4:
        return "/".join(parts[:3])
    return "/".join(parts[:2]) or "(root)"


def stream_bucket(name, t):
    return "Streaming" if t.get("streaming") else "Pinned"


def comp_bucket(name, t):
    return "Uncompressed" if t.get("uncompressed") else "Compressed"


STACK_MODES = {
    "Texture group": group_bucket,
    "Content folder": folder_bucket,
    "Streaming or pinned": stream_bucket,
    "Compression": comp_bucket,
}


def flags_of(t):
    # UNUSED fires for almost every texture in these captures, so it is noise.
    return ", ".join(f for f in pooldump.classify(t) if f != "UNUSED")


def trunc(s, n):
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def fit(text, font, px):
    """Shorten text with an ellipsis until it fits in px pixels."""
    if px <= 0:
        return ""
    if font.measure(text) <= px:
        return text
    while text and font.measure(text + "\u2026") > px:
        text = text[:-1]
    return text + "\u2026"


def nice_step(maxv, target=5):
    if maxv <= 0:
        return 1.0
    raw = maxv / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


# ---------------------------------------------------------------- loading ---
class Report:
    def __init__(self, path, mtime, header, view, textures):
        self.path = path
        self.mtime = mtime
        self.header = header or {}
        self.view = tuple(view) if view else None
        self.textures = textures

    @property
    def device(self):
        return self.header.get("Device Name", "?")

    @property
    def config(self):
        return self.header.get("Config", "?")

    @property
    def cl(self):
        return self.header.get("Changelist", "?")

    @property
    def short(self):
        return time.strftime("%m-%d %H:%M", time.localtime(self.mtime))

    @property
    def long(self):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.mtime))


def read_view_location(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            m = VIEW_RE.search(line)
            if m:
                return [float(v) for v in m.groups()]
            if i > 40:
                break
    return None


def cache_root():
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    root = os.path.join(base, "poolview_cache")
    os.makedirs(root, exist_ok=True)
    return root


def load_one(path, croot, stamp):
    st = os.stat(path)
    key = hashlib.sha1(
        f"{CACHE_VERSION}|{stamp}|{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}"
        .encode()).hexdigest()
    cpath = os.path.join(croot, key + ".json")
    if os.path.exists(cpath):
        try:
            with open(cpath, encoding="utf-8") as fh:
                d = json.load(fh)
            return Report(path, st.st_mtime, d["header"], d["view"], d["textures"]), True
        except (OSError, ValueError, KeyError):
            pass
    textures, _totals, stats = pooldump.parse_memreport(path)
    header = stats.get("header", {}) or {}
    view = read_view_location(path)
    try:
        with open(cpath, "w", encoding="utf-8") as fh:
            json.dump({"header": header, "view": view, "textures": textures}, fh)
    except OSError:
        pass
    return Report(path, st.st_mtime, header, view, textures), False


def loader(folder, q):
    try:
        files = glob.glob(os.path.join(folder, "**", "*.memreport"), recursive=True)
        files.sort(key=os.path.getmtime)
        croot = cache_root()
        stamp = os.path.getmtime(pooldump.__file__)
        out, cached = [], 0
        for i, f in enumerate(files):
            q.put(("progress", i, len(files), os.path.basename(f)))
            try:
                rep, hit = load_one(f, croot, stamp)
                out.append(rep)
                cached += hit
            except Exception as exc:
                q.put(("error", f, str(exc)))
        q.put(("done", out, cached))
    except Exception:
        q.put(("fatal", traceback.format_exc()))


# ----------------------------------------------------------------- charts ---
class Chart(tk.Canvas):
    def __init__(self, master, app, **kw):
        super().__init__(master, background=PANEL, highlightthickness=0, **kw)
        self.app = app
        self._pending = False
        self.bind("<Configure>", lambda e: self.request())

    def S(self, v):
        return self.app.S(v)

    def request(self):
        if not self._pending:
            self._pending = True
            self.after_idle(self._redraw)

    def _redraw(self):
        self._pending = False
        self.delete("all")
        if self.winfo_width() > 40 and self.winfo_height() > 40:
            self.draw()

    def centre_text(self, text):
        self.create_text(self.winfo_width() / 2, self.winfo_height() / 2, text=text,
                         fill=DIM, font=self.app.f_norm, justify="center")


class Timeline(Chart):
    """Stacked column per capture. Click a column to select it."""

    def __init__(self, master, app):
        super().__init__(master, app, height=app.S(270))
        self.labels, self.long_labels, self.series = [], [], []
        self.selected, self.hidden = -1, set()
        self.legend, self.geom = [], None
        self.empty_text = "Reading memreports\u2026"
        self.bind("<Button-1>", self.on_click)
        self.bind("<Motion>", self.on_motion)
        self.bind("<Leave>", lambda e: self.delete("tip"))

    def set_data(self, labels, long_labels, series, selected, hidden):
        self.labels, self.long_labels = labels, long_labels
        self.series, self.selected, self.hidden = series, selected, hidden
        self.request()

    def visible_series(self):
        return [s for s in self.series if s[0] not in self.hidden]

    def draw(self):
        S, f = self.S, self.app
        w, h = self.winfo_width(), self.winfo_height()
        n = len(self.labels)
        self.geom, self.legend = None, []
        if n == 0:
            self.centre_text(self.empty_text)
            return

        l, r, t, b = S(66), w - S(190), S(22), h - S(34)
        pw, ph = max(r - l, 10), max(b - t, 10)
        vis = self.visible_series()
        totals = [sum(s[2][i] for s in vis) for i in range(n)]
        top = max(totals) if totals and max(totals) > 0 else 1.0
        step = nice_step(top)
        ymax = step * (math.floor(top / step) + 1)

        v = 0.0
        while v <= ymax + 1e-9:
            y = b - v / ymax * ph
            self.create_line(l, y, r, y, fill=GRID)
            self.create_text(l - S(8), y, text=f"{v:,.0f} MB", anchor="e",
                             fill=DIM, font=f.f_small)
            v += step

        slot = pw / n
        bw = max(min(slot * 0.68, S(58)), 1.0)
        lab_every = max(1, math.ceil(S(80) / slot))
        for i in range(n):
            cx = l + slot * (i + 0.5)
            is_sel = i == self.selected
            if is_sel:
                self.create_rectangle(l + slot * i + 1, t - S(14), l + slot * (i + 1) - 1, b,
                                      fill=SEL, outline="")
            y = b
            for _cat, color, vals in vis:
                hh = vals[i] / ymax * ph
                if hh >= 0.5:
                    self.create_rectangle(cx - bw / 2, y - hh, cx + bw / 2, y, fill=color,
                                          outline=PANEL if bw > 8 else "")
                y -= hh
            if slot >= S(42) or is_sel:
                self.create_text(cx, y - S(8), text=f"{totals[i]:.0f}",
                                 fill=TEXT if is_sel else DIM,
                                 font=f.f_bold if is_sel else f.f_small)
            if i % lab_every == 0 or is_sel:
                self.create_text(cx, b + S(13), text=self.labels[i],
                                 fill=TEXT if is_sel else DIM, font=f.f_small)

        lx, ly = r + S(22), t
        self.create_text(lx, ly, text="Click to hide or show", anchor="w",
                         fill=FAINT, font=f.f_small)
        ly += S(22)
        sel_ok = 0 <= self.selected < n
        for cat, color, vals in reversed(self.series):
            if ly > h - S(12):
                break
            hid = cat in self.hidden
            sq = S(10)
            self.create_rectangle(lx, ly - sq / 2, lx + sq, ly + sq / 2,
                                  fill=FAINT if hid else color, outline="")
            cur = vals[self.selected] if sel_ok else 0.0
            val = f"{cur:.1f}"
            name_px = (w - S(12) - f.f_small.measure(val) - S(10)) - (lx + sq + S(8))
            self.create_text(lx + sq + S(8), ly, text=fit(cat, f.f_small, name_px), anchor="w",
                             fill=FAINT if hid else TEXT, font=f.f_small)
            self.create_text(w - S(12), ly, text=val, anchor="e",
                             fill=FAINT if hid else DIM, font=f.f_small)
            self.legend.append((lx - S(4), ly - S(9), w - S(4), ly + S(9), cat))
            ly += S(19)

        self.geom = (l, r, t, b, slot, n)

    def index_at(self, x, y):
        if not self.geom:
            return -1
        l, r, t, b, slot, n = self.geom
        if l <= x < r and t - self.S(14) <= y <= b + self.S(24):
            i = int((x - l) / slot)
            if 0 <= i < n:
                return i
        return -1

    def on_click(self, e):
        for x0, y0, x1, y1, cat in self.legend:
            if x0 <= e.x <= x1 and y0 <= e.y <= y1:
                self.app.toggle_category(cat)
                return
        i = self.index_at(e.x, e.y)
        if i >= 0:
            self.app.select_index(i)

    def on_motion(self, e):
        self.delete("tip")
        i = self.index_at(e.x, e.y)
        if i < 0:
            return
        rows = sorted(((s[2][i], s[0]) for s in self.visible_series()), reverse=True)
        lines = [self.long_labels[i], f"Total  {sum(v for v, _ in rows):.1f} MB"]
        lines += [f"{c}  {v:.1f}" for v, c in rows[:8] if v >= 0.05]
        S = self.S
        tid = self.create_text(0, 0, text="\n".join(lines), anchor="nw", fill=TEXT,
                               font=self.app.f_small, tags="tip")
        x0, y0, x1, y1 = self.bbox(tid)
        tw, th, pad = x1 - x0, y1 - y0, S(7)
        x, y = e.x + S(16), e.y + S(12)
        if x + tw + 2 * pad > self.winfo_width():
            x = e.x - tw - 2 * pad - S(10)
        if y + th + 2 * pad > self.winfo_height():
            y = self.winfo_height() - th - 2 * pad - 2
        self.coords(tid, x + pad, y + pad)
        self.create_rectangle(x, y, x + tw + 2 * pad, y + th + 2 * pad, fill=PANEL2,
                              outline=GRID, tags="tip")
        self.tag_raise(tid)


class DeltaChart(Chart):
    """Diverging bars: change per category versus the previous capture."""

    def __init__(self, master, app):
        super().__init__(master, app)
        self.title, self.lines, self.rows, self.empty = "", [], [], "No capture selected"

    def set_data(self, title, lines, rows, empty=None):
        self.title, self.lines, self.rows, self.empty = title, lines, rows, empty
        self.request()

    def draw(self):
        S, f = self.S, self.app
        w, h = self.winfo_width(), self.winfo_height()
        self.create_text(S(14), S(16), text=self.title, anchor="w", fill=TEXT, font=f.f_bold)
        y = S(36)
        for line in self.lines:
            self.create_text(S(14), y, text=line, anchor="w", fill=DIM, font=f.f_small)
            y += S(17)
        if self.empty:
            self.create_text(w / 2, (y + h) / 2, text=self.empty, fill=DIM, font=f.f_norm,
                             justify="center")
            return
        rows = [r for r in self.rows if abs(r[2]) >= 0.05]
        if not rows:
            self.create_text(w / 2, (y + h) / 2, text="No category changed by more than 0.05 MB",
                             fill=DIM, font=f.f_norm)
            return
        top, rh = y + S(8), S(22)
        label_w = S(150)
        shown = rows[: max(1, int((h - top - S(6)) / rh))]
        neg = max([-r[2] for r in shown if r[2] < 0], default=0.0)
        pos = max([r[2] for r in shown if r[2] > 0], default=0.0)
        room = S(52)  # space for the value label at each bar end
        area = max(w - label_w - S(14) - room * ((neg > 0) + (pos > 0)), S(40))
        scale = area / ((neg + pos) or 1.0)
        cx = label_w + (room if neg > 0 else 0) + neg * scale
        for k, (cat, color, d) in enumerate(shown):
            yy = top + k * rh + rh / 2
            sq = S(8)
            self.create_rectangle(S(14), yy - sq / 2, S(14) + sq, yy + sq / 2, fill=color,
                                  outline="")
            self.create_text(S(30), yy, text=fit(cat, f.f_small, label_w - S(36)), anchor="w",
                             fill=TEXT, font=f.f_small)
            length = max(abs(d) * scale, 1.0)
            col = GROW if d > 0 else SHRINK
            if d > 0:
                self.create_rectangle(cx, yy - S(7), cx + length, yy + S(7), fill=col, outline="")
                self.create_text(cx + length + S(6), yy, text=f"{d:+.1f}", anchor="w",
                                 fill=col, font=f.f_small)
            else:
                self.create_rectangle(cx - length, yy - S(7), cx, yy + S(7), fill=col, outline="")
                self.create_text(cx - length - S(6), yy, text=f"{d:+.1f}", anchor="e",
                                 fill=col, font=f.f_small)
        self.create_line(cx, top, cx, top + len(shown) * rh, fill=FAINT)


class HistoryChart(Chart):
    """One texture's resident size in every capture."""

    def __init__(self, master, app):
        super().__init__(master, app)
        self.name, self.labels, self.values, self.formats, self.selected = None, [], [], [], -1

    def set_data(self, name, labels, values, formats, selected):
        self.name, self.labels, self.values = name, labels, values
        self.formats, self.selected = formats, selected
        self.request()

    def draw(self):
        S, f = self.S, self.app
        w, h = self.winfo_width(), self.winfo_height()
        if not self.name:
            self.centre_text("Select a texture in a table to see\nits size in every capture")
            return
        short = self.name.split(".")[0]
        self.create_text(S(14), S(16), text=short.rsplit("/", 1)[-1], anchor="w",
                         fill=TEXT, font=f.f_bold)
        self.create_text(S(14), S(34), text=fit(short, f.f_small, w - S(28)), anchor="w",
                         fill=DIM, font=f.f_small)
        n = len(self.values)
        if n == 0:
            return
        l, r, t, b = S(14), w - S(14), S(62), h - S(34)
        ph = max(b - t, 10)
        present = [v for v in self.values if v is not None]
        mx = max(present) if present else 1.0
        mx = mx or 1.0
        slot = (r - l) / n
        bw = max(min(slot * 0.6, S(40)), 1.0)
        self.create_line(l, b, r, b, fill=GRID)
        prev_fmt = None
        for i, v in enumerate(self.values):
            cx = l + slot * (i + 0.5)
            if i == self.selected:
                self.create_rectangle(l + slot * i + 1, t - S(8), l + slot * (i + 1) - 1, b,
                                      fill=SEL, outline="")
            if v is None:
                self.create_text(cx, b - S(8), text="not loaded", fill=FAINT,
                                 font=f.f_small, angle=90 if slot < S(70) else 0,
                                 anchor="w" if slot < S(70) else "s")
            else:
                hh = max(v / mx * ph, 1.0)
                self.create_rectangle(cx - bw / 2, b - hh, cx + bw / 2, b, fill=WARN,
                                      outline="")
                if slot >= S(34):
                    self.create_text(cx, b - hh - S(8), text=f"{v:.2f}", fill=TEXT,
                                     font=f.f_small)
                fmt = self.formats[i]
                if prev_fmt is not None and fmt != prev_fmt:
                    self.create_text(cx, b + S(26), text=fmt.replace("PF_", ""),
                                     fill=SHRINK, font=f.f_small)
                prev_fmt = fmt
            if slot >= S(60) or i == self.selected:
                self.create_text(cx, b + S(11), text=self.labels[i],
                                 fill=TEXT if i == self.selected else DIM, font=f.f_small)


# ----------------------------------------------------------------- tables ---
class Table(ttk.Frame):
    TEXT_COLS = {"status", "dims", "format", "group", "flags", "name"}

    def __init__(self, master, app, columns, default_sort):
        super().__init__(master)
        self.app, self.columns = app, columns
        self.default_sort = default_sort
        self.sort_col, self.sort_desc = default_sort, True
        self.rows, self.iid_name = [], {}
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show="headings",
                                 selectmode="browse")
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        for cid, head, width, anchor, stretch in columns:
            self.tree.heading(cid, text=head, command=lambda c=cid: self.sort_by(c))
            self.tree.column(cid, width=app.S(width), minwidth=app.S(40), anchor=anchor,
                             stretch=stretch)
        for tag, col in (("grow", GROW), ("new", GROW), ("shrink", SHRINK), ("gone", SHRINK)):
            self.tree.tag_configure(tag, foreground=col)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self.on_double)

    def selected_name(self):
        sel = self.tree.selection()
        return self.iid_name.get(sel[0]) if sel else None

    def set_rows(self, rows):
        self.rows = rows
        self.render()

    def sort_by(self, col):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col = col
            self.sort_desc = col not in self.TEXT_COLS
        self.render()

    def render(self):
        keep = self.selected_name() or self.app.history_name
        tree = self.tree
        tree.delete(*tree.get_children())
        col = self.sort_col
        rows = sorted(self.rows, key=lambda r: r["keys"].get(col, 0), reverse=self.sort_desc)
        self.iid_name = {}
        reselect = None
        for i, r in enumerate(rows):
            iid = f"r{i}"
            tree.insert("", "end", iid=iid, values=[r["values"].get(c[0], "") for c in self.columns],
                        tags=(r["tag"],) if r.get("tag") else ())
            self.iid_name[iid] = r["name"]
            if r["name"] == keep:
                reselect = iid
        for cid, head, *_ in self.columns:
            arrow = (" \u25BC" if self.sort_desc else " \u25B2") if cid == col else ""
            tree.heading(cid, text=head + arrow)
        if reselect:
            tree.selection_set(reselect)
            tree.see(reselect)

    def on_select(self, _e):
        name = self.selected_name()
        if name:
            self.app.show_history(name)

    def on_double(self, _e):
        name = self.selected_name()
        if name:
            path = name.split(".")[0]
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(path)
            self.app.set_status(f"Copied {path}")


# -------------------------------------------------------------------- app ---
class App:
    def __init__(self, root, folder):
        self.root = root
        self.folder = folder
        self.px = root.winfo_fpixels("1i") / 96.0
        self.reports, self.visible = [], []
        self.sel, self.sel_path = -1, None
        self.labels = []
        self.hidden_cats = set()
        self.history_name = None
        self.summary_text = ""
        self.q = queue.Queue()
        self.loading = False
        self._filter_job = None

        self.setup_style()
        self.build()
        root.bind("<Left>", lambda e: self.key_step(-1))
        root.bind("<Right>", lambda e: self.key_step(1))
        root.bind("<Home>", lambda e: self.key_jump(0))
        root.bind("<End>", lambda e: self.key_jump(-1))
        root.after(60, self.start_load)

    def S(self, v):
        return int(round(v * self.px))

    # ---- look ----
    def setup_style(self):
        fams = set(tkfont.families(self.root))
        fam = "Segoe UI" if "Segoe UI" in fams else tkfont.nametofont("TkDefaultFont").actual("family")
        self.f_small = tkfont.Font(root=self.root, family=fam, size=9)
        self.f_norm = tkfont.Font(root=self.root, family=fam, size=10)
        self.f_bold = tkfont.Font(root=self.root, family=fam, size=10, weight="bold")
        for fname in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(fname).configure(family=fam, size=9)

        self.root.configure(background=BG)
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL2,
                     bordercolor=GRID, lightcolor=PANEL2, darkcolor=PANEL2,
                     troughcolor=PANEL, focuscolor=SEL, selectbackground=SEL,
                     selectforeground=TEXT, insertcolor=TEXT)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Dim.TLabel", foreground=DIM)
        st.configure("Warn.TLabel", foreground=WARN)
        st.configure("Big.TLabel", font=self.f_bold)
        st.configure("TButton", background=PANEL2, foreground=TEXT, padding=(self.S(10), self.S(3)),
                     borderwidth=1)
        st.map("TButton", background=[("active", GRID), ("disabled", PANEL)],
               foreground=[("disabled", FAINT)])
        st.configure("TCheckbutton", background=BG, foreground=TEXT, indicatorbackground=PANEL2,
                     indicatorforeground=TEXT)
        st.map("TCheckbutton", background=[("active", BG)],
               indicatorbackground=[("selected", SEL)])
        st.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=TEXT,
                     arrowcolor=TEXT)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL2)],
               foreground=[("readonly", TEXT)], selectbackground=[("readonly", PANEL2)],
               selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", SEL)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        st.configure("TEntry", fieldbackground=PANEL2, foreground=TEXT)
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                     rowheight=self.S(22), borderwidth=0)
        st.map("Treeview", background=[("selected", SEL)], foreground=[("selected", TEXT)])
        st.configure("Treeview.Heading", background=PANEL2, foreground=DIM, relief="flat",
                     padding=(self.S(6), self.S(3)))
        st.map("Treeview.Heading", background=[("active", GRID)])
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=DIM,
                     padding=(self.S(14), self.S(5)), borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", PANEL2)],
               foreground=[("selected", TEXT)])
        for orient in ("Vertical", "Horizontal"):
            st.configure(f"{orient}.TScrollbar", background=PANEL2, troughcolor=PANEL,
                         arrowcolor=DIM, borderwidth=0)
        st.configure("TPanedwindow", background=BG)
        st.configure("Sash", sashthickness=self.S(6), background=BG)

    # ---- layout ----
    def build(self):
        S = self.S
        root = self.root
        root.title("Texture pool viewer")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{min(S(1440), sw - 60)}x{min(S(900), sh - 90)}+20+20")
        root.minsize(S(960), S(620))

        bar1 = ttk.Frame(root, padding=(S(12), S(10), S(12), S(4)))
        bar1.pack(fill="x")
        ttk.Button(bar1, text="Open folder\u2026", command=self.browse).pack(side="left")
        self.rescan_btn = ttk.Button(bar1, text="Rescan", command=self.start_load)
        self.rescan_btn.pack(side="left", padx=(S(6), 0))
        self.folder_lbl = ttk.Label(bar1, text=self.folder, style="Dim.TLabel")
        self.folder_lbl.pack(side="left", padx=(S(12), 0))

        bar2 = ttk.Frame(root, padding=(S(12), S(2), S(12), S(8)))
        bar2.pack(fill="x")

        def field(label, widget, padleft=0):
            ttk.Label(bar2, text=label, style="Dim.TLabel").pack(side="left", padx=(padleft, S(6)))
            widget.pack(side="left")

        self.device_var = tk.StringVar(value="All")
        self.device_cb = ttk.Combobox(bar2, textvariable=self.device_var, state="readonly",
                                      width=16, values=["All"])
        field("Capture type", self.device_cb)
        self.mode_var = tk.StringVar(value="Texture group")
        mode_cb = ttk.Combobox(bar2, textvariable=self.mode_var, state="readonly", width=19,
                               values=list(STACK_MODES))
        field("Stack by", mode_cb, S(18))
        self.only_var = tk.StringVar(value="All")
        only_cb = ttk.Combobox(bar2, textvariable=self.only_var, state="readonly", width=13,
                               values=["All"] + GROUP_ORDER)
        field("Show group", only_cb, S(18))
        self.filter_var = tk.StringVar()
        filt = ttk.Entry(bar2, textvariable=self.filter_var, width=28)
        field("Path contains", filt, S(18))
        self.hide_transient = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar2, text="Hide transient render targets", variable=self.hide_transient,
                        command=self.refresh).pack(side="left", padx=(S(18), 0))

        self.device_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        mode_cb.bind("<<ComboboxSelected>>", lambda e: self.change_mode())
        only_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.filter_var.trace_add("write", lambda *a: self.debounce_filter())

        self.status_lbl = ttk.Label(root, text="", style="Dim.TLabel",
                                    padding=(S(12), S(4), S(12), S(6)))
        self.status_lbl.pack(side="bottom", fill="x")

        vpane = ttk.Panedwindow(root, orient="vertical")
        vpane.pack(fill="both", expand=True, padx=S(12))
        tl_frame = tk.Frame(vpane, background=PANEL)
        self.timeline = Timeline(tl_frame, self)
        self.timeline.pack(fill="both", expand=True)
        vpane.add(tl_frame, weight=2)

        lower = ttk.Frame(vpane)
        vpane.add(lower, weight=5)

        nav = ttk.Frame(lower, padding=(0, S(10), 0, S(2)))
        nav.pack(fill="x")
        self.prev_btn = ttk.Button(nav, text="\u25C0 Previous", command=lambda: self.step(-1))
        self.prev_btn.pack(side="left")
        self.pos_lbl = ttk.Label(nav, text="", style="Big.TLabel", width=18, anchor="center")
        self.pos_lbl.pack(side="left", padx=S(8))
        self.next_btn = ttk.Button(nav, text="Next \u25B6", command=lambda: self.step(1))
        self.next_btn.pack(side="left")
        self.info_lbl = ttk.Label(nav, text="", padding=(S(16), 0))
        self.info_lbl.pack(side="left")
        ttk.Button(nav, text="Copy summary", command=self.copy_summary).pack(side="right")

        self.warn_lbl = ttk.Label(lower, text="", style="Warn.TLabel", padding=(0, 0, 0, S(6)))
        self.warn_lbl.pack(fill="x")

        self.vpane = vpane
        hpane = ttk.Panedwindow(lower, orient="horizontal")
        self.hpane = hpane
        hpane.pack(fill="both", expand=True, pady=(0, S(4)))

        left = ttk.Panedwindow(hpane, orient="vertical")
        dframe = tk.Frame(left, background=PANEL)
        self.delta = DeltaChart(dframe, self)
        self.delta.pack(fill="both", expand=True)
        left.add(dframe, weight=3)
        hframe = tk.Frame(left, background=PANEL)
        self.history = HistoryChart(hframe, self)
        self.history.pack(fill="both", expand=True)
        left.add(hframe, weight=2)
        hpane.add(left, weight=2)

        nb = ttk.Notebook(hpane)
        hpane.add(nb, weight=5)
        self.changes = Table(nb, self, [
            ("status", "Status", 64, "w", False),
            ("change", "Change MB", 96, "e", False),
            ("now", "Now MB", 72, "e", False),
            ("was", "Was MB", 72, "e", False),
            ("dims", "Size", 150, "w", False),
            ("format", "Format", 150, "w", False),
            ("group", "Group", 84, "w", False),
            ("flags", "Flags", 190, "w", False),
            ("name", "Texture", 420, "w", True),
        ], default_sort="mag")
        nb.add(self.changes, text="Changes from previous")
        self.all_tbl = Table(nb, self, [
            ("mb", "MB", 72, "e", False),
            ("cum", "Cumulative", 84, "e", False),
            ("dims", "Size", 104, "w", False),
            ("format", "Format", 110, "w", False),
            ("group", "Group", 84, "w", False),
            ("flags", "Flags", 210, "w", False),
            ("name", "Texture", 460, "w", True),
        ], default_sort="mb")
        nb.add(self.all_tbl, text="All textures in this capture")
        root.after(150, self.place_sashes)

    def place_sashes(self):
        root = self.root
        root.update_idletasks()
        try:
            self.vpane.sashpos(0, int(self.vpane.winfo_height() * 0.40))
            self.hpane.sashpos(0, int(self.hpane.winfo_width() * 0.36))
        except tk.TclError:
            pass

    # ---- loading ----
    def browse(self):
        d = filedialog.askdirectory(initialdir=self.folder, title="Folder with memreports")
        if d:
            self.folder = os.path.normpath(d)
            self.folder_lbl.configure(text=self.folder)
            self.sel_path = None
            self.start_load()

    def start_load(self):
        if self.loading:
            return
        if pooldump is None:
            messagebox.showerror("pooldump.py not found",
                                 f"Put pooldump.py next to poolview.py in\n{HERE}")
            return
        self.loading = True
        self.rescan_btn.state(["disabled"])
        self.errors = []
        self.set_status(f"Scanning {self.folder}\u2026")
        threading.Thread(target=loader, args=(self.folder, self.q), daemon=True).start()
        self.root.after(80, self.poll)

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.set_status(f"Reading {msg[1] + 1} of {msg[2]}: {msg[3]}")
                elif kind == "error":
                    self.errors.append(f"{os.path.basename(msg[1])}: {msg[2]}")
                elif kind == "fatal":
                    self.loading = False
                    self.rescan_btn.state(["!disabled"])
                    messagebox.showerror("Scan failed", msg[1])
                    return
                elif kind == "done":
                    self.loading = False
                    self.rescan_btn.state(["!disabled"])
                    self.on_loaded(msg[1], msg[2])
                    return
        except queue.Empty:
            pass
        self.root.after(80, self.poll)

    def on_loaded(self, reports, cached):
        self.reports = reports
        devices = sorted({r.device for r in reports})
        self.device_cb.configure(values=["All"] + devices)
        if self.device_var.get() not in devices:
            if "Windows" in devices:
                self.device_var.set("Windows")
            elif devices:
                self.device_var.set(max(devices, key=lambda d: sum(r.device == d for r in reports)))
            else:
                self.device_var.set("All")
        if not reports:
            self.timeline.empty_text = ("No .memreport files under this folder.\n"
                                        "Use Open folder to pick the MemReports directory.")
        msg = f"{len(reports)} captures in {self.folder}"
        if reports:
            msg += f", {cached} from cache"
        if self.errors:
            msg += f". {len(self.errors)} could not be read: " + "; ".join(self.errors[:3])
        self.set_status(msg)
        self.refresh()

    # ---- filtering and data ----
    def debounce_filter(self):
        if self._filter_job:
            self.root.after_cancel(self._filter_job)
        self._filter_job = self.root.after(300, self.refresh)

    def change_mode(self):
        self.hidden_cats = set()
        self.refresh()

    def tex_ok(self, name, t):
        if self.hide_transient.get() and name.startswith(TRANSIENT):
            return False
        g = self.only_var.get()
        if g != "All" and group_bucket(name, t) != g:
            return False
        f = self.filter_var.get().strip().lower()
        return not f or f in name.lower()

    def filtered(self, rep):
        return {k: v for k, v in rep.textures.items() if self.tex_ok(k, v)}

    def refresh(self):
        dev = self.device_var.get()
        self.visible = [r for r in self.reports if dev == "All" or r.device == dev]
        mode = self.mode_var.get()
        base = STACK_MODES[mode]

        raw = []
        peak = {}
        for rep in self.visible:
            sums = {}
            for name, t in self.filtered(rep).items():
                c = base(name, t)
                sums[c] = sums.get(c, 0.0) + t["mem_kb"] / 1024.0
            raw.append(sums)
            for c, v in sums.items():
                peak[c] = max(peak.get(c, 0.0), v)

        if mode == "Content folder":
            keep = set(sorted(peak, key=lambda c: -peak[c])[:9])
            self.cat_fn = lambda n, t: base(n, t) if base(n, t) in keep else OTHER_FOLDERS
        else:
            self.cat_fn = base

        per = []
        for sums in raw:
            m = {}
            for c, v in sums.items():
                cc = c if mode != "Content folder" or c in keep else OTHER_FOLDERS
                m[cc] = m.get(cc, 0.0) + v
            per.append(m)
        cats = set().union(*per) if per else set()

        if mode == "Texture group":
            order = [c for c in GROUP_ORDER if c in cats]
            colors = GROUP_COLORS
        elif mode == "Content folder":
            order = sorted((c for c in cats if c != OTHER_FOLDERS), key=lambda c: -peak.get(c, 0))
            colors = {c: FOLDER_PALETTE[i % len(FOLDER_PALETTE)] for i, c in enumerate(order)}
            if OTHER_FOLDERS in cats:
                order.append(OTHER_FOLDERS)
                colors[OTHER_FOLDERS] = "#6E7A8F"
        else:
            order = [c for c in BINARY_COLORS if c in cats]
            colors = BINARY_COLORS
        self.cat_colors = colors
        series = [(c, colors.get(c, "#6E7A8F"), [p.get(c, 0.0) for p in per]) for c in order]
        self.series = series

        paths = [r.path for r in self.visible]
        if self.sel_path in paths:
            self.sel = paths.index(self.sel_path)
        else:
            self.sel = len(self.visible) - 1
            self.sel_path = paths[self.sel] if paths else None

        self.labels = self.make_labels(self.visible)
        self.timeline.set_data(self.labels,
                               [f"{r.long}   CL {r.cl}   {r.device}" for r in self.visible],
                               series, self.sel, self.hidden_cats)
        self.update_selected()

    @staticmethod
    def make_labels(reps):
        base = [r.short for r in reps]
        dup = {b for b in base if base.count(b) > 1}
        return [time.strftime("%m-%d %H:%M:%S", time.localtime(r.mtime)) if b in dup else b
                for r, b in zip(reps, base)]

    def toggle_category(self, cat):
        self.hidden_cats.symmetric_difference_update({cat})
        self.timeline.set_data(self.timeline.labels, self.timeline.long_labels, self.series,
                               self.sel, self.hidden_cats)

    # ---- navigation ----
    def select_index(self, i):
        if not self.visible:
            return
        i = max(0, min(i, len(self.visible) - 1))
        self.sel = i
        self.sel_path = self.visible[i].path
        self.timeline.set_data(self.timeline.labels, self.timeline.long_labels, self.series,
                               self.sel, self.hidden_cats)
        self.update_selected()

    def step(self, d):
        self.select_index(self.sel + d)

    def typing(self):
        w = self.root.focus_get()
        return isinstance(w, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text))

    def key_step(self, d):
        if not self.typing():
            self.step(d)
            return "break"

    def key_jump(self, i):
        if not self.typing() and self.visible:
            self.select_index(i if i >= 0 else len(self.visible) - 1)
            return "break"

    # ---- selected capture ----
    def update_selected(self):
        n = len(self.visible)
        if n == 0:
            self.pos_lbl.configure(text="No captures")
            self.info_lbl.configure(text="")
            self.warn_lbl.configure(text="")
            self.delta.set_data("Change from previous capture", [], [],
                                empty="Nothing to show for this capture type")
            self.changes.set_rows([])
            self.all_tbl.set_rows([])
            self.history.set_data(None, [], [], [], -1)
            self.summary_text = ""
            return

        cur = self.visible[self.sel]
        prev = self.visible[self.sel - 1] if self.sel > 0 else None
        self.prev_btn.state(["!disabled"] if self.sel > 0 else ["disabled"])
        self.next_btn.state(["!disabled"] if self.sel < n - 1 else ["disabled"])
        self.pos_lbl.configure(text=f"Capture {self.sel + 1} of {n}")

        now_map = self.filtered(cur)
        now_total = sum(t["mem_kb"] for t in now_map.values()) / 1024.0
        self.info_lbl.configure(
            text=f"{cur.long}     CL {cur.cl}     {cur.device} / {cur.config}     "
                 f"{now_total:.1f} MB shown     {os.path.basename(cur.path)}")

        warns = []
        if prev and (prev.device, prev.config) != (cur.device, cur.config):
            warns.append(f"The previous capture is {prev.device} / {prev.config}, so the two "
                         f"are not comparable.")
        if prev and prev.view and cur.view:
            metres = math.dist(prev.view, cur.view) / 100.0
            if metres > CAMERA_WARN_METRES:
                warns.append(f"The camera is {metres:,.0f} m from where the previous capture was "
                             f"taken, so world streaming will dominate the difference.")
        self.warn_lbl.configure(text="  ".join(warns))

        # changes
        old_map = self.filtered(prev) if prev else {}
        cat_delta = {}
        rows = []
        counts = {"new": [0, 0.0], "gone": [0, 0.0], "grew": [0, 0.0], "shrank": [0, 0.0]}
        for name in set(old_map) | set(now_map):
            a, b = old_map.get(name), now_map.get(name)
            was = a["mem_kb"] / 1024.0 if a else 0.0
            now = b["mem_kb"] / 1024.0 if b else 0.0
            d = now - was
            if abs(d) < 0.0005:
                continue
            t = b or a
            cat = self.cat_fn(name, t)
            cat_delta[cat] = cat_delta.get(cat, 0.0) + d
            status = "new" if not a else "gone" if not b else ("grew" if d > 0 else "shrank")
            counts[status][0] += 1
            counts[status][1] += d
            fmt = t["format"].replace("PF_", "")
            dims = f'{t["mem_w"]}x{t["mem_h"]}'
            if a and b:
                if a["format"] != b["format"]:
                    fmt = f'{a["format"].replace("PF_", "")} to {b["format"].replace("PF_", "")}'
                if (a["mem_w"], a["mem_h"]) != (b["mem_w"], b["mem_h"]):
                    dims = f'{a["mem_w"]}x{a["mem_h"]} to {b["mem_w"]}x{b["mem_h"]}'
            grp = group_bucket(name, t)
            flags = flags_of(t)
            short = name.split(".")[0]
            rows.append({
                "name": name,
                "tag": {"new": "new", "gone": "gone", "grew": "grow", "shrank": "shrink"}[status],
                "values": {"status": status, "change": f"{d:+.2f}",
                           "now": f"{now:.2f}" if b else "", "was": f"{was:.2f}" if a else "",
                           "dims": dims, "format": fmt, "group": grp, "flags": flags,
                           "name": short},
                "keys": {"mag": abs(d), "status": status, "change": d, "now": now, "was": was,
                         "dims": t["mem_w"] * t["mem_h"], "format": fmt, "group": grp,
                         "flags": flags, "name": short.lower()},
            })
        self.changes.set_rows(rows)

        # all textures in this capture
        items = sorted(now_map.items(), key=lambda kv: -kv[1]["mem_kb"])
        total_kb = sum(t["mem_kb"] for _, t in items) or 1.0
        run = 0.0
        all_rows = []
        for name, t in items:
            run += t["mem_kb"]
            mb = t["mem_kb"] / 1024.0
            grp = group_bucket(name, t)
            flags = flags_of(t)
            short = name.split(".")[0]
            fmt = t["format"].replace("PF_", "")
            all_rows.append({
                "name": name, "tag": "",
                "values": {"mb": f"{mb:.2f}", "cum": f"{100 * run / total_kb:.1f}%",
                           "dims": f'{t["mem_w"]}x{t["mem_h"]}', "format": fmt, "group": grp,
                           "flags": flags, "name": short},
                "keys": {"mb": mb, "cum": run, "dims": t["mem_w"] * t["mem_h"], "format": fmt,
                         "group": grp, "flags": flags, "name": short.lower()},
            })
        self.all_tbl.set_rows(all_rows)

        # delta chart
        if prev is None:
            self.delta.set_data("Change from previous capture",
                                [f"{now_total:.1f} MB shown in this capture"], [],
                                empty="This is the first capture of this type,\n"
                                      "so there is nothing to compare it with")
        else:
            prev_total = sum(t["mem_kb"] for t in old_map.values()) / 1024.0
            cat_rows = sorted(((c, self.cat_colors.get(c, "#6E7A8F"), v)
                               for c, v in cat_delta.items()), key=lambda r: -abs(r[2]))
            c = counts
            self.delta.set_data(
                f"Change from previous capture: {now_total - prev_total:+.1f} MB",
                [f"{prev_total:.1f} MB on {prev.long} to {now_total:.1f} MB now",
                 f"New {c['new'][0]} ({c['new'][1]:+.1f})   gone {c['gone'][0]} ({c['gone'][1]:+.1f})"
                 f"   grew {c['grew'][0]} ({c['grew'][1]:+.1f})"
                 f"   shrank {c['shrank'][0]} ({c['shrank'][1]:+.1f})"],
                cat_rows)
        self.refresh_history()
        self.build_summary(cur, prev, now_total, rows, cat_delta, counts, warns)

    def show_history(self, name):
        self.history_name = name
        self.refresh_history()

    def refresh_history(self):
        name = self.history_name
        if not name:
            self.history.set_data(None, [], [], [], -1)
            return
        vals, fmts = [], []
        for rep in self.visible:
            t = rep.textures.get(name)
            vals.append(t["mem_kb"] / 1024.0 if t else None)
            fmts.append(t["format"] if t else None)
        self.history.set_data(name, self.labels, vals, fmts, self.sel)

    def build_summary(self, cur, prev, now_total, rows, cat_delta, counts, warns):
        lines = [f"Capture {cur.long}, CL {cur.cl}, {cur.device}/{cur.config}",
                 os.path.basename(cur.path)]
        filt = []
        if self.only_var.get() != "All":
            filt.append(f"group {self.only_var.get()}")
        if self.filter_var.get().strip():
            filt.append(f'path contains "{self.filter_var.get().strip()}"')
        if self.hide_transient.get():
            filt.append("transient hidden")
        if filt:
            lines.append("Filters: " + ", ".join(filt))
        if prev is None:
            lines.append(f"Shown: {now_total:.1f} MB. First capture of this type.")
        else:
            d = sum(v[1] for v in counts.values())
            lines.append(f"Shown: {now_total:.1f} MB, {d:+.1f} MB vs {prev.long} (CL {prev.cl})")
            lines.append("  ".join(f"{k} {v[0]} ({v[1]:+.1f})" for k, v in counts.items()))
            lines += warns
            lines.append(f"By {self.mode_var.get().lower()}:")
            for c, v in sorted(cat_delta.items(), key=lambda kv: -abs(kv[1])):
                if abs(v) >= 0.05:
                    lines.append(f"  {c:<24} {v:+8.1f} MB")
            lines.append("Largest changes:")
            for r in sorted(rows, key=lambda r: -r["keys"]["mag"])[:15]:
                v = r["values"]
                lines.append(f"  {v['change']:>8} MB  {v['status']:<6} {v['format']:<22} {v['name']}")
        self.summary_text = "\n".join(lines)

    def copy_summary(self):
        if not self.summary_text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.summary_text)
        self.set_status("Summary copied to the clipboard")

    def set_status(self, text):
        self.status_lbl.configure(text=text)


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    folder = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else HERE
    root = tk.Tk()

    def report_error(exc, val, tb):
        messagebox.showerror("Texture pool viewer", "".join(traceback.format_exception(exc, val, tb)))

    root.report_callback_exception = report_error
    App(root, folder)
    root.mainloop()


if __name__ == "__main__":
    main()
