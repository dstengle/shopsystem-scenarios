"""Allow `python3 -m scenarios ...` to invoke the CLI."""
import sys

from scenarios.cli import main

if __name__ == "__main__":
    sys.exit(main())
