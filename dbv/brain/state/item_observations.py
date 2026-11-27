"""Wake item schedulers on relevant semantic changes, keeping their time deadlines."""


def observed_inputs(game, families):
    observations = getattr(game, "observations", None)
    if observations is None:
        return None
    return observations, observations.revision_for(families)


async def wait_for_inputs(bot, families, cursor, timeout):
    if cursor is None:
        await bot.sleep(timeout)
        return
    observations, revision = cursor
    await observations.wait(families, revision, timeout)
    # Also run the action context's pause/cancellation check on an early wake.
    await bot.sleep(0)
