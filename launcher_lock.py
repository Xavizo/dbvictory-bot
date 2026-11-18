"""Shared Windows lock for file promotion and the standalone session."""
from contextlib import contextmanager


@contextmanager
def launcher_lock(path):
    """The OS releases the lock on exit or crash. Keep the lockfile in place."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Inna sesja lub aktualizacja bota jest już uruchomiona.") from error
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
