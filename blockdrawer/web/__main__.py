"""Launch the browser editor: ``python -m blockdrawer.web [SESSION]``."""

from .server import main


if __name__ == "__main__":
    raise SystemExit(main())
