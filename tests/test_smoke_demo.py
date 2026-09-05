from scripts.smoke_demo import main


def test_complete_offline_demo_smoke(capsys):
    assert main() == 0
    output = capsys.readouterr().out
    assert '"audit_append": "PASS"' in output
    assert '"method": "FALLBACK"' in output
    assert '"amount_difference": "25000.00"' in output
