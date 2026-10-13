"""Database connection. The models reach MongoDB only through get_db() / col()."""
from flask import current_app

from . import memory_db

# Exceptions raised when a unique key already exists, whichever backend is in use.
DuplicateKey = (memory_db.DuplicateKeyError,)
AFTER = True                                    # value of pymongo.ReturnDocument.AFTER
try:                                            # PyMongo is not needed for the memory backend
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError as _MongoDuplicateKeyError
    DuplicateKey = DuplicateKey + (_MongoDuplicateKeyError,)
    AFTER = ReturnDocument.AFTER
except ImportError:                             # pragma: no cover
    pass


class DatabaseUnavailable(RuntimeError):
    pass


def connect(config):
    """Returns a database handle for the configured backend."""
    backend = config["DB_BACKEND"]
    if backend == "memory":
        return memory_db.MemoryDatabase()
    if backend != "mongodb":
        raise DatabaseUnavailable("AOI_DB must be 'mongodb' or 'memory', not %r" % backend)
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise DatabaseUnavailable("PyMongo is not installed. Run: pip install -r requirements.txt") from exc
    client = MongoClient(config["MONGO_URI"], serverSelectionTimeoutMS=5000, tz_aware=True, appname="aoi-labs")
    try:
        client.admin.command("ping")
    except Exception as exc:
        raise DatabaseUnavailable(
            "Cannot reach MongoDB at the address in AOI_MONGO_URI. Start MongoDB (./start.sh does this) "
            "or set AOI_MONGO_URI in .env. Details: %s" % exc) from exc
    return client[config["MONGO_DB"]]


def init_db(app):
    app.extensions["aoi_db"] = connect(app.config)


def get_db():
    return current_app.extensions["aoi_db"]


def col(name):
    return get_db()[name]
