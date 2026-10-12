import asyncio
from pathlib import Path
import sys

import decky

# Decky loads this file by path; its working directory is not the plugin root.
plugin_root = Path(__file__).resolve().parent
sys.path.insert(0, str(plugin_root))
sys.path.insert(0, str(plugin_root / "py_modules"))

from backend.observer import Observer
from backend.state import State


class Plugin:
    def __init__(self):
        self._ready = asyncio.Event()
        self._stopping = False
        self._failed = False

    async def _main(self):
        try:
            self._state = State(decky.DECKY_PLUGIN_SETTINGS_DIR, decky.emit, decky.logger)
            await self._state.start()
            if self._stopping:
                await self._state.close()
                return
            self._observer = Observer(self._state, decky.logger)
            self._observer.start()
            decky.logger.info("Bluetooth Order read-only observer started")
        except Exception:
            self._failed = True
            decky.logger.warning("Bluetooth Order backend initialization failed")
            state = getattr(self, "_state", None)
            if state:
                await state.close()
        finally:
            self._ready.set()

    async def _get_state(self):
        if self._stopping:
            raise RuntimeError("Backend is stopping")
        await asyncio.wait_for(self._ready.wait(), 3.0)
        if self._stopping:
            raise RuntimeError("Backend is stopping")
        if self._failed:
            raise RuntimeError("Backend initialization failed")
        return self._state

    async def get_snapshot(self):
        return (await self._get_state()).snapshot()

    async def set_settings(self, partial: dict):
        return (await self._get_state()).set_settings(partial)

    async def reset_history(self):
        return (await self._get_state()).reset_history()

    async def log_diagnostics(self, entries: list):
        return (await self._get_state()).log_diagnostics(entries)

    async def export_diagnostics(self):
        return (await self._get_state()).export_diagnostics()

    async def _unload(self):
        self._stopping = True
        observer = getattr(self, "_observer", None)
        if observer:
            await observer.close()
        state = getattr(self, "_state", None)
        if state:
            await state.close()
        self._ready.set()
        decky.logger.info("Bluetooth Order stopped")