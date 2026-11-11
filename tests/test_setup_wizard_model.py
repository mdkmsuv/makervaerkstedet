import json, shutil, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'deployment' / 'school-setup' / 'setup-wizard-model.ps1'

class SetupWizardModelTests(unittest.TestCase):
    @classmethod
    def ps(cls):
        return shutil.which('powershell.exe') or shutil.which('pwsh')

    def run_ps(self, body, *args):
        exe = self.ps()
        if not exe: self.skipTest('PowerShell kræves')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'h.ps1'; p.write_text("Set-StrictMode -Version Latest\n. '" + str(MODEL).replace("'", "''") + "'\n" + body, encoding='utf-8-sig')
            return subprocess.run([exe, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(p), *args], capture_output=True, text=True, timeout=30)

    def test_server_model_and_pathological_inputs(self):
        body = "$v=@{schoolName='Skolen Å';deployment='single-computer';component='server';roleIp='192.0.2.10';studentSubnet='192.0.2.0/24';theme='green';machine='fbb2';addressConfirmed=$true;packageRoot='C:\\pkg'}; Test-SetupWizardInput -Values $v | ConvertTo-Json -Depth 8 -Compress"
        r = self.run_ps(body); self.assertEqual(r.returncode, 0, r.stderr); v=json.loads(r.stdout); self.assertTrue(v['valid']); self.assertEqual(v['parameters']['Role'], 'makerspace')
        bad = body.replace("roleIp='192.0.2.10'", "roleIp='127.0.0.1'").replace('addressConfirmed=$true','addressConfirmed=$false')
        v=json.loads(self.run_ps(bad).stdout); self.assertFalse(v['valid']); self.assertGreaterEqual(len(v['errors']), 2)
        string_bool = body.replace('addressConfirmed=$true', "addressConfirmed='True'")
        v=json.loads(self.run_ps(string_bool).stdout); self.assertFalse(v['valid'])

    def test_station_fixture_shape_and_no_powershell_evaluation(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'connection.json'; path.write_text(json.dumps({'schemaVersion':1,'kind':'makervaerkstedet-laserstation-connection','school_name':'Skolen','server_url':'https://192.0.2.10:18780','caCertificatePem':'-----BEGIN CERTIFICATE----- fixture -----END CERTIFICATE-----','station_id':'laser-fbb2','machine':'fbb2'}), encoding='utf-8')
            body = "$v=@{deployment='separate-computers';component='laser-station';connectionFile=$args[0]}; Test-SetupWizardInput -Values $v | ConvertTo-Json -Depth 8 -Compress"
            v=json.loads(self.run_ps(body, str(path)).stdout); self.assertTrue(v['valid']); self.assertEqual(v['parameters']['Action'],'Install')

    def test_station_file_errors_explain_recovery(self):
        body = "$v=@{deployment='separate-computers';component='laser-station';connectionFile=$args[0]}; Test-SetupWizardInput -Values $v | ConvertTo-Json -Depth 8 -Compress"
        missing=json.loads(self.run_ps(body, 'missing-school-connection.json').stdout)
        self.assertFalse(missing['valid']); self.assertIn('laser-station-connection.json', missing['errors'][0]['message'])
        with tempfile.TemporaryDirectory() as d:
            bad=Path(d)/'school-connection.json'; bad.write_text('{not-json', encoding='utf-8')
            malformed=json.loads(self.run_ps(body, str(bad)).stdout)
            self.assertFalse(malformed['valid']); self.assertIn('laser-station-connection.json', malformed['errors'][0]['message'])
            linux=Path(d)/'linux.json'; linux.write_text(json.dumps({'schemaVersion':1,'kind':'elevhub-school-connection','computer':'elev-01'}), encoding='utf-8')
            value=json.loads(self.run_ps(body, str(linux)).stdout)
            self.assertFalse(value['valid']); self.assertIn('Linux', value['errors'][0]['message'])
            large=Path(d)/'large.json'; large.write_text('x'*32769, encoding='utf-8')
            value=json.loads(self.run_ps(body, str(large)).stdout)
            self.assertFalse(value['valid']); self.assertIn('32 KB', value['errors'][0]['message'])
            incomplete=Path(d)/'incomplete.json'; incomplete.write_text(json.dumps({'schemaVersion':1,'kind':'makervaerkstedet-laserstation-connection'}), encoding='utf-8')
            value=json.loads(self.run_ps(body, str(incomplete)).stdout)
            self.assertFalse(value['valid']); self.assertIn('mangler', value['errors'][0]['message'])
            good = {'schemaVersion':1,'kind':'makervaerkstedet-laserstation-connection','school_name':'Skolen','server_url':'https://192.0.2.10:18780','caCertificatePem':'-----BEGIN CERTIFICATE----- fixture -----END CERTIFICATE-----','station_id':'laser-fbb2','machine':'fbb2'}
            for payload in (None, [], [good], [{}, {}], {}, dict(good,server_url='http://192.0.2.10:8780'), dict(good,caCertificatePem='-----BEGIN PRIVATE KEY-----')):
                with self.subTest(payload=payload):
                    invalid=Path(d)/'invalid.json'; invalid.write_text(json.dumps(payload),encoding='utf-8')
                    response=self.run_ps(body,str(invalid)); self.assertEqual(response.returncode,0,response.stderr)
                    value=json.loads(response.stdout)
                    self.assertFalse(value['valid']); self.assertIn('laser-station-connection.json', value['errors'][0]['message'])

    def test_discovery_multiple_adapters_dhcp_and_gateway(self):
        body = "function Get-NetIPInterface { @([pscustomobject]@{InterfaceIndex=3;Dhcp='Enabled'},[pscustomobject]@{InterfaceIndex=4;Dhcp='Disabled'}) }; function Get-NetIPAddress { @([pscustomobject]@{IPAddress='192.0.2.10';PrefixLength=24;InterfaceAlias='LAN';InterfaceIndex=3;AddressState='Preferred'},[pscustomobject]@{IPAddress='10.0.0.4';PrefixLength=8;InterfaceAlias='VPN';InterfaceIndex=4;AddressState='Preferred'}) }; function Get-NetIPConfiguration { param($InterfaceIndex); [pscustomobject]@{IPv4DefaultGateway=[pscustomobject]@{NextHop='192.0.2.1'}} }; Get-SetupNetworkCandidates | ConvertTo-Json -Depth 5 -Compress"
        v=json.loads(self.run_ps(body).stdout); self.assertEqual(len(v),2); self.assertEqual({x['alias'] for x in v},{'LAN','VPN'}); self.assertEqual(v[0]['subnet'],'192.0.2.0/24')

if __name__ == '__main__': unittest.main()
