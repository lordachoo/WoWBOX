"""PyInstaller entry point (the package's __main__ uses relative imports, so it can't be the script itself)."""
import sys

from wowbox.__main__ import main

sys.exit(main())
