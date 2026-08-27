import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from pi.speaker_identity import normalize_audio_level, wav_quality


class SpeakerIdentityAudioTests(unittest.TestCase):
    def test_distance_like_level_change_normalizes_to_same_waveform(self):
        phase = np.linspace(0, 20 * np.pi, 16000, endpoint=False)
        near = (0.20 * np.sin(phase)).astype(np.float32)
        far = near * 0.10

        normalized_near, near_info = normalize_audio_level(near)
        normalized_far, far_info = normalize_audio_level(far)

        np.testing.assert_allclose(normalized_near, normalized_far, atol=1e-6)
        self.assertAlmostEqual(near_info["output_rms"], 0.05, places=5)
        self.assertAlmostEqual(far_info["output_rms"], 0.05, places=5)

    def test_gain_is_bounded_and_peak_safe(self):
        quiet = np.full(1000, 1e-5, dtype=np.float32)
        quiet[0] = 0.5

        normalized, info = normalize_audio_level(quiet)

        self.assertLessEqual(info["gain"], 12.0)
        self.assertLessEqual(float(np.max(np.abs(normalized))), 0.95)

    def test_quality_reports_clipping_separately_from_validity(self):
        samples = np.zeros(16000, dtype=np.int16)
        samples[::100] = 32767
        samples[1::100] = -32768

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clipped.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(samples.astype("<i2").tobytes())
            quality = wav_quality(path)

        self.assertTrue(quality["valid"])
        self.assertEqual(quality["clipping_fraction"], 0.02)
        self.assertNotIn("similarity", quality)


if __name__ == "__main__":
    unittest.main()
