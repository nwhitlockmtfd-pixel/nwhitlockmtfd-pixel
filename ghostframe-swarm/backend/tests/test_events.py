import asyncio

from ghostframe.events import Event, SQLiteEventStore


async def test_publish_subscribe_wildcards(bus):
    seen: list[str] = []

    async def handler(e: Event) -> None:
        seen.append(e.kind)

    bus.subscribe(["task.*"], handler)
    await bus.publish(Event(kind="task.created"))
    await bus.publish(Event(kind="model.call"))
    await bus.publish(Event(kind="task.completed"))
    await bus.drain()
    assert seen == ["task.created", "task.completed"]


async def test_broken_subscriber_does_not_stop_bus(bus):
    good: list[str] = []

    async def bad(e: Event) -> None:
        raise RuntimeError("boom")

    async def ok(e: Event) -> None:
        good.append(e.id)

    bus.subscribe(["*"], bad)
    bus.subscribe(["*"], ok)
    await bus.publish(Event(kind="x"))
    await bus.publish(Event(kind="y"))
    await bus.drain()
    assert len(good) == 2


async def test_store_persists_and_queries(bus, store):
    e1 = Event(kind="task.created", task_id="t1", correlation_id="c1")
    e2 = Event(kind="model.call", task_id="t1", correlation_id="c1")
    e3 = Event(kind="task.created", task_id="t2", correlation_id="c2")
    for e in (e1, e2, e3):
        await bus.publish(e)
    await bus.drain()

    assert [e.id for e in store.query(task_id="t1")] == [e1.id, e2.id]
    assert [e.kind for e in store.query(correlation_id="c1")] == \
        ["task.created", "model.call"]
    assert len(store.query(kind="task.created")) == 2


async def test_store_append_idempotent():
    store = SQLiteEventStore(":memory:")
    e = Event(kind="x")
    store.append(e)
    store.append(e)
    assert len(store.query()) == 1


def test_ids_are_time_ordered():
    a, b = Event(kind="a"), Event(kind="b")
    assert a.id < b.id or a.id[:12] == b.id[:12]  # same-ms ties allowed


async def test_events_are_frozen():
    e = Event(kind="x")
    try:
        e.kind = "y"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised


async def test_close_drains(bus):
    seen = []

    async def slow(e: Event) -> None:
        await asyncio.sleep(0.01)
        seen.append(e.id)

    bus.subscribe(["*"], slow)
    for _ in range(5):
        await bus.publish(Event(kind="x"))
    await bus.close()
    assert len(seen) == 5
