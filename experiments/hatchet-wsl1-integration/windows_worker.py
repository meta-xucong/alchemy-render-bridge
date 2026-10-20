"""Native Windows real-engine smoke. Credentials enter only through stdin."""
import asyncio
import importlib.metadata
import json
import multiprocessing
import os
import sys
from datetime import timedelta

# SDK and spawned listener output is intentionally discarded, never artifacted.
CONTROL = sys.stdout
SINK = open(os.devnull, 'w')
sys.stdout = SINK
sys.stderr = SINK


def emit(value):
    CONTROL.write(json.dumps(value) + '\n')
    CONTROL.flush()


def main():
    from hatchet_sdk import Hatchet, Context, EmptyModel
    from hatchet_sdk.config import ClientConfig, ClientTLSConfig, HealthcheckConfig
    result = {'event': 'worker_complete', 'status': 'blocked', 'platform': sys.platform}
    tasks = []
    observed_children = []
    loop = None
    try:
        if sys.platform != 'win32' or importlib.metadata.version('hatchet-sdk') != '1.42.1+bridgewin1':
            raise RuntimeError('Require native Windows and exact SDK candidate')
        private = json.loads(sys.stdin.readline())
        port = private['grpc_port']
        if type(port) is not int or not 1 <= port <= 65535:
            raise RuntimeError('Invalid engine endpoint')
        client = Hatchet(config=ClientConfig(token=private['token'], tenant_id=private['tenant_id'],
                         host_port=f'127.0.0.1:{port}', server_url='http://127.0.0.1:1',
                         tls_config=ClientTLSConfig(strategy='none'),
                         healthcheck=HealthcheckConfig(enabled=False)))
        private = None
        class Input(EmptyModel):
            marker: str

        @client.task(name='bridge-wsl1-native-synthetic', input_validator=Input, retries=0,
                     execution_timeout=timedelta(seconds=20), schedule_timeout=timedelta(seconds=40))
        def synthetic(input: Input, ctx: Context) -> dict:
            return {'marker': input.marker, 'renderer': 'fake', 'platform': sys.platform, 'pid': os.getpid()}

        async def drive():
            try:
                for number in range(3):
                    marker = f'bridge-native-{number}'
                    value = await asyncio.wait_for(asyncio.to_thread(synthetic.run, Input(marker=marker)), timeout=50)
                    if (value.get('marker') != marker or value.get('renderer') != 'fake'
                            or value.get('platform') != 'win32' or value.get('pid') != os.getpid()):
                        raise RuntimeError('Unexpected real-engine task result')
                    tasks.append(value)
                    if number == 0:
                        emit({'event': 'worker_registered_task_completed', 'native_pid': os.getpid()})
                        approval = await asyncio.wait_for(asyncio.to_thread(sys.stdin.readline), timeout=20)
                        if approval.strip() != 'worker-listeners-verified':
                            raise RuntimeError('Missing worker listener verification')
                result['tasks'] = tasks
            except Exception as exc:
                result['task_error_type'] = type(exc).__name__
            finally:
                # Public lifecycle API on the worker's own running event loop.
                # All submitted tasks finished; REST pause is deliberately unavailable.
                result['graceful_stop_requested'] = True
                observed_children.extend(multiprocessing.active_children())
                try:
                    from worker_lifecycle import orderly_stop
                    await orderly_stop(worker, result)
                except Exception as exc:
                    result['shutdown_error_type'] = type(exc).__name__

        async def lifespan():
            nonlocal loop
            loop = asyncio.get_running_loop()
            driver = asyncio.create_task(drive())
            # Keep a strong reference without awaiting ourselves during cleanup.
            running.append(driver)
            yield

        running = []
        worker = client.worker('bridge-wsl1-native-one-slot', slots=1, workflows=[synthetic], lifespan=lifespan)
        try:
            worker.start()
            result['worker_start_returned'] = True
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise RuntimeError('Worker exited nonzero')
            result['worker_start_zero_exit'] = True
        for child in observed_children:
            child.join(timeout=5)
        result['observed_listener_exits'] = [{'pid': child.pid, 'exitcode': child.exitcode} for child in observed_children]
        result['active_children_after_stop'] = [child.pid for child in multiprocessing.active_children()]
        result['task_count'] = len(tasks)
        result['sdk_version'] = importlib.metadata.version('hatchet-sdk')
        if (len(tasks) == 3 and result.get('graceful_stop_completed') is True
                and observed_children and all(child.exitcode == 0 for child in observed_children)
                and not result['active_children_after_stop'] and not result.get('task_error_type')):
            result['status'] = 'passed'
    except Exception as exc:
        result['error_type'] = type(exc).__name__
    finally:
        if loop is not None and not loop.is_running():
            loop.close()
        emit(result)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
