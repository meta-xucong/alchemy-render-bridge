"""Small local HTTP contract demo; no Hatchet or external renderer is started.

The Windows CI preflight --engine entry point runs the same HTTP fixture with
real Hatchet and the native Windows worker. This entry point is the quick demo.
"""
import argparse
import json
from pathlib import Path
import tempfile

from node_trial_server import NodeTrial
from node_trial_client import NodeClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='New directory for the synthetic PNG and result.json')
    args = parser.parse_args()
    # Never overwrite an earlier demonstration or a user file.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    trial = NodeTrial()
    try:
        config = trial.start()
        with tempfile.TemporaryDirectory(prefix='bridge-demo-node-') as journal_root:
            node = NodeClient(config, Path(journal_root) / 'starts')
            outcomes = [node.run(key)['outcome'] for key in
                        ('happy', 'happy', 'unknown', 'unknown', 'blocked')]
            result = trial.verify()
            result.update(mode='local_http_fixture_without_hatchet', outcomes=outcomes,
                          fake_render_starts=len(node.journal.read_text().splitlines()))
            (args.output_dir / 'synthetic.png').write_bytes(trial.artifact_content)
    finally:
        trial.close()
    result['temporary_runtime_removed'] = not trial.root.exists()
    (args.output_dir / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
