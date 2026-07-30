import pytest

from agent_overlord.services.broadcast import EventBroadcaster


@pytest.mark.asyncio
async def test_slow_stream_client_gets_bounded_resync_signal() -> None:
    broadcaster = EventBroadcaster(queue_size=2)
    slow = await broadcaster.subscribe()
    fast = await broadcaster.subscribe()

    await broadcaster.publish("workers", {"revision": 1})
    await broadcaster.publish("workers", {"revision": 2})
    assert (await fast.get()).data == {"revision": 1}

    await broadcaster.publish("workers", {"revision": 3})

    overflow = await slow.get()
    assert overflow.event == "resync"
    assert overflow.data == {"reason": "client_buffer_overflow"}
    # A client which is consuming remains independent of the overflowing client.
    assert (await fast.get()).data == {"revision": 2}
    assert (await fast.get()).data == {"revision": 3}

    await broadcaster.unsubscribe(slow)
    await broadcaster.unsubscribe(fast)
    assert broadcaster.client_count == 0
