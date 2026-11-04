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
