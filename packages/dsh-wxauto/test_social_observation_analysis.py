import unittest
from social_observation_analysis import METHODS, analyze


class OfflineObservationTests(unittest.TestCase):
    def test_control_inventory_is_useful_but_never_promotes_live_capability(self):
        sdk = {'packageVersion': 'fixture', 'methods': {key: {'available': True} for key in METHODS}}
        report = analyze([('fixture.json', {'source': 'wxauto-uia', 'foregroundClass': 'mmui::SnsPublishPanel',
            'controls': [{'class': 'mmui::XOutlineButton', 'name': '发表'},
                         {'class': 'mmui::XValidatorTextEdit', 'name': ''}], 'passed': True, 'liveReady': True})], sdk)
        self.assertEqual(report['momentPublicControlsSeen']['finalPublish'], ['fixture.json'])
        self.assertEqual(report['publishPanelForegroundSeen'], ['fixture.json'])
        self.assertTrue(report['actions']['publish_moment']['sdkMethodAvailable'])
        self.assertTrue(all(not value['liveReady'] for value in report['actions'].values()))
        self.assertFalse(report['liveAcceptance'])

    def test_untrusted_or_irrelevant_names_are_not_emitted(self):
        private = 'private-person-name-must-not-be-emitted'
        report = analyze([('irrelevant.json', {'source': 'wxauto-uia',
            'controls': [{'class': 'mmui::XTabBarItem', 'name': private}]}),
            ('web.json', {'source': 'web', 'controls': [{'class': 'mmui::XOutlineButton', 'name': '发表'}]})], {})
        self.assertEqual(report['momentPublicControlsSeen']['finalPublish'], [])
        self.assertNotIn(private, str(report))
        self.assertEqual(len(report['actions']), 5)

    def test_missing_patterns_author_and_semantic_new_post_evidence_stay_explicit(self):
        report = analyze([], {'packageVersion': '41.1.7', 'methods': {}})
        missing = report['actions']['publish_moment']['missingEvidence']
        self.assertTrue(any('ValuePattern' in item for item in missing))
        self.assertTrue(any('author' in item for item in missing))
        self.assertTrue(any('runtimeID' in item for item in missing))
        self.assertTrue(all(not value['sdkMethodAvailable'] for value in report['actions'].values()))


if __name__ == '__main__': unittest.main()
