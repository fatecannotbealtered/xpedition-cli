"""The auto-router's passes and layers, parsed from what a request names."""

from __future__ import annotations

import unittest

from xpedition_cli.adapter.plan import PlanError, routing


class RoutePassesTest(unittest.TestCase):
    def test_route_passes_parse_names_and_effort_ranges(self):
        passes = routing.parse_route_passes("route:1-5, viamin, smooth:2")
        self.assertEqual(
            [(p["pass"], p["type"], p["effort"]) for p in passes],
            [("route", 8, [1, 5]), ("viamin", 14, [1, 3]), ("smooth", 10, [2, 2])],
        )

    def test_no_passes_named_is_the_default_list(self):
        passes = routing.parse_route_passes("")
        self.assertEqual([p["pass"] for p in passes], ["route", "viamin", "smooth"])
        self.assertEqual(passes, routing.parse_route_passes(routing.DEFAULT_ROUTE_PASSES))

    def test_bad_passes_are_usage_errors(self):
        for bad in ("tune", "route:0-3", "route:2-9", "route:x", "route:3-1", ", ,"):
            with self.subTest(bad=bad):
                with self.assertRaises(PlanError) as caught:
                    routing.parse_route_passes(bad)
                self.assertEqual(caught.exception.code, "E_USAGE")
        with self.assertRaises(PlanError) as caught:
            routing.parse_route_passes("tune")
        self.assertEqual(caught.exception.details["known"], sorted(routing.ROUTE_PASS_TYPES))
        self.assertIsInstance(caught.exception, ValueError)


class RouteLayersTest(unittest.TestCase):
    def test_route_layers_parse_to_sorted_numbers_or_nothing(self):
        self.assertEqual(routing.parse_route_layers("4,1,4"), [1, 4])
        self.assertEqual(routing.parse_route_layers([1, "2"]), [1, 2])
        self.assertEqual(routing.parse_route_layers(""), [])
        self.assertEqual(routing.parse_route_layers(None), [])

    def test_a_layer_that_is_not_a_positive_number_is_refused(self):
        for bad in ("1,top", "0", "-1"):
            with self.subTest(bad=bad):
                with self.assertRaises(PlanError) as caught:
                    routing.parse_route_layers(bad)
                self.assertEqual(caught.exception.code, "E_USAGE")


if __name__ == "__main__":
    unittest.main()
