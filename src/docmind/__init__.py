import os

from docmind.app import create_app

__all__ = ["create_app", "main"]

# 5000 is taken by AirPlay Receiver on macOS, so default to 8000.
DEFAULT_PORT = 8000


def main() -> None:
    """Run the development server."""
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", DEFAULT_PORT)), debug=True)
