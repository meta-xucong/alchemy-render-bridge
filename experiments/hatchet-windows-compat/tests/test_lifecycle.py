"""Real process/IPC platform tests, with an explicit no-network Client stub.
Not an engine integration, credential, console-signal or active-drain test.
"""
import asyncio
import importlib
import multiprocessing
import queue
import signal
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

class NoNetworkClient:
    def __init__(self, *args, **kwargs):
        pass

class FakeStream:
    def __init__(self): self.stopped = False; self.stop_signal = False; self.cleaned = False
    def stop_stream(self): self.stopped = True
    def cleanup(self): self.cleaned = True


def config():
    from hatchet_sdk.config import ClientConfig, HealthcheckConfig
    # Deliberately invalid token; never passed to a real Client/transport.
    return ClientConfig.model_construct(token='INVALID_TEST_ONLY_NO_AUTHORITY',
        tenant_id='test-only', host_port='127.0.0.1:1',
        server_url='http://127.0.0.1:1',
        healthcheck=HealthcheckConfig(enabled=False), namespace='')


def listener_module(legacy):
    return importlib.import_module('hatchet_sdk.deprecated.action_listener_process' if legacy else 'hatchet_sdk.worker.action_listener_process')


def construct_listener(legacy, ctx, event_queue, stop_event):
    module=listener_module(legacy)
    cls=module.LegacyWorkerActionListenerProcess if legacy else module.WorkerActionListenerProcess
    queues=[ctx.Queue(),ctx.Queue()]
    args=dict(name='offline-probe',actions=[],config=config(),action_queue=queues[0],
              event_queue=event_queue,handle_kill=False,debug=False,labels=[],
              worker_id_queue=queues[1],stop_event=stop_event)
    args.update({'slots':1} if legacy else {'slot_config':{'default':1}})
    try:
        with patch.object(module,'Client',NoNetworkClient):
            instance=cls(**args)
        return instance,queues
    except BaseException:
        for q in queues:q.close();q.join_thread()
        raise


def lifecycle_child(legacy, event_queue, stop_event, results):
    async def run():
        ctx=multiprocessing.get_context('spawn')
        instance,queues=construct_listener(legacy,ctx,event_queue,stop_event)
        stream=FakeStream(); instance.listener=stream
        waiter=asyncio.create_task(instance._wait_for_stop_event())
        sender=asyncio.create_task(instance.start_event_send_loop())
        results.put(('ready',sys.platform))
        await asyncio.wait_for(waiter,10)
        if not (stream.stop_signal if legacy else stream.stopped) or not instance.killing: raise AssertionError('Event did not stop stream')
        if sender.done(): raise AssertionError('Event stopped send loop before STOP_LOOP')
        results.put(('stream_stopped',True))
        await asyncio.wait_for(sender,10)
        if not legacy: instance.finalize_listener_cleanup()
        if not stream.cleaned: raise AssertionError('final cleanup was not called')
        for q in queues:q.close();q.join_thread()
        results.put(('done',True))
    try: asyncio.run(run())
    except BaseException as exc:
        results.put(('error',repr(exc)))
        raise
    finally:
        results.close();results.join_thread()


def worker_child(results):
    from hatchet_sdk.worker import worker as mod
    with patch.object(mod,'Client',NoNetworkClient):
        instance=mod.Worker('offline-probe',config(),{'default':1},handle_kill=False)
    assert instance._ctx.get_start_method()=='spawn'
    assert not instance._stop_listener_event.is_set()
    instance._stop_listener_event.set()
    instance._close_queues()
    instance._worker_id_queue.close();instance._worker_id_queue.join_thread()
    results.put(('done',sys.platform));results.close();results.join_thread()


class LifecycleTests(unittest.TestCase):
    def test_worker_constructor_in_spawn_child(self):
        ctx=multiprocessing.get_context('spawn'); results=ctx.Queue()
        proc=ctx.Process(target=worker_child,args=(results,));proc.start()
        try:
            self.assertEqual(results.get(timeout=20)[0],'done')
            proc.join(10);self.assertEqual(proc.exitcode,0)
        finally:
            if proc.is_alive(): proc.terminate();proc.join(5)
            results.close();results.join_thread();proc.close()

    def check_listener(self,legacy):
        from hatchet_sdk.utils.typing import STOP_LOOP
        ctx=multiprocessing.get_context('spawn'); events=ctx.Queue();results=ctx.Queue();stop=ctx.Event()
        proc=ctx.Process(target=lifecycle_child,args=(legacy,events,stop,results));proc.start()
        try:
            self.assertEqual(results.get(timeout=20)[0],'ready')
            stop.set()
            self.assertEqual(results.get(timeout=15),('stream_stopped',True))
            events.put(STOP_LOOP)
            self.assertEqual(results.get(timeout=15),('done',True))
            proc.join(10);self.assertEqual(proc.exitcode,0)
        finally:
            stop.set();events.put(STOP_LOOP)
            if proc.is_alive(): proc.terminate();proc.join(5)
            events.close();events.join_thread();results.close();results.join_thread();proc.close()

    def test_current_listener_spawn_event_and_queue_stop(self): self.check_listener(False)
    def test_legacy_listener_spawn_event_and_queue_stop(self): self.check_listener(True)

    def test_worker_without_sigquit_still_registers_int_and_term(self):
        from hatchet_sdk.worker.worker import Worker
        fake=SimpleNamespace(SIGTERM=15,SIGINT=2,signal=unittest.mock.Mock())
        instance=SimpleNamespace(_config=SimpleNamespace(force_shutdown_on_shutdown_signal=False),
            _handle_exit_signal=object(),_handle_force_quit_signal=object())
        with patch('hatchet_sdk.worker.worker.signal',fake): Worker._setup_signal_handlers(instance)
        self.assertEqual([call.args[0] for call in fake.signal.call_args_list],[15,2])

    def test_worker_registration_errors_are_not_swallowed(self):
        from hatchet_sdk.worker.worker import Worker
        fake=SimpleNamespace(SIGTERM=15,SIGINT=2,signal=unittest.mock.Mock(side_effect=ValueError('not main thread')))
        instance=SimpleNamespace(_config=SimpleNamespace(force_shutdown_on_shutdown_signal=False),
            _handle_exit_signal=object(),_handle_force_quit_signal=object())
        with patch('hatchet_sdk.worker.worker.signal',fake):
            with self.assertRaisesRegex(ValueError,'not main thread'): Worker._setup_signal_handlers(instance)

    def check_listener_branch(self,legacy,platform,fail=False):
        module=listener_module(legacy);ctx=multiprocessing.get_context('spawn');q=ctx.Queue();event=ctx.Event()
        loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
        try:
            with patch.object(module,'sys',SimpleNamespace(platform=platform)), patch.object(module.signal,'signal') as sig, patch.object(loop,'add_signal_handler') as add:
                if fail:
                    (sig if platform=='win32' else add).side_effect=ValueError('register failed')
                    with self.assertRaisesRegex(ValueError,'register failed'):construct_listener(legacy,ctx,q,event)
                else:
                    instance,qs=construct_listener(legacy,ctx,q,event)
                    self.assertEqual(sig.call_count,2 if platform=='win32' else 0)
                    self.assertEqual(add.call_count,0 if platform=='win32' else 2)
                    for item in qs:item.close();item.join_thread()
        finally:q.close();q.join_thread();loop.close();asyncio.set_event_loop(None)

    def test_branch_fixtures_preserve_global_sys_platform(self):
        real_platform=sys.platform
        original_constructor=construct_listener
        for legacy in (False,True):
            simulated_platform='linux' if real_platform=='win32' else 'win32'
            def checked_constructor(*args,**kwargs):
                self.assertEqual(sys.platform,real_platform)
                self.assertIsNot(listener_module(legacy).sys,sys)
                self.assertEqual(listener_module(legacy).sys.platform,simulated_platform)
                return original_constructor(*args,**kwargs)
            with self.subTest(legacy=legacy), patch(__name__+'.construct_listener',side_effect=checked_constructor) as checked:
                self.check_listener_branch(legacy,simulated_platform)
                checked.assert_called_once()
            self.assertEqual(sys.platform,real_platform)

    def test_utf8_logging_stream_launch_contract(self):
        # The SDK emits emoji in its normal logger. CI and documented launches
        # must use UTF-8 rather than silently dropping encoding errors.
        import codecs
        self.assertEqual(codecs.lookup(sys.stderr.encoding).name,'utf-8')
        self.assertEqual(codecs.lookup(sys.stdout.encoding).name,'utf-8')
        self.assertEqual('🪓'.encode(sys.stderr.encoding).decode('utf-8'),'🪓')

    def test_current_platform_branches_and_errors(self):
        for platform in ('win32','linux'):
            with self.subTest(platform=platform):
                self.check_listener_branch(False,platform);self.check_listener_branch(False,platform,True)
    def test_legacy_platform_branches_and_errors(self):
        for platform in ('win32','linux'):
            with self.subTest(platform=platform):
                self.check_listener_branch(True,platform);self.check_listener_branch(True,platform,True)

if __name__=='__main__': unittest.main()
