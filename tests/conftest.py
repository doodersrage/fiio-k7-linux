import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class SyncWorker:
    """Stands in for qt.Worker: runs jobs immediately on the calling thread."""

    busy = False

    class _Sig:
        def connect(self, *_):
            pass

    busy_changed = _Sig()

    def __init__(self):
        self.jobs = []

    def run(self, fn, on_done=None):
        self.jobs.append(fn)
        try:
            r = fn()
        except Exception as e:
            r = e
        if on_done:
            on_done(r)

    def shutdown(self):
        pass


@pytest.fixture(autouse=True)
def _delete_qt_widgets():
    """Destroy every window a test made, right after it, instead of whenever the
    garbage collector gets to it (which crashed PyQt on Python 3.11 in CI)."""
    yield
    try:
        from k7ctl.qt import QtWidgets
    except ImportError:
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        shutdown = getattr(w, "shutdown", None)
        if callable(shutdown):
            shutdown()
        w.blockSignals(True)
        w.close()
        w.deleteLater()
    from k7ctl.qt import QtCore
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()
    import gc
    gc.collect()


@pytest.fixture
def sync_worker():
    return SyncWorker()


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path
