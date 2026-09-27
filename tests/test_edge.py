from baselinetrading.edge import main


def test_report_runs_on_the_committed_settings(capsys):
    assert main(["--price", "650"]) == 0
    output = capsys.readouterr().out
    assert "Breakeven win rate" in output
    assert "$20.00 (you)" in output


def test_report_refuses_a_bad_config(tmp_path, capsys):
    path = tmp_path / "settings.toml"
    path.write_text("[account]\nequity_cap_usd = 20.0\n")
    assert main(["--config", str(path)]) == 2
    assert "refusing to run" in capsys.readouterr().err
