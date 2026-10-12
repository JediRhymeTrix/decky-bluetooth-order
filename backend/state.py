import asyncio
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from collections import deque
from contextlib import suppress

DEFAULT_SETTINGS = {
    "mode": "recent", "connectedFirst": True,
    "qam": False, "settings": False, "debug": False,
}
LIMIT = 512
MAX_INT = 2**53 - 1
RETRY_DELAYS = (0.1, 0.3)
MAC = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")
EVENTS = frozenset((
    "frontend-start", "frontend-stop", "backend-unavailable", "snapshot-rejected",
    "native-cache-attached", "surface-status", "settings-updated", "history-reset",
    "native-adapters-unavailable", "row-order", "render-duration", "adapter-found",
    "adapter-missing", "patch-error", "reorder", "snapshot", "settings", "reset",
    "native-adapter-fault", "native-unpatch-failed", "qam-renderer-attached",
))
STATUSES = frozenset((
    "ok", "unavailable", "error", "attached", "missing", "enabled", "disabled",
    "off", "ready", "disabled after error", "reopen required", "unsupported layout",
    "unsupported layout; native order", "waiting for native device cache",
    "active; order held until reopen", "active", "ready; reopen Bluetooth Settings",
    "adapter unavailable; native order", "ready; reopen native menu",
    "ready; reopen Quick Access", "pending native mount; hardware gate",
))


def normalize_mac(value):
    return value.upper() if type(value) is str and MAC.fullmatch(value) else None


def validate_settings(partial):
    if type(partial) is not dict or set(partial) - DEFAULT_SETTINGS.keys():
        raise ValueError("Invalid settings keys")
    for key, value in partial.items():
        if key == "mode":
            if type(value) is not str or value not in ("recent", "native", "az", "za"):
                raise ValueError("Invalid sort mode")
        elif type(value) is not bool:
            raise ValueError("Settings flags must be booleans")
    return dict(partial)


def integer(value):
    return type(value) is int and 0 <= value <= MAX_INT


def load_data(path):
    try:
        with path.open("rb") as source:
            raw = source.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("State too large")
        data = json.loads(raw)
        if type(data) is not dict or type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("Invalid state version")
        settings = validate_settings(data["settings"])
        if settings.keys() != DEFAULT_SETTINGS.keys():
            raise ValueError("Incomplete settings")
        history = data["history"]
        sequence = data["sequence"]
        if type(history) is not dict or len(history) > LIMIT or not integer(sequence):
            raise ValueError("Invalid history")
        clean = {}
        for key, entry in history.items():
            mac = normalize_mac(key)
            if (not mac or type(entry) is not dict
                    or set(entry) != {"timestamp", "sequence"}
                    or not integer(entry["timestamp"]) or not integer(entry["sequence"])
                    or entry["sequence"] > sequence or mac in clean):
                raise ValueError("Invalid history entry")
            clean[mac] = dict(entry)
        return settings, clean, sequence, "loaded"
    except FileNotFoundError:
        return dict(DEFAULT_SETTINGS), {}, 0, "new"
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return dict(DEFAULT_SETTINGS), {}, 0, "load-error"


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".bluetooth-order-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, separators=(",", ":"), allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class State:
    """All mutations run without awaits on Decky's single asyncio loop."""

    def __init__(self, directory, emit, logger, clock=None, write=atomic_write,
                 debounce=0.15, max_delay=0.75):
        if not Path(directory).is_absolute():
            raise ValueError("Decky settings directory is unavailable")
        self.path = Path(directory) / "bluetooth-order.json"
        self.emit, self.logger = emit, logger
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.write, self.debounce, self.max_delay = write, debounce, max_delay
        self.settings = dict(DEFAULT_SETTINGS)
        self.history, self.devices, self.sequence = {}, {}, 0
        self.observer_status, self.revision = "starting", 0
        self.persistence_status = "new"
        self.diagnostics = deque(maxlen=64)
        self.dirty = asyncio.Event()
        self.changed = asyncio.Event()
        self.closing = False
        self.writer_task = self.emit_task = None
        self.generation = self.saved_generation = 0

    async def start(self):
        self.settings, self.history, self.sequence, self.persistence_status = (
            await asyncio.to_thread(load_data, self.path))
        if self.persistence_status == "load-error":
            self.logger.warning("Bluetooth Order state could not be loaded")
        self.writer_task = asyncio.create_task(self._writer())
        self.emit_task = asyncio.create_task(self._emitter())

    def snapshot(self):
        return copy.deepcopy({
            "settings": self.settings, "history": self.history, "devices": self.devices,
            "observerStatus": self.observer_status, "revision": self.revision,
        })

    def _change(self, persist=False):
        self.revision += 1
        self.changed.set()
        if persist:
            self.generation += 1
            self.dirty.set()

    def set_settings(self, partial):
        values = validate_settings(partial)
        if self.closing:
            raise RuntimeError("Backend is stopping")
        updated = {**self.settings, **values}
        if updated != self.settings:
            self.settings = updated
            self._change(persist=True)
        return self.snapshot()

    def reset_history(self):
        if self.closing:
            raise RuntimeError("Backend is stopping")
        if self.history:
            self.history = {}
            self._change(persist=True)
        return self.snapshot()

    def status(self, status, clear=False):
        if self.closing:
            return
        if status != self.observer_status or (clear and self.devices):
            self.observer_status = status
            if clear:
                self.devices = {}
            self._change()

    def replace_devices(self, devices):
        if not self.closing and devices != self.devices:
            self.devices = copy.deepcopy(devices)
            self._change()

    def connection(self, mac):
        if self.closing or self.sequence >= MAX_INT:
            return
        self.sequence += 1
        self.history[mac] = {
            "timestamp": min(MAX_INT, max(0, int(self.clock()))), "sequence": self.sequence,
        }
        if len(self.history) > LIMIT:
            del self.history[min(self.history, key=lambda key: self.history[key]["sequence"])]
        self._change(persist=True)

    def log_diagnostics(self, entries):
        if type(entries) is not list or len(entries) > 32:
            raise ValueError("Diagnostics must be a batch of at most 32 objects")
        if any(type(entry) is not dict or len(entry) > 16 for entry in entries):
            raise ValueError("Invalid diagnostic object")
        accepted = 0
        for entry in entries:
            clean = {}
            # Only closed vocabularies can cross the privacy boundary.
            for key, allowed in (
                ("event", EVENTS), ("level", ("debug", "info", "warn", "error")),
                ("surface", ("qam", "settings")),
                ("status", STATUSES),
            ):
                value = entry.get(key)
                if type(value) is str and value in allowed:
                    clean[key] = value
            fields = entry.get("fields", entry)
            if type(fields) is dict:
                for key in ("count", "rowCount", "matched", "changed", "durationMs", "revision"):
                    value = fields.get(key)
                    if type(value) is bool:
                        clean[key] = value
                    elif type(value) in (int, float) and 0 <= value <= MAX_INT and math.isfinite(value):
                        clean[key] = value
                for key, allowed in (("surface", ("qam", "settings")),
                                     ("status", STATUSES)):
                    value = fields.get(key)
                    if type(value) is str and value in allowed:
                        clean[key] = value
            if clean and (clean.get("level") != "debug" or self.settings["debug"]):
                clean["time"] = min(MAX_INT, max(0, int(self.clock())))
                self.diagnostics.append(clean)
                level = clean.get("level", "info")
                log = getattr(self.logger, "warning" if level == "warn" else level)
                log("Bluetooth Order diagnostic %s", json.dumps(clean, separators=(",", ":")))
                accepted += 1
        return {"accepted": accepted}

    def export_diagnostics(self):
        return json.dumps({
            "version": 1, "settings": self.settings, "observerStatus": self.observer_status,
            "revision": self.revision, "persistenceStatus": self.persistence_status,
            "counts": {"observed": len(self.devices),
                       "connected": sum(device["connected"] for device in self.devices.values()),
                       "paired": sum(device["paired"] for device in self.devices.values()),
                       "remembered": len(self.history)},
            "entries": list(self.diagnostics),
        }, separators=(",", ":"), allow_nan=False)

    async def _emitter(self):
        try:
            while True:
                await self.changed.wait()
                for attempt in range(len(RETRY_DELAYS) + 1):
                    self.changed.clear()
                    try:
                        await self.emit("snapshot", self.snapshot())
                        break
                    except Exception:
                        self.logger.warning("Bluetooth Order snapshot emission failed")
                        if attempt < len(RETRY_DELAYS):
                            await asyncio.sleep(RETRY_DELAYS[attempt])
        except asyncio.CancelledError:
            pass

    async def _writer(self):
        try:
            while True:
                await self.dirty.wait()
                deadline = asyncio.get_running_loop().time() + self.max_delay
                while not self.closing:
                    self.dirty.clear()
                    remaining = min(self.debounce, max(0, deadline - asyncio.get_running_loop().time()))
                    try:
                        await asyncio.wait_for(self.dirty.wait(), remaining)
                    except asyncio.TimeoutError:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        break
                for attempt in range(len(RETRY_DELAYS) + 1):
                    self.dirty.clear()
                    generation = self.generation
                    payload = copy.deepcopy({"version": 1, "settings": self.settings,
                                             "history": self.history, "sequence": self.sequence})
                    try:
                        await asyncio.to_thread(self.write, self.path, payload)
                        self.saved_generation = generation
                        self.persistence_status = "saved"
                        break
                    except Exception:
                        self.persistence_status = "write-error"
                        self.logger.warning("Bluetooth Order state could not be saved")
                        if attempt < len(RETRY_DELAYS):
                            await asyncio.sleep(RETRY_DELAYS[attempt])
                if self.generation != generation:
                    self.dirty.set()
                elif self.closing:
                    return
        except asyncio.CancelledError:
            pass

    async def close(self, timeout=2.0):
        self.closing = True
        if self.emit_task:
            self.emit_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.emit_task
        if not self.writer_task:
            return
        if self.generation == self.saved_generation:
            self.writer_task.cancel()
        else:
            self.dirty.set()
        try:
            await asyncio.wait_for(asyncio.shield(self.writer_task), timeout)
        except asyncio.CancelledError:
            if not self.writer_task.cancelled():
                raise
        except asyncio.TimeoutError:
            self.persistence_status = "flush-timeout"
            self.logger.warning("Bluetooth Order state flush timed out")
            # A running filesystem syscall cannot be cancelled; never start another writer.
            self.writer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.writer_task