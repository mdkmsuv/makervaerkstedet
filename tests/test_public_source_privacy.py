"""Publication privacy boundaries: synthetic addresses/identities, no live data."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib
from scripts import public_source as s
from tests.test_public_source_export import _tree


def private_ip():
    return '.'.join(('10','23','45','67'))


def categories(text,policy=None,encoding='utf-8'):
    return {x['category'] for x in s.privacy_findings('src/example.txt',text.encode(encoding),policy)}


class PublicationPrivacyTests(unittest.TestCase):
    def test_private_public_and_mapped_network_addresses(self):
        for value in (private_ip(),':'.join(('fd12','3456','','cafe')),':'.join(('fe80','','cafe')),'%sffff:%s'%('::',private_ip())):
            with self.subTest(kind='synthetic network'):
                self.assertIn('network.private-ip',categories('server='+value))
        self.assertIn('network.public-ip',categories('server='+'.'.join(('9','23','45','67'))))
        self.assertFalse(categories(' '.join(('127.0.0.1','0.0.0.0','192.0.2.9','198.51.100.9','203.0.113.9','2001:db8::7','::1','::'))))
    def test_ipv6_scope_and_hardware_identity(self):
        self.assertIn('network.private-ip',categories(':'.join(('fe80','','cafe'))+'%eth0'))
        for delimiter in (':','-'):
            self.assertIn('inventory.mac',categories(delimiter.join(('02','01','02','03','04','05'))))
        self.assertIn('inventory.mac',categories('.'.join(('0201','0203','0405'))))
        self.assertIn('inventory.mac',categories(':'.join(('02','01','02','03','04','05','06','07'))))
        self.assertFalse(categories(':'.join(('ff',)*6)))
    def test_wide_and_escaped_text_cannot_hide_addresses(self):
        for encoding in ('utf-8','utf-16','utf-16-le','utf-16-be','utf-32','utf-32-le','utf-32-be'):
            with self.subTest(encoding=encoding):
                self.assertIn('network.private-ip',categories('server='+private_ip(),encoding=encoding))
        encoded=''.join('\\u%04x'%ord(c) for c in private_ip())
        self.assertIn('network.private-ip',categories('{"server":"'+encoded+'"}'))
    def test_encoded_and_compressed_payloads_are_scanned(self):
        text=('server='+private_ip()).encode('utf-16')
        for payload in (text,zlib.compress(text)):
            encoded=base64.b64encode(payload).decode()
            self.assertIn('network.private-ip',categories("b64decode('"+encoded+"')"))
    def test_personal_path_email_and_private_host(self):
        self.assertIn('personal.local-home',categories('C:/Users/'+'person-not-a-role'+'/input.txt'))
        self.assertIn('personal.local-home',categories('/home/'+'person-not-a-role'+'/input.txt'))
        self.assertFalse(categories('/home/elev/project /home/elevhub/data'))
        self.assertIn('personal.email',categories('person'+'@'+'private-domain.net'))
        self.assertFalse(categories('person@example.com'))
        self.assertIn('network.local-hostname',categories('server.'+'local'))
    def test_report_contains_no_matched_private_value(self):
        text='server='+private_ip()
        result=s.privacy_findings('src/example.txt',text.encode())
        self.assertEqual(set(result[0]),{'file','line','category'})
        self.assertNotIn(private_ip(),json.dumps(result))
    def test_review_exception_requires_exact_file_line_and_category(self):
        line='network='+private_ip()
        policy={'privacyRules':{'reviewedExceptions':[{'path':'src/example.txt','lineSha256':hashlib.sha256(line.encode()).hexdigest(),'category':'network.private-ip','reason':'Reviewed synthetic address needed to test private-network rejection.'}]}}
        self.assertFalse(categories(line,policy))
        self.assertIn('network.private-ip',categories(line+' # changed',policy))
        self.assertTrue(s.privacy_findings('src/other.txt',line.encode(),policy))
        policy['privacyRules']['reviewedExceptions'][0]['category']='secret.pem'
        with self.assertRaises(s.ExportError):categories(line,policy)
    def test_known_school_fingerprint_has_no_raw_identifier_in_policy(self):
        name='private-'+'machine'
        policy={'privacyRules':{'forbiddenIdentifierSha256':[hashlib.sha256(name.encode()).hexdigest()]}}
        self.assertNotIn(name,json.dumps(policy))
        self.assertIn('inventory.school-identifier',categories(name.upper(),policy))
    def test_credentials_and_ssh_material_are_rejected(self):
        self.assertIn('credential.literal',categories("password='"+'production-'+'credential'+"'"))
        self.assertIn('secret.token-or-ssh-key',categories('gh'+'p_'+'A1b2C3d4'*8))
        self.assertIn('secret.token-or-ssh-key',categories('ssh-'+'ed25519 '+'A1b2C3d4'*8))
        key='-----BEGIN '+'PRIVATE KEY-----\n'+'A1b2C3d4'*10+'\n-----END '+'PRIVATE KEY-----'
        self.assertIn('secret.pem',categories(key))
    def test_token_prefix_inside_upstream_disk_url_is_not_a_secret(self):
        url='https://www.systutorials.com/how-to-find-the-disk-where-root-is-on-in-bash-on-linux/'
        self.assertNotIn('secret.token-or-ssh-key',categories(url))
    def test_token_boundaries_preserve_real_tokens_and_encoded_checks(self):
        tokens=['s'+'k-'+'A1b2C3d4'*4,
                *['gh'+kind+'_'+'A1b2C3d4'*4 for kind in 'pousr'],
                'github'+'_pat_'+'A1b2C3d4'*4,
                'AK'+'IA'+'A1B2'*4]
        for token in tokens:
            for prefix in ('',' ', '"', "'",'=',':','Bearer '):
                with self.subTest(token_type=token[:7],prefix=prefix):
                    self.assertIn('secret.token-or-ssh-key',categories(prefix+token))
            encoded=base64.b64encode(token.encode()).decode()
            self.assertIn('secret.token-or-ssh-key',categories("b64decode('"+encoded+"')"))
            escaped=''.join('\\u%04x'%ord(c) for c in token)
            self.assertIn('secret.token-or-ssh-key',categories(escaped))
        self.assertIn('secret.token-or-ssh-key',categories('ssh-'+'ed25519 '+'A1b2C3d4'*8))
    def test_reviewed_exception_cannot_allow_a_token(self):
        sample='s'+'k-'+'A1b2C3d4'*4
        policy={'privacyRules':{'reviewedExceptions':[{
            'path':'src/example.txt','lineSha256':hashlib.sha256(sample.encode()).hexdigest(),
            'category':'secret.token-or-ssh-key','reason':'Even a reviewed line must never exempt an actual secret token.'}]}}
        with self.assertRaises(s.ExportError):categories(sample,policy)
    def test_prose_next_line_and_punctuation_bearing_private_audit(self):
        secret='private-'+'fixture$!'
        for text in ('Adgangskode `'+secret+'`.', 'RDP adgangskode\n  `'+secret+'`.'):
            result=s.privacy_findings('docs/example.md',text.encode())
            self.assertIn('credential.prose',{x['category'] for x in result})
            self.assertNotIn(secret,json.dumps(result))
        policy={'privacyRules':{'forbiddenIdentifierSha256':[hashlib.sha256(secret.encode()).hexdigest()]}}
        self.assertIn('inventory.school-identifier',categories('The value is `'+secret+'`.',policy))
        self.assertFalse(s.privacy_findings('docs/example.md',b'Ingen adgangskode; brug `sudo` med SSH.'))
        self.assertFalse(s.privacy_findings('docs/example.md',b'Adgangskode `<CHOOSE_ONE>`'))
        self.assertFalse(categories(r'broken surrogate \ud800'))
    def test_ssh_fingerprints_are_machine_inventory(self):
        for value in ('SHA256:'+'A'*42+'/', 'MD5:'+':'.join(['01']*16)):
            self.assertIn('inventory.ssh-fingerprint',categories(value))
    def test_hash_credentials_python_tokens_and_inventory_filename(self):
        self.assertIn('credential.literal',categories("CONFIG={'token':'"+'A'*43+"'}"))
        self.assertIn('credential.literal',categories('{"local_admin":{"password_hash":"'+'hash-value'+'"}}'))
        self.assertIn('secret.password-hash',categories('d-i passwd/root-password-crypted password $'+'6$'+'saltsalt$'+'x'*80))
        self.assertIn('network.private-ip',{x['category'] for x in s.privacy_findings('device_'+private_ip()+'_inventory.json',b'{}')})
    def test_modified_font_or_fake_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'fake.ttf';p.write_bytes(b'\x00\x01\x00\x00'+b'not an approved font')
            with self.assertRaisesRegex(s.ExportError,'publicFonts'):s._read_source(p,1024)
            p=Path(d)/'data.txt';p.write_bytes(b'\x00\xff\x00\x01')
            with self.assertRaises(s.ExportError):s._read_source(p,1024)
    def test_rogue_tracked_file_and_staged_secret_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root,policy=_tree(Path(d));safe=[(p.relative_to(root).as_posix(),p.read_bytes()) for p in root.rglob('*') if p.is_file()]
            with patch.object(s,'_git_index_contents',return_value=safe+[('private-outside.txt',b'private')]):
                with self.assertRaisesRegex(s.ExportError,'uden for public allowlisten'):s.audit_repository(root,policy)
            staged=[(n,('server='+private_ip()).encode() if n=='src/main.py' else b) for n,b in safe]
            with patch.object(s,'_git_index_contents',return_value=staged):
                with self.assertRaisesRegex(s.ExportError,'network.private-ip'):s.audit_repository(root,policy)
            self.assertEqual((root/'src/main.py').read_text(),"print('source')\n")
    def test_exact_git_index_protocol_and_staged_link_rejection(self):
        oid=b'a'*40;blob=b"print('safe')\n";calls=[]
        def fake(command,**kwargs):
            calls.append(command)
            if command[-1]=='-z':out=b'100644 '+oid+b' 0\tsrc/main.py\0'
            elif '--batch-check=' in command[-1]:out=oid+b' blob '+str(len(blob)).encode()+b'\n'
            else:out=oid+b' blob '+str(len(blob)).encode()+b'\n'+blob+b'\n'
            return SimpleNamespace(returncode=0,stdout=out)
        with patch.object(s.subprocess,'run',side_effect=fake):
            self.assertEqual(s._git_index_contents(Path.cwd(),{}),[('src/main.py',blob)])
        self.assertEqual(calls[0][-4:],['ls-files','--cached','--stage','-z'])
        with patch.object(s.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'120000 '+oid+b' 0\tsrc/link.py\0')):
            with self.assertRaisesRegex(s.ExportError,'link'):s._git_index_contents(Path.cwd(),{})
    def test_known_properties_units_and_credential_templates_are_not_identities(self):
        self.assertFalse(s.privacy_findings('src/example.py',b'Path.home(); self.local; System.getProperty("user.home"); getty@tty1.service'))
        self.assertIn('network.local-hostname',categories("host='self.local'"))
        self.assertFalse(s.privacy_findings('src/template.yml',b'password: "!"'))
        self.assertFalse(s.privacy_findings('src/template.yml',b'password: "{{ configured_password }}"'))
        self.assertIn('credential.literal',categories('password="!"'))
    def test_entities_unicode_width_and_opaque_payload_fail_closed(self):
        escaped=''.join('&#%d;'%ord(c) for c in private_ip())
        self.assertIn('network.private-ip',categories(escaped))
        fullwidth=''.join(chr(ord(c)+0xfee0) if c.isdigit() else c for c in private_ip())
        self.assertIn('network.private-ip',categories(fullwidth))
        opaque=base64.b64encode(b'\x00\xff\x00\x01').decode()
        self.assertIn('encoded.opaque-content',categories("b64decode('"+opaque+"')"))
    def test_public_email_review_is_exact_and_does_not_allow_new_contact(self):
        contact='upstream'+'@'+'author-domain.net'
        policy={'privacyRules':{'publicAttributionEmailSha256':[{'sha256':hashlib.sha256(contact.encode()).hexdigest(),'reason':'Reviewed public upstream copyright contact retained for license attribution.'}]}}
        self.assertFalse(categories(contact,policy))
        self.assertIn('personal.email',categories('other'+'@'+'author-domain.net',policy))
    def test_manifest_schema_checks_staged_bytes_but_not_obsolete_content_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root,policy=_tree(Path(d))
            value={'schemaVersion':1,'project':policy['project'],'credit':s.CREDIT,'generator':{'path':'scripts/public_source.py','version':1},'sourcePolicy':'deployment/public-source-manifest.json','sourcePolicySha256':'0'*64,'files':[{'path':'src/main.py','size':999,'sha256':'a'*64,'mode':'0644'}]}
            path=root/s.MANIFEST_NAME;path.write_text(json.dumps(value))
            s._check_generated_manifest(path,policy)
            with self.assertRaisesRegex(s.ExportError,'manifest schema'):
                s._check_generated_manifest(path,policy,b'{"wrong":"schema"}')

if __name__=='__main__':unittest.main(verbosity=2)
