import time
from dataclasses import dataclass, field

from database.models import Track


@dataclass
class NowPlaying:
    track_id: int
    title: str
    artist: str
    duration: int | None
    started_at: float = field(default_factory=time.monotonic)
    queue_name: str = ""
    position: int = 0
    queue_size: int = 0

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.started_at)


_now: dict[int, NowPlaying] = {}


def set_now_playing(user_id: int, track: Track, *, queue_name: str = "",
                    position: int = 0, queue_size: int = 0) -> None:
    _now[user_id] = NowPlaying(
        track_id=track.id, title=track.title, artist=track.artist,
        duration=track.duration, queue_name=queue_name,
        position=position, queue_size=queue_size,
    )


def get_now_playing(user_id: int) -> NowPlaying | None:
    return _now.get(user_id)


def clear(user_id: int) -> None:
    _now.pop(user_id, None)


def fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "--:--"
    return f"{seconds // 60}:{seconds % 60:02d}"
