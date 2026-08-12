"""Языки не должны расходиться.

Перевод ломается не тем, что фраза кривая — это видно глазами. Ломается тем,
что в одном языке имя есть, а в другом нет (тогда падает импорт или экран), или
что в переведённой строке потерялся `{printer}` — и человек получает сообщение
без имени принтера. И то, и другое всплывает в бою, поэтому проверяется здесь.
"""

import re
from string import Formatter

import pytest

from app.enums import MachineKind
from app.texts import en, ru

LANGUAGES = {"ru": ru, "en": en}
PUBLIC = {name for name in vars(ru) if not name.startswith("_") and name.isupper()}

# Словари, где обязан быть ключ на каждый тип оборудования.
KIND_DICTS = ("MACHINE_KIND_TITLE", "MACHINE_KIND_ONE", "MACHINE_BUSY_WORD")


def placeholders(value: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(value) if name}


def strings_of(module) -> dict[str, str]:
    """Все строки языка с путём до них: `BOT_HELP`, `UI[tile_free]`."""
    found = {}
    for name in PUBLIC:
        value = getattr(module, name, None)
        if isinstance(value, str):
            found[name] = value
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str):
                    found[f"{name}[{key}]"] = item
    return found


def test_languages_have_the_same_names():
    assert {name for name in vars(en) if name.isupper()} >= PUBLIC


@pytest.mark.parametrize("lang", list(LANGUAGES))
@pytest.mark.parametrize("dict_name", KIND_DICTS)
def test_every_kind_of_machine_has_words(lang, dict_name):
    """Новый тип в `MachineKind` без надписей — это `engraver` на стене.

    Молча и до тех пор, пока кто-нибудь не заметит: код везде подставляет
    значение по умолчанию, то есть саму строку enum.
    """
    words = getattr(LANGUAGES[lang], dict_name)

    assert set(words) == set(MachineKind), f"{lang}: {dict_name} не покрывает все типы"


@pytest.mark.parametrize("lang", ["en"])
def test_placeholders_survive_translation(lang):
    origin = strings_of(ru)
    translated = strings_of(LANGUAGES[lang])

    assert set(translated) == set(origin), "разошёлся набор ключей внутри словарей"

    for path, value in origin.items():
        assert placeholders(translated[path]) == placeholders(value), (
            f"{lang}: в {path} потерялись или добавились плейсхолдеры"
        )


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_every_string_formats(lang):
    """Лишняя фигурная скобка в переводе — это ошибка на экране, а не опечатка."""
    for path, value in strings_of(LANGUAGES[lang]).items():
        try:
            value.format(**dict.fromkeys(placeholders(value), "x"))
        except (KeyError, IndexError, ValueError) as error:
            pytest.fail(f"{lang}: {path} не форматируется — {error}")


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_no_stray_cyrillic_in_english(lang):
    if lang == "ru":
        pytest.skip("русский файл на то и русский")
    stray = {path: value for path, value in strings_of(LANGUAGES[lang]).items()
             if re.search(r"[А-Яа-яЁё]", value)}
    assert not stray, f"непереведённое: {stray}"


# Только теги, которые понимает Telegram. Иначе проверка ловит `<tg_id>` из
# подсказки к команде CLI — угловые скобки там значат совсем другое.
TAGS = ("b", "i", "u", "s", "a", "code", "pre")


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_html_tags_match(lang):
    """Бот шлёт HTML: незакрытый <b> Telegram отклонит сообщение целиком."""
    pattern = re.compile(rf"</?({'|'.join(TAGS)})>")
    for path, value in strings_of(LANGUAGES[lang]).items():
        opened = [m.group(1) for m in pattern.finditer(value) if not m.group(0).startswith("</")]
        closed = [m.group(1) for m in pattern.finditer(value) if m.group(0).startswith("</")]
        assert sorted(opened) == sorted(closed), f"{lang}: несогласованные теги в {path}"
