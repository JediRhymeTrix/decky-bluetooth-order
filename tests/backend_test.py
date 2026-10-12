import asyncio
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from backend.observer import BLUEZ, DBUS, DEVICE, OBJECTS, PROPERTIES, RULES, Observer, auth_readline
from backend.state import DEFAULT_SETTINGS, LIMIT, State, atomic_write, load_data

# Synthetic identities only. No test connects to a real bus or settings directory.
ADDRESS = "12:34:56:78:9A:BC"
PATH = "/org/bluez/hci0/dev_12_34_56_78_9A_BC"


async def eventually(predicate, timeout=1.0):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), timeout)


class MessageType:
    SIGNAL = "signal"
    METHOD_RETURN = "return"
    ERROR = "error"


class Message:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.message_type = kwargs.get("message_type", MessageType.METHOD_RETURN)
        self.body = kwargs.get("body", [])
        self.error_name = kwargs.get("error_name")


def signal(interface, member, body, path=PATH, sender=":1.42"):
    return Message(message_type=MessageType.SIGNAL, interface=interface,
                   member=member, body=body, path=path, sender=sender)


def objects(connected=False):
    return {PATH: {DEVICE: {"Address": ADDRESS, "Connected": connected, "Paired": True,
                          "Name": "Synthetic private name"}}}


def change(connected):
    return signal(PROPERTIES, "PropertiesChanged", [DEVICE, {"Connected": connected}, []])


class FakeBus:
    def __init__(self, baseline=None, handshake=None):
        self.baseline = baseline if baseline is not None else objects()
        self.handshake = handshake
        self.handlers, self.calls = [], []
        self.owner = ":1.42"
        self.disconnected = False
        self.disconnect_event = asyncio.Event()
        self.method_error = None

    async def connect(self):
        return self

    async def call(self, message):
        self.calls.append(message)
        if message.member == self.method_error:
            return Message(message_type=MessageType.ERROR, error_name="synthetic.error",
                           body=[ADDRESS + " Synthetic private name"])
        if message.member == "GetNameOwner":
            if not self.owner:
                return Message(message_type=MessageType.ERROR,
                               error_name="org.freedesktop.DBus.Error.NameHasNoOwner")
            return Message(body=[self.owner])
        if message.member == "GetManagedObjects":
            if self.handshake:
                handshake, self.handshake = self.handshake, None
                handshake(self)
            return Message(body=[self.baseline])
        return Message(body=[])

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    def remove_message_handler(self, handler):
        self.handlers.remove(handler)

    def fire(self, message):
        for handler in list(self.handlers):
            handler(message)

    def disconnect(self):
        self.disconnected = True
        self.disconnect_event.set()

    async def wait_for_disconnect(self):
        await self.disconnect_event.wait()


def dbus_modules():
    root = types.ModuleType("dbus_next")
    root.Message, root.MessageType = Message, MessageType
    root.BusType = types.SimpleNamespace(SYSTEM="system")
    aio = types.ModuleType("dbus_next.aio")
    aio.MessageBus = FakeBus
    return {"dbus_next": root, "dbus_next.aio": aio}


class StateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.emit, self.logger = AsyncMock(), Mock()
        self.state = State(self.temp.name, self.emit, self.logger, clock=lambda: 123456,
                           debounce=0.01, max_delay=0.03)
        await self.state.start()

    async def asyncTearDown(self):
        await self.state.close()
        self.temp.cleanup()

    async def test_defaults_strict_settings_no_partial_mutation(self):
        self.assertEqual(self.state.snapshot()["settings"], DEFAULT_SETTINGS)
        for partial in ({"mode": "other"}, {"qam": 1}, {"connectedFirst": None},
                        {"unknown": False}, [], {"qam": True, "debug": "true"}):
            with self.assertRaises(ValueError):
                self.state.set_settings(partial)
            self.assertEqual(self.state.revision, 0)
        result = self.state.set_settings({"mode": "az", "qam": True})
        self.assertEqual(result["revision"], 1)
        result["settings"]["mode"] = "za"
        self.assertEqual(self.state.settings["mode"], "az")
        self.state.set_settings({"mode": "az"})
        self.assertEqual(self.state.revision, 1)
        await eventually(lambda: self.emit.call_count == 1)
        self.assertEqual(self.emit.call_args.args[0], "snapshot")
        for directory in ("", ".", "relative/path"):
            with self.assertRaises(ValueError):
                State(directory, self.emit, self.logger)

    async def test_reset_and_restart_preserve_sequence_not_runtime_devices(self):
        self.state.connection(ADDRESS)
        self.state.reset_history()
        self.state.connection(ADDRESS)
        self.assertEqual(self.state.history[ADDRESS], {"timestamp": 123456, "sequence": 2})
        self.state.reset_history()
        await self.state.close()
        loaded = State(self.temp.name, self.emit, self.logger, clock=lambda: 42)
        await loaded.start()
        try:
            self.assertEqual(loaded.sequence, 2)
            self.assertEqual(loaded.history, {})
            self.assertEqual(loaded.devices, {})
            loaded.connection(ADDRESS)
            self.assertEqual(loaded.history[ADDRESS]["sequence"], 3)
        finally:
            await loaded.close()

    async def test_debounce_coalesces_many_changes(self):
        calls = []
        self.state.write = lambda path, data: (calls.append(data), atomic_write(path, data))
        for index in range(20):
            self.state.set_settings({"mode": "az" if index % 2 else "za"})
        await eventually(lambda: self.state.saved_generation == self.state.generation)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["settings"]["mode"], "az")

    async def test_continuous_changes_have_maximum_debounce_delay(self):
        self.state.debounce, self.state.max_delay = 0.03, 0.04
        async def produce():
            index = 0
            while True:
                self.state.set_settings({"mode": "az" if index % 2 else "za"})
                index += 1
                await asyncio.sleep(0.005)
        async def inline_write(write, *args):
            return write(*args)
        self.state.write = Mock()
        # Check starvation while updates continue, not a hosted runner's disk latency.
        with patch("backend.state.asyncio.to_thread", new=AsyncMock(side_effect=inline_write)):
            producer = asyncio.create_task(produce())
            try:
                await eventually(lambda: self.state.saved_generation > 0)
                self.assertFalse(producer.done())
                self.state.write.assert_called()
            finally:
                producer.cancel()
                try:
                    await producer
                except asyncio.CancelledError:
                    pass

    async def test_emit_error_and_slow_emit_coalesce_without_event_tasks(self):
        self.emit.side_effect = RuntimeError(ADDRESS)
        self.state.set_settings({"mode": "az"})
        await eventually(lambda: self.logger.warning.call_count == 1)
        self.assertFalse(self.state.emit_task.done())
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_emit(*args):
            entered.set()
            await release.wait()
        self.emit.side_effect = slow_emit
        self.state.set_settings({"mode": "za"})
        await entered.wait()
        for index in range(20):
            self.state.set_settings({"mode": "az" if index % 2 else "za"})
        release.set()
        await eventually(lambda: self.emit.call_count == 3)
        self.assertNotIn(ADDRESS, str(self.logger.mock_calls))

    async def test_emit_retries_final_snapshot_then_stops_after_bounded_failures(self):
        self.emit.side_effect = [RuntimeError("synthetic"), None]
        self.state.set_settings({"mode": "az"})
        await eventually(lambda: self.emit.call_count == 1)
        self.state.set_settings({"mode": "za"})
        await eventually(lambda: self.emit.call_count == 2)
        self.assertEqual(self.emit.call_args.args[1]["settings"]["mode"], "za")
        self.emit.reset_mock()
        self.emit.side_effect = RuntimeError("synthetic")
        self.state.set_settings({"mode": "az"})
        await eventually(lambda: self.emit.call_count == 3)
        await asyncio.sleep(0.03)
        self.assertEqual(self.emit.call_count, 3)

    async def test_writer_retries_idle_failure_without_parallel_or_endless_retry(self):
        attempts = []
        def transient_write(path, data):
            attempts.append(data)
            if len(attempts) == 1:
                raise OSError("synthetic")
            atomic_write(path, data)
        self.state.write = transient_write
        self.state.set_settings({"mode": "za"})
        await eventually(lambda: self.state.persistence_status == "saved")
        self.assertEqual(len(attempts), 2)
        self.state.write = Mock(side_effect=OSError("synthetic"))
        self.state.set_settings({"mode": "az"})
        await eventually(lambda: self.state.write.call_count == 3)
        await asyncio.sleep(0.03)
        self.assertEqual(self.state.write.call_count, 3)

    async def test_writer_serializes_and_does_not_lose_change_during_write(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def write(path, data):
            calls.append(data)
            if len(calls) == 1:
                entered.set()
                if not release.wait(1):
                    raise TimeoutError("synthetic writer wait")
            atomic_write(path, data)
        self.state.write = write
        self.state.set_settings({"mode": "az"})
        await eventually(entered.is_set)
        self.state.set_settings({"mode": "za"})
        release.set()
        await eventually(lambda: self.state.saved_generation == self.state.generation)
        self.assertEqual([call["settings"]["mode"] for call in calls], ["az", "za"])
        self.assertEqual(load_data(self.state.path)[0]["mode"], "za")

    async def test_write_error_preserves_last_file_then_close_retries(self):
        self.state.set_settings({"mode": "az"})
        await eventually(lambda: self.state.persistence_status == "saved")
        self.state.write = Mock(side_effect=OSError(ADDRESS))
        self.state.set_settings({"mode": "za"})
        await eventually(lambda: self.state.persistence_status == "write-error")
        self.assertEqual(load_data(self.state.path)[0]["mode"], "az")
        self.assertNotIn(ADDRESS, str(self.logger.mock_calls))
        self.state.write = atomic_write
        await self.state.close()
        self.assertEqual(load_data(self.state.path)[0]["mode"], "za")

    async def test_unload_flush_is_bounded_with_slow_filesystem(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def write(path, data):
            entered.set()
            release.wait(1)
            finished.set()
        self.state.write = write
        self.state.set_settings({"mode": "az"})
        await eventually(entered.is_set)
        try:
            await asyncio.wait_for(self.state.close(timeout=0.01), 0.1)
            self.assertEqual(self.state.persistence_status, "flush-timeout")
            self.assertTrue(self.state.writer_task.done())
        finally:
            release.set()
            await eventually(finished.is_set)

    async def test_debug_redaction_ring_and_batch_bounds(self):
        self.state.connection(ADDRESS)
        self.state.replace_devices({ADDRESS: {"connected": True, "paired": True}})
        entry = {"event": "frontend-start", "level": "info", "fields": {
            "surface": "qam", "status": ADDRESS, "name": "Synthetic private name",
            "rowCount": 4, "durationMs": float("nan"), ADDRESS: True,
        }, "name": "Synthetic private name", "device": ADDRESS}
        for _ in range(3):
            self.assertEqual(self.state.log_diagnostics([entry] * 32), {"accepted": 32})
        report = self.state.export_diagnostics()
        self.assertNotIn(ADDRESS, report)
        self.assertNotIn("Synthetic private name", report)
        self.assertNotIn("history", report)
        self.assertNotIn('"devices"', report)
        self.assertNotIn("NaN", report)
        self.assertEqual(len(json.loads(report)["entries"]), 64)
        self.assertEqual(json.loads(report)["entries"][-1]["time"], 123456)
        self.assertNotIn(ADDRESS, str(self.logger.mock_calls))
        self.assertNotIn("Synthetic private name", str(self.logger.mock_calls))
        with self.assertRaises(ValueError):
            self.state.log_diagnostics([entry] * 33)
        with self.assertRaises(ValueError):
            self.state.log_diagnostics(["not object"])
        self.assertEqual(self.state.log_diagnostics([{"event": "patch-error", "level": "debug"}]), {"accepted": 0})
        self.state.set_settings({"debug": True})
        self.assertEqual(self.state.log_diagnostics([{"event": "patch-error", "level": "debug"}]), {"accepted": 1})
        self.state.log_diagnostics([{"event": "row-order", "fields": {"rowCount": 10**1000}}])
        self.assertNotIn("rowCount", json.loads(self.state.export_diagnostics())["entries"][-1])
        for status in ("off", "ready", "waiting for native device cache", "unsupported layout; native order"):
            self.state.log_diagnostics([{"event": "surface-status", "time": ADDRESS,
                                         "fields": {"surface": "qam", "status": status}}])
            self.assertEqual(json.loads(self.state.export_diagnostics())["entries"][-1],
                             {"event": "surface-status", "surface": "qam", "status": status, "time": 123456})

    async def test_corrupt_oversized_and_strict_persistence(self):
        for contents in (b"invalid", b"x" * (256 * 1024 + 1), b'{"version":1}',
                         b"[" * 2000 + b"0" + b"]" * 2000,
                         json.dumps({"version": 1, "settings": DEFAULT_SETTINGS,
                                     "history": {ADDRESS: {"timestamp": True, "sequence": 0}},
                                     "sequence": 0}).encode()):
            self.state.path.write_bytes(contents)
            settings, history, sequence, status = load_data(self.state.path)
            self.assertEqual((settings, history, sequence, status), (DEFAULT_SETTINGS, {}, 0, "load-error"))

    async def test_atomic_replace_failure_keeps_old_file_cleans_temp_and_private_mode(self):
        atomic_write(self.state.path, {"old": 1})
        with patch("backend.state.os.replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                atomic_write(self.state.path, {"new": 2})
        self.assertEqual(json.loads(self.state.path.read_text()), {"old": 1})
        self.assertEqual(list(Path(self.temp.name).glob(".bluetooth-order-*")), [])
        self.assertEqual(os.stat(self.state.path).st_mode & 0o777, 0o600)

    async def test_history_bound(self):
        for index in range(LIMIT + 1):
            self.state.connection(f"02:00:00:00:{index // 256:02X}:{index % 256:02X}")
        self.assertEqual(len(self.state.history), LIMIT)
        self.assertEqual(self.state.sequence, LIMIT + 1)
        self.assertNotIn("02:00:00:00:00:00", self.state.history)


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.logger, self.emit = Mock(), AsyncMock()
        self.state = State(self.temp.name, self.emit, self.logger, clock=lambda: 100,
                           debounce=0.01)
        await self.state.start()
        self.module_patch = patch.dict(sys.modules, dbus_modules())
        self.module_patch.start()
        self.observer = None

    async def asyncTearDown(self):
        if self.observer:
            await self.observer.close()
        await self.state.close()
        self.module_patch.stop()
        self.temp.cleanup()

    async def start_observer(self, bus, **kwargs):
        self.observer = Observer(self.state, self.logger, bus_factory=lambda: bus, **kwargs)
        self.observer.start()
        await eventually(lambda: self.state.observer_status in ("observing", "bluez-unavailable", "unavailable"))

    async def test_initial_connected_is_baseline_not_history_and_subscribe_first(self):
        bus = FakeBus(objects(True))
        await self.start_observer(bus)
        self.assertEqual(self.state.history, {})
        self.assertEqual(self.state.devices[ADDRESS], {"connected": True, "paired": True})
        self.assertEqual([call.member for call in bus.calls], ["AddMatch"] * len(RULES) + ["GetNameOwner", "GetManagedObjects"])
        bus.fire(change(True))
        self.assertEqual(self.state.history, {})
        bus.fire(change(False))
        bus.fire(change(True))
        bus.fire(change(True))
        self.assertEqual(self.state.history[ADDRESS], {"timestamp": 100, "sequence": 1})
        self.assertEqual({call.member for call in bus.calls}, {"AddMatch", "GetNameOwner", "GetManagedObjects"})

    async def test_disconnect_preserves_the_last_successful_connection(self):
        bus = FakeBus(objects(False))
        await self.start_observer(bus)
        bus.fire(change(True))
        recorded = dict(self.state.history[ADDRESS])
        bus.fire(change(False))
        self.assertEqual(self.state.history[ADDRESS], recorded)
        self.assertFalse(self.state.devices[ADDRESS]["connected"])
        self.assertEqual(self.state.sequence, 1)

    async def test_partial_subscription_buffer_is_discarded_before_baseline(self):
        class PartialSubscriptionBus(FakeBus):
            async def call(self, message):
                if message.member == "AddMatch" and "InterfacesAdded" in message.body[0]:
                    for _ in range(1025):
                        self.fire(signal(OBJECTS, "InterfacesAdded", [PATH, objects()[PATH]]))
                return await super().call(message)
        bus = PartialSubscriptionBus({})
        await self.start_observer(bus)
        self.assertEqual(self.state.devices, {})
        self.assertEqual(self.state.history, {})
        self.assertFalse(self.observer.overflow)

    async def test_partial_subscription_connected_state_does_not_override_baseline(self):
        class PartialSubscriptionBus(FakeBus):
            async def call(self, message):
                if message.member == "AddMatch" and "InterfacesAdded" in message.body[0]:
                    self.fire(signal(OBJECTS, "InterfacesAdded", [PATH, objects(False)[PATH]]))
                return await super().call(message)
        await self.start_observer(PartialSubscriptionBus(objects(True)))
        self.assertTrue(self.state.devices[ADDRESS]["connected"])
        self.assertEqual(self.state.history, {})

    async def test_baseline_race_true_signal_already_reflected_in_read(self):
        bus = FakeBus(objects(True), handshake=lambda connected: connected.fire(change(True)))
        await self.start_observer(bus)
        self.assertEqual(self.state.history[ADDRESS]["sequence"], 1)
        self.assertTrue(self.state.devices[ADDRESS]["connected"])

    async def test_baseline_race_multiple_signals_same_clock(self):
        def handshake(bus):
            for connected in (True, False, True):
                bus.fire(change(connected))
        bus = FakeBus(objects(True), handshake=handshake)
        await self.start_observer(bus)
        self.assertEqual(self.state.history[ADDRESS], {"timestamp": 100, "sequence": 2})

    async def test_handshake_invalidation_does_not_invent_previous_false(self):
        def handshake(bus):
            bus.fire(signal(PROPERTIES, "PropertiesChanged", [DEVICE, {}, ["Connected"]]))
            bus.fire(change(True))
        await self.start_observer(FakeBus(objects(True), handshake=handshake))
        self.assertEqual(self.state.history, {})

    async def test_handshake_missing_or_malformed_baseline_never_fabricates_history(self):
        for invalid in (None, "true", 1):
            baseline = objects()
            if invalid is None:
                del baseline[PATH][DEVICE]["Connected"]
            else:
                baseline[PATH][DEVICE]["Connected"] = invalid
            bus = FakeBus(baseline, handshake=lambda bus: bus.fire(change(True)))
            await self.start_observer(bus)
            self.assertEqual(self.state.history, {})
            self.assertTrue(self.state.devices[ADDRESS]["connected"])
            await self.observer.close()

    async def test_unknown_connected_omits_mac_and_aggregate_true_beats_unknown(self):
        baseline = objects()
        del baseline[PATH][DEVICE]["Connected"]
        bus = FakeBus(baseline)
        await self.start_observer(bus)
        self.assertNotIn(ADDRESS, self.state.devices)
        bus.fire(change(False))
        self.assertFalse(self.state.devices[ADDRESS]["connected"])
        bus.fire(change("malformed"))
        self.assertNotIn(ADDRESS, self.state.devices)
        second_path = PATH.replace("hci0", "hci1")
        bus.fire(signal(OBJECTS, "InterfacesAdded", [second_path, objects(False)[PATH]]))
        self.assertNotIn(ADDRESS, self.state.devices)
        bus.fire(signal(PROPERTIES, "PropertiesChanged", [DEVICE, {"Connected": True}, []], path=second_path))
        self.assertTrue(self.state.devices[ADDRESS]["connected"])
        bus.fire(signal(PROPERTIES, "PropertiesChanged", [DEVICE, {"Connected": False}, []], path=second_path))
        self.assertNotIn(ADDRESS, self.state.devices)
        bus.fire(change(False))
        self.assertFalse(self.state.devices[ADDRESS]["connected"])

    async def test_authentication_eof_and_size_are_bounded_and_deadline_cancellable(self):
        bus = types.SimpleNamespace(_loop=types.SimpleNamespace(sock_recv=AsyncMock(return_value=b"")),
                                    _sock=object())
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(auth_readline(bus), 0.05)
        self.assertEqual(bus._loop.sock_recv.call_count, 1)
        bus._loop.sock_recv = AsyncMock(return_value=b"aa")
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(auth_readline(bus), 0.05)
        self.assertEqual(bus._loop.sock_recv.call_count, 2049)
        bus._loop.sock_recv = AsyncMock(side_effect=[b"OK", b"\r\n"])
        self.assertEqual(await auth_readline(bus), "OK")
        async def hung_recv(*args):
            await asyncio.Event().wait()
        bus._loop.sock_recv = hung_recv
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(auth_readline(bus), 0.01)

    async def test_default_bus_local_auth_override_handles_eof_without_dependency_edits(self):
        created = []
        class EOFAuthBus(FakeBus):
            def __init__(self, **kwargs):
                super().__init__()
                self._loop = types.SimpleNamespace(sock_recv=AsyncMock(return_value=b""))
                self._sock = object()
                created.append(self)
            async def connect(self):
                await self._auth_readline()
        modules = dbus_modules()
        modules["dbus_next.aio"].MessageBus = EOFAuthBus
        with patch.dict(sys.modules, modules):
            self.observer = Observer(self.state, self.logger, retry_delays=())
            self.observer.start()
            await eventually(lambda: self.state.observer_status == "unavailable")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]._loop.sock_recv.call_count, 1)
        self.assertTrue(created[0].disconnected)

    async def test_interfaces_added_removed_unknown_invalidation_and_spoof(self):
        bus = FakeBus({})
        await self.start_observer(bus)
        bus.fire(signal(OBJECTS, "InterfacesAdded", [PATH, objects(True)[PATH]]))
        self.assertEqual(self.state.history, {})
        bus.fire(signal(PROPERTIES, "PropertiesChanged", [DEVICE, {}, ["Connected"]]))
        bus.fire(change(True))
        self.assertEqual(self.state.history, {})
        bus.fire(change(False))
        bus.fire(signal(PROPERTIES, "PropertiesChanged", [DEVICE, {"Connected": True}, []], sender=":1.bad"))
        self.assertEqual(self.state.history, {})
        bus.fire(change(True))
        self.assertEqual(self.state.sequence, 1)
        bus.fire(signal(OBJECTS, "InterfacesRemoved", [PATH, [DEVICE]]))
        self.assertEqual(self.state.devices, {})
        bus.fire(change(True))
        self.assertEqual(self.state.sequence, 1)

    async def test_bluez_owner_change_rebaseline_no_fake_connection(self):
        bus = FakeBus(objects(True))
        await self.start_observer(bus)
        bus.owner = ""
        bus.fire(signal(DBUS, "NameOwnerChanged", [BLUEZ, ":1.42", ""],
                        path="/org/freedesktop/DBus", sender=DBUS))
        await eventually(lambda: self.state.observer_status == "bluez-unavailable")
        self.assertEqual(self.state.devices, {})
        bus.owner = ":1.43"
        bus.fire(signal(DBUS, "NameOwnerChanged", [BLUEZ, "", ":1.43"],
                        path="/org/freedesktop/DBus", sender=DBUS))
        await eventually(lambda: self.state.observer_status == "observing")
        self.assertEqual(self.state.history, {})
        self.assertEqual([call.member for call in bus.calls].count("GetManagedObjects"), 2)

    async def test_owner_change_during_handshake_discards_old_baseline(self):
        def handshake(bus):
            bus.fire(change(True))
            bus.owner = ":1.43"
            bus.fire(signal(DBUS, "NameOwnerChanged", [BLUEZ, ":1.42", ":1.43"],
                            path="/org/freedesktop/DBus", sender=DBUS))
        bus = FakeBus(objects(True), handshake=handshake)
        await self.start_observer(bus)
        self.assertEqual(self.state.history, {})
        self.assertEqual([call.member for call in bus.calls].count("GetManagedObjects"), 2)
        self.assertEqual(self.observer.owner, ":1.43")

    async def test_no_polling_or_tasks_per_signal_and_variant_properties(self):
        bus = FakeBus()
        bus.baseline[PATH][DEVICE] = {
            key: types.SimpleNamespace(value=value) for key, value in objects()[PATH][DEVICE].items()
        }
        await self.start_observer(bus)
        before = len(bus.calls)
        with patch("backend.observer.asyncio.create_task", side_effect=AssertionError("per signal task")):
            for index in range(100):
                bus.fire(change(bool(index % 2)))
        self.assertEqual(self.state.sequence, 50)
        await asyncio.sleep(0.03)
        self.assertEqual(len(bus.calls), before)

    async def test_unload_during_hung_read_and_immediate_unload(self):
        entered = asyncio.Event()
        class HungBus(FakeBus):
            async def call(self, message):
                if message.member == "GetManagedObjects":
                    entered.set()
                    await asyncio.Event().wait()
                return await super().call(message)
        bus = HungBus()
        self.observer = Observer(self.state, self.logger, bus_factory=lambda: bus)
        self.observer.start()
        await entered.wait()
        await asyncio.wait_for(self.observer.close(), 0.2)
        self.assertTrue(bus.disconnected)
        self.assertEqual(bus.handlers, [])
        next_observer = Observer(self.state, self.logger, bus_factory=lambda: FakeBus())
        next_observer.start()
        await asyncio.wait_for(next_observer.close(), 0.2)
        self.assertTrue(next_observer.task.done())

    async def test_missing_dependency_has_static_status_not_exception(self):
        with patch.dict(sys.modules, {"dbus_next": None}):
            self.observer = Observer(self.state, self.logger)
            self.observer.start()
            await eventually(lambda: self.state.observer_status == "unavailable")
            self.assertTrue(self.observer.task.done())

    async def test_stop_during_connect_does_not_subscribe_or_backoff(self):
        class StoppingBus(FakeBus):
            async def connect(bus):
                self.observer.stopping = True
                return bus
        bus = StoppingBus()
        self.observer = Observer(self.state, self.logger, bus_factory=lambda: bus)
        with patch("backend.observer.asyncio.sleep", new=AsyncMock()) as backoff:
            await self.observer._run()
        self.assertEqual(bus.calls, [])
        self.assertEqual(bus.handlers, [])
        self.assertTrue(bus.disconnected)
        backoff.assert_not_awaited()

    async def test_bounded_failed_reconnect_redacted_errors(self):
        bus = FakeBus()
        bus.method_error = "GetManagedObjects"
        attempts = []
        def factory():
            next_bus = FakeBus()
            next_bus.method_error = "GetManagedObjects"
            attempts.append(next_bus)
            return next_bus
        self.observer = Observer(self.state, self.logger, bus_factory=factory, retry_delays=(0, 0))
        self.observer.start()
        await eventually(lambda: self.state.observer_status == "unavailable")
        self.assertEqual(len(attempts), 3)
        self.assertTrue(all(bus.disconnected and not bus.handlers for bus in attempts))
        self.assertNotIn(ADDRESS, str(self.logger.mock_calls))

    async def test_disconnect_reconnect_and_unload_removes_handlers(self):
        buses = [FakeBus(), FakeBus(objects(True))]
        calls = []
        def factory():
            calls.append(1)
            return buses[len(calls) - 1]
        self.observer = Observer(self.state, self.logger, bus_factory=factory, retry_delays=(0,))
        self.observer.start()
        await eventually(lambda: self.state.observer_status == "observing")
        buses[0].disconnect()
        await eventually(lambda: len(calls) == 2 and self.state.observer_status == "observing")
        self.assertEqual(self.state.history, {})
        await asyncio.wait_for(self.observer.close(), 0.2)
        self.assertTrue(all(bus.disconnected and not bus.handlers for bus in buses))
        self.assertTrue(self.observer.task.done())
        buses[1].fire(change(False))
        buses[1].fire(change(True))
        self.assertEqual(self.state.history, {})

    async def test_buffer_and_devices_bounded(self):
        def handshake(bus):
            for _ in range(1025):
                bus.fire(change(True))
        bus = FakeBus(handshake=handshake)
        self.observer = Observer(self.state, self.logger, bus_factory=lambda: bus, retry_delays=())
        self.observer.start()
        await eventually(lambda: self.state.observer_status == "unavailable")
        self.assertLessEqual(len(self.observer.buffer), 1024)
        self.assertEqual(self.state.history, {})

    async def test_overflow_before_rebaseline_discards_incomplete_replay(self):
        bus = FakeBus(objects(True))
        await self.start_observer(bus)
        bus.owner = ":1.43"
        bus.fire(signal(DBUS, "NameOwnerChanged", [BLUEZ, ":1.42", ":1.43"],
                        path="/org/freedesktop/DBus", sender=DBUS))
        for index in range(1025):
            bus.fire(signal(PROPERTIES, "PropertiesChanged",
                            [DEVICE, {"Connected": index % 2 == 0}, []], sender=bus.owner))
        self.assertTrue(self.observer.overflow)
        self.assertFalse(self.observer.buffer[-1][1][2]["Connected"])
        await eventually(lambda: self.state.observer_status == "observing")
        self.assertTrue(self.state.devices[ADDRESS]["connected"])
        self.assertEqual(self.state.history, {})
        self.assertEqual(len(self.observer.buffer), 0)
        self.assertFalse(self.observer.overflow)
        self.assertEqual([call.member for call in bus.calls].count("GetManagedObjects"), 2)

    async def test_device_cap_ignores_excess_paths(self):
        baseline = {}
        for index in range(LIMIT + 1):
            address = f"02:00:00:00:{index // 256:02X}:{index % 256:02X}"
            path = "/org/bluez/hci0/dev_" + address.replace(":", "_")
            baseline[path] = {DEVICE: {"Address": address, "Connected": False, "Paired": True}}
        await self.start_observer(FakeBus(baseline))
        self.assertEqual(len(self.state.devices), LIMIT)
        self.assertEqual(len(self.observer.paths), LIMIT)


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_frozen_etree_uses_only_the_bundled_xml_fallback(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            plugin = Path(directory) / "plugin"
            shutil.copytree(root / "backend", plugin / "backend", ignore=shutil.ignore_patterns("__pycache__"))
            compatibility = plugin / "py_modules" / "stdlib_xml" / "etree"
            compatibility.mkdir(parents=True)
            (compatibility / "__init__.py").write_text("")
            (compatibility / "ElementTree.py").write_text("fixture = True\n")
            dependency = plugin / "py_modules" / "dbus_next"
            dependency.mkdir()
            (dependency / "__init__.py").write_text(
                "import xml.etree.ElementTree\nBusType = type('BusType', (), {'SYSTEM': 'system'})\nMessage = MessageType = object\n")
            (dependency / "aio.py").write_text("MessageBus = object\n")
            script = """
import asyncio
from pathlib import Path
import sys
import types
from unittest.mock import Mock

root = Path(sys.argv[1])
scenario = sys.argv[2]
sys.path[:0] = [str(root), str(root / 'py_modules')]
xml = types.ModuleType('xml')
native = root / 'native_xml'
if scenario != 'missing':
    (native / 'etree').mkdir(parents=True, exist_ok=True)
    (native / 'etree' / '__init__.py').write_text('')
    if scenario == 'native':
        (native / 'etree' / 'ElementTree.py').write_text('native = True\\n')
    elif scenario == 'unrelated':
        (native / 'etree' / 'ElementTree.py').write_text('import fixture_unrelated_missing\\n')
    xml.__path__ = [str(native)]
else:
    xml.__path__ = []
sys.modules['xml'] = xml
original_path = list(xml.__path__)
from backend.observer import Observer
state, logger = Mock(), Mock()
factory = Mock(side_effect=RuntimeError('fixture stops before connecting'))
asyncio.run(Observer(state, logger, bus_factory=factory, retry_delays=())._run())
if scenario == 'missing':
    assert factory.call_count == 1, 'dependency import must reach the fake factory'
    assert xml.__path__ == [str(root / 'py_modules' / 'stdlib_xml')]
    assert sys.modules['xml.etree.ElementTree'].fixture
elif scenario == 'native':
    assert factory.call_count == 1
    assert xml.__path__ == original_path
    assert sys.modules['xml.etree.ElementTree'].native
else:
    assert factory.call_count == 0
    assert xml.__path__ == original_path
    assert any('dependency unavailable' in str(call) for call in logger.warning.call_args_list)
"""
            for scenario in ("missing", "native", "partial", "unrelated"):
                with self.subTest(scenario=scenario):
                    shutil.rmtree(plugin / "native_xml", ignore_errors=True)
                    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(plugin), scenario],
                                            cwd=directory, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_import_by_file_from_an_unrelated_directory(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / "plugin"
            installed.mkdir()
            shutil.copy2(root / "main.py", installed / "main.py")
            shutil.copytree(root / "backend", installed / "backend", ignore=shutil.ignore_patterns("__pycache__"))
            script = """
import importlib.util
from pathlib import Path
import sys
import types

path = Path(sys.argv[1]).resolve()
assert str(path.parent) not in sys.path
sys.modules['decky'] = types.ModuleType('decky')
spec = importlib.util.spec_from_file_location('decky_test_plugin', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.Plugin().__class__.__module__ == 'decky_test_plugin'
assert Path(sys.modules['backend.state'].__file__).parent == path.parent / 'backend'
assert str(path.parent / 'py_modules') in sys.path
"""
            result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(installed / "main.py")],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    async def test_rpc_and_unload_with_mock_decky_and_bus(self):
        with tempfile.TemporaryDirectory() as directory:
            decky = types.ModuleType("decky")
            decky.DECKY_PLUGIN_SETTINGS_DIR = directory
            decky.logger, decky.emit = Mock(), AsyncMock()
            created = []
            class PluginBus(FakeBus):
                def __init__(self, **kwargs):
                    super().__init__()
                    created.append(self)
            modules = dbus_modules()
            modules["dbus_next.aio"].MessageBus = PluginBus
            with patch.dict(sys.modules, {"decky": decky, **modules}):
                sys.modules.pop("main", None)
                plugin = importlib.import_module("main").Plugin()
                early_snapshot = asyncio.create_task(plugin.get_snapshot())
                await asyncio.sleep(0)
                self.assertFalse(early_snapshot.done())
                await plugin._main()
                self.assertEqual((await early_snapshot)["settings"], DEFAULT_SETTINGS)
                try:
                    await eventually(lambda: plugin._state.observer_status == "observing")
                    bus = created[0]
                    self.assertFalse((await plugin.get_snapshot())["settings"]["qam"])
                    self.assertEqual((await plugin.set_settings({"mode": "za"}))["settings"]["mode"], "za")
                    bus.fire(change(True))
                    self.assertEqual((await plugin.reset_history())["history"], {})
                    await plugin.log_diagnostics([{"event": "history-reset", "level": "info"}])
                    self.assertEqual(json.loads(await plugin.export_diagnostics())["entries"][0]["event"], "history-reset")
                finally:
                    await asyncio.wait_for(plugin._unload(), 1)
                    sys.modules.pop("main", None)
                self.assertTrue(bus.disconnected)
                self.assertEqual(bus.handlers, [])
                self.assertEqual(load_data(Path(directory) / "bluetooth-order.json")[2], 1)
                self.assertTrue(plugin._state.writer_task.done())
                self.assertTrue(plugin._state.emit_task.done())
                with self.assertRaises(RuntimeError):
                    await plugin.get_snapshot()

    async def test_main_failure_releases_waiters_with_static_error(self):
        decky = types.ModuleType("decky")
        decky.DECKY_PLUGIN_SETTINGS_DIR = ""
        decky.logger, decky.emit = Mock(), AsyncMock()
        with patch.dict(sys.modules, {"decky": decky}):
            sys.modules.pop("main", None)
            plugin = importlib.import_module("main").Plugin()
            waiter = asyncio.create_task(plugin.get_snapshot())
            await plugin._main()
            with self.assertRaisesRegex(RuntimeError, "initialization failed"):
                await asyncio.wait_for(waiter, 0.05)
            self.assertTrue(plugin._ready.is_set())
            await plugin._unload()
            sys.modules.pop("main", None)


if __name__ == "__main__":
    unittest.main()