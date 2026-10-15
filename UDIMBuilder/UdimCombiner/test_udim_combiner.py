import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import UdimCombiner as combiner


class RequirementsTests(unittest.TestCase):
    def test_present_packages_do_not_trigger_install(self) -> None:
        with patch.object(combiner, "_package_ready", return_value=True), \
                patch.object(combiner.subprocess, "run") as install:
            self.assertTrue(combiner._ensure_requirements(ui=True, sync=True))

        install.assert_not_called()

    def test_unreal_ui_launch_installs_missing_package_with_its_python(self) -> None:
        with patch.object(combiner, "_package_ready", side_effect=[True, False, True, True]), \
                patch.object(combiner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as install:
            self.assertTrue(combiner._ensure_requirements(ui=True, sync=False))

        install.assert_called_once_with(
            [sys.executable, "-m", "pip", "install", "-r", str(combiner.SCRIPT_DIR / "requirements.txt")],
            cwd=str(combiner.SCRIPT_DIR),
            check=False,
        )

    def test_failed_install_stops_before_build(self) -> None:
        output = io.StringIO()
        with patch.object(combiner, "_resolve_configs", return_value=[Path("set.txt")]), \
                patch.object(combiner, "_package_ready", return_value=False), \
                patch.object(combiner.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
                patch.object(combiner, "run_configs") as build, redirect_stdout(output):
            self.assertEqual(combiner.main([]), 2)

        build.assert_not_called()
        marker = output.getvalue().split("[MANIFEST] ", 1)[1].splitlines()[0]
        self.assertFalse(Path(marker).exists())

    def test_no_sync_headless_build_does_not_require_perforce(self) -> None:
        with patch.object(combiner, "_resolve_configs", return_value=[Path("set.txt")]), \
                patch.object(combiner, "_ensure_requirements", return_value=True) as ensure, \
                patch.object(combiner, "run_configs", return_value=([], Path("manifest.json"))) as build:
            self.assertEqual(combiner.main(["--no-sync"]), 0)

        ensure.assert_called_once_with(ui=False, sync=False)
        self.assertFalse(build.call_args.kwargs["sync"])
        self.assertEqual(build.call_args.kwargs["manifest_path"], combiner.DEFAULT_MANIFEST_PATH)


if __name__ == "__main__":
    unittest.main()
