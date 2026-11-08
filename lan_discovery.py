"""Cross-platform mDNS service discovery for Android wireless debugging."""
import ipaddress
from threading import Lock

from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf


class LanDiscovery(ServiceListener):
    def __init__(self):
        self.lock = Lock()
        self.entries = {}
        self.zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        self.browser = ServiceBrowser(self.zeroconf, [
            '_adb-tls-pairing._tcp.local.', '_adb-tls-connect._tcp.local.',
        ], listener=self)

    def add_service(self, zeroconf, service_type, name):
        self.update_service(zeroconf, service_type, name)

    def update_service(self, zeroconf, service_type, name):
        info = zeroconf.get_service_info(service_type, name, timeout=1000)
        if not info:
            return
        kind = service_type.removesuffix('.local.')
        instance = name.removesuffix('.' + service_type)
        rows = [(instance, kind, f'{address}:{info.port}') for address in info.parsed_addresses()
                if ipaddress.ip_address(address).version == 4 and 1 <= info.port <= 65535]
        with self.lock:
            self.entries[(service_type, name)] = rows

    def remove_service(self, zeroconf, service_type, name):
        with self.lock:
            self.entries.pop((service_type, name), None)

    def snapshot(self, kind):
        with self.lock:
            return [row for rows in self.entries.values() for row in rows if row[1] == kind]

    def close(self):
        self.browser.cancel()
        self.zeroconf.close()
