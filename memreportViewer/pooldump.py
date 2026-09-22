#!/usr/bin/env python3
"""
pooldump.py - read UE5 `memreport -full` output as a texture-pool report.

Four subcommands:

  list      one report, sorted per-texture listing        (what is in the pool)
  snapshot  one report -> a JSON snapshot                 (for later comparison)
  diff      two snapshots -> what changed and why
  auto      newest two reports in a folder -> same as diff, no filenames needed

Any place a memreport path is accepted you may pass a DIRECTORY instead, and
the newest .memreport found underneath it is used. So from inside
Saved/Profiling/MemReports, "." is usually all you need.

--------------------------------------------------------------------- list ---
  python pooldump.py list .
  python pooldump.py list . --group UI --top 40          biggest UI first
  python pooldump.py list . --uncompressed --top 40      compression candidates
  python pooldump.py list . --group UI --uncompressed --csv ui.csv
  python pooldump.py list . --pinned --min-mb 4          big never-streaming
  python pooldump.py list . --contains EscapeMenu        one folder or asset

  The cum% column is the useful one: it usually shows a handful of assets
  holding most of a group, which is where the work is.

----------------------------------------------------------------- snapshot ---
  python pooldump.py snapshot . -o snaps/2026-09-01.json

  Labels itself from the memreport's Changelist header, so --label is only
  needed when you want something else.

--------------------------------------------------------------- diff / auto ---
  python pooldump.py auto                                newest two, here
  python pooldump.py auto --device Windows               cooked only
  python pooldump.py auto --device WindowsEditor         editor only
  python pooldump.py auto --group UI --exclude-transient
  python pooldump.py auto --uncompressed                 compression progress
  python pooldump.py diff snaps/mon.json snaps/tue.json --fail-mb 32

  --device matches the memreport's Device Name exactly. Use it whenever
  editor and cooked reports share a folder tree, or "newest two" will pair
  captures that are not comparable. A mismatch prints a warning either way.

  --fail-mb exits 1 when the pool grew past the budget, for CI gating.
  --json writes the same report machine-readably.

------------------------------------------------------------- filter flags ---
  --group SUBSTRING       LODGroup substring: UI, World, Character, Skybox
  --uncompressed          only uncompressed pixel formats
  --exclude-transient     drop runtime scratch: /Engine/Transient render
                          targets and textures created by level actors
  --contains SUBSTRING    asset path substring                    (list only)
  --pinned / --streaming  never-streaming vs streaming            (list only)
  --min-mb N              hide small textures                     (list only)
  --min-delta-mb N        hide small changes               (diff / auto only)

---------------------------------------------------------------- gotchas -----
  Camera position dominates. World streaming swings the total by 100 MB+
  between two captures taken metres apart, which will bury any content
  change. Capture from a fixed spot, or filter to --group UI.

  Session state matters for UI. Opening menus loads screen-specific art that
  may never release, so a report taken after browsing menus is not comparable
  to one taken at load. Decide which state you are measuring and stick to it.

  Run `obj gc` before capturing if you are testing whether something
  unloads. Dropping a reference does not free memory; collection does, and
  the default gap between purges is around a minute.

  The memreport's own Uncompressed column is unreliable (it can read NO for a
  PF_B8G8R8A8 texture), so compression here is derived from the pixel format.
  The raw column is kept as uncompressed_flag in snapshots.

  Editor captures include /Engine/Transient render targets and editor-only
  plugin allocations that never ship. Prefer cooked, or --exclude-transient.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

# "4096x4096 (10922 KB, ?), 2048x2048 (2731 KB), <rest>"
ROW_RE = re.compile(
    r"^\s*(?P<dx>\d+)x(?P<dy>\d+)\s*\(\s*(?P<dkb>[\d.]+)\s*KB[^)]*\)\s*,\s*"
    r"(?P<mx>\d+)x(?P<my>\d+)\s*\(\s*(?P<mkb>[\d.]+)\s*KB\s*\)\s*,\s*"
    r"(?P<rest>.+?)\s*$"
)

HEADER_MARK = "Current/InMem"
TOTAL_RE = re.compile(
    r"Total size:\s*InMem=\s*(?P<inmem>[\d.]+)\s*MB\s+OnDisk=\s*(?P<ondisk>[\d.]+)\s*MB\s*Count=(?P<count>\d+)"
)


def resolve_memreport(path, index=0):
    """Accept a file, or a directory to search recursively for .memreport files.

    index 0 is the newest by modification time, 1 the one before it.
    """
    import os
    import glob as _glob
    if os.path.isfile(path):
        return path
    if not os.path.isdir(path):
        raise SystemExit(f"not a file or directory: {path}")
    found = _glob.glob(os.path.join(path, "**", "*.memreport"), recursive=True)
    if len(found) <= index:
        raise SystemExit(f"found {len(found)} memreports under {path}, need at least {index+1}")
    found.sort(key=os.path.getmtime, reverse=True)
    return found[index]


def normalize(field):
    return field.strip().lower().replace(" ", "").replace("_", "")


def header_tail_fields(header_line):
    """Derive the trailing column names from the memreport header line."""
    # Everything after the second "(Size in KB)" block is the tail.
    idx = header_line.rfind("KB)")
    tail = header_line[idx + 3:] if idx != -1 else header_line
    return [normalize(f) for f in tail.split(",") if f.strip()]


def parse_memreport(path):
    """Return (textures, section_totals, stats)."""
    textures = {}
    totals = []
    fields = None
    unparsed = 0
    header = {}
    current_section = None
    cap_label = None

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            for key in ("Changelist", "Config", "Device Name", "Device Profile"):
                if line.startswith(key + ":"):
                    header[key] = line.split(":", 1)[1].strip()

            if HEADER_MARK in line and "Format" in line:
                fields = header_tail_fields(line)
                cap_label = line.split(":")[0].strip()
                continue

            low = line.strip().lower()
            if low.startswith("listing "):
                current_section = line.strip().rstrip(".")
                continue

            tm = TOTAL_RE.search(line)
            if tm:
                totals.append({
                    "section": current_section,
                    "inmem_mb": float(tm.group("inmem")),
                    "ondisk_mb": float(tm.group("ondisk")),
                    "count": int(tm.group("count")),
                })
                continue

            m = ROW_RE.match(line)
            if not m:
                if line.strip() and "," in line and " KB" in line:
                    unparsed += 1
                continue
            if not fields:
                unparsed += 1
                continue

            rest = [p.strip() for p in m.group("rest").split(",")]
            if len(rest) < len(fields):
                unparsed += 1
                continue
            # If the asset name contained commas, extra parts land in the middle.
            # Trust the tail alignment: last N-? fields are fixed-position.
            row = dict(zip(fields, rest[: len(fields)]))

            name = row.get("name")
            if not name:
                unparsed += 1
                continue

            def yn(key):
                v = row.get(key, "")
                return v.strip().upper() in ("YES", "TRUE", "1")

            def num(key, default=0):
                try:
                    return int(float(row.get(key, default)))
                except (TypeError, ValueError):
                    return default

            entry = {
                "name": name,
                "cap_w": int(m.group("dx")),
                "cap_h": int(m.group("dy")),
                "cap_kb": float(m.group("dkb")),
                "mem_w": int(m.group("mx")),
                "mem_h": int(m.group("my")),
                "mem_kb": float(m.group("mkb")),
                "format": row.get("format", ""),
                "lod_group": row.get("lodgroup", ""),
                "streaming": yn("streaming"),
                "unknown_ref": yn("unknownref"),
                "vt": yn("vt"),
                "usage_count": num("usagecount"),
                "num_mips": num("nummips"),
                # The memreport's own Uncompressed column is unreliable (it can
                # read NO for a PF_B8G8R8A8 texture), so derive it from format.
                "uncompressed": not row.get("format", "").startswith(
                    ("PF_DXT", "PF_BC", "PF_ASTC", "PF_ETC", "PF_PVRTC")),
                "uncompressed_flag": yn("uncompressed"),
            }
            # A texture can appear in both the NONVT and VT tables; keep the larger.
            prev = textures.get(name)
            if prev is None or entry["mem_kb"] > prev["mem_kb"]:
                textures[name] = entry

    return textures, totals, {"unparsed_rows": unparsed, "fields": fields, "cap_label": cap_label, "header": header}


def classify(t):
    """Why is this texture sitting in memory? Ordered most-actionable first."""
    tags = []
    if t["vt"]:
        tags.append("VT")
    if not t["streaming"]:
        tags.append("NEVER_STREAM")
    if t["lod_group"].upper().endswith("UI"):
        tags.append("UI")
    if t["uncompressed"]:
        tags.append("UNCOMPRESSED")
    if t["unknown_ref"]:
        tags.append("UNKNOWN_REF")
    if t["num_mips"] <= 1 and max(t["mem_w"], t["mem_h"]) > 512:
        tags.append("NO_MIPS")
    if t["usage_count"] == 0:
        tags.append("UNUSED")
    return tags


def mb(kb):
    return kb / 1024.0


def cmd_snapshot(args):
    path = resolve_memreport(args.memreport)
    textures, totals, stats = parse_memreport(path)
    snap = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": path,
        "label": args.label,
        "section_totals": totals,
        "parse_stats": stats,
        "textures": textures,
    }
    out = args.output or "snapshot.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1, sort_keys=True)

    total_mb = mb(sum(t["mem_kb"] for t in textures.values()))
    print(f"parsed {len(textures)} textures, {total_mb:.1f} MB resident -> {out}")
    if stats["unparsed_rows"]:
        print(f"WARNING: {stats['unparsed_rows']} rows did not match the row pattern")

    buckets = defaultdict(float)
    for t in textures.values():
        for tag in classify(t) or ["PLAIN_STREAMING"]:
            buckets[tag] += mb(t["mem_kb"])
    print("\nresident MB by tag (textures can carry several tags):")
    for tag, v in sorted(buckets.items(), key=lambda kv: -kv[1]):
        print(f"  {tag:<16} {v:8.1f} MB")
    return 0


def cmd_diff(args):
    with open(args.old, encoding="utf-8") as fh:
        old = json.load(fh)
    with open(args.new, encoding="utf-8") as fh:
        new = json.load(fh)
    return render_diff(old, new, args)


def build_snapshot(path, label=None):
    textures, totals, stats = parse_memreport(path)
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": path,
        "label": label or stats.get("header", {}).get("Changelist"),
        "section_totals": totals,
        "parse_stats": stats,
        "textures": textures,
    }


def device_name(snap):
    h = snap.get("parse_stats", {}).get("header", {}) or {}
    return h.get("Device Name", "?")


def capture_kind(snap):
    """Device Name + Config, e.g. 'Windows/Development'. Used to refuse
    comparing an editor PIE capture against a cooked one."""
    h = snap.get("parse_stats", {}).get("header", {}) or {}
    return f"{h.get('Device Name', '?')}/{h.get('Config', '?')}"


TRANSIENT_PREFIXES = ("/Engine/Transient", "/Engine/EditorResources")


def apply_filters(textures, args):
    out = dict(textures)
    if getattr(args, "exclude_transient", False):
        out = {k: v for k, v in out.items()
               if not k.startswith(TRANSIENT_PREFIXES) and ":PersistentLevel." not in k}
    grp = getattr(args, "group", None)
    if grp:
        out = {k: v for k, v in out.items() if grp.lower() in v["lod_group"].lower()}
    if getattr(args, "uncompressed", False):
        out = {k: v for k, v in out.items() if v["uncompressed"]}
    return out


def render_diff(old, new, args):
    ok, nk = capture_kind(old), capture_kind(new)
    if ok != nk:
        print(f"WARNING: capture types differ ({ok} vs {nk}). "
              f"These numbers are not comparable.\n")

    o = apply_filters(old["textures"], args)
    n = apply_filters(new["textures"], args)
    o_total = sum(t["mem_kb"] for t in o.values())
    n_total = sum(t["mem_kb"] for t in n.values())
    delta_mb = mb(n_total - o_total)

    added = [n[k] for k in n.keys() - o.keys()]
    removed = [o[k] for k in o.keys() - n.keys()]
    changed = []
    for k in n.keys() & o.keys():
        d = n[k]["mem_kb"] - o[k]["mem_kb"]
        if abs(d) > 0.5:
            e = dict(n[k])
            e["delta_kb"] = d
            e["was_mem_kb"] = o[k]["mem_kb"]
            changed.append(e)

    grown = [t for t in changed if t["delta_kb"] > 0]
    shrunk = [t for t in changed if t["delta_kb"] < 0]

    added.sort(key=lambda t: -t["mem_kb"])
    removed.sort(key=lambda t: -t["mem_kb"])
    grown.sort(key=lambda t: -t["delta_kb"])
    shrunk.sort(key=lambda t: t["delta_kb"])

    added_mb = mb(sum(t["mem_kb"] for t in added))
    removed_mb = mb(sum(t["mem_kb"] for t in removed))
    grown_mb = mb(sum(t["delta_kb"] for t in grown))
    shrunk_mb = mb(sum(t["delta_kb"] for t in shrunk))

    print(f"pool resident: {mb(o_total):.1f} MB -> {mb(n_total):.1f} MB  "
          f"({delta_mb:+.1f} MB)")
    print(f"  new textures:    {len(added):>5}  {added_mb:+8.1f} MB")
    print(f"  gone textures:   {len(removed):>5}  {-removed_mb:+8.1f} MB")
    print(f"  grew in place:   {len(grown):>5}  {grown_mb:+8.1f} MB")
    print(f"  shrank in place: {len(shrunk):>5}  {shrunk_mb:+8.1f} MB")
    residual = delta_mb - (added_mb - removed_mb + grown_mb + shrunk_mb)
    if abs(residual) > 0.05:
        print(f"  UNRECONCILED:            {residual:+8.1f} MB  (parser bug)")

    # Attribute the delta to categories so "why" is answerable at a glance.
    attrib = defaultdict(float)
    for t in added:
        for tag in classify(t) or ["PLAIN_STREAMING"]:
            attrib[tag] += mb(t["mem_kb"])
    for t in changed:
        for tag in classify(t) or ["PLAIN_STREAMING"]:
            attrib[tag] += mb(t["delta_kb"])
    if attrib:
        print("\ngrowth attributed by tag:")
        for tag, v in sorted(attrib.items(), key=lambda kv: -kv[1]):
            if abs(v) >= 0.1:
                print(f"  {tag:<16} {v:+8.1f} MB")

    by_group = defaultdict(float)
    for t in added:
        by_group[t["lod_group"] or "(none)"] += mb(t["mem_kb"])
    for t in changed:
        by_group[t["lod_group"] or "(none)"] += mb(t["delta_kb"])
    if by_group:
        print("\ngrowth by LODGroup:")
        for g, v in sorted(by_group.items(), key=lambda kv: -kv[1])[:12]:
            if abs(v) >= 0.1:
                print(f"  {g:<32} {v:+8.1f} MB")

    def show(title, rows, key):
        if not rows:
            return
        rows = [t for t in rows if abs(mb(t.get(key, 0))) >= args.min_delta_mb]
        if not rows:
            return
        print(f"\n{title}")
        for t in rows[: args.top]:
            tags = ",".join(classify(t)) or "-"
            print(f"  {mb(t[key]):+8.2f} MB  {t['mem_w']}x{t['mem_h']} "
                  f"{t['format']:<12} [{tags}]  {t['name']}")

    show(f"top new (of {len(added)}):", added, "mem_kb")
    show(f"top grown (of {len(grown)}):", grown, "delta_kb")
    show(f"top shrank (of {len(shrunk)}):", shrunk, "delta_kb")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({
                "old_label": old.get("label"),
                "new_label": new.get("label"),
                "old_mb": mb(o_total),
                "new_mb": mb(n_total),
                "delta_mb": delta_mb,
                "attribution_mb": dict(attrib),
                "by_lod_group_mb": dict(by_group),
                "added": added,
                "grown": grown,
                "shrunk": shrunk,
                "removed": removed,
            }, fh, indent=1, sort_keys=True)
        print(f"\nwrote {args.json}")

    if args.fail_mb is not None and delta_mb > args.fail_mb:
        print(f"\nFAIL: pool grew {delta_mb:.1f} MB, budget is {args.fail_mb:.1f} MB")
        return 1
    return 0


def cmd_list(args):
    path = resolve_memreport(args.memreport)
    print(f"# {path}\n")
    textures, totals, stats = parse_memreport(path)
    T = list(textures.values())

    if args.group:
        g = args.group.lower()
        T = [t for t in T if g in t["lod_group"].lower()]
    if args.contains:
        c = args.contains.lower()
        T = [t for t in T if c in t["name"].lower()]
    if args.min_mb:
        T = [t for t in T if mb(t["mem_kb"]) >= args.min_mb]
    if args.pinned:
        T = [t for t in T if not t["streaming"]]
    if args.streaming:
        T = [t for t in T if t["streaming"]]
    if args.uncompressed:
        T = [t for t in T if t["uncompressed"]]

    T.sort(key=lambda t: -t["mem_kb"])
    grand = sum(t["mem_kb"] for t in T)

    print(f"{len(T)} textures, {mb(grand):.1f} MB total\n")
    print(f"{'MB':>7} {'cum%':>6}  {'dims':<12} {'format':<14} {'flags':<22} name")
    run = 0.0
    for t in T[: args.top]:
        run += t["mem_kb"]
        flags = ",".join(classify(t)) or "-"
        print(f"{mb(t['mem_kb']):7.2f} {100*run/grand:5.1f}%  "
              f"{str(t['mem_w'])+'x'+str(t['mem_h']):<12} {t['format']:<14} "
              f"{flags:<22} {t['name'].split('.')[0]}")
    if len(T) > args.top:
        rest = grand - run
        print(f"\n... {len(T)-args.top} more, {mb(rest):.1f} MB")

    if args.csv:
        import csv as _csv
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            w.writerow(["mb", "width", "height", "format", "lod_group",
                        "streaming", "uncompressed", "num_mips", "flags", "name"])
            for t in T:
                w.writerow([round(mb(t["mem_kb"]), 3), t["mem_w"], t["mem_h"],
                            t["format"], t["lod_group"], t["streaming"],
                            t["uncompressed"], t["num_mips"],
                            "|".join(classify(t)), t["name"]])
        print(f"\nwrote {args.csv}")
    return 0


def cmd_auto(args):
    import glob as _glob
    import os
    found = _glob.glob(os.path.join(args.dir, "**", "*.memreport"), recursive=True)
    found.sort(key=os.path.getmtime, reverse=True)

    picked = []
    for path in found:
        snap = build_snapshot(path)
        if args.device and args.device.lower() != device_name(snap).lower():
            continue
        picked.append((path, snap))
        if len(picked) == 2:
            break
    if len(picked) < 2:
        raise SystemExit(f"need 2 matching memreports under {args.dir}, found {len(picked)}")

    (new_path, new), (old_path, old) = picked
    print(f"# older: {old_path}\n# newer: {new_path}\n")
    if args.save_dir:
        import os
        os.makedirs(args.save_dir, exist_ok=True)
        for snap, tag in ((old, "prev"), (new, "latest")):
            with open(os.path.join(args.save_dir, tag + ".json"), "w",
                      encoding="utf-8") as fh:
                json.dump(snap, fh, indent=1, sort_keys=True)
    return render_diff(old, new, args)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="parse a .memreport into a JSON snapshot")
    s.add_argument("memreport")
    s.add_argument("-o", "--output")
    s.add_argument("--label", help="e.g. the CL/commit this build came from")
    s.set_defaults(func=cmd_snapshot)

    d = sub.add_parser("diff", help="compare two snapshots")
    d.add_argument("old")
    d.add_argument("new")
    d.add_argument("--json", help="also write a machine-readable report here")
    d.add_argument("--top", type=int, default=15)
    d.add_argument("--group", help="restrict the diff to a LODGroup substring")
    d.add_argument("--uncompressed", action="store_true",
                   help="only textures in an uncompressed pixel format")
    d.add_argument("--exclude-transient", action="store_true",
                   help="ignore /Engine/Transient runtime scratch")
    d.add_argument("--min-delta-mb", type=float, default=0.05,
                   help="hide per-texture rows below this")
    d.add_argument("--fail-mb", type=float,
                   help="exit 1 if resident pool grew more than this")
    d.set_defaults(func=cmd_diff)

    l = sub.add_parser("list", help="sorted per-texture listing from one memreport")
    l.add_argument("memreport")
    l.add_argument("--group", help="filter by LODGroup substring, e.g. UI, World, Character")
    l.add_argument("--contains", help="filter by asset path substring")
    l.add_argument("--min-mb", type=float, default=0.0)
    l.add_argument("--pinned", action="store_true", help="only non-streaming textures")
    l.add_argument("--streaming", action="store_true", help="only streaming textures")
    l.add_argument("--uncompressed", action="store_true")
    l.add_argument("--top", type=int, default=40)
    l.add_argument("--csv", help="write the full filtered list here")
    l.set_defaults(func=cmd_list)

    a = sub.add_parser("auto", help="diff the two most recent memreports in a folder tree")
    a.add_argument("dir", nargs="?", default=".",
                   help="folder to search recursively (default: current)")
    a.add_argument("--json", help="also write a machine-readable report here")
    a.add_argument("--save-dir", help="keep the two parsed snapshots here")
    a.add_argument("--top", type=int, default=15)
    a.add_argument("--group", help="restrict the diff to a LODGroup substring")
    a.add_argument("--uncompressed", action="store_true",
                   help="only textures in an uncompressed pixel format")
    a.add_argument("--exclude-transient", action="store_true",
                   help="ignore /Engine/Transient runtime scratch")
    a.add_argument("--min-delta-mb", type=float, default=0.05,
                   help="hide per-texture rows below this")
    a.add_argument("--fail-mb", type=float)
    a.add_argument("--device", help="only consider captures whose Device Name "
                                    "matches, e.g. Windows or WindowsEditor")
    a.set_defaults(func=cmd_auto)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
