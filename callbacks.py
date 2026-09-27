from aiogram.filters.callback_data import CallbackData


class PlayCB(CallbackData, prefix="p"):
    track_id: int


class FavCB(CallbackData, prefix="f"):
    track_id: int


class FavDelCB(CallbackData, prefix="fd"):
    track_id: int


class PlMenuCB(CallbackData, prefix="plm"):
    pass


class PlNewCB(CallbackData, prefix="pln"):
    track_id: int = 0  # если != 0 — сразу добавить этот трек после создания


class PlViewCB(CallbackData, prefix="plv"):
    playlist_id: int


class PlSelCB(CallbackData, prefix="pls"):
    track_id: int


class PlAddQCB(CallbackData, prefix="plaq"):
    playlist_id: int


class PlAddTrackCB(CallbackData, prefix="pla"):
    playlist_id: int
    track_id: int


class PlDelCB(CallbackData, prefix="pld"):
    playlist_id: int


class PlDelTrackCB(CallbackData, prefix="pldt"):
    playlist_id: int
    track_id: int


class PlayAllCB(CallbackData, prefix="pa"):
    playlist_id: int


class LyricsCB(CallbackData, prefix="ly"):
    track_id: int


class RadioCB(CallbackData, prefix="rad"):
    track_id: int


class SetQualityCB(CallbackData, prefix="sq"):
    quality: int


class SetProtectCB(CallbackData, prefix="sp"):
    pass


class DlAllCB(CallbackData, prefix="dla"):
    key: str


class DlStopCB(CallbackData, prefix="dls"):
    key: str