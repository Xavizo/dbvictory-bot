"""Identity-scoped authorization, invalidated by reconnect even on the same proxy."""
import asyncio


class SessionLease:
    def __init__(self, state):
        self.state = state
        self.game = state.game_state
        self.proxy = state.game_proxy
        self.generation = getattr(self.proxy, 'session_generation', 0)
        self.player = self.game.player_id

    def valid(self):
        state = self.state
        return bool(state.connected and state.game_state is self.game
                    and state.game_proxy is self.proxy
                    and getattr(self.proxy, 'session_generation', 0) == self.generation
                    and (not self.player or self.game.player_id == self.player))


def task_alive(task):
    return task is None or not task.done() and not task.cancelling()


def current_task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None
