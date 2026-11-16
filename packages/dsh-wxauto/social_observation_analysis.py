"""Offline inventory analysis only; never imports/connects wxauto or enables UI.

Flat class/name inventories establish that a control was seen. They do not
establish ancestry, supported patterns, account identity or a submitted result.
"""
import argparse
import json
from pathlib import Path

METHODS = {'publish_moment': 'PublishMoment', 'set_group_announcement': 'SetGroupAnnouncement',
           'create_group': 'CreateGroup', 'add_friend': 'AddNewFriend', 'accept_friend': 'GetNewFriends'}
PUBLIC_CONTROLS = {
    'discover': ('mmui::XTabBarItem', '发现'),
    'moments': ('mmui::ExtensionDiscoverContentCell', '朋友圈'),
    'timelinePublishEntry': ('mmui::XTabBarItem', '发表'),
    'textEntry': ('mmui::XMenuView', '发表文字'),
    'textEditor': ('mmui::XValidatorTextEdit', None),
    'privacy': ('mmui::PublishPrivacyView', None),
    'finalPublish': ('mmui::XOutlineButton', '发表'),
    'cancel': ('mmui::XOutlineButton', '取消'),
}
MISSING = {
    'publish_moment': [
        'Full SNS toolbar/menu/editor ancestry and uniqueness in the actual main UI tree.',
        'Actual XValidatorTextEdit ValuePattern support and exact draft SetValue/Value readback.',
        'Actual own-account author identification and independent own-timeline reader.',
        'New-post proof beyond a changed runtimeID: actual creation/order boundary or stable post identity.',
        'A plan-bound, version/binding-hash-matched real before/after acceptance; media editor remains unobserved.',
    ],
    'set_group_announcement': [
        'Allowed exact group identity/type and actual announcement navigation controls.',
        'Complete old text, editor permissions, exact new draft readback and unchanged-old-text check.',
        'Actual submit control and independent reopening/readback in that same group.',
    ],
    'create_group': [
        'Actual creation entry, contact selection UI and unambiguous identities of all approved contacts.',
        'Exact selected set before submission and new-group identity/member readback afterwards.',
        'Persisted owner-private approval for this exact plan/hash; creation does not extend the chat allowlist.',
    ],
    'add_friend': [
        'Actual search/result identity and full verification-message/remark/tag editor readback.',
        'Exact application submit control and independent application-sent evidence.',
        'Persisted owner-private approval for this exact plan/hash; sent application is not accepted friendship.',
    ],
    'accept_friend': [
        'Actual pending-request UI and stable request identity, applicant and verification text.',
        'Exact approved request, remark/tags, accept control and independent accepted/contact readback.',
        'Persisted owner-private approval for this exact plan/hash; same display name alone is insufficient.',
    ],
}


def analyze(documents, sdk):
    seen = {name: [] for name in PUBLIC_CONTROLS}
    publish_panel = []
    for label, document in documents:
        if not isinstance(document, dict) or document.get('source') != 'wxauto-uia':
            continue
        if document.get('foregroundClass') == 'mmui::SnsPublishPanel':
            publish_panel.append(label)
        controls = document.get('controls')
        if not isinstance(controls, list):
            continue
        for control in controls:
            if not isinstance(control, dict):
                continue
            for key, (class_name, public_name) in PUBLIC_CONTROLS.items():
                if control.get('class') == class_name and (public_name is None or control.get('name') == public_name):
                    if label not in seen[key]:
                        seen[key].append(label)
    methods = sdk.get('methods', {}) if isinstance(sdk, dict) else {}
    return {'kind': 'offline-social-observation-analysis', 'liveAcceptance': False,
            'sdkVersion': sdk.get('packageVersion') if isinstance(sdk, dict) else None,
            'momentPublicControlsSeen': seen, 'publishPanelForegroundSeen': publish_panel,
            'actions': {action: {'sdkMethod': method,
                'sdkMethodAvailable': methods.get(action, {}).get('available') is True,
                'liveReady': False, 'missingEvidence': missing}
                for action, method in METHODS.items() for missing in [MISSING[action]]},
            'limits': ['No UI instantiated, clicked, typed or submitted by this analyzer.',
                       'Flat control inventories and SDK method presence cannot authorize or verify an action.',
                       'No acceptance receipt, binding registration or production setting is generated.',
                       'RuntimeID changes are not proof of a new semantic post after UI virtualization.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-json', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('artifacts', nargs='+')
    args = parser.parse_args()
    sdk = json.loads(Path(args.sdk_json).read_text('utf-8-sig'))
    documents = [(Path(p).name, json.loads(Path(p).read_text('utf-8-sig'))) for p in args.artifacts]
    Path(args.output).write_text(json.dumps(analyze(documents, sdk), ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
