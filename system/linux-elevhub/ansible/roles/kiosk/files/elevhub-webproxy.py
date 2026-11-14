#!/usr/bin/env python3
"""Small, fail-closed Squid policy builder used by elevhub-regler.

The generated file is included by a root-owned static Squid configuration.  It
contains no user supplied shell syntax: every hostname is checked as an ASCII
DNS name before it is copied to an ACL.  Squid remains a plain explicit proxy;
TLS is never decrypted.  CONNECT is restricted to TCP/443 and the ACLs are
evaluated against the CONNECT authority.  Chromium is forced to send normal
hostname CONNECT requests, so an IP-literal tunnel is never an allow-list
match while the workshop internet is closed.  CONNECT/SNI policy gates the
destination and tunnel, not decrypted HTTP content; shared CDN hosts therefore
remain shared capabilities and must be listed deliberately.
"""
import ipaddress
import re
from urllib.parse import urlsplit


_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")
_SERVICE = "elevhub-webproxy.service"
_BLOCKED_IPV4 = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
)
_BLOCKED_IPV6 = (
    "::/128", "::1/128", "100::/64", "2001:db8::/32", "fc00::/7",
    "fe80::/10", "ff00::/8",
)


def _mapped_private_networks():
    """Return IPv4-mapped forms without blocking every mapped public IPv4."""
    base = int(ipaddress.IPv6Address("::ffff:0:0"))
    return [str(ipaddress.IPv6Network((base + int(ipaddress.ip_network(net).network_address),
                                       96 + ipaddress.ip_network(net).prefixlen)))
            for net in _BLOCKED_IPV4]


def valid_hostname(value):
    """Return a lowercase exact DNS host, rejecting wildcards and IPs."""
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("Ugyldigt webdomæne.")
    if value.endswith(".") or value != value.lower() or not value.isascii():
        raise ValueError("Webdomæner skal være små ASCII-værtsnavne.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("Webdomæner må ikke være IP-adresser.")
    if not _HOST.fullmatch(value) or any(len(label) > 63 for label in value.split(".")):
        raise ValueError("Ugyldigt webdomæne.")
    return value


def web_domains(app):
    """Validate and return one web app's exact domain list."""
    if not isinstance(app, dict) or app.get("type") != "web":
        return []
    start = app.get("start_url")
    if not isinstance(start, str):
        raise ValueError("Webapp mangler start_url.")
    url = urlsplit(start)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("Webappens start_url skal være HTTPS uden credentials.")
    if url.port not in (None, 443):
        raise ValueError("Webappens start_url skal bruge HTTPS-port 443.")
    domains = app.get("domaener")
    if not isinstance(domains, list) or not domains:
        raise ValueError("Webapp mangler domaener.")
    result = []
    for domain in domains:
        host = valid_hostname(domain)
        if host not in result:
            result.append(host)
    if valid_hostname(url.hostname) not in result:
        raise ValueError("Webappens start_url-host skal være i domaener.")
    return result


def snapshot_domains(apps, internet_open):
    """Return open and closed workshop host sets for an already validated list."""
    if not isinstance(apps, list) or type(internet_open) is not bool:
        raise ValueError("Ugyldig app-politik.")
    open_domains, closed_domains = [], []
    for app in apps:
        if not isinstance(app, dict):
            raise ValueError("Ugyldig app.")
        if app.get("type") == "web":
            domains = web_domains(app)
            (open_domains if app.get("aaben") is True else closed_domains).extend(domains)
    # A domain may be shared by an open and a closed app.  Access stays open
    # because denying it would break the open app; the server's exact app
    # metadata still controls its launch tile.
    return sorted(set(open_domains)), sorted(set(closed_domains) - set(open_domains))


def _acl(name, domains):
    # Squid accepts one or more arguments after dstdomain.  Keep an impossible
    # reserved name for empty ACLs so a generated config remains syntactically
    # valid and cannot accidentally turn into an allow-all rule.
    values = domains or ["no-open-workshop.invalid"]
    return "acl {} dstdomain {}".format(name, " ".join(values))


def _ssl_acl(name, domains):
    values = domains or ["no-open-workshop.invalid"]
    return "acl {} ssl::server_name --client-requested {}".format(name, " ".join(values))


def render_policy(apps, internet_open, blocked_ips=()):
    """Render the dynamic, loopback-only Squid policy."""
    open_domains, closed_domains = snapshot_domains(apps, internet_open)
    lines = [
        "# Generated by elevhub-regler; do not edit.",
        _acl("open_workshops", open_domains),
        _acl("closed_workshops", closed_domains),
        _ssl_acl("open_sni", open_domains),
        _ssl_acl("closed_sni", closed_domains),
        "acl https_port port 443",
        "acl CONNECT method CONNECT",
        "acl step1 at_step SslBump1",
        # Never permit the proxy to reach private, link-local, multicast,
        # loopback, documentation or unspecified destinations.
        "acl blocked_private dst " + " ".join(_BLOCKED_IPV4),
        "acl blocked_private6 dst " + " ".join(_BLOCKED_IPV6 + tuple(_mapped_private_networks())),
    ]
    for index, address in enumerate(blocked_ips):
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as error:
            raise ValueError("Ugyldig serveradresse.") from error
        lines.append("acl blocked_server_{} dst {}".format(index, parsed))
    lines += [
        "http_access deny !CONNECT",
        "http_access deny CONNECT !https_port",
        "http_access deny blocked_private",
        "http_access deny blocked_private6",
    ]
    lines += ["http_access deny blocked_server_{}".format(i) for i, _ in enumerate(blocked_ips)]
    lines += [
        "http_access deny closed_workshops",
        "http_access allow CONNECT open_workshops",
    ]
    if internet_open:
        lines.append("http_access allow CONNECT https_port")
    lines += [
        "http_access deny all",
        # Peek only at ClientHello SNI and splice the original TLS stream;
        # Squid never generates certificates or decrypts workshop traffic.
        "ssl_bump peek step1",
        "ssl_bump terminate closed_sni",
    ]
    if not internet_open:
        lines.append("ssl_bump terminate !open_sni")
    lines += [
        "ssl_bump splice all",
        "shutdown_lifetime 1 seconds",
    ]
    return "\n".join(lines) + "\n"


def service_restart(run, service=_SERVICE):
    """Restart only the isolated web proxy after a changed policy."""
    return run(["systemctl", "restart", service], check=True, timeout=10)


def service_stop(run, service=_SERVICE):
    """Stop the proxy on startup/contact failure; missing service is failure."""
    return run(["systemctl", "stop", service], check=True, timeout=10)


def service_is_active(run, service=_SERVICE):
    """Return whether the isolated service is alive without starting anything."""
    result = run(["systemctl", "is-active", "--quiet", service], check=False, timeout=5)
    return bool(result is not None and getattr(result, "returncode", 1) == 0)
