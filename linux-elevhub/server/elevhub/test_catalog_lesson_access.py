"""Installed software and lesson access stay independent for a new app ID."""
import json
from pathlib import Path
import tempfile
import unittest
from tilstand import Tilstand


class CatalogAccessTests(unittest.TestCase):
    def test_new_app_gets_lesson_control_and_saved_closure_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);clock=lambda:1000
            app={'id':'noter','navn':'Noter','description':'Skriv noter','category':'Skriv',
                 'start':'http://127.0.0.1:8801/'}
            state=Tilstand(root,[],ur=clock)
            self.assertTrue(callable(getattr(state,'include_app_descriptors',None)))
            state.include_app_descriptors([app])
            self.assertTrue(state.apps_til_elever()['apps'][0]['aaben'])
            state.saet({'noter':False})
            reopened=Tilstand(root,[],ur=clock)
            reopened.include_app_descriptors([app])
            policy=reopened.apps_til_elever()['apps'][0]
            self.assertFalse(policy['aaben']);self.assertEqual(policy['start_url'],app['start'])
            before=reopened.version;reopened.include_app_descriptors([app])
            self.assertEqual(reopened.version,before)
            with self.assertRaises(KeyError):reopened.saet({'ukendt':True})


if __name__=='__main__':unittest.main()
