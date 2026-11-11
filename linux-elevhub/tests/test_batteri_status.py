"""Pure sysfs tests for the pupil-bar battery indicator."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
import os
import shutil
import sys
from pathlib import Path


_ROOT = Path(__file__).parents[1]
_FILE = _ROOT / 'ansible' / 'roles' / 'kiosk' / 'files' / 'elevbjaelke.py'
_SPEC = importlib.util.spec_from_file_location('elevbjaelke_batteri_test', _FILE)
ELEVBAELKE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(ELEVBAELKE)

try:
    sys.path.insert(0, str(_ROOT / 'tests'))
    from test_pause_overlay_x11 import _Xvfb
except (ImportError, OSError):  # pragma: no cover - Linux/WSL only
    _Xvfb = None


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding='ascii')


class BatteriStatusTest(unittest.TestCase):
    def test_missing_battery_is_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            status = ELEVBAELKE.batteri_status(directory)
        self.assertFalse(status.batteri_fundet)
        self.assertEqual(ELEVBAELKE.batteri_tekst(status), '')
        self.assertIsNone(ELEVBAELKE.batteri_advarsel(status))

    def test_discharge_percentage_and_low_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / 'BAT0/type', 'Battery\n')
            _write(root / 'BAT0/capacity', '18\n')
            _write(root / 'BAT0/status', 'Discharging\n')
            _write(root / 'AC/online', '0\n')
            status = ELEVBAELKE.batteri_status(root)
        self.assertEqual(status.percent, 18)
        self.assertFalse(status.oplader)
        self.assertFalse(status.stroemforsyning)
        self.assertEqual(ELEVBAELKE.batteri_tekst(status), 'Batteri 18 % · På batteri')
        self.assertEqual(ELEVBAELKE.batteri_advarsel(status), 'Lavt batteri · Tilslut strøm')

    def test_critical_warning_is_suppressed_when_charging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / 'BAT0/type', 'Battery\n')
            _write(root / 'BAT0/capacity', '4\n')
            _write(root / 'BAT0/status', 'Charging\n')
            _write(root / 'AC/online', '1\n')
            status = ELEVBAELKE.batteri_status(root)
        self.assertTrue(status.oplader)
        self.assertTrue(status.stroemforsyning)
        self.assertEqual(ELEVBAELKE.batteri_tekst(status), 'Batteri 4 % · Oplader')
        self.assertIsNone(ELEVBAELKE.batteri_advarsel(status))

    def test_unknown_capacity_does_not_invent_a_percentage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / 'BAT0/type', 'Battery\n')
            _write(root / 'BAT0/capacity', 'unknown\n')
            _write(root / 'BAT0/status', 'Unknown\n')
            status = ELEVBAELKE.batteri_status(root)
        self.assertIsNone(status.percent)
        self.assertEqual(ELEVBAELKE.batteri_tekst(status), 'Batteri · Strømstatus ukendt')
        self.assertIsNone(ELEVBAELKE.batteri_advarsel(status))

    def test_missing_status_is_unknown_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / 'BAT0/type', 'Battery\n')
            _write(root / 'BAT0/capacity', '64\n')
            status = ELEVBAELKE.batteri_status(root)
        self.assertEqual(status.percent, 64)
        self.assertIsNone(status.oplader)
        self.assertIsNone(status.stroemforsyning)
        self.assertEqual(ELEVBAELKE.batteri_tekst(status), 'Batteri 64 % · Strømstatus ukendt')

    def test_negative_energy_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / 'BAT0/type', 'Battery\n')
            _write(root / 'BAT0/energy_now', '-1\n')
            _write(root / 'BAT0/energy_full', '500\n')
            _write(root / 'BAT0/status', 'Discharging\n')
            status = ELEVBAELKE.batteri_status(root)
        self.assertIsNone(status.percent)

    def test_multiple_batteries_use_total_energy_or_conservative_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / 'BAT0/type', 'Battery\n')
            _write(root / 'BAT0/energy_now', '900\n')
            _write(root / 'BAT0/energy_full', '1000\n')
            _write(root / 'BAT0/status', 'Discharging\n')
            _write(root / 'BAT1/type', 'Battery\n')
            _write(root / 'BAT1/energy_now', '100\n')
            _write(root / 'BAT1/energy_full', '500\n')
            _write(root / 'BAT1/status', 'Discharging\n')
            weighted = ELEVBAELKE.batteri_status(root)
            _write(root / 'BAT1/energy_now', '-1\n')
            _write(root / 'BAT1/energy_full', '500\n')
            conservative = ELEVBAELKE.batteri_status(root)
        self.assertEqual(weighted.percent, 67)
        self.assertEqual(conservative.percent, 90)

    def test_energy_values_are_a_safe_fallback_for_missing_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root / 'BAT0/type', 'Battery\n')
            _write(root / 'BAT0/energy_now', '250\n')
            _write(root / 'BAT0/energy_full', '500\n')
            _write(root / 'BAT0/status', 'Discharging\n')
            status = ELEVBAELKE.batteri_status(root)
        self.assertEqual(status.percent, 50)


@unittest.skipUnless(_Xvfb is not None and shutil.which('Xvfb') and sys.platform.startswith('linux'),
                     'private Xvfb is required')
class BatteriLayoutX11Test(unittest.TestCase):
    def test_status_warning_usb_and_admin_stay_inside_1366px_bar(self):
        import tkinter as tk

        with _Xvfb():
            root = tk.Tk(className='elevbjaelke-layout-test')
            root.geometry('1366x60+0+0')
            root.configure(bg='white')
            frame = tk.Frame(root, bg='white')
            frame.pack(fill='both', expand=True)
            tk.Button(frame, text='⌂  Til start', width=12).pack(side='left', padx=(10, 16))
            tk.Label(frame, text='Værksted 01', font=('Metrophobic', 14)).pack(side='left')
            battery = tk.Frame(frame, bg='white', width=190, height=42)
            battery.pack_propagate(False)
            tk.Label(battery, text='Batteri 18 % · På batteri', font=('Metrophobic', 13),
                     anchor='e', justify='right', wraplength=190).pack(fill='x')
            tk.Label(battery, text='Lavt · tilslut strøm', font=('Metrophobic', 11),
                     fg='#8a5700', anchor='e', justify='right', wraplength=190).pack(fill='x')
            battery.pack(side='right', padx=(8, 10))
            tk.Button(frame, text='Admin', width=9).pack(side='right', padx=(8, 10))
            usb_text = tk.Label(frame, text='Dine filer gemmes på USB-nøglen «ElevUSB»', font=('Metrophobic', 14))
            usb_text.pack(side='right')
            tk.Button(frame, text='Tag USB-nøglen ud', width=18).pack(side='right', padx=(8, 12))
            root.update_idletasks()
            try:
                for widget in (battery, *frame.winfo_children()):
                    left = widget.winfo_rootx()
                    right = left + widget.winfo_width()
                    self.assertGreaterEqual(left, 0, widget)
                    self.assertLessEqual(right, 1366, widget)
            finally:
                root.destroy()

if __name__ == '__main__':
    unittest.main()
