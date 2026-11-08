"""Query one known LAN phone directly when multicast discovery fails."""
import ipaddress
import secrets
import socket
import struct
import sys


def read_name(packet, offset, visited=None):
    visited = set() if visited is None else visited
    labels = []
    end = None
    while True:
        if offset in visited or offset >= len(packet):
            raise ValueError('Invalid DNS name')
        visited.add(offset)
        size = packet[offset]
        if size & 0xc0 == 0xc0:
            pointer = ((size & 0x3f) << 8) | packet[offset + 1]
            end = offset + 2 if end is None else end
            suffix, _ = read_name(packet, pointer, visited)
            labels.append(suffix)
            break
        if size & 0xc0:
            raise ValueError('Invalid DNS label')
        offset += 1
        if size == 0:
            end = offset if end is None else end
            break
        if offset + size > len(packet):
            raise ValueError('Truncated DNS label')
        labels.append(packet[offset:offset + size].decode('utf-8'))
        offset += size
    return '.'.join(labels), end


def records(packet):
    _id, flags, questions, answers, authorities, additional = struct.unpack_from('!6H', packet)
    if not flags & 0x8000:
        return []
    offset = 12
    for _ in range(questions):
        _, offset = read_name(packet, offset)
        offset += 4
    result = []
    for _ in range(answers + authorities + additional):
        name, offset = read_name(packet, offset)
        kind, _class, ttl, length = struct.unpack_from('!HHIH', packet, offset)
        offset += 10
        if offset + length > len(packet):
            raise ValueError('Truncated DNS record')
        if kind == 33 and length >= 7 and ttl:
            port = struct.unpack_from('!H', packet, offset + 4)[0]
            result.append((name, port))
        offset += length
    return result


def lookup(host, kind):
    host = str(ipaddress.IPv4Address(host))
    if kind not in ('_adb-tls-connect._tcp', '_adb-tls-pairing._tcp'):
        raise ValueError('Unsupported service')
    query_name = kind + '.local'
    encoded = b''.join(bytes([len(label)]) + label.encode() for label in query_name.split('.')) + b'\0'
    identifier = secrets.randbelow(65536)
    query = struct.pack('!6H', identifier, 0, 1, 0, 0, 0) + encoded + struct.pack('!HH', 12, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(1.5)
        connection.connect((host, 5353))
        connection.send(query)
        packet = connection.recv(65535)
    if struct.unpack_from('!H', packet)[0] != identifier:
        return []
    suffix = '.' + query_name
    return [(name[:-len(suffix)], kind, f'{host}:{port}') for name, port in records(packet)
            if name.endswith(suffix) and 1 <= port <= 65535]


if __name__ == '__main__':
    try:
        for row in lookup(sys.argv[1], sys.argv[2]):
            print('\t'.join(row), flush=True)
    except (OSError, ValueError, IndexError, UnicodeError, struct.error):
        sys.exit(1)
