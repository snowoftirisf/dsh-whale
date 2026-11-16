"""Read-only privacy candidates grounded in inspect-002.json; no UI actions.

The returned option is a selector candidate, NOT selected-state evidence.
Caller must bind the owned draft, PID/foreground/geometry and re-observe the
composer summary after any separately authorized selection. No publish API.
"""
from social_moments_binding import privacy_state


def _walk(root):
    todo = [(root, 0)]; result = []
    while todo:
        item, depth = todo.pop()
        if depth > 14 or len(result) >= 128:
            raise RuntimeError('social-moments-privacy-tree-budget')
        result.append(item)
        if item.ControlTypeName not in ('TextControl', 'CheckBoxControl', 'ButtonControl'):
            todo.extend((child, depth + 1) for child in reversed(item.GetChildren()))
    return result


def _one(items, predicate):
    found = [item for item in items if predicate(item)]
    if len(found) != 1:
        raise RuntimeError('social-moments-private-option-unverified')
    return found[0]


def _owned_layer(panel, expected_panel_runtime_id):
    if (panel.ClassName != 'mmui::SnsPublishPanel'
            or list(panel.GetRuntimeId()) != list(expected_panel_runtime_id)
            or not expected_panel_runtime_id):
        raise RuntimeError('social-moments-owned-draft-mismatch')
    return _one(panel.GetChildren(), lambda c: c.ClassName == 'mmui::XLayerWindow'
                and c.Name == 'Weixin' and c.ControlTypeName == 'WindowControl')


def private_layer_controls(panel, *, expected_panel_runtime_id):
    """Return exact layer-local 确定/取消 candidates, never composer actions.

    inspect-002.json records the confirmation Name as 确定 (not 确认).
    No input, selection, cancellation or click is performed here.
    """
    layer = _owned_layer(panel, expected_panel_runtime_id)
    nodes = _walk(layer)
    buttons = {}
    for key, name in (('confirm', '确定'), ('cancel', '取消')):
        buttons[key] = _one(nodes, lambda c: c.ClassName == 'mmui::XOutlineButton'
                            and c.ControlTypeName == 'ButtonControl' and c.Name == name)
    return {'layer': layer, **buttons}


def private_option_candidate(panel, *, expected_panel_runtime_id):
    """Locate only an observed exact private radio in the owned composer.

    Does not inspect unsupported SelectionPattern/checkbox glyphs, click the
    option, close the layer, or assert privacy. Runtime IDs are caller-bound,
    not persisted selectors across sessions.
    """
    return _option_candidate(panel, expected_panel_runtime_id, '私密', '仅自己可见')


def public_option_candidate(panel, *, expected_panel_runtime_id):
    """Observed public radio, for restoring an explicitly observed old setting."""
    return _option_candidate(panel, expected_panel_runtime_id, '公开', '所有朋友可见')


def _option_candidate(panel, expected_panel_runtime_id, name, description):
    # Scope to the popup rather than scanning unrelated editor/detail trees.
    layer = _owned_layer(panel, expected_panel_runtime_id)
    nodes = _walk(layer)
    option = _one(nodes, lambda c: c.ClassName == 'mmui::PublishPrivacySelection'
                  and c.ControlTypeName == 'RadioButtonControl' and c.Name == name)
    labels = [c.Name for c in option.GetChildren()
              if c.ClassName == 'mmui::XTextView' and c.ControlTypeName == 'TextControl'
              and c.Name]
    if labels != [name, description]:
        raise RuntimeError('social-moments-private-option-unverified')
    return option


def read_private_summary(panel, *, expected_panel_runtime_id):
    """Return explicit private-summary evidence; never infer from option existence."""
    return read_visibility_summary(panel, expected_panel_runtime_id=expected_panel_runtime_id,
                                   expected_textSequence=['谁可以看', '私密'])


def read_visibility_summary(panel, *, expected_panel_runtime_id, expected_textSequence):
    """Verify only the two observed exact vocabulary choices, never custom scopes."""
    if (not isinstance(expected_textSequence, (list, tuple))
            or list(expected_textSequence) not in (['谁可以看', '公开'], ['谁可以看', '私密'])):
        raise RuntimeError('social-moments-visibility-expectation-unsupported')
    if (panel.ClassName != 'mmui::SnsPublishPanel' or not expected_panel_runtime_id
            or list(panel.GetRuntimeId()) != list(expected_panel_runtime_id)):
        raise RuntimeError('social-moments-owned-draft-mismatch')
    if any(c.ClassName == 'mmui::XLayerWindow' for c in panel.GetChildren()):
        raise RuntimeError('social-moments-privacy-layer-still-open')
    privacy = _one(_walk(panel), lambda c: c.ClassName == 'mmui::PublishPrivacyView')
    value = privacy_state(privacy)
    if value['textSequence'] != list(expected_textSequence):
        raise RuntimeError('social-moments-private-summary-required')
    return {'source': 'wxauto-uia', 'scope': 'owned-draft-privacy-summary',
            'privateSummaryObserved': value['textSequence'][1] == '私密',
            'visibilitySummaryObserved': value['textSequence'][1], 'publicationConfirmed': False,
            'panelRuntimeId': list(panel.GetRuntimeId()), 'privacy': value}
