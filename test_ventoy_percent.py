#!/usr/bin/env python3
"""Disk-free checks for sizing, confirmation controls, and execution safeguards."""

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
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

  def run_cli(
    self, extra=(), metadata=None, install_status=0, answers=(), iso_percent="15",
    sleep_error=None,
  ):
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
      mock.patch("builtins.input", side_effect=answers) as prompt,
      mock.patch.object(ventoy.time, "sleep", side_effect=sleep_error) as pause,
      contextlib.redirect_stdout(output),
      contextlib.redirect_stderr(output),
    ):
      try:
        status = ventoy.main(
          [
            *(["--iso-percent", iso_percent] if iso_percent is not None else []),
            "-g",
            "-L",
            "iso_disk",
            *extra,
            "/dev/example",
          ]
        )
      except SystemExit as error:
        status = error.code
    return status, output.getvalue(), run, prompt, pause

  def test_preview_never_calls_installer(self):
    status, output, run, prompt, pause = self.run_cli()
    self.assertEqual(status, 0)
    self.assertIn("Preview only", output)
    self.assertEqual(run.call_count, 1)
    self.assertEqual(run.call_args.args[0][0], "lsblk")
    prompt.assert_not_called()
    pause.assert_not_called()

  def test_execution_passes_correct_command_and_exit_status(self):
    status, _, run, prompt, pause = self.run_cli(["--execute"], install_status=7)
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
    prompt.assert_not_called()
    pause.assert_not_called()

  def test_operation_aliases_and_native_prompts(self):
    for aliases, native in [
      (["--install", "-i"], "-i"),
      (["--reinstall", "--re-install", "-I"], "-I"),
      (["--update", "-u", "-U"], "-u"),
    ]:
      for alias in aliases:
        with self.subTest(alias=alias):
          status, output, run, prompt, pause = self.run_cli(["--execute", alias])
          self.assertEqual(status, 0, output)
          self.assertEqual(run.call_args.args[0][1], native)
          self.assertEqual(run.call_args.kwargs, {"check": False})
          prompt.assert_not_called()
          pause.assert_not_called()

  def test_update_needs_no_percentage_and_keeps_layout(self):
    status, output, run, _, _ = self.run_cli(
      ["--execute", "--update"], iso_percent=None,
      metadata=disk_metadata(size=8 * 1024 * ventoy.GIB),
    )
    self.assertEqual(status, 0, output)
    self.assertEqual(
      run.call_args.args[0], ["/usr/bin/ventoy", "-u", "/dev/example"],
    )
    self.assertIn("preserves the existing partition layout", output)
    self.assertIn("ignored", output)
    self.assertNotIn("ERASE", output)
    self.assertNotIn("Reserved for later partitions:", output)

  def test_install_percentage_and_operations_are_validated(self):
    for flags in [[], ["--reinstall"]]:
      with self.subTest(flags=flags):
        status, output, run, _, _ = self.run_cli(flags, iso_percent=None)
        self.assertEqual(status, 2)
        self.assertIn("--iso-percent is required", output)
        run.assert_not_called()
    status, _, run, _, _ = self.run_cli(["--reinstall", "--update"])
    self.assertEqual(status, 2)
    run.assert_not_called()

  def test_apply_keeps_final_confirmation(self):
    status, _, run, prompt, pause = self.run_cli(
      ["--execute", "--apply"], answers=["y"], install_status=7,
    )
    self.assertEqual(status, 7)
    self.assertEqual(prompt.call_count, 1)
    self.assertIn("Final confirmation", prompt.call_args.args[0])
    self.assertEqual(
      run.call_args.kwargs, {"input": "y\ny\n", "text": True, "check": False},
    )
    pause.assert_not_called()

  def test_yes_aliases_keep_first_confirmation_and_pause_before_launch(self):
    for alias in ["--yes", "--noconfirm", "--noconfir"]:
      with self.subTest(alias=alias):
        status, output, run, prompt, pause = self.run_cli(
          ["--execute", alias], answers=["Y"],
        )
        self.assertEqual(status, 0, output)
        self.assertEqual(prompt.call_count, 1)
        self.assertIn("Apply install", prompt.call_args.args[0])
        self.assertIn("All remaining confirmation protections", output)
        self.assertIn("only an error may halt it", output)
        self.assertEqual(run.call_args.kwargs["input"], "y\ny\n")
        pause.assert_called_once_with(3)

  def test_fully_automatic_modes_never_prompt(self):
    for flags in [
      ["--apply", "--yes"], ["--apply", "--noconfirm"],
      ["--apply", "--noconfir"], ["--force"],
    ]:
      with self.subTest(flags=flags):
        status, output, run, prompt, pause = self.run_cli(["--execute", *flags])
        self.assertEqual(status, 0, output)
        self.assertIn("confirmation protections have been disabled", output)
        self.assertIn("Device and sizing validation remain enforced", output)
        prompt.assert_not_called()
        pause.assert_called_once_with(3)
        # --force is confirmation control, not an implicit destructive reinstall.
        self.assertEqual(run.call_args.args[0][1], "-i")
        self.assertEqual(run.call_args.kwargs["input"], "y\ny\n")

  def test_automatic_update_uses_one_native_answer(self):
    status, output, run, prompt, pause = self.run_cli(
      ["--execute", "-U", "--force"], iso_percent=None,
    )
    self.assertEqual(status, 0, output)
    self.assertEqual(run.call_args.args[0], ["/usr/bin/ventoy", "-u", "/dev/example"])
    self.assertEqual(run.call_args.kwargs["input"], "y\n")
    prompt.assert_not_called()
    pause.assert_called_once_with(3)

  def test_verify_requires_exact_yes_and_implies_verbose_and_apply(self):
    for extra in [[], ["--yes"], ["--noconfirm"], ["--force"]]:
      with self.subTest(extra=extra):
        status, output, run, prompt, pause = self.run_cli(
          ["--execute", "--verify", *extra], answers=["YES"],
        )
        self.assertEqual(status, 0, output)
        self.assertIn("Disk metadata (lsblk):", output)
        self.assertIn('"path": "/dev/example"', output)
        self.assertIn("Requested ISO percentage: 15%", output)
        self.assertIn("final=exact YES", output)
        self.assertEqual(prompt.call_count, 1)
        self.assertIn("type YES", prompt.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["input"], "y\ny\n")
        self.assertNotIn("All remaining confirmation protections", output)
        if extra:
          pause.assert_called_once_with(3)
        else:
          pause.assert_not_called()

  def test_requested_reinstall_command(self):
    status, output, run, prompt, _ = self.run_cli(
      ["--execute", "--reinstall", "--apply", "--verify", "--verbose"],
      answers=["YES"],
    )
    self.assertEqual(status, 0, output)
    self.assertEqual(run.call_args.args[0][1], "-I")
    self.assertIn("Installation will ERASE", output)
    self.assertIn("Partition table: GPT; label: iso_disk", output)
    self.assertEqual(prompt.call_count, 1)

  def test_declining_any_wrapper_prompt_never_launches(self):
    for flags, answers in [
      (["--apply"], ["n"]), (["--yes"], [""]),
      (["--verify"], ["yes"]), (["--verify"], ["y"]),
      (["--verify"], ["Yes"]), (["--verify"], [""]),
      (["--verify"], [" YES "]), (["--verify", "--force"], ["no"]),
    ]:
      with self.subTest(flags=flags, answers=answers):
        status, output, run, _, pause = self.run_cli(
          ["--execute", *flags], answers=answers,
        )
        self.assertEqual(status, 1)
        self.assertIn("Cancelled", output)
        self.assertEqual(run.call_count, 1)
        pause.assert_not_called()

  def test_closed_input_and_interrupted_delay_never_launch(self):
    status, output, run, _, _ = self.run_cli(
      ["--execute", "--verify", "--force"], answers=EOFError,
    )
    self.assertEqual(status, 1)
    self.assertIn("confirmation input closed", output)
    self.assertEqual(run.call_count, 1)
    status, output, run, _, _ = self.run_cli(
      ["--execute", "--apply"], answers=KeyboardInterrupt,
    )
    self.assertEqual(status, 130)
    self.assertIn("interrupted", output)
    self.assertEqual(run.call_count, 1)
    status, output, run, prompt, pause = self.run_cli(
      ["--execute", "--force"], sleep_error=KeyboardInterrupt,
    )
    self.assertEqual(status, 130)
    self.assertIn("interrupted", output)
    self.assertEqual(run.call_count, 1)
    prompt.assert_not_called()
    pause.assert_called_once_with(3)

  def test_approval_flags_and_verbose_always_preview_without_execute(self):
    for flags in [
      ["--apply"], ["--verify"], ["--yes"], ["--noconfirm"],
      ["--force"], ["-v"], ["--verbose"], ["--apply", "--yes"],
    ]:
      with self.subTest(flags=flags):
        status, output, run, prompt, pause = self.run_cli(flags)
        self.assertEqual(status, 0, output)
        self.assertIn("Preview only", output)
        self.assertEqual(run.call_count, 1)
        prompt.assert_not_called()
        pause.assert_not_called()

  def test_verbose_alone_preserves_native_confirmations(self):
    for flag in ["-v", "--verbose"]:
      with self.subTest(flag=flag):
        status, output, run, prompt, pause = self.run_cli(["--execute", flag])
        self.assertEqual(status, 0, output)
        self.assertIn("Disk metadata (lsblk):", output)
        self.assertEqual(run.call_args.kwargs, {"check": False})
        prompt.assert_not_called()
        pause.assert_not_called()

  def test_warning_and_delay_precede_automatic_execution(self):
    events = []
    output = io.StringIO()
    disk = disk_metadata()
    with (
      mock.patch.object(ventoy, "read_disk", return_value=disk),
      mock.patch.object(ventoy, "check_execution"),
      mock.patch.object(ventoy.shutil, "which", return_value="/usr/bin/ventoy"),
      contextlib.redirect_stdout(output),
      mock.patch.object(
        ventoy.time, "sleep", side_effect=lambda seconds: events.append(
          ("pause", seconds, output.getvalue()),
        ),
      ),
      mock.patch.object(
        ventoy.subprocess, "run", side_effect=lambda *args, **kwargs: (
          events.append(("execute",)), subprocess.CompletedProcess([], 0),
        )[1],
      ),
    ):
      self.assertEqual(ventoy.main([
        "--execute", "--iso-percent", "15", "/dev/example", "--force",
      ]), 0)
    self.assertEqual([event[0] for event in events], ["pause", "execute"])
    self.assertEqual(events[0][1], 3)
    self.assertIn("protections have been disabled", events[0][2])

  def test_native_answer_stream_is_finite_with_real_child(self):
    # This child only reads stdin. It never invokes Ventoy or opens a disk.
    real_run = subprocess.run
    with tempfile.TemporaryDirectory() as directory:
      fake = Path(directory) / "fake_ventoy.py"
      fake.write_text(
        "import sys\n"
        "lines = list(sys.stdin)\n"
        "expected = ['y\\n'] * (1 if sys.argv[1] == '-u' else 2)\n"
        "sys.exit(0 if lines == expected else 9)\n"
      )
      for operation in ["--install", "--reinstall", "--update"]:
        with self.subTest(operation=operation):
          disk = disk_metadata()
          with (
            mock.patch.object(ventoy, "read_disk", return_value=disk),
            mock.patch.object(ventoy, "check_execution"),
            mock.patch.object(ventoy.shutil, "which", return_value=sys.executable),
            mock.patch.object(ventoy.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(
              ventoy.subprocess, "run", side_effect=lambda command, **kwargs: real_run(
                [sys.executable, str(fake), *command[1:]], timeout=5, **kwargs,
              ),
            ),
          ):
            self.assertEqual(ventoy.main([
              "--execute", operation, "--iso-percent", "15", "/dev/example", "--force",
            ]), 0)

  def test_rejects_partition_read_only_and_native_4k(self):
    for changes in [{"type": "part"}, {"ro": True}, {"log-sec": 4096}]:
      with self.subTest(changes=changes):
        status, _, run, _, _ = self.run_cli(metadata=disk_metadata(**changes))
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
        for flags in [[], ["--force"], ["--verify"], ["--apply", "--yes"]]:
          with self.subTest(flags=flags):
            status, _, run, prompt, pause = self.run_cli(
              ["--execute", *flags], disk_metadata(children=[child]),
            )
            self.assertEqual(status, 1)
            self.assertEqual(run.call_count, 1)
            prompt.assert_not_called()
            pause.assert_not_called()

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
