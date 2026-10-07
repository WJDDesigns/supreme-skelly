import pytest

from skelly import protocol as p


def test_crc8_maxim_check_value():
    # CRC-8/MAXIM check value for "123456789"
    assert p.crc8(b"123456789") == 0xA1


def test_build_pads_payload_and_appends_crc():
    frame = p.query(p.Cmd.QUERY_VERSION)
    assert frame[:2] == b"\xaa\xee"
    assert len(frame) == 2 + 8 + 1
    assert frame[-1] == p.crc8(frame[:-1])


def test_movement_frame_layout():
    frame = p.movement(0x05)
    # AA CA mask 00 cluster(4) filename(00) then zero padding to 8 bytes
    assert frame[:-1] == bytes.fromhex("AACA0500000000000000")


def test_color_frame_layout():
    frame = p.color(1, 255, 0, 16, cycle=True)
    assert frame[:-1].hex().upper() == "AAF401FF0010010000000000"


def test_filename_ref_with_and_without_length():
    body = bytes.fromhex("5C55") + "a.mp3".encode("utf-16le")
    assert p.filename_ref("a.mp3") == bytes([len(body)]) + body
    assert p.filename_ref("a.mp3", with_length=False) == body
    assert p.filename_ref("") == b"\x00"


def test_channel_validation():
    with pytest.raises(ValueError):
        p.light_mode(6, 1)
    assert p.light_mode(p.ALL_CHANNELS, 1)[2] == 0xFF


def test_speed_mapping_round_trip():
    assert p.ui_speed_to_device(254) == 0
    assert p.ui_speed_to_device(0) == 254
    assert p.device_speed_to_ui(255) == 254
    assert p.device_speed_to_ui(p.ui_speed_to_device(100)) == 100


def test_pin_and_name():
    frame = p.set_pin_and_name("1234", "Skelly")
    assert frame[2:6] == b"1234"
    assert frame[14] == 6 and frame[15:21] == b"Skelly"
    with pytest.raises(ValueError):
        p.set_pin_and_name("12a4", "x")


def test_filename_validation():
    p.validate_filename("x" * 33)
    with pytest.raises(ValueError):
        p.validate_filename("x" * 34)
    with pytest.raises(ValueError):
        p.validate_filename("bad/name.mp3")


def _notify(cmd: int, payload: bytes) -> bytes:
    return bytes([0xBB, cmd]) + payload + b"\x00"


def test_parse_simple_responses():
    assert p.parse(_notify(0xEE, bytes([68]))).data == {"version": "v68"}
    assert p.parse(_notify(0xE5, bytes([200]))).data == {"volume": 200}
    assert p.parse(_notify(0xE6, bytes([3]) + b"Bob")).data == {"name": "Bob"}
    ev = p.parse(_notify(0xD2, (4096).to_bytes(4, "big") + bytes([7, 1])))
    assert ev.data == {"free_kb": 4096, "file_count": 7, "action_mode": 1}
    ev = p.parse(_notify(0xC6, bytes([0, 3, 1, 0, 9])))
    assert ev.data == {"serial": 3, "playing": True, "duration": 9}


def test_parse_params():
    payload = bytes([1, 1, 0, 0, 0, 0]) + b"4321" + bytes(8) + bytes([0]) + bytes(7) + bytes([4]) + b"Live"
    ev = p.parse(_notify(0xE0, payload))
    assert ev.kind == "params"
    assert ev.data == {"channels": [1, 1, 0, 0, 0, 0], "pin": "4321", "show_mode": 0, "name": "Live"}


def test_parse_live_state_and_file():
    light = bytes([2, 100, 1, 2, 3, 0, 7])
    ev = p.parse(_notify(0xE1, bytes([5]) + light * 6 + bytes([9])))
    assert ev.data["movement"] == 5 and ev.data["eye"] == 9
    assert ev.data["lights"][0] == {"mode": 2, "brightness": 100, "rgb": (1, 2, 3), "cycle": False, "speed": 7}

    payload = ((3).to_bytes(2, "big") + (77).to_bytes(4, "big") + (2).to_bytes(2, "big") + bytes(2)
               + bytes([255]) + light * 6 + bytes([4, 1]) + bytes(2) + "\\Boo.mp3".encode("utf-16le"))
    ev = p.parse(_notify(0xD0, payload))
    assert ev.kind == "file"
    assert ev.data["serial"] == 3 and ev.data["cluster"] == 77 and ev.data["total"] == 2
    assert ev.data["movement"] == 255 and ev.data["eye"] == 4 and ev.data["name"] == "Boo.mp3"


def test_parse_keepalive_and_unknown():
    assert p.parse(bytes.fromhex("FEDC0102EF")).kind == "keepalive"
    assert p.parse(_notify(0x42, b"")).kind == "unknown"
    assert p.parse(b"\x01") is None
