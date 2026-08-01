"""Launch the app straight from a source checkout: ``python run.py``."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "src"))

from dupvideo.app import main  # noqa: E402  (needs the path fix above)

if __name__ == "__main__":
    main()
