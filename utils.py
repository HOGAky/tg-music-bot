from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message


def msg_of(callback: CallbackQuery) -> Message | None:
    """callback.message может быть InaccessibleMessage — тогда edit невозможен."""
    return callback.message if isinstance(callback.message, Message) else None


async def safe_edit(msg: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            await msg.answer(text, reply_markup=reply_markup)
    except Exception:
        await msg.answer(text, reply_markup=reply_markup)


def short(s: str, n: int = 48) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"