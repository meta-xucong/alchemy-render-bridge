"""Public SDK shutdown in the same task, preserving evidence before loop.stop."""
import asyncio


async def orderly_stop(worker, evidence):
    # wait_for() would put shutdown in a separate task. SDK loop.stop() can then
    # stop the loop before the waiting driver resumes to record completion.
    async with asyncio.timeout(45):
        await worker.exit_gracefully()
    evidence['graceful_stop_completed'] = True
