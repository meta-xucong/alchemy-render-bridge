"""Build an isolated versioned test wheel from the exact official SDK wheel.
No network, installation, runtime monkeypatch or credentials are used here.
"""
import argparse, base64, csv, difflib, hashlib, io, json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
UPSTREAM_SHA = 'da9b111aac64fb678f371b92862f48f475631994ba2340ebea28c94de21a61fd'
VERSION = '1.42.1+bridgewin1'
OLD_INFO = 'hatchet_sdk-1.42.1.dist-info/'
NEW_INFO = 'hatchet_sdk-' + VERSION + '.dist-info/'

def sha(data): return hashlib.sha256(data).hexdigest()

def build(wheel, output):
    raw = wheel.read_bytes()
    if sha(raw) != UPSTREAM_SHA:
        raise ValueError('official wheel SHA256 mismatch; refusing to patch')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        entries = {n: z.read(n) for n in z.namelist() if not n.endswith('/')}
    changed = {}
    paths = ['hatchet_sdk/worker/worker.py',
             'hatchet_sdk/worker/action_listener_process.py',
             'hatchet_sdk/deprecated/action_listener_process.py']
    for path in paths:
        before = entries[path].decode('utf-8')
        if path.endswith('/worker.py'):
            old = '        signal.signal(signal.SIGQUIT, self._handle_force_quit_signal)\n'
            new = '        if hasattr(signal, "SIGQUIT"):\n            signal.signal(signal.SIGQUIT, self._handle_force_quit_signal)\n'
            after = before.replace(old, new)
        else:
            old = ('        loop = asyncio.get_event_loop()\n'
                   '        loop.add_signal_handler(signal.SIGINT, lambda: None)\n'
                   '        loop.add_signal_handler(signal.SIGTERM, lambda: None)\n')
            new = ('        if sys.platform == "win32":\n'
                   '            # Windows asyncio has no add_signal_handler. The parent\n'
                   '            # still controls shutdown with Event and STOP_LOOP.\n'
                   '            signal.signal(signal.SIGINT, lambda *_: None)\n'
                   '            signal.signal(signal.SIGTERM, lambda *_: None)\n'
                   '        else:\n'
                   '            loop = asyncio.get_event_loop()\n'
                   '            loop.add_signal_handler(signal.SIGINT, lambda: None)\n'
                   '            loop.add_signal_handler(signal.SIGTERM, lambda: None)\n')
            after = before.replace(old, new).replace('import signal\n', 'import signal\nimport sys\n', 1)
        if before.count(old) != 1 or after == before:
            raise ValueError('unexpected pinned source layout: ' + path)
        entries[path] = after.encode()
        changed[path] = {'before_sha256': sha(before.encode()), 'after_sha256': sha(after.encode())}
        changed[path]['diff'] = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile='a/'+path, tofile='b/'+path))
    renamed = {n.replace(OLD_INFO, NEW_INFO, 1) if n.startswith(OLD_INFO) else n: data for n,data in entries.items()}
    renamed[NEW_INFO+'METADATA'] = renamed[NEW_INFO+'METADATA'].replace(b'Version: 1.42.1\n', ('Version: '+VERSION+'\n').encode(),1)
    renamed[NEW_INFO+'licenses/LICENSE'] = (ROOT/'UPSTREAM_LICENSE').read_bytes()
    renamed[NEW_INFO+'licenses/BRIDGE_PATCH_NOTICE'] = (ROOT/'NOTICE.md').read_bytes()
    renamed.pop(NEW_INFO+'RECORD')
    record = io.StringIO(newline='')
    writer = csv.writer(record, lineterminator='\n')
    for n,data in sorted(renamed.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
        writer.writerow([n,'sha256='+digest,str(len(data))])
    writer.writerow([NEW_INFO+'RECORD','',''])
    renamed[NEW_INFO+'RECORD'] = record.getvalue().encode()
    output.mkdir(parents=True,exist_ok=True)
    dest = output/('hatchet_sdk-'+VERSION+'-py3-none-any.whl')
    with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_STORED) as z:
        for n,data in sorted(renamed.items()):
            info=zipfile.ZipInfo(n,date_time=(2020,2,2,0,0,0)); info.create_system=3; info.compress_type=zipfile.ZIP_STORED; info.external_attr=0o100644<<16
            z.writestr(info,data)
    (output/'windows-signals.patch').write_text(''.join(x['diff'] for x in changed.values()),encoding='utf-8',newline='\n')
    for value in changed.values(): del value['diff']
    manifest={'upstream_version':'1.42.1','upstream_source_sha':'19a3474b7963139124151e7a3b1357b1971c2eb8',
              'upstream_wheel_sha256':UPSTREAM_SHA,'candidate_version':VERSION,
              'candidate_wheel_sha256':sha(dest.read_bytes()),'changed_sources':changed}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(manifest,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('wheel',type=Path); parser.add_argument('--output',type=Path,default=ROOT/'dist')
    args=parser.parse_args(); build(args.wheel,args.output)
