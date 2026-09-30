from types import SimpleNamespace

from backend import serial_io


def test_candidate_ports_prefer_configured_then_usb(monkeypatch):
    ports = [
        SimpleNamespace(device="COM8", description="Bluetooth", vid=None),
        SimpleNamespace(device="COM4", description="USB-SERIAL CH340", vid=0x1A86),
        SimpleNamespace(device="COM9", description="Bluetooth", vid=None),
    ]
    monkeypatch.setattr(serial_io.list_ports, "comports", lambda: ports)
    monkeypatch.setattr(serial_io, "SERIAL_PORT", "COM9")
    assert serial_io._candidate_ports() == ["COM9", "COM4", "COM8"]


def test_probe_claims_only_recognizable_frame(monkeypatch):
    class FakeSerial:
        def __init__(self, *_args, **_kwargs):
            self.lines = iter([
                b"boot message\n",
                b'{"temp":26,"humi":50,"mq135_adc":20,"mq135_mv":18,"motion":null,"sound_db":46}\n',
            ])
            self.closed = False

        def readline(self):
            return next(self.lines, b"")

        def close(self):
            self.closed = True

    monkeypatch.setattr(serial_io.serial, "Serial", FakeSerial)
    connection, line = serial_io._probe_port("COM4")
    assert connection is not None
    assert line.startswith('{"temp":26')
    assert not connection.closed
