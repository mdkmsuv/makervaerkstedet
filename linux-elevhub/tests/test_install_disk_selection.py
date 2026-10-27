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
    def test_generic_diskguard_requires_yes_and_rejects_no_cancel_or_go_error(self):
        """Exercise the generic Debconf gate with a fake disk, never /sys or /dev."""
        if not shells():
            self.skipTest("POSIX shell is required")
        guard_source = (ROOT / "installation/partman-disk-guard.sh").read_text()
        selector_source = (ROOT / "installation/select-install-disk.sh").read_text()
        templates = (ROOT / "installation/disk-selection.templates").read_text()
        for confirmation, input_rc, go_rc, expected in (
                ("true", "0", "0", 0), ("false", "0", "0", 1),
                ("true", "1", "0", 1), ("true", "0", "1", 1)):
            with self.subTest(confirmation=confirmation, input_rc=input_rc, go_rc=go_rc):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    cdrom = root / "cdrom/elevhub"
                    cdrom.mkdir(parents=True)
                    (cdrom / "onboarding.py").write_text("# generic marker")
                    (cdrom / "select-install-disk.sh").write_text(selector_source)
                    (cdrom / "partman-disk-guard.sh").write_text(guard_source)
                    sysroot = root / "sys"
                    target = root / "devices/disk/block/sda"
                    target.mkdir(parents=True)
                    (target / "removable").write_text("0\n")
                    (target / "ro").write_text("0\n")
                    (target / "size").write_text("125829120\n")
                    (target / "device").mkdir()
                    (target / "device/model").write_text("Fixture Disk\n")
                    (sysroot / "block").mkdir(parents=True)
                    (sysroot / "block/sda").symlink_to(target, target_is_directory=True)
                    mounts = root / "mounts"
                    mounts.write_text(f"/dev/sr0 {root / 'cdrom'} iso9660 ro 0 0\n")
                    fake_conf = root / "fake-confmodule"
                    fake_conf.write_text("""\
db_get() { case \"$1\" in partman-auto/disk) RET=/dev/sda;; elevhub/disk-wipe-confirm) RET=\"${FAKE_CONFIRM:-false}\";; *) RET=;; esac; return 0; }
db_input() { return \"${FAKE_INPUT_RC:-0}\"; }
db_go() { return \"${FAKE_GO_RC:-0}\"; }
db_progress() { return 0; }
db_x_loadtemplatefile() { return 0; }
db_subst() { return 0; }
db_fset() { return 0; }
db_set() { return 0; }
""")
                    (root / "usr/share/elevhub").mkdir(parents=True)
                    (root / "usr/share/elevhub/disk-selection.templates").write_text(templates)
                    # Use the fixture paths while preserving the real script logic.
                    guard = root / "guard.sh"
                    guard.write_text(guard_source
                                     .replace(". /usr/share/debconf/confmodule", f". '{fake_conf}'")
                                     .replace("/cdrom", str(root / "cdrom"))
                                     .replace("/sys", str(sysroot))
                                     .replace("/usr/share/elevhub", str(root / "usr/share/elevhub"))
                                     .replace("/var/log/elevhub-disk-selection.log", str(root / "selection.log")))
                    selector = root / "cdrom/elevhub/select-install-disk.sh"
                    selector.write_text(selector_source.replace("/cdrom", str(root / "cdrom"))
                                        .replace("/sys", str(sysroot)))
                    env = os.environ.copy()
                    env.update(ELEVHUB_TEST_MODE="1", ELEVHUB_TEST_SYS_ROOT=str(sysroot),
                               ELEVHUB_TEST_MOUNTS=str(mounts), FAKE_CONFIRM=confirmation,
                               FAKE_INPUT_RC=input_rc, FAKE_GO_RC=go_rc)
                    result = subprocess.run(["sh", str(guard)], env=env, text=True,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    @unittest.skipUnless(os.name == 'posix', 'Run the sysfs/shell fixtures in Linux/WSL')
    def run_case(self, disks, mounts, expected=None, pci=()):
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
                result = subprocess.run(shell + [str(SCRIPT)], env=env, text=True,
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
