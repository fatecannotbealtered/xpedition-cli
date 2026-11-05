from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xpedition_cli.adapter.plan import symbols


def _pins_on_grid(symbol: symbols.Symbol) -> bool:
    return all(symbols.on_grid(pin.x) and symbols.on_grid(pin.y) for pin in symbol.pins)


class SymbolsTest(unittest.TestCase):
    def tmp_dir(self) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name)

    def test_two_terminal_parts_have_two_grid_pins_forty_apart(self):
        for factory in [
            symbols.resistor,
            symbols.capacitor,
            symbols.inductor,
            symbols.diode,
            symbols.switch,
            symbols.battery,
            lambda: symbols.capacitor(polarised=True),
            lambda: symbols.diode(led=True),
        ]:
            with self.subTest(factory=factory):
                symbol = factory()
                self.assertEqual(len(symbol.pins), 2)
                self.assertTrue(_pins_on_grid(symbol))
                self.assertEqual(symbol.pins[1].x - symbol.pins[0].x, 40)
                self.assertEqual(symbol.pins[0].side, "left")
                self.assertEqual(symbol.pins[1].side, "right")

    def test_two_terminal_parts_are_not_bare_rectangles(self):
        resistor = symbols.resistor().shapes
        capacitor = symbols.capacitor().shapes
        led = symbols.diode(led=True).shapes
        self.assertEqual(len(resistor), 1)
        self.assertTrue(resistor[0].startswith("l 5 "))
        self.assertEqual(len(capacitor), 2)
        self.assertTrue(all(shape.startswith("l 2 ") for shape in capacitor))
        self.assertEqual(len(led), 4)

    def test_diode_pin_numbers_follow_cathode_first_convention(self):
        diode = symbols.diode()
        self.assertEqual(diode.pin("2").name, "A")
        self.assertEqual(diode.pin("2").side, "left")
        self.assertEqual(diode.pin("1").name, "K")
        self.assertEqual(diode.pin("1").side, "right")

    def test_box_pins_sit_on_grid_with_pitch_ten(self):
        mcu = symbols.box(
            "MCU",
            left=[("2", "GND"), ("3", "LID"), ("4", "RST")],
            right=[("5", "LED1"), ("6", "LED2"), ("", ""), ("7", "SDA"), ("8", "SCL")],
            top=[("1", "VDD")],
            pintypes={"1": "POWER", "2": "GROUND"},
        )
        self.assertTrue(_pins_on_grid(mcu))
        left = [pin for pin in mcu.pins if pin.side == "left"]
        self.assertEqual([pin.y for pin in left], [left[0].y, left[0].y - 10, left[0].y - 20])
        right = [pin for pin in mcu.pins if pin.side == "right"]
        # the empty entry leaves a gap row between LED2 and SDA
        self.assertEqual(right[1].y - right[2].y, 20)
        self.assertEqual(mcu.pin("1").side, "top")
        self.assertEqual(mcu.pin("1").pintype, "POWER")
        self.assertGreater(mcu.pin("1").y, mcu.pin("2").y)
        self.assertGreater(mcu.width, 0)
        self.assertGreater(mcu.height, 0)

    def test_box_widens_for_long_pin_names(self):
        narrow = symbols.box("A", left=[("1", "IN")], right=[("2", "OUT")])
        wide = symbols.box(
            "B", left=[("1", "VERY_LONG_INPUT_NAME")], right=[("2", "VERY_LONG_OUTPUT_NAME")]
        )
        self.assertGreater(wide.width, narrow.width)
        self.assertTrue(symbols.on_grid(wide.pin("1").x))

    def test_render_produces_v53_file_with_attributes_and_pins(self):
        text = symbols.resistor().render()
        lines = text.splitlines()
        self.assertEqual(lines[0], "V 53")
        self.assertTrue(lines[1].endswith(" R.1"))
        self.assertTrue(any(line.startswith("U ") and line.endswith("DEVICE=R") for line in lines))
        self.assertTrue(any(line.startswith("U ") and line.endswith("REFDES=") for line in lines))
        self.assertEqual(sum(line.startswith("P ") for line in lines), 2)
        self.assertEqual(sum("PINTYPE=" in line for line in lines), 2)
        self.assertEqual(lines[-1], "E")

    def test_write_symbol_places_file_under_partition(self):
        tmp_path = self.tmp_dir()
        path = symbols.write_symbol(tmp_path, "Case", symbols.capacitor("C10U"))
        self.assertEqual(path, tmp_path / "Case" / "sym" / "C10U.1")
        self.assertTrue(path.read_text(encoding="utf-8").startswith("V 53\n"))

    def test_power_symbol_carries_netname_and_is_not_a_part(self):
        tmp_path = self.tmp_dir()
        self.assertEqual(symbols.power_symbol_name("+5V"), "PWR_P5V")
        self.assertEqual(symbols.power_symbol_name("VBAT"), "PWR_VBAT")
        path = symbols.write_symbol(tmp_path, "Case", symbols.power_symbol("+3V3"))
        text = path.read_text(encoding="utf-8")
        self.assertEqual(path.name, "PWR_P3V3.1")
        self.assertIn("\nY 4\n", text, msg="power symbols are Designer 'pin' symbols (type 4), parts are type 1")
        self.assertIn("NETNAME=+3V3", text)
        self.assertIn("FORWARD_PCB=0", text)
        self.assertIn("PINTYPE=POWER", text)
        self.assertIn("P 10 0 0 0 20", text)
        self.assertIn("\nY 1\n", symbols.resistor().render())

    def test_pin_lookup_raises_for_unknown_number(self):
        with self.assertRaises(KeyError):
            symbols.resistor().pin("3")

    def test_thermistor_is_a_two_terminal_resistor_with_a_diagonal_mark(self):
        ntc = symbols.thermistor()
        self.assertEqual(len(ntc.pins), 2)
        self.assertTrue(_pins_on_grid(ntc))
        self.assertEqual(ntc.pins[1].x - ntc.pins[0].x, 40)
        self.assertTrue(any("MARK=-t" in label for label in ntc.labels))
        self.assertEqual(len(ntc.shapes), 2)
        self.assertTrue(ntc.shapes[1].startswith("l 3 "))

    def test_mosfet_has_gate_left_and_sot23_pin_numbers(self):
        for channel in ["N", "P"]:
            with self.subTest(channel=channel):
                fet = symbols.mosfet(channel=channel)
                self.assertTrue(_pins_on_grid(fet))
                by_number = {pin.number: pin for pin in fet.pins}
                self.assertEqual(set(by_number), {"1", "2", "3"})
                self.assertEqual(by_number["1"].side, "left")
                self.assertEqual(by_number["1"].name, "G")
                self.assertEqual(by_number["2"].name, "S")
                self.assertEqual(by_number["3"].name, "D")
                top = by_number["2"] if channel == "P" else by_number["3"]
                self.assertEqual(top.side, "top")
                self.assertEqual((top.x, top.y), (0, 20))
                rendered = fet.render().splitlines()
                self.assertIn("Y 1", rendered)
                self.assertEqual(sum(line.startswith("P ") for line in rendered), 3)

    def test_test_point_has_one_pin_down_and_a_hole_has_none(self):
        tp = symbols.test_point()
        self.assertEqual(len(tp.pins), 1)
        self.assertTrue(_pins_on_grid(tp))
        self.assertEqual(tp.pins[0].side, "bottom")
        self.assertEqual((tp.pins[0].x, tp.pins[0].y), (0, -20))
        hole = symbols.mounting_hole()
        self.assertEqual(hole.pins, [])
        self.assertEqual(len(hole.shapes), 3)
        rendered = hole.render().splitlines()
        self.assertIn("Y 1", rendered)
        self.assertFalse(any(line.startswith("P ") for line in rendered))
