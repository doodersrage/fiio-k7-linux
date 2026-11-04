import pytest

from k7ctl import cli


def test_parser_commands():
    p = cli.build_parser()
    assert p.parse_args(["rates", "enable"]).action == "enable"
    assert p.parse_args(["rates"]).action == "status"
    assert p.parse_args(["status", "--json"]).json
    with pytest.raises(SystemExit):
        p.parse_args(["rates", "bogus"])


def test_no_k7(monkeypatch, capsys):
    monkeypatch.setattr(cli.device, "find", lambda: None)
    monkeypatch.setattr(cli.rates, "live_allowed_rates", lambda: [48000])
    assert cli.main(["status"]) == 1
    assert "No FiiO K7" in capsys.readouterr().out
    assert cli.main(["firmware"]) == 1


def test_gui_without_qt(monkeypatch, capsys):
    import builtins
    real_import = builtins.__import__

    def no_qt(name, *a, **k):
        if name in ("PyQt6", "PySide6") or name.startswith(("PyQt6.", "PySide6.")):
            raise ImportError(name)
        return real_import(name, *a, **k)
    import sys
    for m in [m for m in sys.modules if m.startswith(("k7ctl.qt", "k7ctl.window", "k7ctl.tray"))]:
        monkeypatch.delitem(sys.modules, m)
    monkeypatch.setattr(builtins, "__import__", no_qt)
    assert cli.main(["gui"]) == 1
    assert "need PyQt6 or PySide6" in capsys.readouterr().err
