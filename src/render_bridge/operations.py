"""Local single-node control and bounded metadata audit; no credentials are read."""
import argparse
import json

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('node-status', 'revoke-node', 'reactivate-node'):
        sub.add_parser(command)
    audit = sub.add_parser('audit')
    audit.add_argument('--limit', type=int, default=50)
    args = parser.parse_args(argv)
    from .runtime import RuntimeConfig
    from .domain import BridgeStore, SourceScope, DomainError
    try:
        config = RuntimeConfig.load(args.config)
        database = config.data_dir / 'bridge.sqlite'
        if not database.is_file():
            raise ValueError('Configured Bridge database must already exist')
        store = BridgeStore(database, config.data_dir / 'artifacts',
                            enabled_profiles=config.enabled_profiles,
                            accepting_new_tasks=config.accepting_new_tasks)
        scope = SourceScope(config.source_id, config.workspace_id)
        if args.command == 'audit':
            result = store.audit_events(scope, args.limit)
        elif args.command == 'node-status':
            result = store.configured_node_status(scope, config.node_id, config.executor_instance)
        else:
            result = store.set_node_active(scope, config.node_id, config.executor_instance,
                                           args.command == 'reactivate-node')
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, DomainError):
        print(json.dumps({'error': 'operation_failed'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
