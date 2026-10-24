"""Bounded public attention vocabulary; never forwards CLI text or credentials."""
ATTENTION_MESSAGES = {
    'profile_not_enabled': 'Enable the matching profile in this node configuration before resuming the same task.',
    'insufficient_disk': 'Free local disk space, then resume the same task.',
    'codex_local_acceptance_required': 'Complete local Codex capability and tool-policy acceptance before resuming.',
    'codex_version_not_validated': 'Use the validated local Codex version before resuming.',
    'codex_requested_user_attention': 'Inspect the local Codex approval or input request; do not start another generation.',
    'codex_single_image_event_required': 'The expected single original image was not established. Reconcile this attempt locally.',
    'codex_original_path_missing': 'The completed original file is unavailable. Reconcile this attempt locally.',
    'output_format_mismatch': 'The retained original does not match the requested format. Review it without regenerating.',
    'import_original': 'Generate manually and import one confirmed original for this task.',
    'awaiting_user_submission': 'Submit the exported task manually, then import its original.',
    'execution_requires_reconciliation': 'Execution may have started. Reconcile this same attempt; do not regenerate.',
    'codex_prior_start_requires_reconciliation': 'A prior Codex start needs reconciliation, not another turn.',
    'codex_execution_requires_reconciliation': 'Codex execution is uncertain. Reconcile this same attempt.',
    'codex_turn_not_confirmed_complete': 'Codex completion was not confirmed. Reconcile this same turn.',
    'attempt_already_started': 'This attempt has already started. Recover its evidence rather than executing again.',
}
