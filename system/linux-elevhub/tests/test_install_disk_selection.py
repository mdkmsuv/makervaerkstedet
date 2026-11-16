"""Fixture tests for the fail-closed physical installer disk selector."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "installation" / "select-install-disk.sh"


def shells():
    """The installer runs busybox ash; also cover the host's /bin/sh."""
    found = [[shutil.which("sh")]] if shutil.which("sh") else []
    if shutil.which("busybox"):
        found.append([shutil.which("busybox"), "sh"])
    return found


class DiskSelectionTests(unittest.TestCase):
    def run_guard(self, *, disks=("sda",), selection="cancel", confirmation="false",
                  input_error="", go_error="", go_rc="1", stale_at="", stale_kind="ro", generic=True):
        """Run the REAL guard/selector, replacing only absolute fixture paths and Debconf.

        debconf-set is a recording stub. No block devices or partitioner are used.
        """
        if os.name != "posix" or not shells():
            self.skipTest("Run the sysfs/shell fixtures in Linux/WSL")
        guard_source = (ROOT / "installation/partman-disk-guard.sh").read_text()
        selector_source = SCRIPT.read_text()
        for shell in shells():
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                cdrom = root / "cdrom/elevhub"
                cdrom.mkdir(parents=True)
                if generic:
                    (cdrom / "onboarding.py").write_text("# generic marker")
                sysroot = root / "sys"
                block = sysroot / "block"
                block.mkdir(parents=True)
                for name in disks:
                    target = root / "devices/pci0000:00/internal/block" / name
                    target.mkdir(parents=True)
                    (target / "removable").write_text("0\n")
                    (target / "ro").write_text("0\n")
                    (target / "size").write_text("125829120\n")
                    (target / "device").mkdir()
                    (target / "device/model").write_text("Fixture, Disk\nExtra Model\tText\n")
                    (block / name).symlink_to(target, target_is_directory=True)
                usb = root / "devices/pci0000:00/usb1/1-1/block/sdc"
                usb.mkdir(parents=True)
                (usb / "removable").write_text("0\n")
                (usb / "ro").write_text("0\n")
                (usb / "size").write_text("29360128\n")
                (block / "sdc").symlink_to(usb, target_is_directory=True)
                mounts = root / "mounts"
                mounts.write_text(f"/dev/sdc1 {root / 'cdrom'} iso9660 ro 0 0\n")
                fake_conf = root / "fake-confmodule"
                fake_conf.write_text(r"""db_get() {
    case "$1" in
        elevhub/install-disk) RET="$FAKE_SELECTION";;
        elevhub/disk-wipe-confirm) RET="$FAKE_CONFIRM";;
        *) RET=;;
    esac
    return 0
}
db_input() { question=$2; printf 'input|%s\n' "$question" >> "$TRACE"; [ "$question" != "$FAKE_INPUT_ERROR" ]; }
db_go() {
    printf 'go|%s\n' "$question" >> "$TRACE"
    if [ "$question" = "$FAKE_STALE_AT" ]; then
        case "$FAKE_STALE_KIND" in
            ro) echo 1 > "$ELEVHUB_TEST_SYS_ROOT/block/sda/ro";;
            removable) echo 1 > "$ELEVHUB_TEST_SYS_ROOT/block/sda/removable";;
            gone) rm "$ELEVHUB_TEST_SYS_ROOT/block/sda";;
            usb) rm "$ELEVHUB_TEST_SYS_ROOT/block/sda"; ln -s "$USB_FIXTURE" "$ELEVHUB_TEST_SYS_ROOT/block/sda";;
        esac
    fi
    if [ "$question" = "$FAKE_GO_ERROR" ]; then return "$FAKE_GO_RC"; fi
    return 0
}
db_progress() { return 0; }
db_x_loadtemplatefile() { return 0; }
db_subst() { printf 'subst|%s|%s|%s\n' "$1" "$2" "$3" >> "$TRACE"; }
db_fset() { return 0; }
db_set() { printf 'set|%s|%s\n' "$1" "$2" >> "$TRACE"; }
""")
                binary = root / "bin"
                binary.mkdir()
                setter = binary / "debconf-set"
                setter.write_text('#!/bin/sh\nprintf "target|%s|%s\\n" "$1" "$2" >> "$TRACE"\n')
                setter.chmod(0o755)
                guard = root / "guard.sh"
                guard.write_text(guard_source
                                 .replace(". /usr/share/debconf/confmodule", f". '{fake_conf}'")
                                 .replace("/cdrom", str(root / "cdrom"))
                                 .replace("/sys", str(sysroot))
                                 .replace("/var/log/elevhub-disk-selection.log", str(root / "selection.log")))
                (cdrom / "select-install-disk.sh").write_text(
                    selector_source.replace("/cdrom", str(root / "cdrom")))
                env = os.environ.copy()
                env.update(ELEVHUB_TEST_MODE="0", ELEVHUB_TEST_SYS_ROOT=str(sysroot),
                           ELEVHUB_TEST_MOUNTS=str(mounts), FAKE_SELECTION=selection,
                           FAKE_CONFIRM=confirmation, FAKE_INPUT_ERROR=input_error,
                           FAKE_GO_ERROR=go_error, FAKE_GO_RC=go_rc, FAKE_STALE_AT=stale_at,
                           FAKE_STALE_KIND=stale_kind, USB_FIXTURE=str(usb),
                           TRACE=str(root / "trace"), PATH=str(binary) + ":" + env["PATH"])
                result = subprocess.run(shell + [str(guard)], env=env, text=True,
                                        capture_output=True, timeout=10)
                trace = (root / "trace").read_text() if (root / "trace").exists() else ""
                log = (root / "selection.log").read_text()
                yield result, trace, log

    def test_generic_diskguard_requires_yes_and_rejects_no_cancel_or_go_error(self):
        for confirmation, input_error, go_error, expected in (
                ("true", "", "", 0), ("false", "", "", 1),
                ("true", "elevhub/disk-wipe-confirm", "", 1),
                ("true", "", "elevhub/disk-wipe-confirm", 1)):
            for result, trace, log in self.run_guard(confirmation=confirmation,
                    input_error=input_error, go_error=go_error):
                self.assertEqual(result.returncode, expected, log)
                self.assertIn("set|elevhub/disk-wipe-confirm|false", trace)
                if expected:
                    self.assertNotIn("target|", trace)
                else:
                    self.assertIn("target|partman-auto/disk|/dev/sda", trace)
                    self.assertIn("target|grub-installer/bootdev|/dev/sda", trace)
                    self.assertLess(trace.index("go|elevhub/disk-wipe-confirm"), trace.index("target|"))

    def test_generic_multiple_disks_explicit_pick_with_separate_wipe_confirmation(self):
        for selected in ("/dev/sda", "/dev/sdb"):
            for result, trace, log in self.run_guard(disks=("sda", "sdb"),
                                                    selection=selected, confirmation="true"):
                self.assertEqual(result.returncode, 0, log)
                self.assertIn("set|elevhub/install-disk|cancel", trace)
                self.assertIn("subst|elevhub/install-disk|CHOICES_C|cancel, /dev/sda, /dev/sdb", trace)
                labels = next(line for line in trace.splitlines()
                              if line.startswith("subst|elevhub/install-disk|CHOICES|"))
                self.assertEqual(labels.count(","), 2, labels)
                self.assertIn("/dev/sda — 60 GB — Fixture Disk Extra Model Text", labels)
                self.assertNotIn("/dev/sdc", labels)
                self.assertIn(f"subst|elevhub/disk-wipe-confirm|DISK|{selected} — 60 GB", trace)
                self.assertIn(f"target|partman-auto/disk|{selected}", trace)
                self.assertIn(f"target|grub-installer/bootdev|{selected}", trace)

    def test_generic_multiple_disks_cancel_back_invalid_and_no_never_set_targets(self):
        for options in (
                {}, {"selection": "/dev/sdc", "confirmation": "true"},
                {"selection": "/dev/no-such-disk", "confirmation": "true"},
                {"selection": "", "confirmation": "true"},
                {"selection": "/dev/sda", "input_error": "elevhub/install-disk"},
                {"selection": "/dev/sda", "go_error": "elevhub/install-disk"},
                {"selection": "/dev/sda", "confirmation": "false"}):
            for result, trace, log in self.run_guard(disks=("sda", "sdb"), **options):
                self.assertNotEqual(result.returncode, 0, log)
                self.assertNotIn("target|", trace)

    def test_generic_stale_selection_revalidated_before_and_after_confirmation(self):
        for when in ("elevhub/install-disk", "elevhub/disk-wipe-confirm"):
            for kind in ("ro", "removable", "gone", "usb"):
                for result, trace, log in self.run_guard(disks=("sda", "sdb"), selection="/dev/sda",
                        confirmation="true", stale_at=when, stale_kind=kind):
                    self.assertNotEqual(result.returncode, 0, log)
                    self.assertNotIn("target|", trace)
                    self.assertIn("not a current safe internal target", log)

    def test_back_returns_to_menu_without_claiming_a_disk_failure(self):
        for when in ("elevhub/install-disk", "elevhub/disk-wipe-confirm"):
            for result, trace, log in self.run_guard(disks=("sda", "sdb"), selection="/dev/sda",
                    confirmation="true", go_error=when, go_rc="30"):
                self.assertNotEqual(result.returncode, 0, log)
                self.assertNotIn("target|", trace)
                self.assertNotIn("input|elevhub/disk-selection-failed", trace)

    def test_legacy_media_still_refuse_multiple_disks_without_prompting(self):
        for result, trace, log in self.run_guard(disks=("sda", "sdb"), generic=False):
            self.assertNotEqual(result.returncode, 0, log)
            self.assertNotIn("target|", trace)
            self.assertNotIn("input|elevhub/install-disk", trace)
            self.assertIn("found 2 safe internal disk candidates", log)

    def test_generic_no_safe_disk_aborts_before_any_wipe_prompt_or_target(self):
        for result, trace, log in self.run_guard(disks=(), confirmation="true"):
            self.assertNotEqual(result.returncode, 0, log)
            self.assertNotIn("target|", trace)
            self.assertNotIn("input|elevhub/disk-wipe-confirm", trace)
            self.assertIn("found 0 safe internal disk candidates", log)

    @unittest.skipUnless(os.name == 'posix', 'Run the sysfs/shell fixtures in Linux/WSL')
    def run_case(self, disks, mounts, expected=None, pci=(), args=()):
        """disks: (name, usb, removable, writable); pci: (address, vendor, device, class)."""
        results = []
        if not shells():
            self.skipTest("POSIX shell is required; run this test in WSL")
        for shell in shells():
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                sysroot = root / "sys"
                block = sysroot / "block"
                block.mkdir(parents=True)
                for name, usb, removable, writable in disks:
                    entry = block / name
                    # The selector only needs ancestry to identify USB fixtures.
                    target = root / ("devices/pci0000:00/usb1/1-1/" if usb else "devices/pci0000:00/0000:00:1f.2/") / "block" / name
                    target.mkdir(parents=True)
                    (target / "removable").write_text("1\n" if removable else "0\n")
                    (target / "ro").write_text("0\n" if writable else "1\n")
                    (target / "size").write_text("125829120\n")  # 60 GB in 512-byte sectors
                    entry.symlink_to(target, target_is_directory=True)
                for address, vendor, device, klass in pci:
                    node = sysroot / "bus/pci/devices" / address
                    node.mkdir(parents=True)
                    (node / "vendor").write_text(vendor + "\n")
                    (node / "device").write_text(device + "\n")
                    (node / "class").write_text(klass + "\n")
                mount_file = root / "mounts"
                mount_file.write_text(mounts)
                env = os.environ.copy()
                env.update(ELEVHUB_TEST_SYS_ROOT=str(sysroot),
                           ELEVHUB_TEST_MOUNTS=str(mount_file), ELEVHUB_TEST_MODE="1")
                result = subprocess.run(shell + [str(SCRIPT), *args], env=env, text=True,
                                        capture_output=True, timeout=10)
                label = " ".join(shell) + ": " + result.stdout + result.stderr
                if expected is None:
                    self.assertNotEqual(result.returncode, 0, label)
                else:
                    self.assertEqual(result.returncode, 0, label)
                    self.assertEqual(result.stdout.strip(), expected, label)
                    self.assertNotIn("RÅD", result.stderr, label)
                results.append(result)
        return results

    def test_nvme_and_usb_removable_zero(self):
        self.run_case([("nvme0n1", False, False, True), ("sda", True, False, True)],
                      "/dev/sda1 /cdrom iso9660 ro 0 0\n", "/dev/nvme0n1")

    def test_second_attached_usb_removable_zero_is_excluded(self):
        self.run_case([("nvme0n1", False, False, True), ("sda", True, False, True),
                       ("sdb", True, False, True)],
                      "/dev/sda1 /cdrom iso9660 ro 0 0\n", "/dev/nvme0n1")

    def test_sata_and_usb(self):
        self.run_case([("sda", False, False, True), ("sdb", True, True, True)],
                      "/dev/sdb1 /cdrom iso9660 ro 0 0\n", "/dev/sda")

    def test_list_mode_returns_only_safe_paths_without_target_writes(self):
        self.run_case([("sda", False, False, True), ("sdb", False, False, True),
                       ("sdc", True, False, True), ("sdd", True, False, True),
                       ("mmcblk0boot0", False, False, True), ("nvme0n1", False, False, False)],
                      "/dev/sdc1 /cdrom iso9660 ro 0 0\n", "/dev/sda\n/dev/sdb",
                      args=("--list-candidates",))

    def test_explicit_selection_requires_a_current_safe_whole_disk(self):
        disks = [("sda", False, False, True), ("sdb", False, False, True),
                 ("sdc", True, False, True), ("sdd", True, False, True),
                 ("mmcblk0boot0", False, False, True), ("nvme0n1", False, False, False)]
        for mode in ("--check", "--select"):
            for choice in ("/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd", "/dev/nvme0n1",
                           "/dev/mmcblk0boot0", "/dev/sda1", "/dev/no-such-disk", ""):
                self.run_case(disks, "/dev/sdc1 /cdrom iso9660 ro 0 0\n",
                              choice if choice in ("/dev/sda", "/dev/sdb") else None,
                              args=(mode, choice))

    def test_list_mode_fails_closed_with_no_safe_disks(self):
        self.run_case([("sda", True, False, True)],
                      "/dev/sda1 /cdrom iso9660 ro 0 0\n", args=("--list-candidates",))

    def test_two_internal_disks_abort(self):
        self.run_case([("sda", False, False, True), ("nvme0n1", False, False, True),
                       ("sdb", True, True, True)],
                      "/dev/sdb1 /cdrom iso9660 ro 0 0\n")

    def test_usb_only_abort(self):
        self.run_case([("sda", True, True, True)],
                      "/dev/sda1 /cdrom iso9660 ro 0 0\n")

    def test_usb_data_disk_removable_zero_abort(self):
        self.run_case([("sda", True, False, True), ("sdb", True, False, True)],
                      "/dev/sda1 /cdrom iso9660 ro 0 0\n")

    def test_optical_boot_source_is_valid(self):
        self.run_case([("nvme0n1", False, False, True)],
                      "/dev/sr0 /cdrom iso9660 ro 0 0\n", "/dev/nvme0n1")

    def test_boot_device_internal_is_excluded(self):
        self.run_case([("sda", False, False, True), ("nvme0n1", False, False, True)],
                      "/dev/nvme0n1p1 /cdrom iso9660 ro 0 0\n", "/dev/sda")

    def test_read_only_internal_aborts(self):
        self.run_case([("nvme0n1", False, False, False), ("sda", True, True, True)],
                      "/dev/sda1 /cdrom iso9660 ro 0 0\n")

    def test_missing_boot_device_fails_closed(self):
        self.run_case([("nvme0n1", False, False, True)], "")

    def test_unresolvable_boot_device_fails_closed(self):
        self.run_case([("nvme0n1", False, False, True)],
                      "/dev/loop0 /cdrom squashfs ro 0 0\n")

    def test_nvme_partition_10_parent_is_correct(self):
        self.run_case([("nvme0n1", False, False, True), ("sda", False, False, True)],
                      "/dev/nvme0n1p10 /cdrom iso9660 ro 0 0\n", "/dev/sda")

    def test_mmc_boot_partition_is_not_a_candidate(self):
        self.run_case([("mmcblk0", False, False, True), ("mmcblk0boot0", False, False, True),
                       ("sda", False, False, True)],
                      "/dev/sda1 /cdrom iso9660 ro 0 0\n", "/dev/mmcblk0")

    def test_mmc_rpmb_and_boot_areas_are_never_candidates(self):
        self.run_case([("mmcblk0", False, False, True), ("mmcblk0rpmb", False, False, True),
                       ("mmcblk0boot0", False, False, True), ("mmcblk0boot1", False, False, True),
                       ("sda", True, False, True)],
                      "/dev/sda1 /cdrom iso9660 ro 0 0\n", "/dev/mmcblk0")

    def test_rpmb_alone_cannot_be_selected(self):
        self.run_case([("mmcblk0rpmb", False, False, True), ("sda", True, False, True)],
                      "/dev/sda1 /cdrom iso9660 ro 0 0\n")

    # The failure screen must explain an unknown PC from one photo.

    def test_intel_raid_mode_without_disks_advises_ahci(self):
        for result in self.run_case(
                [("sda", True, True, True)], "/dev/sda1 /cdrom iso9660 ro 0 0\n",
                pci=[("0000:00:17.0", "0x8086", "0x282a", "0x010400")]):
            self.assertIn("RAID/RST", result.stderr)
            self.assertIn("AHCI", result.stderr)
            self.assertIn("8086:282a/010400", result.stderr)
            self.assertIn("found 0 safe internal disk candidates", result.stderr)

    def test_no_disk_without_raid_gives_generic_bios_advice(self):
        for result in self.run_case(
                [("sda", True, True, True)], "/dev/sda1 /cdrom iso9660 ro 0 0\n",
                pci=[("0000:00:17.0", "0x8086", "0x9d03", "0x010601")]):
            self.assertIn("Ingen intern disk", result.stderr)
            self.assertNotIn("RAID/RST", result.stderr)

    def test_vmd_without_disks_advises_disabling_vmd(self):
        for result in self.run_case(
                [("sda", True, True, True)], "/dev/sda1 /cdrom iso9660 ro 0 0\n",
                pci=[("0000:00:0e.0", "0x8086", "0x9a0b", "0x010400")]):
            # A VMD bridge also reports the RAID class; either advice leads to BIOS.
            self.assertTrue("VMD" in result.stderr or "RAID/RST" in result.stderr, result.stderr)

    def test_two_disks_lists_both_and_explains(self):
        for result in self.run_case(
                [("sda", False, False, True), ("nvme0n1", False, False, True), ("sdb", True, True, True)],
                "/dev/sdb1 /cdrom iso9660 ro 0 0\n"):
            self.assertIn("flere mulige interne diske", result.stderr)
            self.assertIn("sda=60GB,intern,rm0,ro0", result.stderr)
            self.assertIn("nvme0n1=60GB,intern", result.stderr)
            self.assertIn("sdb=60GB,usb,rm1", result.stderr)
            self.assertIn("Installationsmedie: /dev/sdb1", result.stderr)

    def test_inventory_survives_incomplete_pci_entries(self):
        for result in self.run_case(
                [("sda", True, True, True)], "/dev/sda1 /cdrom iso9660 ro 0 0\n",
                pci=[("0000:00:17.0", "", "", "0x010601")]):
            self.assertIn("Controllere:", result.stderr)
            self.assertIn("Ingen intern disk", result.stderr)


if __name__ == "__main__":
    unittest.main()
