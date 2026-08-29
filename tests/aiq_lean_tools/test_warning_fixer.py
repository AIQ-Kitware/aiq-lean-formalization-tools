from aiq_lean_tools.warning_fixer import parse_diagnostics


def test_warning_parser():
    rows = parse_diagnostics(
        "warning: Lib/Test.lean:10:3: `old` has been deprecated: Use `new` instead\n"
        "\n"
        "warning: Lib/Test.lean:20:5: This simp argument is unused:\n"
        "foo\n"
    )
    assert len(rows) == 2
    assert rows[0].file == "Lib/Test.lean"
    assert rows[1].line == 20
