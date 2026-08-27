import unittest

from server.deployments import (
    BodyKind,
    Capability,
    ConfigurationError,
    DeploymentCatalog,
)


class DeploymentCatalogTests(unittest.TestCase):
    def test_physical_bodies_can_grant_only_their_declared_capabilities(self):
        catalog = DeploymentCatalog.from_config(
            {
                "entities": [
                    {"id": "voice-box-001", "display_name": "Talking Box"}
                ],
                "bodies": [
                    {
                        "id": "talking-box-pi",
                        "display_name": "Talking Box Pi",
                        "kind": "physical",
                        "capabilities": ["speaker", "button"],
                    }
                ],
                "bindings": [
                    {
                        "id": "home-talking-box",
                        "entity_id": "voice-box-001",
                        "body_id": "talking-box-pi",
                        "capability_grants": ["speaker", "button"],
                    }
                ],
            }
        )

        self.assertEqual(
            catalog.bindings["home-talking-box"].capability_grants,
            frozenset({Capability.SPEAKER, Capability.BUTTON}),
        )

    def test_binding_rejects_capability_missing_from_body(self):
        with self.assertRaisesRegex(ConfigurationError, "absent from body.*motion"):
            DeploymentCatalog.from_config(
                {
                    "entities": [{"id": "box"}],
                    "bodies": [
                        {"id": "box-body", "capabilities": ["speaker"]}
                    ],
                    "bindings": [
                        {
                            "id": "unsafe",
                            "entity_id": "box",
                            "body_id": "box-body",
                            "capability_grants": ["speaker", "motion"],
                        }
                    ],
                }
            )

    def test_binding_rejects_unknown_entity_or_body(self):
        base = {
            "entities": [{"id": "ferret"}],
            "bodies": [{"id": "ferret-body"}],
        }
        for field, value, expected in (
            ("entity_id", "missing", "unknown entity"),
            ("body_id", "missing", "unknown body"),
        ):
            binding = {
                "id": field,
                "entity_id": "ferret",
                "body_id": "ferret-body",
            }
            binding[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                ConfigurationError, expected
            ):
                DeploymentCatalog.from_config({**base, "bindings": [binding]})

    def test_simulated_body_uses_the_same_capability_validation(self):
        catalog = DeploymentCatalog.from_config(
            {
                "entities": [{"id": "ferret"}],
                "bodies": [
                    {
                        "id": "ferret-simulator",
                        "kind": "simulation",
                        "capabilities": ["camera", "motion"],
                    }
                ],
                "bindings": [
                    {
                        "id": "ferret-test",
                        "entity_id": "ferret",
                        "body_id": "ferret-simulator",
                        "capability_grants": ["motion"],
                    }
                ],
            }
        )

        self.assertEqual(
            catalog.bodies["ferret-simulator"].kind, BodyKind.SIMULATION
        )
        self.assertNotIn(
            Capability.CAMERA,
            catalog.bindings["ferret-test"].capability_grants,
        )

    def test_unknown_capability_and_duplicate_ids_are_rejected(self):
        with self.assertRaisesRegex(ConfigurationError, "unknown capability"):
            DeploymentCatalog.from_config(
                {"bodies": [{"id": "body", "capabilities": ["telepathy"]}]}
            )
        with self.assertRaisesRegex(ConfigurationError, "duplicate entity id"):
            DeploymentCatalog.from_config(
                {"entities": [{"id": "same"}, {"id": "same"}]}
            )


if __name__ == "__main__":
    unittest.main()
