import unittest

from pydantic import ValidationError

from no1.contracts.v1 import Actor, ActorProfile


class TestActorRuntimeOptions(unittest.TestCase):
    def test_actor_accepts_opencode_default_variant(self) -> None:
        actor = Actor(
            id="opencode-1",
            runtime="opencode",
            command=["opencode", "-m", "onecolleague/deepseek-v4-pro"],
            runtime_options={"opencode": {"default_variant": "max"}},
        )
        self.assertEqual(actor.runtime_options.opencode.default_variant, "max")

    def test_missing_variant_is_backward_compatible(self) -> None:
        actor = Actor(id="legacy", runtime="opencode")
        self.assertIsNone(actor.runtime_options.opencode)

    def test_invalid_variant_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Actor(
                id="invalid",
                runtime="opencode",
                runtime_options={"opencode": {"default_variant": "medium"}},
            )

    def test_profile_persists_runtime_options(self) -> None:
        profile = ActorProfile(
            id="deepseek",
            runtime="opencode",
            runtime_options={"opencode": {"default_variant": "low"}},
        )
        payload = profile.model_dump(exclude_none=True)
        self.assertEqual(payload["runtime_options"]["opencode"]["default_variant"], "low")


if __name__ == "__main__":
    unittest.main()
