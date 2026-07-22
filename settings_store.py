from dataclasses import asdict, dataclass


@dataclass
class AppSettings:
    default_cover_path: str = ""
    chunk_size_mb: int = 100
    upload_workers: int = 3
    stego_chunks: bool = True


def _normalize_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


class SettingsStore:
    def __init__(self, base_dir=None):
        self._settings = AppSettings()
        self._history = []

    def load_settings(self):
        return AppSettings(
            default_cover_path=self._settings.default_cover_path,
            chunk_size_mb=_normalize_int(self._settings.chunk_size_mb, 100, 5, 200),
            upload_workers=_normalize_int(self._settings.upload_workers, 3, 1, 8),
            stego_chunks=self._settings.stego_chunks,
        )

    def save_settings(self, settings):
        self._settings = AppSettings(
            default_cover_path=settings.default_cover_path,
            chunk_size_mb=settings.chunk_size_mb,
            upload_workers=settings.upload_workers,
            stego_chunks=settings.stego_chunks,
        )

    def load_upload_history(self):
        return list(self._history)

    def save_upload_history(self, history):
        self._history = list(history)[:200]

    def add_upload_history(self, record):
        self._history.insert(0, record)
        self._history = self._history[:200]
