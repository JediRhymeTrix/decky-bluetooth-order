import asyncio
from collections import deque
from contextlib import suppress
from pathlib import Path

from .state import LIMIT, normalize_mac

BLUEZ = "org.bluez"
DEVICE = "org.bluez.Device1"
OBJECTS = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
DBUS = "org.freedesktop.DBus"
RULES = (
    "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.bluez'",
    "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager',member='InterfacesAdded'",
    "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager',member='InterfacesRemoved'",
    "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged',arg0='org.bluez.Device1'",
)


async def auth_readline(bus):
    # dbus-next 0.2.3 spins on authentication EOF; keep that path cancellable.
    line = bytearray()
    while line[-2:] != b"\r\n":
        chunk = await bus._loop.sock_recv(bus._sock, 2)
        if not chunk:
            raise ConnectionError("Observer authentication connection closed")
        line.extend(chunk)
        if len(line) > 4096:
            raise ConnectionError("Observer authentication response too large")
    return line[:-2].decode("ascii")


def properties(values):
    if type(values) is not dict:
        return {}
    clean = {}
    for key in ("Address", "Connected", "Paired"):
        if key not in values:
            continue
        value = values.get(key)
        value = getattr(value, "value", value)
        if key == "Address":
            value = normalize_mac(value)
            if value:
                clean[key] = value
        else:
            clean[key] = value if type(value) is bool else None
    return clean


class Observer:
    def __init__(self, state, logger, bus_factory=None, call_timeout=2.0,
                 retry_delays=(0.25, 0.75, 1.5)):
        self.state, self.logger = state, logger
        self.bus_factory, self.call_timeout = bus_factory, call_timeout
        self.retry_delays = retry_delays
        self.bus = self.task = self.disconnect_task = None
        self.wake = asyncio.Event()
        self.owner, self.owner_epoch = "", 0
        self.paths = {}
        self.buffer = deque()
        self.buffering, self.overflow = True, False
        self.stopping, self.disconnected = False, False

    def start(self):
        self.task = asyncio.create_task(self._run())

    async def _call(self, destination, path, interface, member, signature="", body=None):
        reply = await asyncio.wait_for(self.bus.call(self.Message(
            destination=destination, path=path, interface=interface,
            member=member, signature=signature, body=body or [],
        )), self.call_timeout)
        if reply is None or reply.message_type != self.MessageType.METHOD_RETURN:
            if (member == "GetNameOwner" and reply is not None
                    and reply.error_name == "org.freedesktop.DBus.Error.NameHasNoOwner"):
                return [""]
            raise RuntimeError("Observer read failed")
        return reply.body

    async def _run(self):
        try:
            try:
                import xml.etree.ElementTree
            except ModuleNotFoundError as error:
                if error.name != "xml.etree":
                    raise
                # Decky 3.2.10 freezes xml but omits etree, which dbus-next imports.
                import xml
                xml.__path__.append(str(Path(__file__).resolve().parents[1] / "py_modules" / "stdlib_xml"))
                import xml.etree.ElementTree
            from dbus_next import BusType, Message, MessageType
            from dbus_next.aio import MessageBus
            self.Message, self.MessageType = Message, MessageType
            if self.bus_factory:
                factory = self.bus_factory
            else:
                class ReadOnlyMessageBus(MessageBus):
                    _auth_readline = auth_readline
                factory = lambda: ReadOnlyMessageBus(bus_type=BusType.SYSTEM)
            failures = 0
            while not self.stopping:
                try:
                    self.buffering, self.overflow, self.disconnected = True, False, False
                    self.buffer.clear()
                    self.owner, self.paths = "", {}
                    self.bus = factory()
                    await asyncio.wait_for(self.bus.connect(), self.call_timeout)
                    self.bus.add_message_handler(self._message)
                    self.disconnect_task = asyncio.create_task(self._watch_disconnect(self.bus))
                    for rule in RULES:
                        await self._call(DBUS, "/org/freedesktop/DBus", DBUS, "AddMatch", "s", [rule])
                    # Partial subscriptions can miss a counterpart signal. Baseline
                    # starts only after all four match acknowledgements.
                    self.buffer.clear()
                    self.overflow = False
                    while not self.stopping:
                        self.wake.clear()
                        await self._baseline()
                        failures = 0
                        await self.wake.wait()
                        if self.disconnected:
                            raise RuntimeError("Observer bus disconnected")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.state.status("reconnecting", clear=True)
                    self.logger.warning("Bluetooth Order observer temporarily unavailable")
                finally:
                    await self._release_bus()
                if failures >= len(self.retry_delays):
                    self.state.status("unavailable", clear=True)
                    return
                delay = self.retry_delays[failures]
                failures += 1
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            pass
        except ImportError:
            self.state.status("unavailable", clear=True)
            self.logger.warning("Bluetooth Order observer dependency unavailable")

    async def _watch_disconnect(self, bus):
        try:
            await bus.wait_for_disconnect()
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        if bus is self.bus:
            self.disconnected = True
            self.wake.set()

    async def _baseline(self):
        self.buffering = True
        if self.overflow:
            # An incomplete pre-read stream cannot be replayed; start this read fresh.
            self.buffer.clear()
            self.overflow = False
        self.paths = {}
        epoch = self.owner_epoch
        body = await self._call(DBUS, "/org/freedesktop/DBus", DBUS, "GetNameOwner", "s", [BLUEZ])
        if epoch != self.owner_epoch:
            self.wake.set()
            return
        self.owner = body[0]
        if not self.owner:
            self.buffer.clear()
            self.state.status("bluez-unavailable", clear=True)
            return
        # The unique owner pins this read to the daemon whose signals we buffered.
        body = await self._call(self.owner, "/", OBJECTS, "GetManagedObjects")
        if epoch != self.owner_epoch:
            self.wake.set()
            return
        if self.overflow:
            raise RuntimeError("Observer handshake buffer overflow")
        objects = body[0]
        if type(objects) is not dict:
            raise RuntimeError("Invalid observer baseline")
        for path, interfaces in objects.items():
            if type(interfaces) is dict and DEVICE in interfaces:
                self._add(path, properties(interfaces[DEVICE]))
        events = [event for sender, event in self.buffer if sender == self.owner]
        self.buffer.clear()
        # A changed boolean signal proves its prior value. Rewind to the first
        # buffered signal so a connection during the read is not lost to baseline.
        seen = set()
        for event in events:
            kind, path, values = event
            if kind in ("added", "removed"):
                seen.add(path)
            elif kind == "properties" and "Connected" in values and path not in seen:
                seen.add(path)
                if (path in self.paths and type(values["Connected"]) is bool
                        and type(self.paths[path].get("Connected")) is bool):
                    self.paths[path]["Connected"] = not values["Connected"]
        for event in events:
            self._apply(event)
        self.buffering = False
        self._publish()
        self.state.status("observing")

    def _message(self, message):
        if self.stopping or message.message_type != self.MessageType.SIGNAL:
            return
        body = message.body
        if (message.interface == DBUS and message.member == "NameOwnerChanged"
                and message.sender == DBUS and message.path == "/org/freedesktop/DBus"
                and len(body) == 3 and body[0] == BLUEZ):
            self.owner_epoch += 1
            self.owner = body[2]
            self.paths = {}
            self.buffer.clear()
            self.buffering = True
            self.state.status("starting" if self.owner else "bluez-unavailable", clear=True)
            self.wake.set()
            return
        if self.owner and message.sender != self.owner:
            return
        event = None
        if message.interface == OBJECTS and message.member == "InterfacesAdded" and len(body) == 2:
            if type(body[1]) is dict and DEVICE in body[1]:
                event = ("added", body[0], properties(body[1][DEVICE]))
        elif message.interface == OBJECTS and message.member == "InterfacesRemoved" and len(body) == 2:
            if type(body[1]) is list and DEVICE in body[1]:
                event = ("removed", body[0], {})
        elif (message.interface == PROPERTIES and message.member == "PropertiesChanged"
              and len(body) == 3 and body[0] == DEVICE):
            values = properties(body[1])
            invalid = body[2]
            if type(invalid) is list:
                for key in ("Connected", "Paired", "Address"):
                    if key in invalid:
                        values[key] = None
            if values:
                event = ("properties", message.path, values)
        if event is None or type(event[1]) is not str or not event[1].startswith("/org/bluez/"):
            return
        if self.buffering:
            if len(self.buffer) < 1024:
                self.buffer.append((message.sender, event))
            else:
                self.overflow = True
            return
        self._apply(event)
        self._publish()

    def _add(self, path, values):
        if type(path) is str and path.startswith("/org/bluez/") and (path in self.paths or len(self.paths) < LIMIT):
            if "Address" in values:
                self.paths[path] = dict(values)

    def _apply(self, event):
        kind, path, values = event
        if kind == "removed":
            self.paths.pop(path, None)
        elif kind == "added":
            self._add(path, values)
        elif path in self.paths:
            device = self.paths[path]
            before = device.get("Connected")
            device.update(values)
            mac = device.get("Address")
            if mac and before is False and device.get("Connected") is True:
                self.state.connection(mac)

    def _publish(self):
        devices = {}
        unknown = set()
        for device in self.paths.values():
            mac = device.get("Address")
            if not mac:
                continue
            current = devices.setdefault(mac, {"connected": False, "paired": False})
            current["connected"] |= device.get("Connected") is True
            current["paired"] |= device.get("Paired") is True
            if type(device.get("Connected")) is not bool:
                unknown.add(mac)
        for mac in unknown:
            if not devices[mac]["connected"]:
                del devices[mac]
        self.state.replace_devices(devices)

    async def _release_bus(self):
        bus, self.bus = self.bus, None
        if bus is None:
            return
        try:
            bus.remove_message_handler(self._message)
        except Exception:
            pass
        # Match rules are connection-scoped and vanish when we disconnect.
        try:
            bus.disconnect()
        except Exception:
            self.logger.warning("Bluetooth Order observer disconnect failed")
        if self.disconnect_task:
            try:
                await asyncio.wait_for(asyncio.shield(self.disconnect_task), 0.2)
            except asyncio.TimeoutError:
                self.disconnect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.disconnect_task
            self.disconnect_task = None

    async def close(self, timeout=1.0):
        self.stopping = True
        if self.task:
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout)
            except asyncio.CancelledError:
                if not self.task.cancelled():
                    raise
            except asyncio.TimeoutError:
                await self._release_bus()
                self.logger.warning("Bluetooth Order observer shutdown timed out")