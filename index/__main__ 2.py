"""`python -m index` syncs the corpus into Qdrant."""

import sys

from index.sync import main

sys.exit(main())
