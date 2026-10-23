"""Allow running the tool as `python -m satpass`."""

import sys

from .cli import main

sys.exit(main())
