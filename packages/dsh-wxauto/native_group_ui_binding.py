"""Private capabilities for first UI handoff and committed monitor restoration.

Neither capability sends, navigates, admits a group, nor submits a message.
A committed source binding deliberately cannot authorize a first admission.
"""
from dataclasses import dataclass, field
import copy
import re

from native_group_handoff import revalidate_handoff
from native_group_text_authority import NativeGroupTextAuthority
from native_owner_text_claim import _read

_TOKEN = object()


def _need(value):
    if not value:
        raise RuntimeError('native-group-ui-binding-unverified')


@dataclass(frozen=True)
class RegisteredGroupHandoff:
    account_id: str
    native_chat_key: str
    target: str
    session_id: str
    claim_id: str
    claim_hash: str
    snapshot_id: str
    _handoff: object = field(repr=False, compare=False)
    _config: dict = field(repr=False, compare=False)
    _token: object = field(repr=False, compare=False)


def confirm_registered_handoff(config, handoff, result, *, routes, registry, previous=None):
    """Called immediately after the sole worker's actual register_group result."""
    h = revalidate_handoff(handoff, target=handoff.target, native_chat_key=handoff.native_chat_key)
    _need(h.account_id == config.get('accountId') and routes.get(h.target) == 'group'
          and registry.get(h.target) == h.native_chat_key)
    _need(type(result) is dict and result.get('ok') is True
          and result.get('actualUiGroupConfirmed') is True and result.get('pending') is False)
    if result.get('confirmationReused') is True:
        return revalidate_registered_handoff(previous, config=config,
            native_chat_key=h.native_chat_key, claim_id=h.claim_id, claim_hash=h.claim_hash)
    _need(result.get('checkedThisCall') is True and result.get('originalChatRestored') is True
          and result.get('nativeQueuedHandoff') is True)
    return RegisteredGroupHandoff(h.account_id, h.native_chat_key, h.target, h.session_id,
        h.claim_id, h.claim_hash, h.snapshot_id, h, copy.deepcopy(config), _TOKEN)


def revalidate_registered_handoff(proof, *, config, native_chat_key, claim_id, claim_hash):
    _need(type(proof) is RegisteredGroupHandoff and proof._token is _TOKEN)
    _need(proof._config == config and proof.account_id == config.get('accountId')
          and (proof.native_chat_key, proof.claim_id, proof.claim_hash)
          == (native_chat_key, claim_id, claim_hash))
    h = revalidate_handoff(proof._handoff, target=proof.target, native_chat_key=native_chat_key)
    _need((h.account_id, h.session_id, h.claim_id, h.claim_hash, h.snapshot_id)
          == (proof.account_id, proof.session_id, claim_id, claim_hash, proof.snapshot_id))
    return proof


def verify_native_group_text_ui_handoff(config, proof, source_proof):
    p = revalidate_registered_handoff(proof, config=config,
        native_chat_key=source_proof.native_chat_key,
        claim_id=source_proof.claim_id, claim_hash=source_proof.claim_hash)
    _need((p.account_id, p.target, p.session_id, p.snapshot_id)
          == (source_proof.account_id, source_proof.target, source_proof.session_id, source_proof.snapshot_id))
    return p


@dataclass(frozen=True)
class NativeMonitorBinding:
    account_id: str
    native_chat_key: str
    target: str
    session_id: str
    chat_id: str
    authority_epoch: str
    marker_sha256: str
    _config: dict = field(repr=False, compare=False)
    _token: object = field(repr=False, compare=False)


def verify_native_monitor_binding(config, native_chat_key, authority_epoch, target):
    """Read actual committed source and canonical route; no queued claim needed."""
    _need(isinstance(authority_epoch, str) and re.fullmatch('[a-f0-9]{32}', authority_epoch))
    controller = NativeGroupTextAuthority(config, native_chat_key)
    try:
        state = controller.restore()
        _need(state.get('mode') == 'native' and state.get('authorityEpoch') == authority_epoch
              and state.get('target') == target)
        settings = controller.settings()
        marker, token = _read(controller.paths()[1])
        _need(marker.get('authorityEpoch') == authority_epoch and marker.get('active') is True)
        return NativeMonitorBinding(settings['accountId'], native_chat_key, target,
            settings['sessionId'], settings['chatId'], authority_epoch, token[0],
            copy.deepcopy(config), _TOKEN)
    finally:
        controller.close()


def revalidate_native_monitor_binding(proof, *, target, native_chat_key, account_id):
    _need(type(proof) is NativeMonitorBinding and proof._token is _TOKEN)
    _need((proof.target, proof.native_chat_key, proof.account_id) == (target, native_chat_key, account_id))
    current = verify_native_monitor_binding(proof._config, native_chat_key, proof.authority_epoch, target)
    _need(current == proof)
    return proof
