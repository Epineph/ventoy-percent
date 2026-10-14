"""Disk-free checks for sizing, preview behavior, and execution safeguards."""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

loader = importlib.machinery.SourceFileLoader(
  "ventoy_percent", str(Path(__file__).with_name("ventoy-percent"))
)
spec = importlib.util.spec_from_loader(loader.name, loader)
ventoy = importlib.util.module_from_spec(spec)
loader.exec_module(ventoy)


def disk_metadata(**changes):
  disk = {
    "path": "/dev/example",
    "type": "disk",
    "size": 100 * ventoy.GIB,
    "log-sec": 512,
    "mountpoints": [None],
    "ro": False,
    "children": [],
  }
  disk.update(changes)
  return disk


class VentoyPercentTests(unittest.TestCase):
  def test_percentage_validation(self):
    self.assertEqual(ventoy.percentage("12.5%"), ventoy.Fraction(25, 2))
    for value in ["0", "100", "-15", "NaN", "inf", "15%%", "1e99"]:
      with (
        self.subTest(value=value),
        self.assertRaises(argparse.ArgumentTypeError),
      ):
        ventoy.percentage(value)

  def test_sizing_respects_target_and_conserves_space(self):
    for total in [ventoy.GIB, 500107862016, 8 * 1024 * ventoy.GIB]:
      for gpt in [False, True]:
        for percent in ["15", "12.5", "95"]:
          with self.subTest(total=total, gpt=gpt, percent=percent):
            fraction = ventoy.percentage(percent)
            reserved, iso = ventoy.partition_sizes(total, fraction, gpt)
            target = total * fraction / 100
            self.assertLessEqual(iso, target)
            self.assertLess(target - iso, ventoy.MIB)
            self.assertGreater(reserved, 0)
            self.assertEqual(iso % 4096, 0)
            overhead = total - iso - reserved * ventoy.MIB
            minimum = 33 * ventoy.MIB + (33 * 512 if gpt else 0)
            self.assertGreaterEqual(overhead, minimum)
            self.assertLess(overhead - minimum, 4096)

  def test_boot_overhead_and_too_small_targets(self):
    with self.assertRaisesRegex(ValueError, "below"):
      ventoy.partition_sizes(ventoy.GIB, ventoy.percentage("0.01"), True)
    with self.assertRaisesRegex(ValueError, "boot data"):
      ventoy.partition_sizes(ventoy.GIB, ventoy.percentage("99.9"), True)

  def run_cli(self, extra=(), metadata=None, install_status=0):
    disk = disk_metadata() if metadata is None else metadata
    inspection = subprocess.CompletedProcess(
      [], 0, stdout=json.dumps({"blockdevices": [disk]})
    )
    completed = subprocess.CompletedProcess([], install_status)
    output = io.StringIO()
    with (
      mock.patch.object(
        ventoy.subprocess, "run", side_effect=[inspection, completed]
      ) as run,
      mock.patch.object(ventoy.shutil, "which", return_value="/usr/bin/ventoy"),
      mock.patch.object(ventoy.os, "geteuid", return_value=0),
      mock.patch.object(ventoy.Path, "is_block_device", return_value=True),
      contextlib.redirect_stdout(output),
      contextlib.redirect_stderr(output),
    ):
      try:
        status = ventoy.main(
          [
            "--iso-percent",
            "15",
            "-g",
            "-L",
            "iso_disk",
            *extra,
            "/dev/example",
          ]
        )
      except SystemExit as error:
        status = error.code
    return status, output.getvalue(), run

  def test_preview_never_calls_installer(self):
    status, output, run = self.run_cli()
    self.assertEqual(status, 0)
    self.assertIn("Preview only", output)
    self.assertEqual(run.call_count, 1)
    self.assertEqual(run.call_args.args[0][0], "lsblk")

  def test_execution_passes_correct_command_and_exit_status(self):
    status, _, run = self.run_cli(["--execute"], install_status=7)
    self.assertEqual(status, 7)
    self.assertEqual(run.call_count, 2)
    self.assertEqual(
      run.call_args.args[0],
      [
        "/usr/bin/ventoy",
        "-i",
        "-r",
        "87007",
        "-g",
        "-L",
        "iso_disk",
        "/dev/example",
      ],
    )
    self.assertEqual(run.call_args.kwargs, {"check": False})

  def test_rejects_partition_read_only_and_native_4k(self):
    for changes in [{"type": "part"}, {"ro": True}, {"log-sec": 4096}]:
      with self.subTest(changes=changes):
        status, _, run = self.run_cli(metadata=disk_metadata(**changes))
        self.assertEqual(status, 1)
        self.assertEqual(run.call_count, 1)

  def test_rejects_mounted_swap_and_active_mappings(self):
    for kind, mounts in [
      ("part", ["/data"]),
      ("part", ["[SWAP]"]),
      ("crypt", [None]),
    ]:
      with self.subTest(kind=kind, mounts=mounts):
        child = disk_metadata(path="/dev/example1", type=kind, mountpoints=mounts)
        status, _, run = self.run_cli(
          ["--execute"], disk_metadata(children=[child])
        )
        self.assertEqual(status, 1)
        self.assertEqual(run.call_count, 1)

  def test_requires_root_and_accessible_device_for_execution(self):
    disk = disk_metadata()
    with (
      mock.patch.object(ventoy.os, "geteuid", return_value=1000),
      self.assertRaisesRegex(ValueError, "requires root"),
    ):
      ventoy.check_execution(Path("/dev/example"), disk)
    with (
      mock.patch.object(ventoy.os, "geteuid", return_value=0),
      mock.patch.object(ventoy.Path, "is_block_device", return_value=False),
      self.assertRaisesRegex(ValueError, "accessible block device"),
    ):
      ventoy.check_execution(Path("/dev/example"), disk)


if __name__ == "__main__":
  unittest.main()
