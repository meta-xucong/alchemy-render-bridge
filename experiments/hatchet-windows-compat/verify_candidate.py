"""Validate deterministic candidate bytes, RECORD and immutable source map."""
import base64,csv,hashlib,io,json,zipfile
from pathlib import Path
from build_candidate import ROOT
expected=json.loads((ROOT/'expected_manifest.json').read_text())
actual=json.loads((ROOT/'dist/manifest.json').read_text())
assert actual==expected,'candidate manifest mismatch'
assert (ROOT/'dist/windows-signals.patch').read_bytes()==(ROOT/'windows-signals.patch').read_bytes(),'reviewed patch mismatch'
wheel=ROOT/'dist'/('hatchet_sdk-'+expected['candidate_version']+'-py3-none-any.whl')
assert hashlib.sha256(wheel.read_bytes()).hexdigest()==expected['candidate_wheel_sha256']
with zipfile.ZipFile(wheel) as z:
    names=z.namelist(); record=[name for name in names if name.endswith('.dist-info/RECORD')][0]
    rows=list(csv.reader(io.StringIO(z.read(record).decode())))
    assert set(row[0] for row in rows)==set(names)
    for name,digest,size in rows:
        if name==record:assert not digest and not size;continue
        data=z.read(name);encoded=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
        assert digest=='sha256='+encoded and int(size)==len(data),name
print('CANDIDATE_IDENTITY_AND_RECORD_PASS')
