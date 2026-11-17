"""Teacher-only native server update routes; host operations never execute."""
import http.client
import json
import threading
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import elevhub
from tilstand import Laererkode, Tilstand


class ServerUpdateHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.server=elevhub.Server(('127.0.0.1',0),elevhub.Laerer)
        cls.server.kode=Laererkode(Path(cls.temp.name));cls.server.kode.vaelg('synthetic-teacher-code')
        cls.server.tilstand=Tilstand(cls.temp.name,[]);cls.server.apps=[];cls.server.navn='server-update-test'
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.temp.cleanup()

    def call(self,method,path='',body=None,login=True,origin=True):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Content-Type':'application/json'}
        if login:headers['Cookie']='elevhub_laerer='+self.server.kode.log_ind('synthetic-teacher-code','127.0.0.1')
        if origin:headers['Origin']=f'http://127.0.0.1:{self.server.server_port}' if origin is True else origin
        try:
            connection.request(method,'/api/admin/server-update'+path,json.dumps(body) if body is not None else None,headers)
            response=connection.getresponse();return response.status,json.loads(response.read())
        finally:connection.close()

    def test_requires_teacher_session(self):
        self.assertEqual(self.call('GET',login=False)[0],401)
        self.assertEqual(self.call('POST','/check',{'command_id':str(uuid.uuid4())},login=False)[0],401)

    def test_rejects_foreign_origin_and_unbounded_install_fields(self):
        body={'command_id':str(uuid.uuid4()),'manifest_sha256':'a'*64,'confirmation':'OPDATER SERVER'}
        self.assertEqual(self.call('POST','/install',body,origin=False)[0],403)
        self.assertEqual(self.call('POST','/install',body,origin='https://other.example')[0],403)
        for invalid in ({**body,'url':'https://other.example/a.exe'},{**body,'manifest_sha256':'../a'},
                        {**body,'confirmation':'yes'},{**body,'command_id':'not-a-guid'},
                        {**body,'command_id':str(uuid.UUID(int=0))},[]):
            with self.subTest(invalid=invalid):self.assertEqual(self.call('POST','/install',invalid)[0],400)

    def test_valid_request_uses_fixed_bridge_and_returns_same_command_id(self):
        import server_update
        identifier=str(uuid.uuid4())
        with patch.object(server_update,'invoke',return_value={'schema':1,'accepted':True,'command_id':identifier}) as bridge:
            status,body=self.call('POST','/check',{'command_id':identifier})
            self.assertEqual(status,202);self.assertEqual(body['command_id'],identifier)
            self.assertEqual(bridge.call_args.args,('Check',identifier,None))

    def test_status_drops_private_worker_fields(self):
        import server_update
        with patch.object(server_update,'invoke',return_value={'available':True,'pending':False,'state':{
            'schema':1,'state':'available','installed_version':'2.1.0-20261117.3',
            'candidate':{'version':'2.1.0-20261117.4','manifest_sha256':'a'*64,'size':100,'exe':'private'},
            'private_key':'synthetic-private-value','exe':'private','command_id':None}}):
            status,body=self.call('GET')
            self.assertEqual(status,200);self.assertEqual(body['state'],'available')
            self.assertNotIn('private',json.dumps(body));self.assertNotIn('secret',json.dumps(body))


if __name__=='__main__':unittest.main()
