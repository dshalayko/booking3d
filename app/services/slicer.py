"""Оценка времени FFF-печати через PrusaSlicer CLI.

Это намеренно не генератор готового файла для принтера. Пользователь получает
время и расход пластика для планирования брони, а G-code удаляется вместе с
исходным STL сразу после расчёта.

Профиль пока один, базовый: PLA, сопло 0.4 мм, стол 220x220 мм. Параметры слоя
и заполнения приходят из закрытого списка формы. Когда модели реальных машин
будут известны, профиль нужно заменить их экспортированными полными INI.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app import texts as t
from app.config import settings

logger = logging.getLogger(__name__)

PROFILE = Path(__file__).resolve().parents[1] / "slicer_profiles" / "generic_pla_0.4.ini"
ALLOWED_LAYER_HEIGHTS = (0.15, 0.20, 0.28)
ALLOWED_INFILL_PERCENTS = (10, 15, 20)

# PrusaSlicer заметно грузит CPU. Для небольшого парка одна очередь честнее,
# чем несколько одновременных процессов, которые замедлят и бот, и друг друга.
_SLICER_SLOT = asyncio.Semaphore(1)

_TIME_LINE = re.compile(
    rb"^; estimated printing time \(normal mode\) =\s*([^\r\n]+)\r?$", re.MULTILINE
)
_FILAMENT_MM = re.compile(rb"^; filament used \[mm\] =\s*([0-9.]+)\r?$", re.MULTILINE)
_FILAMENT_G = re.compile(
    rb"^; (?:total )?filament used \[g\] =\s*([0-9.]+)\r?$", re.MULTILINE
)
_TIME_TOKEN = re.compile(r"(\d+)\s*([dhms])")


class SlicerError(RuntimeError):
    """Понятный пользователю отказ расчёта."""


@dataclass(frozen=True)
class SliceEstimate:
    seconds: int
    display_time: str
    filament_mm: float
    filament_g: float | None
    layer_height: float
    infill_percent: int


def validate_options(layer_height: float, infill_percent: int) -> None:
    if layer_height not in ALLOWED_LAYER_HEIGHTS:
        raise SlicerError(t.ERR_SLICER_OPTIONS)
    if infill_percent not in ALLOWED_INFILL_PERCENTS:
        raise SlicerError(t.ERR_SLICER_OPTIONS)


def validate_stl(data: bytes) -> None:
    """Отсеять пустые, переименованные и явно битые файлы до запуска CLI."""
    if not data:
        raise SlicerError(t.ERR_SLICER_EMPTY)

    # Бинарный STL: 80 байт заголовка, uint32 числа треугольников и ровно по
    # 50 байт на треугольник. Так ZIP или произвольный файл не пройдут только
    # потому, что их переименовали в .stl.
    if len(data) >= 84:
        triangles = struct.unpack_from("<I", data, 80)[0]
        if triangles > 0 and len(data) == 84 + triangles * 50:
            return

    # ASCII STL. Не декодируем весь многомегабайтный файл: для допуска нужны
    # маркеры начала, хотя бы одна грань и закрывающий endsolid.
    head = data[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    tail = data[-4096:].lower()
    if head.startswith(b"solid") and b"facet" in data[:65536].lower() and b"endsolid" in tail:
        return

    raise SlicerError(t.ERR_SLICER_INVALID_STL)


def _seconds(value: str) -> int:
    factors = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    tokens = _TIME_TOKEN.findall(value)
    if not tokens:
        raise SlicerError(t.ERR_SLICER_NO_ESTIMATE)
    return sum(int(amount) * factors[unit] for amount, unit in tokens)


def parse_gcode(data: bytes, layer_height: float, infill_percent: int) -> SliceEstimate:
    time_match = _TIME_LINE.search(data)
    filament_match = _FILAMENT_MM.search(data)
    if time_match is None or filament_match is None:
        raise SlicerError(t.ERR_SLICER_NO_ESTIMATE)

    raw_time = time_match.group(1).decode("ascii", errors="strict").strip()
    grams_match = _FILAMENT_G.search(data)
    return SliceEstimate(
        seconds=_seconds(raw_time),
        display_time=raw_time,
        filament_mm=float(filament_match.group(1)),
        filament_g=float(grams_match.group(1)) if grams_match else None,
        layer_height=layer_height,
        infill_percent=infill_percent,
    )


async def estimate_stl(
    data: bytes, *, layer_height: float = 0.20, infill_percent: int = 15
) -> SliceEstimate:
    validate_options(layer_height, infill_percent)
    validate_stl(data)

    executable = shutil.which(settings.prusa_slicer_bin)
    if executable is None:
        raise SlicerError(t.ERR_SLICER_UNAVAILABLE)

    with tempfile.TemporaryDirectory(prefix="booking-slice-") as directory:
        work = Path(directory)
        source = work / "model.stl"
        output = work / "estimate.gcode"
        source.write_bytes(data)

        command = (
            executable,
            "--load",
            str(PROFILE),
            "--layer-height",
            f"{layer_height:.2f}",
            "--fill-density",
            f"{infill_percent}%",
            "--export-gcode",
            "--output",
            str(output),
            str(source),
        )

        async with _SLICER_SLOT:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=settings.slicer_timeout_seconds
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                logger.warning("PrusaSlicer превысил таймаут %s с", settings.slicer_timeout_seconds)
                raise SlicerError(t.ERR_SLICER_TIMEOUT) from exc

        if process.returncode != 0 or not output.is_file():
            # Подробности остаются в цепочке исключения/логах сервера; пути из
            # временной директории и вывод CLI пользователю не показываем.
            detail = stderr.decode("utf-8", errors="replace")[-2000:]
            logger.warning("PrusaSlicer завершился с кодом %s: %s", process.returncode, detail)
            raise SlicerError(t.ERR_SLICER_FAILED) from RuntimeError(detail)

        return parse_gcode(output.read_bytes(), layer_height, infill_percent)
