"""Allow ``python -m kicadgen ...``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
