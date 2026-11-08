import struct
import unittest
from unittest.mock import patch, MagicMock
import mdns_lookup


def name(value):
    return b''.join(bytes([len(part)]) + part.encode() for part in value.split('.')) + b'\0'


class DnsTest(unittest.TestCase):
    def test_direct_lookup_srv_with_compression(self):
        service = '_adb-tls-connect._tcp.local'
        question = name(service) + struct.pack('!HH', 12, 1)
        # A real DNS compression pointer reuses the question's service suffix.
        owner = b'\x08adb-test\xc0\x0c'
        data = struct.pack('!HHH', 0, 0, 42157) + name('Android.local')
        record = owner + struct.pack('!HHIH', 33, 1, 10, len(data)) + data
        mock_socket = MagicMock()
        mock_socket.__enter__.return_value = mock_socket
        mock_socket.recv.side_effect = lambda _size: (
            mock_socket.send.call_args.args[0][:2] + struct.pack('!5H', 0x8400, 1, 1, 0, 0)
            + question + record)
        with patch('mdns_lookup.socket.socket', return_value=mock_socket):
            self.assertEqual(mdns_lookup.lookup('192.168.3.4', '_adb-tls-connect._tcp'),
                             [('adb-test', '_adb-tls-connect._tcp', '192.168.3.4:42157')])
        mock_socket.connect.assert_called_once_with(('192.168.3.4', 5353))

    def test_corrupt_compression_and_truncation(self):
        with self.assertRaises(ValueError):
            mdns_lookup.read_name(b'\xc0\x00', 0)
        with self.assertRaises(ValueError):
            mdns_lookup.read_name(b'\x05abc', 0)


if __name__ == '__main__':
    unittest.main()
