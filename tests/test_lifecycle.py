import unittest

from pi.lifecycle import calculate_sleep_duration, sleep_context_from_state


class SleepLifecycleTests(unittest.TestCase):
    def test_duration_comes_from_persisted_shutdown_and_boot_times(self):
        duration = calculate_sleep_duration(
            "2026-08-27T08:00:00+00:00",
            "2026-08-27T09:02:03.500000+00:00",
        )

        self.assertEqual(duration, 3723.5)

    def test_missing_invalid_or_future_shutdown_time_is_unknown(self):
        booted_at = "2026-08-27T09:00:00+00:00"

        for shutdown_at in (
            None,
            "not-a-date",
            "2026-08-27T10:00:00+00:00",
        ):
            with self.subTest(shutdown_at=shutdown_at):
                self.assertIsNone(
                    calculate_sleep_duration(shutdown_at, booted_at)
                )

    def test_context_contains_duration_without_private_timestamps(self):
        context = sleep_context_from_state(
            {
                "last_sleep_seconds": 3723.456,
                "last_shutdown_at": "private-local-value",
                "last_boot_at": "private-local-value",
            }
        )

        self.assertEqual(
            context,
            {"status": "known", "duration_seconds": 3723.5},
        )

    def test_context_is_explicitly_unavailable_without_measurement(self):
        self.assertEqual(
            sleep_context_from_state({"last_sleep_seconds": None}),
            {"status": "unavailable"},
        )


if __name__ == "__main__":
    unittest.main()
