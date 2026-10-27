from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app import background
from app import laser_server
from app import generation_runtime


class GenerationChildTests(unittest.TestCase):
    def test_background_uses_generation_command_and_cwd(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            process = mock.Mock(pid=17)
            process.wait.return_value = 0
            process.poll.return_value = 0
            with mock.patch.object(background, 'child_command', return_value=['stable-child']), \
                 mock.patch.object(background, 'child_cwd', return_value='stable-cwd'), \
                 mock.patch.object(background.subprocess, 'Popen', return_value=process) as popen, \
                 mock.patch.object(background, 'ProcessTree'):
                self.assertEqual(background.supervise(root, 8765, '127.0.0.1'), 0)
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], ['stable-child'])
            self.assertEqual(popen.call_args.kwargs['cwd'], 'stable-cwd')

    def test_boxes_worker_uses_generation_command_and_cwd(self):
        process = mock.Mock(returncode=0)
        process.communicate.return_value = (b'{"svg":"<svg/>","details":{}}', b'')
        with mock.patch.object(laser_server, 'child_command', return_value=['stable-box']), \
             mock.patch.object(laser_server, 'child_cwd', return_value='stable-cwd'), \
             mock.patch.object(laser_server.subprocess, 'Popen', return_value=process) as popen:
            laser_server.generate_box_svg_timed({'width': 10})
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ['stable-box'])
        self.assertEqual(popen.call_args.kwargs['cwd'], 'stable-cwd')

    def test_real_boxes_worker_returns_svg_through_default_child_command(self):
        with mock.patch.object(generation_runtime, '_selection', None):
            svg, details = laser_server.generate_box_svg_timed(
                {'kind': 'abox', 'x': 80, 'y': 60, 'h': 40, 'thickness': 3, 'burn': 0.1})
        self.assertIn('<svg', svg)
        self.assertEqual(details['generator'], 'Boxes.py ABox')

    def test_main_does_not_create_logs_under_restored_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.restore-hold.json').write_text('{}', encoding='utf-8')
            target = root / 'school'
            with mock.patch.object(background.sys, 'argv', ['background', '--data', str(target)]), \
                 mock.patch.object(background, 'RotatingFileHandler') as handler:
                with self.assertRaises(RuntimeError):
                    background.main()
                handler.assert_not_called()
            self.assertFalse(target.exists())

    def test_restore_hold_blocks_before_supervisor_mkdir(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.restore-hold.json').write_text('{}', encoding='utf-8')
            target = root / 'new-school'
            with self.assertRaises(RuntimeError):
                background.supervise(target, 8765, '127.0.0.1')
            self.assertFalse(target.exists())

    def test_restore_hold_is_checked_again_after_supervisor_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'school'
            calls = mock.Mock(side_effect=[None, RuntimeError('hold')])
            with mock.patch.object(background, 'assert_not_restored', calls):
                with self.assertRaisesRegex(RuntimeError, 'hold'):
                    background.supervise(target, 8765, '127.0.0.1')
            self.assertFalse((target / 'stopped-by-teacher').exists())
            self.assertEqual(calls.call_count, 2)


if __name__ == '__main__':
    unittest.main()
