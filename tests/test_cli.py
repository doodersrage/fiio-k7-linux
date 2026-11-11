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


def test_diag_report_runs(monkeypatch, xdg):
    from k7ctl import diagnostics, snapshot
    monkeypatch.setattr(diagnostics.device, "find", lambda *a, **k: None)
    monkeypatch.setattr(diagnostics.device, "list_usb_cards", lambda: [])
    monkeypatch.setattr(diagnostics.snapshot, "take", lambda *a, **k: snapshot.Snapshot(
        None, None, None, None, None, None, False, None, False))
    monkeypatch.setattr(diagnostics, "_run", lambda args, limit=4000: "(stub)")
    text = diagnostics.report()
    for section in ("## k7ctl", "## USB sound cards", "## Device", "## PipeWire", "## EQ service"):
        assert section in text
    assert "not connected" in text


def test_exclusive_auto_cli(xdg, monkeypatch, capsys):
    from k7ctl import cli, exclusive
    got = []
    monkeypatch.setattr(exclusive, "set_auto_players", lambda p: got.append(p) or p)
    assert cli.main(["exclusive", "auto", "strawberry,", "mpd"]) == 0
    assert got == [["strawberry", "mpd"]]
    assert cli.main(["exclusive", "auto", "off"]) == 0
    assert got[-1] == []
    assert cli.main(["exclusive", "on", "mpd"]) == 1
    assert "after 'auto'" in capsys.readouterr().err
    monkeypatch.setattr(exclusive, "auto_players", lambda: [])
    assert cli.main(["exclusive", "auto"]) == 0
    assert "off" in capsys.readouterr().out
