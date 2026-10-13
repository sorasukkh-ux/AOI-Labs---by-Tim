"""Checks that the configured MongoDB can be reached:  python -m scripts.check_db"""
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def main():
    from app.config import Config
    try:
        from pymongo import MongoClient
    except ImportError:
        print("PyMongo is not installed. Run: pip install -r requirements.txt")
        return 2
    client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=3000)
    try:
        version = client.server_info().get("version", "?")
    except Exception as error:
        print("MongoDB is not reachable: %s" % str(error).splitlines()[0][:200])
        return 1
    print("MongoDB %s is reachable. Database name: %s" % (version, Config.MONGO_DB))
    return 0


if __name__ == "__main__":
    sys.exit(main())
