"""Start the saved action list once for the currently logged-in game session."""
import asyncio
import logging

log = logging.getLogger(__name__)


class LoginStartup:
    def __init__(self):
        self.phase = "waiting_for_login"
        self._proxy = None
        self._generation = None
        self._task = None

    def owns(self, proxy):
        return proxy is not None and self._proxy is proxy and self._generation == proxy.session_generation

    def cancel(self):
        task, self._task = self._task, None
        self._proxy = self._generation = None
        self.phase = "waiting_for_login"
        if task is not None and not task.done():
            task.cancel()
        return task

    def schedule(self, state, start_enabled):
        proxy, session = state.game_proxy, state.game_state
        if self.owns(proxy):
            return self._task
        self.cancel()
        self._proxy, self._generation = proxy, proxy.session_generation
        self.phase = "waiting_for_session"

        async def activate():
            while (self.owns(proxy) and state.game_proxy is proxy and state.game_state is session
                   and state.connected and proxy.logged_in):
                if proxy.server_writer is not None:
                    try:
                        count = start_enabled()
                    except Exception:
                        self.phase = "failed"
                        log.exception("Could not initialize saved actions for this login")
                    else:
                        self.phase = "activated"
                        log.info("Initialized saved actions for this login (%s started)", count)
                    return
                await asyncio.sleep(.1)

        self._task = asyncio.create_task(activate(), name="saved-actions-login")
        return self._task
