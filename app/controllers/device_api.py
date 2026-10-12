"""Device API: how machines and smart meters talk to the system (JSON over HTTPS).

Every call sends the device key in the header  X-Device-Key: <key>  (issued on Hardware Control or Electricity;
it is shown once). No browser session or CSRF token is used here.

    POST /api/v1/machines/<MACHINE_ID>/status      {"status": "R|I|F|M|O", "note": "..."}   report the status
    GET  /api/v1/machines/<MACHINE_ID>/commands                                           start/stop work to do
    POST /api/v1/commands/<CMD_ID>/ack              {"ok": true, "result": "..."}         command carried out
    POST /api/v1/meters/<METER_ID>/readings         {"kwh": 12345.6, "time": "2026-10-12T09:00:00+07:00"}

See scripts/device_sim.py for a working example.
"""
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from ..i18n import t
from ..models.automation import SOURCE_DEVICE, Machine, MachineCommand, Meter
from ..models.base import ValidationError

bp = Blueprint("device_api", __name__, url_prefix="/api/v1")


def _key():
    return request.headers.get("X-Device-Key", "")


def _error(error, status=422):
    return jsonify({"ok": False, "error": error.key, "message": t(error.key, **error.params)}), status


def _denied():
    return jsonify({"ok": False, "error": "unauthorized"}), 401


@bp.route("/machines/<machine_id>/status", methods=["POST"])
def machine_status(machine_id):
    machine = Machine.by_key(machine_id, _key())
    if not machine:
        return _denied()
    body = request.get_json(silent=True) or {}
    try:
        updated = Machine.set_status(machine, str(body.get("status", "")).upper(), SOURCE_DEVICE, "device:" + machine["MACHINE_ID"],
                                     note=body.get("note"))
    except ValidationError as error:
        return _error(error)
    return jsonify({"ok": True, "status": updated["MACHINE_STATUS"]})


@bp.route("/machines/<machine_id>/commands", methods=["GET"])
def machine_commands(machine_id):
    machine = Machine.by_key(machine_id, _key())
    if not machine:
        return _denied()
    Machine.seen(machine)
    return jsonify({"ok": True, "commands": [{"id": c["CMD_ID"], "command": c["CMD"], "requested": c["REQ_DATE"].isoformat()}
                                             for c in MachineCommand.take_pending(machine)]})


@bp.route("/commands/<cmd_id>/ack", methods=["POST"])
def command_ack(cmd_id):
    body = request.get_json(silent=True) or {}
    cmd = MachineCommand.get(cmd_id)
    machine = Machine.by_key(cmd["MACHINE_ID"], _key()) if cmd else None
    if not machine:
        return _denied()
    try:
        cmd = MachineCommand.acknowledge(machine, cmd_id.upper(), bool(body.get("ok")), body.get("result"))
    except ValidationError as error:
        return _error(error)
    return jsonify({"ok": True, "status": cmd["CMD_STATUS"]})


@bp.route("/meters/<meter_id>/readings", methods=["POST"])
def meter_reading(meter_id):
    meter = Meter.by_key(meter_id, _key())
    if not meter:
        return _denied()
    body = request.get_json(silent=True) or {}
    when = None
    if body.get("time"):
        try:
            when = datetime.fromisoformat(str(body["time"]))
        except ValueError:
            return jsonify({"ok": False, "error": "time"}), 422
        when = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    try:
        reading = Meter.add_reading(meter, body.get("kwh"), when, SOURCE_DEVICE, "device:" + meter["METER_ID"])
    except ValidationError as error:
        return _error(error)
    return jsonify({"ok": True, "id": reading["READ_ID"], "used_kwh": reading["USED_KWH"]})
