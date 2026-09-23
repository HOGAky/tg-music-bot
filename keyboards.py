from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from callbacks import (FavCB, FavDelCB, LyricsCB, PlayAllCB, PlayCB, PlAddQCB, PlAddTrackCB,
                       PlDelCB, PlDelTrackCB, PlMenuCB, PlNewCB, PlSelCB, PlViewCB,
                       RadioCB, SetProtectCB, SetQualityCB)
from database.models import Playlist, Track, User
from database.repo import QUALITIES
from utils import short


def _label(t: Track) -> str:
    return short(f"{t.artist} — {t.title}" if t.artist else t.title, 48)


def results_kb(tracks: list[Track]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_label(t), callback_data=PlayCB(track_id=t.id).pack())]
        for t in tracks
    ])


def track_kb(track_id: int, in_fav: bool, share_query: str = "") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text="💔 Убрать из избранного" if in_fav else "❤️ В избранное",
            callback_data=FavCB(track_id=track_id).pack())],
        [
            InlineKeyboardButton(text="📝 Текст", callback_data=LyricsCB(track_id=track_id).pack()),
            InlineKeyboardButton(text="📻 Радио", callback_data=RadioCB(track_id=track_id).pack()),
        ],
        [InlineKeyboardButton(text="📁 Добавить в плейлист",
                              callback_data=PlSelCB(track_id=track_id).pack())],
    ]
    if share_query:
        rows.append([InlineKeyboardButton(text="↗️ Поделиться",
                                          switch_inline_query=short(share_query, 60))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_kb(user: User) -> InlineKeyboardMarkup:
    quality_row = [
        InlineKeyboardButton(text=("✅ " if user.quality == q else "") + f"{q} kbps",
                             callback_data=SetQualityCB(quality=q).pack())
        for q in QUALITIES
    ]
    protect = "🔒 Защита от пересылки: вкл" if user.protect_content else "🔓 Защита от пересылки: выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        quality_row,
        [InlineKeyboardButton(text=protect, callback_data=SetProtectCB().pack())],
    ])


def playlists_kb(playlists: list[Playlist]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"🎵 {p.name}",
                                  callback_data=PlViewCB(playlist_id=p.id).pack())]
            for p in playlists]
    rows.append([InlineKeyboardButton(text="➕ Новый плейлист", callback_data=PlNewCB().pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def playlist_kb(pl: Playlist) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="▶️ Слушать всё",
                                  callback_data=PlayAllCB(playlist_id=pl.id).pack())]]
    for t in pl.tracks:
        rows.append([
            InlineKeyboardButton(text=short(_label(t), 40), callback_data=PlayCB(track_id=t.id).pack()),
            InlineKeyboardButton(text="✖️", callback_data=PlDelTrackCB(playlist_id=pl.id, track_id=t.id).pack()),
        ])
    rows.append([
        InlineKeyboardButton(text="➕ Добавить трек", callback_data=PlAddQCB(playlist_id=pl.id).pack()),
        InlineKeyboardButton(text="🗑 Удалить плейлист", callback_data=PlDelCB(playlist_id=pl.id).pack()),
    ])
    rows.append([InlineKeyboardButton(text="⬅️ К плейлистам", callback_data=PlMenuCB().pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def playlists_select_kb(playlists: list[Playlist], track_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"🎵 {p.name}",
                                  callback_data=PlAddTrackCB(playlist_id=p.id, track_id=track_id).pack())]
            for p in playlists]
    rows.append([InlineKeyboardButton(text="➕ Создать новый плейлист",
                                      callback_data=PlNewCB(track_id=track_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_to_playlist_kb(playlist_id: int, tracks: list[Track]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=_label(t),
                                  callback_data=PlAddTrackCB(playlist_id=playlist_id, track_id=t.id).pack())]
            for t in tracks]
    rows.append([InlineKeyboardButton(text="⬅️ К плейлисту",
                                      callback_data=PlViewCB(playlist_id=playlist_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def favorites_kb(tracks: list[Track]) -> InlineKeyboardMarkup:
    rows = []
    for t in tracks:
        rows.append([
            InlineKeyboardButton(text=short(_label(t), 40), callback_data=PlayCB(track_id=t.id).pack()),
            InlineKeyboardButton(text="✖️", callback_data=FavDelCB(track_id=t.id).pack()),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_playlist_kb(playlist_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ К плейлисту", callback_data=PlViewCB(playlist_id=playlist_id).pack())
    ]])