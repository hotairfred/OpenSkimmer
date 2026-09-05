import json
import ctypes
import struct
import unittest
from unittest.mock import Mock, patch

from sparkgap import (
    SpotTracker,
    SpotIntent,
    _ItilaScanner,
    _cw_spot_payload,
    _cw_evidence_payload,
    _derive_cw_window,
    _max_envelope_correlation,
    _publish_mqtt_json,
    _unpack_scanner_result,
)


class DeriveCwWindowTests(unittest.TestCase):
    def test_region_1_strict_cw_segments(self):
        expected = {
            3530: (3500.0, 3570.0),
            7022.5: (7000.0, 7040.0),
            10120: (10100.0, 10130.0),
            14030: (14000.0, 14070.0),
            18080: (18068.0, 18095.0),
            21030: (21000.0, 21070.0),
        }
        for center, window in expected.items():
            with self.subTest(center=center):
                self.assertEqual(
                    _derive_cw_window(center, exclude_ft4=True, region=1),
                    window,
                )

    def test_region_1_40m_never_includes_ft4_segment(self):
        self.assertEqual(
            _derive_cw_window(7022.5, exclude_ft4=False, region=1),
            (7000.0, 7040.0),
        )

    def test_region_2_behavior_remains_compatible(self):
        self.assertEqual(
            _derive_cw_window(7030, exclude_ft4=False, region=2),
            (7000.0, 7070.0),
        )
        self.assertEqual(
            _derive_cw_window(7030, exclude_ft4=True, region=2),
            (7000.0, 7045.0),
        )

    def test_unknown_region_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "expected 1 or 2"):
            _derive_cw_window(7030, region=3)


class ItilaScannerRateTests(unittest.TestCase):
    def test_native_scanner_rejects_wrong_sample_rate(self):
        with self.assertRaisesRegex(ValueError, "requires sample_rate=192000"):
            _ItilaScanner(sample_rate=48000, center_khz=7022.5)

    def test_native_result_abi_offsets(self):
        buf = ctypes.create_string_buffer(1328)
        struct.pack_into('=ddi', buf, 0, 7_033_050.0, 23.5, 34)
        buf[24:24 + 10] = b'CQ LY7M\0\0\0'
        struct.pack_into('=dQii256f', buf, 280, 0.75, 19, 200, 3,
                         1.0, -1.0, 0.5, *([0.0] * 253))
        result = _unpack_scanner_result(buf)
        self.assertEqual(result[:3], (7_033_050.0, 23.5, 34))
        self.assertEqual(result[3], 'CQ LY7M')
        self.assertEqual(result[4:7], (0.75, 19, 200))
        self.assertEqual(result[7], (1.0, -1.0, 0.5))


class RespotTests(unittest.TestCase):
    def setUp(self):
        self.tracker = SpotTracker(set(), set(), respot_interval=120)

    def test_nearby_frequency_is_suppressed(self):
        self.tracker._mark_spotted('N0AV', 21030.5, 1000)
        self.assertFalse(self.tracker._can_respot('N0AV', 21030.6, 1010))
        self.assertFalse(self.tracker._can_respot('N0AV', 21031.5, 1010))

    def test_real_qsy_or_expired_spot_is_allowed(self):
        self.tracker._mark_spotted('N0AV', 21030.5, 1000)
        self.assertTrue(self.tracker._can_respot('N0AV', 21031.6, 1010))
        self.assertTrue(self.tracker._can_respot('N0AV', 21030.6, 1121))


class FrequencyConsensusTests(unittest.TestCase):
    def test_repeated_agreement_is_required_before_emit(self):
        tracker = SpotTracker(
            {'N0AV'}, set(), respot_interval=120,
            gate_config={'gate_freq_consensus': True},
        )
        intent = SpotIntent(
            call='N0AV', freq_khz=21030.5, snr_db=26, wpm=36,
            is_runner=True, bin_id=1, window_id=10, path_hz=100,
        )
        self.assertEqual(tracker.process_intent(intent), [])
        # Duplicate delivery and the other filter path over the same IQ
        # window are not independent evidence.
        self.assertEqual(tracker.process_intent(intent), [])
        same_window = SpotIntent(
            call='N0AV', freq_khz=21030.5, snr_db=26, wpm=36,
            is_runner=True, bin_id=1, window_id=10, path_hz=200,
        )
        self.assertEqual(tracker.process_intent(same_window), [])
        intent2 = SpotIntent(
            call='N0AV', freq_khz=21030.5, snr_db=25, wpm=35,
            is_runner=True, bin_id=1, window_id=11, path_hz=100,
        )
        spots = tracker.process_intent(intent2)
        self.assertEqual(len(spots), 1)
        self.assertEqual(spots[0]['call'], 'N0AV')
        self.assertEqual(spots[0]['evidence_count'], 2)
        self.assertEqual(spots[0]['activity'], 'cq')

    def test_evidence_on_another_frequency_does_not_advance_consensus(self):
        tracker = SpotTracker({'LY7M'}, set())
        first = SpotIntent('LY7M', 7033.1, 23, 34, True,
                           window_id=1, bin_id=11, path_hz=100)
        wrong_freq = SpotIntent('LY7M', 7021.9, 18, 44, True,
                                window_id=1, bin_id=22, path_hz=100)
        second = SpotIntent('LY7M', 7033.1, 22, 34, True,
                            window_id=2, bin_id=11, path_hz=100)
        self.assertEqual(tracker.process_intent(first), [])
        self.assertEqual(tracker.process_intent(wrong_freq), [])
        spots = tracker.process_intent(second)
        self.assertEqual([(s['call'], s['freq_khz']) for s in spots],
                         [('LY7M', 7033.1)])

    def test_envelope_correlation_is_reported_but_does_not_cross_vote(self):
        tracker = SpotTracker({'LY7M'}, set())
        sig = tuple(([1.0, -1.0, -1.0, 1.0] * 64))
        a = SpotIntent('LY7M', 7033.1, 23, 34, True, window_id=1,
                       bin_id=11, path_hz=100, envelope_signature=sig)
        b = SpotIntent('LY7M', 7021.1, 18, 34, True, window_id=1,
                       bin_id=22, path_hz=100, envelope_signature=sig)
        tracker.process_intent(a)
        self.assertEqual(tracker.process_intent(b), [])
        self.assertGreater(b.correlation, 0.99)
        self.assertEqual(b.evidence_count, 1)

    def test_correlation_rejects_unrelated_envelopes(self):
        left = tuple(([1.0, 1.0, -1.0, -1.0] * 64))
        right = tuple(([1.0, -1.0] * 128))
        self.assertLess(_max_envelope_correlation(left, right), 0.1)

    def test_evidence_caches_expire(self):
        tracker = SpotTracker({'LY7M'}, set())
        tracker._itila_seen_evidence[(1, 2, 100)] = 1.0
        tracker._itila_windows[('LY7M', 14066)] = [(1.0, (1, 2))]
        tracker._itila_corr_history['LY7M'] = [(1.0, 14066, 7033.0, (1.0,) * 256)]
        stats = tracker._sweep_all_caches(1000.0)
        self.assertEqual(stats['itila_seen_evidence'][1], 0)
        self.assertEqual(stats['itila_windows'][1], 0)
        self.assertEqual(stats['itila_corr_history'][1], 0)


class MqttTests(unittest.TestCase):
    def test_cw_payload_contract(self):
        payload = _cw_spot_payload(
            {'call': 'KR2Q', 'snr': 15.4, 'wpm': 41, 'method': 'exact'},
            21028.7, '15m', 'OH2ABC', 'KP20AA', timestamp=1234,
        )
        self.assertEqual(payload, {
            'schema': 'sparkgap.spot.v1',
            'call': 'KR2Q',
            'freq_hz': 21028700,
            'snr': 15,
            'wpm': 41,
            'mode': 'CW',
            'method': 'exact',
            'band': '15m',
            'receiver_call': 'OH2ABC',
            'receiver_grid': 'KP20AA',
            'ts': 1234,
        })

    def test_cw_payload_corrects_stale_manager_band(self):
        payload = _cw_spot_payload(
            {'call': 'N7NR', 'snr': 21, 'wpm': 36},
            21027.45, '80m', 'OH2ABC', '', timestamp=1234,
        )
        self.assertEqual('15m', payload['band'])

    def test_evidence_payload_preserves_raw_window_identity(self):
        intent = SpotIntent(
            'LY7M', 7033.05, 23, 34, True,
            raw_text='CQ CQ LY7M LY7M TEST', window_id=17,
            bin_id=991, path_hz=200, evidence_id='991:17:200',
            evidence_count=2, evidence_required=2, decision='emitted',
            correlation=0.93456, correlated_freq_khz=7021.05,
        )
        payload = _cw_evidence_payload(
            intent, '40m', 'OH3CUF', 'KP20', timestamp=1234)
        self.assertEqual(payload['schema'], 'sparkgap.cw_evidence.v1')
        self.assertEqual(payload['raw_text'], 'CQ CQ LY7M LY7M TEST')
        self.assertEqual(payload['window_id'], 17)
        self.assertEqual(payload['path_hz'], 200)
        self.assertEqual(payload['evidence_id'], '991:17:200')
        self.assertEqual(payload['correlated_freq_hz'], 7021050)

    def test_accepted_spot_links_to_decisive_raw_evidence(self):
        payload = _cw_spot_payload(
            {'call': 'LY7M', 'snr': 23, 'wpm': 34, 'method': 'exact',
             'session_id': 'session-1', 'evidence_id': 'session-1:991:17:200',
             'bin_id': 991, 'window_id': 17, 'path_hz': 200,
             'activity': 'cq',
             'raw_text': 'CQ CQ LY7M LY7M', 'evidence_count': 2,
             'evidence_required': 2},
            7033.05, '40m', 'OH3CUF', 'KP20', timestamp=1234)
        self.assertEqual(payload['raw_text'], 'CQ CQ LY7M LY7M')
        self.assertEqual(payload['evidence_id'], 'session-1:991:17:200')
        self.assertEqual(payload['evidence_count'], 2)
        self.assertEqual(payload['activity'], 'cq')

    @patch('sparkgap._get_mqtt_publisher')
    def test_publish_uses_configured_qos_without_retaining(self, get_pub):
        result = Mock(rc=0)
        client = Mock()
        client.publish.return_value = result
        get_pub.return_value = client
        payload = {'schema': 'sparkgap.spot.v1', 'call': 'KR2Q'}

        self.assertTrue(_publish_mqtt_json(
            {'enabled': True, 'qos': 1, 'retain': False},
            'skimmer/cw/spots', payload,
        ))
        _, encoded = client.publish.call_args.args
        self.assertEqual(json.loads(encoded), payload)
        self.assertEqual(client.publish.call_args.kwargs,
                         {'qos': 1, 'retain': False})

    @patch('sparkgap._get_mqtt_publisher')
    def test_publish_is_accepted_during_async_connect(self, get_pub):
        client = Mock()
        client.publish.return_value = Mock(rc=4)  # MQTT_ERR_NO_CONN, queued
        get_pub.return_value = client
        self.assertTrue(_publish_mqtt_json(
            {'enabled': True, 'qos': 1},
            'skimmer/cw/spots', {'call': 'KR2Q'},
        ))

    @patch('sparkgap._get_mqtt_publisher', return_value=None)
    def test_broker_failure_is_nonfatal(self, _get_pub):
        self.assertFalse(_publish_mqtt_json(
            {'enabled': True}, 'skimmer/cw/spots', {'call': 'KR2Q'}))


if __name__ == '__main__':
    unittest.main()
