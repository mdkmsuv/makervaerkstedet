"""Authenticated dispatch-pause client; no hardware idle/stop decision."""
import http.client
import importlib
from pathlib import Path
import sys

from .validation import APIError, decode, encode, identifier


class MachineMaintenanceBarrier:
    """Stable holder is the update UUID, including after reboot.

    Native updater can use protected loopback profiles without opening machine
    databases or starting a control-plane application.
    """
    def __init__(self, holder_id, *, printer_profile=None, laser_profile=None, configuration_root=None):
        self.holder_id = identifier(holder_id)
        directory = Path(__file__).resolve().parents[2] / 'linux-elevhub' / 'server' / 'elevhub'
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
        root = Path(configuration_root or '.')
        self.clients = {
            'printer': importlib.import_module('makerspace_printers').PrinterAdministration(printer_profile, root),
            'laser': importlib.import_module('makerspace_laser_admin').LaserAdministration(laser_profile, root)}

    @staticmethod
    def _names(component_id):
        if component_id == 'server':
            return ('printer', 'laser')
        if component_id == 'laser':
            return ('laser',)
        raise APIError('maintenance_component_unsupported', 409)

    def _call(self, name, action):
        client = self.clients[name]
        if client.backend is None:
            raise APIError('maintenance_backend_not_configured', 503)
        connection = http.client.HTTPConnection(*client.backend, timeout=5)
        path = '/api/makerspace/' + ('printers' if name == 'printer' else 'laser') + '/maintenance'
        try:
            connection.request('POST', path, encode({'action': action, 'holder_id': self.holder_id}).encode(),
                headers={'Authorization': 'Bearer ' + client._key(), 'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(65537)
            if len(raw) > 65536:
                raise APIError('maintenance_backend_response_invalid', 503)
            result = decode(raw)
            if response.status != 200:
                raise APIError(result.get('error', 'maintenance_backend_unavailable'), 409 if response.status == 409 else 503)
            expected = action != 'release'
            if (not isinstance(result, dict) or result.get('barrier_version') != 1
                    or result.get('holder_id') != self.holder_id or result.get('held') is not expected
                    or result.get('admission_closed') is not expected or result.get('physical_stop_sent') is not False):
                raise APIError('maintenance_backend_response_invalid', 503)
            return result
        except (http.client.HTTPException, OSError) as error:
            raise APIError('maintenance_backend_unavailable', 503) from error
        finally:
            connection.close()

    def _operate(self, action, component_id):
        results = {name: self._call(name, action) for name in self._names(component_id)}
        return {'holder_id': self.holder_id, 'admission_closed': action != 'release', 'backends': results}

    def acquire(self, component_id): return self._operate('acquire', component_id)
    def assert_held(self, component_id): return self._operate('assert', component_id)
    def release(self, component_id): return self._operate('release', component_id)


class RemoteMaintenanceBarrier:
    """Separate Windows station uses its real mTLS transport to central queues."""
    def __init__(self, transport, holder_id):
        self.transport, self.holder_id = transport, identifier(holder_id)

    def _operate(self, action, component_id):
        result = self.transport.post_json('/api/device-control/v1/maintenance',
            {'holder_id': self.holder_id, 'action': action, 'component_id': component_id})
        if result.get('holder_id') != self.holder_id or result.get('admission_closed') is not (action != 'release'):
            raise APIError('maintenance_backend_response_invalid', 503)
        return result

    def acquire(self, component_id): return self._operate('acquire', component_id)
    def assert_held(self, component_id): return self._operate('assert', component_id)
    def release(self, component_id): return self._operate('release', component_id)
