"""Разбор STL и статистики PrusaSlicer без запуска тяжёлого бинарника."""

import struct

import pytest

from app.services import slicer


def binary_stl() -> bytes:
    triangle = struct.pack(
        "<12fH",
        0,
        0,
        1,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        1,
        0,
        0,
    )
    return b"binary cube".ljust(80, b"\0") + struct.pack("<I", 1) + triangle


def test_accepts_binary_stl():
    slicer.validate_stl(binary_stl())


def test_accepts_ascii_stl():
    slicer.validate_stl(
        b"solid cube\nfacet normal 0 0 1\nouter loop\nendloop\nendfacet\nendsolid cube\n"
    )


@pytest.mark.parametrize("data", [b"", b"not an stl", b"PK\x03\x04renamed zip"])
def test_refuses_non_stl(data):
    with pytest.raises(slicer.SlicerError):
        slicer.validate_stl(data)


def test_parses_prusaslicer_statistics():
    result = slicer.parse_gcode(
        b"; estimated printing time (normal mode) = 1h 4m 41s\r\n"
        b"; filament used [mm] = 4521.7\r\n"
        b"; filament used [g] = 13.37\r\n",
        layer_height=0.2,
        infill_percent=15,
    )

    assert result.seconds == 3881
    assert result.filament_mm == 4521.7
    assert result.filament_g == 13.37


def test_refuses_unknown_options():
    with pytest.raises(slicer.SlicerError):
        slicer.validate_options(0.13, 99)
