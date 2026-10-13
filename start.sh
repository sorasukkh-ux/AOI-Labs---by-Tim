#!/usr/bin/env bash
# Prepares everything and starts AOI Labs:   ./start.sh
#   ./start.sh demo   run without MongoDB (sample data only, nothing is saved)
#   ./start.sh test   run the automated tests (also against MongoDB when it is reachable)
set -euo pipefail
cd "$(dirname "$0")"

# a Python that has pip: in some codespace terminals "python3" is the system one without pip
PY=""
for candidate in python3 /usr/local/python/current/bin/python3 python /usr/local/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -m pip --version >/dev/null 2>&1; then
    PY=$(command -v "$candidate"); break
  fi
done
if [ -z "$PY" ]; then
  echo "== pip is missing: installing it"
  sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip >/dev/null
  PY=$(command -v python3)
fi
echo "== Installing Python packages ($PY)"
"$PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  KEY=$("$PY" -c "import secrets; print(secrets.token_hex(32))")
  sed -i "s|^AOI_SECRET_KEY=.*|AOI_SECRET_KEY=${KEY}|" .env
  echo "== Created .env with a new secret key"
fi

if [ "${1:-}" = "demo" ]; then
  echo "== Demo mode: no database, nothing is saved"
  AOI_DB=memory exec "$PY" run.py
fi

mongo_ready() { "$PY" -m scripts.check_db >/dev/null 2>&1; }

start_mongo() {
  if command -v mongod >/dev/null 2>&1; then
    echo "== Starting the local MongoDB server"
    mkdir -p .mongo-data
    mongod --dbpath .mongo-data --bind_ip 127.0.0.1 --port 27017 --fork --logpath .mongo-data/mongod.log >/dev/null
  elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "== Starting MongoDB in Docker (container aoi-mongo, data kept in the volume aoi-mongo-data)"
    # MongoDB needs a high limit on open files. A container made without it is replaced; the data stays in the volume.
    if docker inspect aoi-mongo >/dev/null 2>&1; then
      limits=$(docker inspect aoi-mongo --format '{{range .HostConfig.Ulimits}}{{.Name}}={{.Soft}} {{end}}' 2>/dev/null || true)
      if [[ "$limits" != *"nofile=64000"* ]] && [ ! -f .mongo-no-ulimit ]; then
        docker rm -f aoi-mongo >/dev/null 2>&1 || true
      fi
    fi
    if ! docker start aoi-mongo >/dev/null 2>&1; then
      if ! docker run -d --name aoi-mongo --ulimit nofile=64000:64000 -p 127.0.0.1:27017:27017 \
             -v aoi-mongo-data:/data/db mongo:7 >/dev/null 2>&1; then
        # this Docker does not allow the higher limit: run with its default instead
        docker rm -f aoi-mongo >/dev/null 2>&1 || true
        touch .mongo-no-ulimit
        docker run -d --name aoi-mongo -p 127.0.0.1:27017:27017 -v aoi-mongo-data:/data/db mongo:7 >/dev/null
      fi
    fi
  else
    return 1
  fi
  for _ in $(seq 1 30); do
    if mongo_ready; then return 0; fi
    sleep 1
  done
  return 1
}

if [ "${1:-}" = "test" ]; then
  echo "== Running the tests on the in-memory database"
  "$PY" -m unittest discover -s tests
  if mongo_ready || start_mongo; then
    echo "== Running the same tests on MongoDB (in one throw-away database named aoi_test_...)"
    if ! AOI_TEST_MONGO=1 "$PY" -m unittest discover -s tests; then
      echo "== The MongoDB run failed. Last serious lines of the MongoDB log, if any:"
      docker logs aoi-mongo 2>&1 | grep -E 'Too many open files|rlimits|Fatal assertion|Invariant failure|Got signal' | tail -6 | cut -c1-300 || true
      exit 1
    fi
  else
    echo "== MongoDB is not reachable, so the MongoDB run was skipped"
  fi
  exit 0
fi

if ! mongo_ready; then
  if ! start_mongo; then
    echo
    echo "MongoDB could not be started here. Choose one:"
    echo "  1. In Codespaces: open the Command Palette, run 'Codespaces: Rebuild Container', then ./start.sh again."
    echo "  2. Use MongoDB Atlas: put its connection string in .env as AOI_MONGO_URI, then ./start.sh again."
    echo "  3. Try the screens without a database: ./start.sh demo"
    exit 1
  fi
fi
"$PY" -m scripts.check_db

echo "== Adding the starting data (only what is missing)"
"$PY" -m scripts.seed
echo "== Starting the server. Open the address shown below (port 5000)."
exec "$PY" run.py
