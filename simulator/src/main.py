import asyncio
import logging
import signal

from src.config import SimulatorConfig
from src.publisher import connect_with_retry, run_satellite
from src.satellite import CubeSat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = SimulatorConfig()

    logger.info(
        "CubeSat Simulator | satellites=%s interval=%.1fs fault_prob=%.4f",
        config.satellite_ids,
        config.interval_s,
        config.fault_probability,
    )

    nc = await connect_with_retry(config.nats_url)
    js = nc.jetstream()

    satellites = [
        CubeSat(
            satellite_id=sat_id,
            fault_probability=config.fault_probability,
            safe_recovery_s=config.safe_recovery_s,
        )
        for sat_id in config.satellite_ids
    ]

    tasks = [
        asyncio.create_task(run_satellite(js, sat, config.interval_s), name=sat.satellite_id)
        for sat in satellites
    ]

    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def _handle_signal() -> None:
        if not stop.done():
            stop.set_result(None)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass  # Windows — handled by KeyboardInterrupt below

    try:
        await stop
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down simulator...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
