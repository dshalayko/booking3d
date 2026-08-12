"""Проводка aiogram.

Long polling, а не вебхук: серверу тогда не нужны ни входящие соединения, ни
проброс портов — он сам ходит наружу. При двух принтерах лишний опрос ничего не
стоит, а точек отказа на одну меньше.

Внимание: с одним токеном может работать только один процесс polling. Для
локальной разработки заводи отдельного тестового бота, иначе Telegram ответит
`Conflict: terminated by other getUpdates`.
"""

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand, Message

from app import texts as t
from app.bot import commands, notify, texts
from app.config import settings
from app.db import SessionLocal
from app.enums import MachineKind

logger = logging.getLogger(__name__)

dispatcher = Dispatcher()

BOT_COMMANDS = [
    BotCommand(command=command, description=description)
    for command, description in t.BOT_COMMAND_DESCRIPTIONS.items()
]

# Очередь у каждого типа своя, поэтому и команда своя: в меню Telegram человек
# видит «очередь на принтер» и «очередь на гравировщик» и не гадает, куда его
# поставит безымянный /queue.
QUEUE_COMMANDS = {f"queue_{kind.value}": kind.value for kind in MachineKind}


@dispatcher.message(CommandStart())
async def handle_start(message: Message) -> None:
    async with SessionLocal() as db:
        await message.answer(await commands.start(db, message.chat.id))


@dispatcher.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.answer(texts.HELP)


@dispatcher.message(Command("status"))
async def handle_status(message: Message) -> None:
    async with SessionLocal() as db:
        await message.answer(await commands.status(db))


@dispatcher.message(Command("my"))
async def handle_my(message: Message) -> None:
    async with SessionLocal() as db:
        await message.answer(await commands.my(db, message.chat.id))


@dispatcher.message(Command(*QUEUE_COMMANDS))
async def handle_queue_kind(message: Message, command: CommandObject) -> None:
    async with SessionLocal() as db:
        kind = QUEUE_COMMANDS[command.command]
        await message.answer(await commands.queue_join(db, message.chat.id, kind))


@dispatcher.message(Command("queue"))
async def handle_queue(message: Message) -> None:
    """Без типа: сработает, только пока парк однороден, иначе спросит какой."""
    async with SessionLocal() as db:
        await message.answer(await commands.queue_join(db, message.chat.id))


@dispatcher.message(Command("leave"))
async def handle_leave(message: Message) -> None:
    async with SessionLocal() as db:
        await message.answer(await commands.queue_leave(db, message.chat.id))


@dispatcher.message(Command("free"))
async def handle_free(message: Message) -> None:
    async with SessionLocal() as db:
        await message.answer(await commands.free(db, message.chat.id))


@dispatcher.message(Command("pin"))
async def handle_pin(message: Message) -> None:
    async with SessionLocal() as db:
        await message.answer(await commands.new_pin(db, message.chat.id))


@dispatcher.message()
async def handle_anything_else(message: Message) -> None:
    """Второй шаг регистрации живёт здесь: логин приходит обычным сообщением."""
    async with SessionLocal() as db:
        await message.answer(await commands.text_message(db, message.chat.id, message.text or ""))


def build_bot() -> Bot:
    return Bot(
        token=settings.tg_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def attach_notifier(bot: Bot) -> None:
    """Подключить отправку уведомлений.

    Вызывается синхронно до старта polling: догоняющая сверка на запуске
    отправляет сообщения сразу, и если сделать это внутри `start_polling`,
    первые уведомления после рестарта молча пропадут — задача с polling к тому
    моменту ещё не успеет выполниться.
    """
    notify.set_sender(lambda chat_id, text: bot.send_message(chat_id, text))


async def start_polling(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
    logger.info("бот запущен")
    await dispatcher.start_polling(bot, handle_signals=False)
