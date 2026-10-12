"""Starts the development server:  python run.py   (or ./start.sh, which also prepares MongoDB)."""
import os
import sys

from app import create_app, DatabaseUnavailable

if __name__ == "__main__":
    try:
        app = create_app()
    except DatabaseUnavailable as error:
        print("ERROR: %s" % error)
        sys.exit(1)
    app.run(host=os.environ.get("AOI_HOST", "127.0.0.1"), port=int(os.environ.get("AOI_PORT", "5000")),
            debug=os.environ.get("AOI_DEBUG", "0") == "1")
