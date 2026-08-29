from aiq_lean_tools.cli import build_parser


def test_final_port_commands_are_publicly_parseable():
    parser = build_parser()
    cases = [
        ["signatures", "check", "policy.yaml", "--no-build"],
        ["foundations", "validate", "foundations.yaml"],
        ["foundations", "html", "foundations.yaml", "-o", "out.html"],
        ["literature", "validate", "literature.yaml"],
        ["literature", "patch", "literature.yaml", "--id", "Paper", "--set", "distilled_status=complete"],
        ["source", "staging", "staging.yaml"],
        ["source", "export", "export.yaml", "--target-root", "/tmp/upstream"],
        ["source", "module-plan", "module-plan.yaml", "--render"],
        ["certify", "build", "certificate.yaml", "--out", "build/cert"],
    ]
    for argv in cases:
        args = parser.parse_args(argv)
        assert callable(args.func)
