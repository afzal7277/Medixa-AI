"""
Root conftest — shared helpers and project-level sys.path setup.
Heavy dependency mocking lives in tests/unit/conftest.py.

IMPORTANT: fakeredis must be imported HERE (before unit/conftest.py runs) so
that FakeRedis's class body executes while redis.Redis is still the real class.
Unit conftest patches redis.Redis = MagicMock(), and if fakeredis hasn't built
its FakeRedis class yet at that point, the later `import fakeredis` in
integration/conftest.py fails with a metaclass conflict.
"""
import os
import sys
import importlib.util

# Pre-import fakeredis so FakeRedis(FakeRedisMixin, redis.Redis) is evaluated
# before unit/conftest.py replaces redis.Redis with a MagicMock.
try:
    import fakeredis  # noqa: F401
except ImportError:
    pass  # not installed — integration tests will skip gracefully

# ── Project root ───────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load_service(alias: str, rel_path: str):
    """Load a service module under a unique alias to avoid namespace collisions
    when multiple services share the same filename (e.g. main.py)."""
    full_path = os.path.join(PROJECT_ROOT, rel_path)
    spec = importlib.util.spec_from_file_location(alias, full_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod
