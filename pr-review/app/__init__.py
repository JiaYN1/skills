"""PR review service package."""

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - allows importing parser modules without runtime deps.
    load_dotenv = None

if load_dotenv:
    load_dotenv()

