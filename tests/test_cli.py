from gmail_bot.cli import build_parser


def test_cli_inbox_once() -> None:
    args = build_parser().parse_args(["inbox", "--once"])
    assert args.command == "inbox"
    assert args.once is True


def test_cli_auth_email() -> None:
    args = build_parser().parse_args(["auth", "--email", "ada@example.com"])
    assert args.command == "auth"
    assert args.email == "ada@example.com"
