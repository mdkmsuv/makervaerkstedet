# Windows ISO-writer

`Write-ElevhubIso.ps1` is a deliberately narrow raw writer for the approved
USB target. It is not a general disk utility and does not format, partition,
clear, or select disks automatically.

The write path requires an already elevated PowerShell session. It re-reads
the disk identity immediately before dismounting and writing and requires all
of these to match the supplied arguments: disk number, USB bus, exact
`UniqueId`, exact byte size, expected drive letter, and expected filesystem
label. It rejects system/boot disks, a missing or ambiguous target partition,
an ISO stored on the target volume, and a target smaller than the ISO.
For a previously flashed installer, inspect the label again; its EFI volume
can have an empty label. Pass `-ExpectedLabel ''` explicitly in that case.
The equality check still applies, along with the full disk identity checks.

Before touching the target it opens the ISO with a read lock, hashes the whole
file, and compares it with `-ExpectedSha256`. It then locks and dismounts the
existing target volume using `FSCTL_LOCK_VOLUME` and `FSCTL_DISMOUNT_VOLUME`,
writes the ISO to `\\.\PhysicalDriveN` in 4 MiB chunks, flushes the device, and
hashes the complete written byte range back. The report is atomically replaced
after each stage and contains no credentials.

`-CheckOnly` performs only source hashing and target identity checks. It does
not require elevation and does not open, lock, dismount, or write the target.

Example for the approved <TEST-USB> target (verify the ISO checksum and path
before invoking the write):

```powershell
.\Write-ElevhubIso.ps1 `
  -IsoPath 'C:\path\complete-install.iso' `
  -ExpectedSha256 '<64 lowercase hexadecimal characters>' `
  -DiskNumber 1 `
  -ExpectedUniqueId 'INDSAET_DET_VALGTE_USB_DREVS_UNIQUEID' `
  -ExpectedSize 62914560000 `
  -ExpectedDriveLetter E `
  -ExpectedLabel 'TEGNEHAV' `
  -ReportPath 'C:\path\elevhub-usb-write-report.json'
```

Run the same command with `-CheckOnly` first. The final write must be started
from an administrator PowerShell session after independently checking that
the ISO is the complete intended image. Do not use this script against any
other disk identity.

The safe stream core can be tested without elevation or a disk with:

```powershell
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
  -File .\Test-UsbCopy.ps1
```

That test extracts the core and C# declaration from the writer, uses temporary
regular files only, and covers a non-aligned multi-chunk roundtrip, wrong hash
rejection before copy, and truncated source rejection.
