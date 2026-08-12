"""`python -m corpus` builds the corpus.

`corpus.build` is the module with the CLI; running `python -m corpus.build`
directly triggers a runpy re-import warning, so this thin entry point exists to
give `python -m corpus` a clean invocation.
"""

import sys

from corpus.build import main

sys.exit(main())
