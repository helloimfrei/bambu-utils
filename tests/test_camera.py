import struct

from bambu_utils.camera import camera_auth_packet


def test_camera_auth_packet_uses_bambu_tunnel_format() -> None:
    packet = camera_auth_packet("12345678")

    assert len(packet) == 80
    assert struct.unpack("<IIII", packet[:16]) == (0x40, 0x3000, 0, 0)
    assert packet[16:48].rstrip(b"\0") == b"bblp"
    assert packet[48:80].rstrip(b"\0") == b"12345678"
