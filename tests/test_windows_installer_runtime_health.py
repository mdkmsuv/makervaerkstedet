import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('installer_runtime_health',ROOT/'windows/inno/lifecycle.py')
life=importlib.util.module_from_spec(spec);spec.loader.exec_module(life)

class RuntimeHealthTests(unittest.TestCase):
    def config(self):
        return {'backend_ports':{'print':8765,'design':8766,'teacher':8767},
                'external_backends':['print'],'apps':[{'id':'3dprint','bagved':'127.0.0.1:8765'}],
                'laerer_adresse':'127.0.0.1','laerer_port':8780,'elev_adresse':'192.0.2.2',
                'tls':{'ca':'security/ca.crt'}}
    def test_external_loopback_print_requires_real_configured_semantic_health_not_bundle_version(self):
        with tempfile.TemporaryDirectory() as temp:
            targets=life.health_targets(self.config(),{'externalBackends':['print']},Path(temp))
        target=next(t for t in targets if t['component']=='print')
        self.assertFalse(target['owned']);self.assertEqual((target['host'],target['port']),('127.0.0.1',8765))
        life.validate_health({'ok':True,'service':'printvaerksted','release_id':'older-independent-release'},target,'new-server-release')
        for payload in ({'ok':False,'service':'printvaerksted'},{'ok':True,'service':'unrelated-server'}):
            with self.assertRaises(life.LifecycleError):life.validate_health(payload,target,'new-server-release')
        hub=next(t for t in targets if t['component']=='hub')
        self.assertEqual((hub['scheme'],hub['host'],hub['server_hostname']),('https','127.0.0.1','192.0.2.2'))
    def test_remote_laser_teacher_binding_uses_own_ip_and_verified_tls_hostname(self):
        import ssl,threading,ipaddress
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        from datetime import datetime,timedelta,timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Isolated teacher binding test')])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
              .add_extension(x509.BasicConstraints(ca=True,path_length=None),True)
              .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.2'))]),False).sign(key,hashes.SHA256()))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                body=json.dumps({'ok':True,'service':'elevhub'}).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);ca=root/'ca.pem';private=root/'key.pem'
            ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM));private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            server=ThreadingHTTPServer(('127.0.0.2',0),Handler);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(ca,private);server.socket=context.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                config=self.config();config.update(elev_adresse='127.0.0.2',laerer_adresse='127.0.0.2',laerer_port=server.server_port,tls={'ca':'ca.pem'})
                target=life.health_targets(config,{'externalBackends':['print']},root)[-1]
                self.assertEqual(target['host'],'127.0.0.2');self.assertEqual(life.fetch_health(target)['service'],'elevhub')
                with self.assertRaises(ssl.SSLCertVerificationError):life.fetch_health({**target,'server_hostname':'127.0.0.3'})
                with self.assertRaises(life.LifecycleError):life.health_targets({**config,'laerer_adresse':'unrelated.example'}, {'externalBackends':['print']},root)
            finally:server.shutdown();server.server_close();thread.join(timeout=3)

    def test_external_remote_endpoint_is_followed_and_not_replaced_with_loopback(self):
        config=self.config();config['apps'][0]['bagved']='print.example:9876'
        with tempfile.TemporaryDirectory() as temp:target=life.health_targets(config,{'externalBackends':['print']},Path(temp))[0]
        self.assertEqual((target['host'],target['port']),('print.example',9876))
    def test_owned_process_must_report_exact_version_and_process_identity(self):
        target={'service':'designvaerksted','surface':'teacher','owned':True}
        value={'ok':True,'service':'designvaerksted','surface':'teacher','release_id':'2.0.1','pid':123,'runtime_instance':str(uuid.uuid4())}
        life.validate_health(value,target,'2.0.1')
        for key,bad in [('release_id','2.0.0'),('release_id',None),('surface','student'),('pid',True),('runtime_instance','')]:
            with self.subTest(key=key,bad=bad), self.assertRaises(life.LifecycleError):life.validate_health({**value,key:bad},target,'2.0.1')
    def test_unowned_endpoint_or_ownership_conflict_is_rejected(self):
        for patch in ({'external_backends':[]},{'apps':[{'id':'3dprint','bagved':'http://user:secret@host:8765/'}]}):
            with tempfile.TemporaryDirectory() as temp,self.assertRaises(life.LifecycleError):
                life.health_targets({**self.config(),**patch},{'externalBackends':['print']},Path(temp))
    def test_installation_with_separate_older_print_program_is_refused(self):
        with self.assertRaisesRegex(life.LifecycleError,'external_print_installation_not_supported'):
            life.require_local_print_ownership({'externalBackends':['print']})
        life.require_local_print_ownership({'externalBackends':[]})

    def test_fresh_install_cannot_report_success_without_version_health(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);native=life.NativeWindows.__new__(life.NativeWindows)
            native.program=root/'new-program';native.state=root/'new-state'
            def setup(*args):
                self.assertIn('-RemoteLaserStation',args)
                native.state.mkdir();(native.state/'installation-owner.json').write_text('{}')
            native.command=setup;native.health=Mock(side_effect=life.LifecycleError('wrong_runtime_version'))
            profile={'school_name':'Test','server_mode':'vm','role_ip':'192.0.2.2','student_subnet':'192.0.2.0/24','theme':'green','machine':'fbb2'}
            with self.assertRaisesRegex(life.LifecycleError,'wrong_runtime_version'):native.install_server(root,profile)
            native.health.assert_called_once_with()

    def test_installed_descriptor_requires_exact_role_and_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'runtime-release.json';p.write_text(json.dumps({'schema':1,'release_id':'2.0.1','role':'server'}))
            self.assertEqual(life.runtime_identity(Path(temp),'server'),'2.0.1')
            with self.assertRaises(life.LifecycleError):life.runtime_identity(Path(temp),'laser_station')
            p.write_text(json.dumps({'schema':True,'release_id':'2.0.1','role':'server'}))
            with self.assertRaises(life.LifecycleError):life.runtime_identity(Path(temp),'server')

if __name__=='__main__':unittest.main()
