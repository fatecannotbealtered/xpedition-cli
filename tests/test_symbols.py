from pathlib import Path

import pytest

from xpedition_cli import symbols


def _pins_on_grid(symbol: symbols.Symbol) -> bool:
    return all(symbols.on_grid(pin.x) and symbols.on_grid(pin.y) for pin in symbol.pins)


@pytest.mark.parametrize(
    "factory",
    [
        symbols.resistor,
        symbols.capacitor,
        symbols.inductor,
        symbols.diode,
        symbols.switch,
        symbols.battery,
        lambda: symbols.capacitor(polarised=True),
        lambda: symbols.diode(led=True),
    ],
)
def test_two_terminal_parts_have_two_grid_pins_forty_apart(factory):
    symbol = factory()
    assert len(symbol.pins) == 2
    assert _pins_on_grid(symbol)
    assert symbol.pins[1].x - symbol.pins[0].x == 40
    assert symbol.pins[0].side == "left" and symbol.pins[1].side == "right"


def test_two_terminal_parts_are_not_bare_rectangles():
    resistor = symbols.resistor().shapes
    capacitor = symbols.capacitor().shapes
    led = symbols.diode(led=True).shapes
    assert len(resistor) == 1 and resistor[0].startswith("l 5 ")
    assert len(capacitor) == 2 and all(shape.startswith("l 2 ") for shape in capacitor)
    assert len(led) == 4


def test_diode_pin_numbers_follow_cathode_first_convention():
    diode = symbols.diode()
    assert diode.pin("2").name == "A" and diode.pin("2").side == "left"
    assert diode.pin("1").name == "K" and diode.pin("1").side == "right"


def test_box_pins_sit_on_grid_with_pitch_ten():
    mcu = symbols.box(
        "MCU",
        left=[("2", "GND"), ("3", "LID"), ("4", "RST")],
        right=[("5", "LED1"), ("6", "LED2"), ("", ""), ("7", "SDA"), ("8", "SCL")],
        top=[("1", "VDD")],
        pintypes={"1": "POWER", "2": "GROUND"},
    )
    assert _pins_on_grid(mcu)
    left = [pin for pin in mcu.pins if pin.side == "left"]
    assert [pin.y for pin in left] == [left[0].y, left[0].y - 10, left[0].y - 20]
    right = [pin for pin in mcu.pins if pin.side == "right"]
    # the empty entry leaves a gap row between LED2 and SDA
    assert right[1].y - right[2].y == 20
    assert mcu.pin("1").side == "top" and mcu.pin("1").pintype == "POWER"
    assert mcu.pin("1").y > mcu.pin("2").y
    assert mcu.width > 0 and mcu.height > 0


def test_box_widens_for_long_pin_names():
    narrow = symbols.box("A", left=[("1", "IN")], right=[("2", "OUT")])
    wide = symbols.box(
        "B", left=[("1", "VERY_LONG_INPUT_NAME")], right=[("2", "VERY_LONG_OUTPUT_NAME")]
    )
    assert wide.width > narrow.width
    assert symbols.on_grid(wide.pin("1").x)


def test_render_produces_v53_file_with_attributes_and_pins():
    text = symbols.resistor().render()
    lines = text.splitlines()
    assert lines[0] == "V 53"
    assert lines[1].endswith(" R.1")
    assert any(line.startswith("U ") and line.endswith("DEVICE=R") for line in lines)
    assert any(line.startswith("U ") and line.endswith("REFDES=") for line in lines)
    assert sum(line.startswith("P ") for line in lines) == 2
    assert sum("PINTYPE=" in line for line in lines) == 2
    assert lines[-1] == "E"


def test_write_symbol_places_file_under_partition(tmp_path: Path):
    path = symbols.write_symbol(tmp_path, "Case", symbols.capacitor("C10U"))
    assert path == tmp_path / "Case" / "sym" / "C10U.1"
    assert path.read_text(encoding="utf-8").startswith("V 53\n")


def test_power_symbol_carries_netname_and_is_not_a_part(tmp_path: Path):
    assert symbols.power_symbol_name("+5V") == "PWR_P5V"
    assert symbols.power_symbol_name("VBAT") == "PWR_VBAT"
    path = symbols.write_symbol(tmp_path, "Case", symbols.power_symbol("+3V3"))
    text = path.read_text(encoding="utf-8")
    assert path.name == "PWR_P3V3.1"
    assert "\nY 4\n" in text, "power symbols are Designer 'pin' symbols (type 4), parts are type 1"
    assert "NETNAME=+3V3" in text
    assert "FORWARD_PCB=0" in text
    assert "PINTYPE=POWER" in text
    assert "P 10 0 0 0 20" in text
    assert "\nY 1\n" in symbols.resistor().render()


def test_pin_lookup_raises_for_unknown_number():
    with pytest.raises(KeyError):
        symbols.resistor().pin("3")


def test_thermistor_is_a_two_terminal_resistor_with_a_diagonal_mark():
    ntc = symbols.thermistor()
    assert len(ntc.pins) == 2 and _pins_on_grid(ntc)
    assert ntc.pins[1].x - ntc.pins[0].x == 40
    assert any("MARK=-t" in label for label in ntc.labels)
    assert len(ntc.shapes) == 2 and ntc.shapes[1].startswith("l 3 ")


@pytest.mark.parametrize("channel", ["N", "P"])
def test_mosfet_has_gate_left_and_sot23_pin_numbers(channel):
    fet = symbols.mosfet(channel=channel)
    assert _pins_on_grid(fet)
    by_number = {pin.number: pin for pin in fet.pins}
    assert set(by_number) == {"1", "2", "3"}
    assert by_number["1"].side == "left" and by_number["1"].name == "G"
    assert by_number["2"].name == "S" and by_number["3"].name == "D"
    top = by_number["2"] if channel == "P" else by_number["3"]
    assert top.side == "top" and (top.x, top.y) == (0, 20)
    rendered = fet.render().splitlines()
    assert "Y 1" in rendered
    assert sum(line.startswith("P ") for line in rendered) == 3


def test_test_point_has_one_pin_down_and_a_hole_has_none():
    tp = symbols.test_point()
    assert len(tp.pins) == 1 and _pins_on_grid(tp)
    assert tp.pins[0].side == "bottom" and (tp.pins[0].x, tp.pins[0].y) == (0, -20)
    hole = symbols.mounting_hole()
    assert hole.pins == [] and len(hole.shapes) == 3
    rendered = hole.render().splitlines()
    assert "Y 1" in rendered and not any(line.startswith("P ") for line in rendered)
