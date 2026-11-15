#!/usr/bin/python3
import sys

sys.path.insert(0, "/app/share/perch")

from perch.main import main  # noqa: E402

sys.exit(main(sys.argv))
