import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="nbi-test-")
os.environ["NBI_DATABASE_URL"] = os.environ.get("NBI_TEST_DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ["NBI_DATA_DIR"] = _tmp
os.environ["NBI_ODDS_API_KEY"] = ""
os.environ["NBI_API_TOKEN"] = ""

import pytest  # noqa: E402

from app import db as dbm  # noqa: E402


@pytest.fixture()
def db():
    """Fresh empty database per test."""
    dbm.reset_engine()
    dbm.Base.metadata.drop_all(dbm.get_engine())
    dbm.init_db()
    with dbm.session_scope() as s:
        yield s
    for f in ("correlations.json",):
        p = os.path.join(_tmp, "artifacts", f)
        if os.path.exists(p):
            os.remove(p)
