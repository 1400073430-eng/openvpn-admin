import json
import subprocess
import unittest
from unittest.mock import patch
from backend.services import node_check


class NodeCheckTests(unittest.TestCase):
    def test_partial_failure_and_cache(self):
        metadata = json.dumps({'success': True, 'ip': '8.8.8.8', 'country': '<test>', 'connection': {'asn': 15169}})
        with patch.object(node_check, '_cached', None), patch.object(node_check, '_fetch', side_effect=['8.8.8.8', metadata, subprocess.TimeoutExpired('curl', 5)]) as fetch:
            data = node_check.check()
            self.assertEqual(data['results'][0]['asn'], '15169')
            self.assertEqual(data['results'][1]['status'], 'unavailable')
            self.assertTrue(node_check.check()['cached'])
            self.assertEqual(fetch.call_count, 3)

    def test_invalid_egress_never_queried_for_metadata(self):
        with patch.object(node_check, '_fetch', return_value='127.0.0.1') as fetch:
            self.assertEqual(node_check._family(4)['status'], 'unavailable')
            self.assertEqual(fetch.call_count, 1)

    def test_failed_metadata_preserves_egress(self):
        with patch.object(node_check, '_fetch', side_effect=['8.8.8.8', '{invalid']):
            result = node_check._family(4)
            self.assertEqual(result['ip'], '8.8.8.8')
            self.assertEqual(result['metadata_status'], 'unavailable')
