"""Original-wheel control: real Windows constructors must reproduce both bugs.
Linux must construct successfully. Client is a no-network test stub.
"""
import asyncio
import importlib.metadata
import multiprocessing
import signal
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent/'tests'))
from test_lifecycle import NoNetworkClient, config, construct_listener

def worker_probe():
    from hatchet_sdk.worker import worker as mod
    instance=mod.Worker.__new__(mod.Worker)
    try:
        with patch.object(mod,'Client',NoNetworkClient):
            mod.Worker.__init__(instance,'baseline',config(),{'default':1},handle_kill=False)
        return None
    except Exception as error:
        return error
    finally:
        if hasattr(instance,'_action_queue'): instance._close_queues()
        if hasattr(instance,'_worker_id_queue'):
            instance._worker_id_queue.close();instance._worker_id_queue.join_thread()

def main():
    assert importlib.metadata.version('hatchet-sdk')=='1.42.1'
    original={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM)}
    if hasattr(signal,'SIGQUIT'):original[signal.SIGQUIT]=signal.getsignal(signal.SIGQUIT)
    loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
    ctx=multiprocessing.get_context('spawn')
    try:
        error=worker_probe()
        if sys.platform=='win32': assert isinstance(error,AttributeError) and 'SIGQUIT' in str(error),repr(error)
        else: assert error is None,repr(error)
        for legacy in (False,True):
            q=ctx.Queue();ev=ctx.Event();error=None;queues=[]
            try:
                try: instance,queues=construct_listener(legacy,ctx,q,ev)
                except Exception as exc:error=exc
                if sys.platform=='win32': assert isinstance(error,NotImplementedError),repr(error)
                else: assert error is None,repr(error)
            finally:
                for item in queues:item.close();item.join_thread()
                q.close();q.join_thread()
        print('UPSTREAM_CONTROL_PASS platform='+sys.platform)
    finally:
        loop.close();asyncio.set_event_loop(None)
        for sig,handler in original.items():signal.signal(sig,handler)

if __name__=='__main__': main()
