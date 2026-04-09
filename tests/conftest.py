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



# Cache modules by file path so the same service is never executed twice.
# This prevents duplicate Prometheus metric registration when unit and
# integration tests both load the same service file under different aliases.
_service_cache: dict[str, object] = {}


def load_service(alias: str, rel_path: str):
    """Load a service module under a unique alias to avoid namespace collisions
    when multiple services share the same filename (e.g. main.py).

    The service directory is temporarily added to sys.path so that relative
    sibling imports (e.g. `from rag import RAGService` inside genai-service/)
    resolve correctly without polluting the path permanently.

    Subsequent calls with the same rel_path return the cached module under
    the new alias — module-level code (incl. Prometheus metric registration)
    is only executed once per test session.
    """
    if rel_path in _service_cache:
        mod = _service_cache[rel_path]
        sys.modules[alias] = mod
        return mod

    full_path = os.path.join(PROJECT_ROOT, rel_path)
    service_dir = os.path.dirname(full_path)
    injected = service_dir not in sys.path
    if injected:
        sys.path.insert(0, service_dir)
    try:
        spec = importlib.util.spec_from_file_location(alias, full_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[alias] = mod
        spec.loader.exec_module(mod)
    finally:
        if injected:
            sys.path.remove(service_dir)

    _service_cache[rel_path] = mod
    return mod
