"""
Test package.

The API and CLI resolve the database path from APTHUNT_DB_PATH when
``apthunt.db`` is first imported, so point it at a throwaway file before
any test module loads. Run from the repository root:

    python3 -m unittest discover -s tests -t . -v
"""

import atexit
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="apthunt-tests-")
os.environ["APTHUNT_DB_PATH"] = os.path.join(_TMP, "apthunt.db")
os.environ.pop("APTHUNT_REQUIRE_LISTINGS", None)
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
