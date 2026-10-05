#!/usr/bin/env python3
"""Tests for pooldump.py. Standard library only.

    python -m unittest discover memreportViewer/tests

Memreports are written to a temporary folder, because the capture time is
read from the folder and file names.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pooldump  # noqa: E402

HEADER = (
    "MaxAllowedSize: Width x Height (Size in KB, Authored Bias), Current/InMem: "
    "Width x Height (Size in KB), Format, LODGroup, Name, Streaming, UnknownRef, "
    "VT, Usage Count, NumMips, Uncompressed\n"
)
FULL_MARKER = 'MemReport: Begin command "ListTextures"\n'


def texture_row(name, kb, fmt="PF_DXT1", group="TEXTUREGROUP_World",
                dims=(1024, 1024), streaming="YES", mips=11):
    w, h = dims
    return (f"{w}x{h} ({kb} KB, 0), {w}x{h} ({kb} KB), {fmt}, {group}, "
            f"{name}.{name.rsplit('/', 1)[-1]}, {streaming}, NO, NO, 0, {mips}, NO\n")


def write_memreport(path, rows, full=True, config="Development",
                    device="Windows", changelist="12345"):
    """Write a memreport holding one texture table."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"Changelist: {changelist}\nConfig: {config}\nDevice Name: {device}\n\n")
        if full:
            fh.write(FULL_MARKER)
        fh.write("Listing all textures.\n")
        fh.write(HEADER)
        for row in rows:
            fh.write(row)
        total = sum(float(r.split("(")[2].split(" KB")[0]) for r in rows) / 1024.0
        fh.write(f"Total size: InMem= {total:.2f} MB  OnDisk= {total:.2f} MB  "
                 f"Count={len(rows)}\n")
    return path


class Args:
    """Stand-in for the argparse namespace render_diff expects."""

    def __init__(self, **kw):
        self.top = 15
        self.group = None
        self.uncompressed = False
        self.exclude_transient = False
        self.min_delta_mb = 0.05
        self.fail_mb = None
        self.json = None
        self.__dict__.update(kw)


class TempReports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def session(self, name):
        """A UE session folder, named the way UE names them."""
        return os.path.join(self.root, name)


class AttributionTest(TempReports):
    """A texture that disappears has to count against the change."""

    def diff_output(self, old_rows, new_rows):
        old = write_memreport(self.session("S-01.01-00.00.00") + "/old-01-00.00.00.memreport",
                              old_rows)
        new = write_memreport(self.session("S-01.01-00.00.00") + "/new-01-00.00.01.memreport",
                              new_rows)
        out = io.StringIO()
        with redirect_stdout(out):
            pooldump.render_diff(pooldump.build_snapshot(old),
                                 pooldump.build_snapshot(new), Args())
        return out.getvalue()

    def test_removed_textures_subtract_from_attribution(self):
        # 100 MB leaves, 20 MB arrives: a 80 MB drop, not 20 MB of growth.
        text = self.diff_output(
            [texture_row("/Game/Old", 102400)],
            [texture_row("/Game/New", 20480)],
        )
        self.assertIn("-80.0 MB", text)
        line = next(l for l in text.splitlines() if "TEXTUREGROUP_World" in l)
        self.assertIn("-80.0", line)

    def test_attribution_matches_the_reported_total(self):
        text = self.diff_output(
            [texture_row("/Game/Stays", 10240), texture_row("/Game/Goes", 51200)],
            [texture_row("/Game/Stays", 20480), texture_row("/Game/Comes", 5120)],
        )
        self.assertNotIn("UNRECONCILED", text)
        attributed = sum(
            float(l.split()[-2]) for l in text.splitlines()
            if l.startswith("  TEXTUREGROUP"))
        self.assertAlmostEqual(attributed, -35.0, places=1)


class FullReportTest(TempReports):
    def test_full_report_is_recognised(self):
        path = write_memreport(self.session("S-01.01-00.00.00") + "/a-01-00.00.00.memreport",
                               [texture_row("/Game/A", 1024)], full=True)
        _tex, _totals, stats = pooldump.parse_memreport(path)
        self.assertTrue(stats["is_full_report"])

    def test_partial_report_is_recognised_and_warns(self):
        path = write_memreport(self.session("S-01.01-00.00.00") + "/b-01-00.00.00.memreport",
                               [texture_row("/Game/B", 1024)], full=False)
        _tex, _totals, stats = pooldump.parse_memreport(path)
        self.assertFalse(stats["is_full_report"])
        out = io.StringIO()
        with redirect_stdout(out):
            pooldump.warn_if_not_full(path, stats)
        self.assertIn("not a full memreport", out.getvalue())

    def test_full_report_does_not_warn(self):
        path = write_memreport(self.session("S-01.01-00.00.00") + "/c-01-00.00.00.memreport",
                               [texture_row("/Game/C", 1024)], full=True)
        _tex, _totals, stats = pooldump.parse_memreport(path)
        out = io.StringIO()
        with redirect_stdout(out):
            pooldump.warn_if_not_full(path, stats)
        self.assertEqual(out.getvalue(), "")


class OrderingTest(TempReports):
    """Ordering comes from the names UE writes, not the file's mtime.

    Copying or unzipping reports can give every file the same mtime.
    """

    def test_capture_time_beats_modification_time(self):
        session = self.session("MainLevel-09.01-10.00.00")
        early = write_memreport(session + "/Pid1_MainLevel-Windows-01-10.00.00.memreport",
                                [texture_row("/Game/A", 1024)])
        late = write_memreport(session + "/Pid2_MainLevel-Windows-01-18.30.00.memreport",
                               [texture_row("/Game/B", 1024)])
        # The later capture was copied first, so its mtime is older. Both were
        # copied the same day, which is where the missing year comes from.
        os.utime(late, (1_800_000_000, 1_800_000_000))
        os.utime(early, (1_800_000_500, 1_800_000_500))
        self.assertLess(pooldump.memreport_capture_time(early),
                        pooldump.memreport_capture_time(late))
        self.assertEqual(sorted([late, early], key=pooldump.memreport_sort_key),
                         [early, late])

    def test_session_crossing_a_month_boundary(self):
        session = self.session("MainLevel-01.31-23.00.00")
        before = write_memreport(session + "/Pid1_MainLevel-Windows-31-23.30.00.memreport",
                                 [texture_row("/Game/A", 1024)])
        after = write_memreport(session + "/Pid2_MainLevel-Windows-01-00.30.00.memreport",
                                [texture_row("/Game/B", 1024)])
        self.assertLess(pooldump.memreport_capture_time(before),
                        pooldump.memreport_capture_time(after))

    def test_unrecognised_names_fall_back_to_mtime(self):
        path = write_memreport(os.path.join(self.root, "loose", "report.memreport"),
                               [texture_row("/Game/A", 1024)])
        os.utime(path, (1_700_000_000, 1_700_000_000))
        self.assertEqual(pooldump.memreport_capture_time(path), 1_700_000_000)

    def test_resolve_picks_the_newest_capture(self):
        session = self.session("MainLevel-09.01-10.00.00")
        write_memreport(session + "/Pid1_MainLevel-Windows-01-10.00.00.memreport",
                        [texture_row("/Game/A", 1024)])
        newest = write_memreport(session + "/Pid2_MainLevel-Windows-01-18.30.00.memreport",
                                 [texture_row("/Game/B", 1024)])
        self.assertEqual(os.path.abspath(pooldump.resolve_memreport(self.root)),
                         os.path.abspath(newest))


class SnapshotMetadataTest(TempReports):
    def test_label_defaults_to_the_changelist(self):
        path = write_memreport(self.session("S-01.01-00.00.00") + "/a-01-00.00.00.memreport",
                               [texture_row("/Game/A", 1024)], changelist="54321")
        snap = pooldump.build_snapshot(path)
        self.assertEqual(snap["label"], "54321")

    def test_captured_at_is_the_capture_time(self):
        path = write_memreport(self.session("S-01.01-00.00.00") + "/a-01-00.00.00.memreport",
                               [texture_row("/Game/A", 1024)])
        snap = pooldump.build_snapshot(path)
        self.assertEqual(snap["captured_at"], pooldump.memreport_capture_iso(path))
        self.assertNotEqual(snap["captured_at"], snap["snapshot_created_at"])


class CaptureKindTest(TempReports):
    def test_config_is_part_of_the_capture_kind(self):
        dev = write_memreport(self.session("S-01.01-00.00.00") + "/a-01-00.00.00.memreport",
                              [texture_row("/Game/A", 1024)], config="Development")
        test = write_memreport(self.session("S-01.01-00.00.00") + "/b-01-00.00.01.memreport",
                               [texture_row("/Game/A", 1024)], config="Test")
        dev_snap, test_snap = pooldump.build_snapshot(dev), pooldump.build_snapshot(test)
        self.assertEqual(pooldump.device_name(dev_snap), pooldump.device_name(test_snap))
        self.assertNotEqual(pooldump.config_name(dev_snap), pooldump.config_name(test_snap))
        self.assertNotEqual(pooldump.capture_kind(dev_snap), pooldump.capture_kind(test_snap))

    def test_mismatched_kinds_warn(self):
        dev = write_memreport(self.session("S-01.01-00.00.00") + "/a-01-00.00.00.memreport",
                              [texture_row("/Game/A", 1024)], config="Development")
        test = write_memreport(self.session("S-01.01-00.00.00") + "/b-01-00.00.01.memreport",
                               [texture_row("/Game/A", 2048)], config="Test")
        out = io.StringIO()
        with redirect_stdout(out):
            pooldump.render_diff(pooldump.build_snapshot(dev),
                                 pooldump.build_snapshot(test), Args())
        self.assertIn("capture types differ", out.getvalue())


if __name__ == "__main__":
    unittest.main()
