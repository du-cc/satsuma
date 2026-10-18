import sys


def main() -> int:
    from .cli import COMMANDS
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        from .cli import main as cli_main
        return cli_main(sys.argv[1:])
    from .app import run_gui
    return run_gui(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
