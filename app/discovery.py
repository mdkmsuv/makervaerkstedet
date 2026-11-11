"""Bounded Bambu SSDP discovery; never sends print or configuration commands."""
import ipaddress
import re
import select
import socket
import time

MULTICAST = '239.255.255.250'
MODEL_NAMES = {'N1': 'A1 mini', 'C12': 'P1S', 'O1D': 'H2D'}


def parse_announcement(data, source_ip):
    try:
        address = ipaddress.ip_address(source_ip)
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
            return None
        lines = data.decode('utf-8', errors='replace').splitlines()
        if not lines or not lines[0].startswith(('NOTIFY ', 'HTTP/1.1 200')):
            return None
        headers = {key.strip().lower(): value.strip() for line in lines[1:] if ':' in line for key, value in [line.split(':', 1)]}
        model = MODEL_NAMES.get(headers.get('devmodel.bambu.com'))
        serial = headers.get('usn', '').removeprefix('uuid:').split('::', 1)[0]
        if not model or not re.fullmatch(r'[A-Za-z0-9]{8,30}', serial):
            return None
        return {'serial': serial, 'model': model, 'host': str(address), 'name': headers.get('devname.bambu.com', model)[:80], 'connection': headers.get('devconnect.bambu.com', 'unknown')}
    except (ValueError, TypeError):
        return None


def discover(seconds=8, interface='0.0.0.0'):
    sockets, found, issues = [], {}, []
    try:
        # Listen to announcements and unicast responses on both Bambu discovery ports.
        for port in (1990, 2021, 0):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('', port))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(MULTICAST) + socket.inet_aton(interface))
                if interface != '0.0.0.0':
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
                sockets.append(sock)
            except OSError as error:
                issues.append(f'UDP {port}: {error.__class__.__name__}')
                sock.close()
        if not sockets:
            return {'printers': [], 'issues': issues}
        for port in (1990, 2021):
            query = f'M-SEARCH * HTTP/1.1\r\nHOST: {MULTICAST}:{port}\r\nMAN: "ssdp:discover"\r\nMX: 2\r\nST: urn:bambulab-com:device:3dprinter:1\r\n\r\n'
            try:
                sockets[-1].sendto(query.encode('ascii'), (MULTICAST, port))
            except OSError as error:
                issues.append(f'Search {port}: {error.__class__.__name__}')
        deadline = time.monotonic() + min(max(seconds, 1), 15)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select(sockets, [], [], min(0.5, remaining))
            for sock in ready:
                data, source = sock.recvfrom(8192)
                printer = parse_announcement(data, source[0])
                if printer:
                    found[printer['serial']] = printer
        return {'printers': list(found.values()), 'issues': issues}
    finally:
        for sock in sockets:
            sock.close()
