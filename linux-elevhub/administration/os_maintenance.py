#!/usr/bin/env python3
"""Read-only Debian maintenance/reboot status. Never runs apt or reboots."""
import json
import os
from pathlib import Path
import platform

def status(root=Path("/"),running_kernel=None):
    running=running_kernel or platform.release()
    link=root/"vmlinuz"
    target=os.readlink(link) if link.is_symlink() else None
    selected=Path(target).name.removeprefix("vmlinuz-") if target and Path(target).name.startswith("vmlinuz-") else None
    distro_flag=(root/"run/reboot-required").exists()
    mismatch=selected is not None and selected!=running
    return {"running_kernel":running,"selected_kernel":selected,"debian_reboot_flag":distro_flag,
            "kernel_reboot_required":mismatch,"reboot_required":True if distro_flag or mismatch else False if selected is not None else None,
            "kernel_selection_known":selected is not None,"automatic_reboot":False}

if __name__=="__main__":print(json.dumps(status()))
