from xpedition_cli import review_engine as R


def _project(components, nets=None, connections=None):
    return {
        "project": "t",
        "revision": "R0",
        "sheets": [],
        "components": components,
        "nets": nets or [],
        "connections": connections or [],
    }


def _part(refdes, part, *pins, **extra):
    """pins are (number, net) pairs; net None means open; 'NC' means marked no-connect."""
    plist = []
    for number, net in pins:
        if net == "NC":
            plist.append({"number": number, "net": None, "no_connect": True})
        else:
            plist.append({"number": number, "net": net, "no_connect": False})
    return {"refdes": refdes, "internal_part_no": part, "pins": plist, **extra}


def _sources(findings):
    return sorted(f["source"] for f in findings)


def test_open_pin_reported_unless_marked_no_connect():
    project = _project(
        [
            _part("U1", "IC", ("1", "+3V3"), ("2", "GND"), ("3", None), ("4", "NC")),
            _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
        ]
    )
    findings = R.schematic_findings(project)
    open_pins = [f for f in findings if f["source"] == "cli/open-pin"]
    assert [f["evidence"] for f in open_pins] == [["U1.3"]]


def test_single_pin_net_and_unnamed_junction_rules():
    project = _project(
        [
            _part("U1", "IC", ("1", "+3V3"), ("2", "GND"), ("3", "LONELY")),
            _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
            _part("R1", "RES", ("1", "$2N9"), ("2", "GND")),
            _part("R2", "RES", ("1", "$2N9"), ("2", "GND")),
            _part("R3", "RES", ("1", "$2N9"), ("2", "GND")),
        ],
        nets=[{"name": "$2N9", "unnamed": True}],
    )
    findings = R.schematic_findings(project)
    assert [f["net"] for f in findings if f["source"] == "cli/single-pin-net"] == ["LONELY"]
    assert [f["net"] for f in findings if f["source"] == "cli/unnamed-net"] == ["$2N9"]


def test_decoupling_and_i2c_pullups():
    project = _project(
        [
            _part("U1", "MCU", ("1", "+3V3"), ("2", "GND"), ("3", "I2C_SDA"), ("4", "I2C_SCL")),
            _part("U2", "SENSOR", ("1", "+5V"), ("2", "GND"), ("3", "I2C_SDA"), ("4", "I2C_SCL")),
            _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
            _part("R1", "RES", ("1", "I2C_SDA"), ("2", "+3V3")),
        ]
    )
    findings = R.schematic_findings(project)
    decoupling = [(f["refdes"], f["net"]) for f in findings if f["source"] == "cli/decoupling"]
    assert decoupling == [("U2", "+5V")]
    pullups = [f["net"] for f in findings if f["source"] == "cli/i2c-pullup"]
    assert pullups == ["I2C_SCL"]


def test_missing_part_numbers_are_one_summary_finding():
    project = _project(
        [_part("R1", "", ("1", "A"), ("2", "GND")), _part("R2", "", ("1", "A"), ("2", "GND"))]
    )
    findings = [
        f for f in R.schematic_findings(project) if f["source"] == "cli/missing-part-number"
    ]
    assert len(findings) == 1
    assert findings[0]["evidence"] == ["R1", "R2"]


def test_net_name_and_refdes_prefix_rules():
    project = _project([_part("R1", "RES", ("1", "sda_low"), ("2", "GND"), ("3", "GND"))])
    findings = R.schematic_findings(project)
    assert [f["net"] for f in findings if f["source"] == "cli/net-name"] == ["sda_low"]
    assert [f["refdes"] for f in findings if f["source"] == "cli/refdes-prefix"] == ["R1"]


def test_power_and_ground_recognition():
    assert (
        R.is_power_net("+3V3")
        and R.is_power_net("VBUS")
        and R.is_power_net("5V")
        and R.is_power_net("VDD_IO")
    )
    assert R.is_ground_net("GND") and R.is_ground_net("AGND") and R.is_ground_net("VSS")
    assert not R.is_power_net("GND") and not R.is_power_net("LID_DET")


def test_run_review_merges_extra_findings_and_sorts_by_severity():
    project = _project(
        [
            _part("U1", "IC", ("1", "+3V3"), ("2", "GND")),
            _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
        ]
    )
    extra = [
        {
            "severity": "low",
            "refdes": None,
            "net": None,
            "source": "xpedition/grc:x",
            "finding": "tool said so",
            "evidence": [],
            "suggestion": "",
            "confidence": 1.0,
        }
    ]
    report = R.run_review(project, None, extra)
    assert report["summary"]["total"] == 1
    assert report["findings"][0]["source"] == "xpedition/grc:x"
    assert report["summary"]["valid"] is False


def test_i2c_pullup_counts_one_series_resistor_away():
    project = _project(
        [
            _part("U1", "MCU", ("1", "+3V3"), ("2", "GND"), ("3", "I2C_SDA"), ("4", "I2C_SCL")),
            _part("R1", "RES", ("1", "I2C_SDA"), ("2", "+3V3")),
            _part("R2", "RES", ("1", "I2C_SCL"), ("2", "+3V3")),
            _part("R3", "RES", ("1", "I2C_SDA"), ("2", "SDA_HOST")),
            _part("R4", "RES", ("1", "I2C_SCL"), ("2", "SCL_HOST")),
            _part("J1", "CON", ("1", "SDA_HOST"), ("2", "SCL_HOST"), ("3", "GND")),
            _part("R5", "RES", ("1", "SDA_AUX"), ("2", "AUX_X")),
            _part("U2", "SENSOR", ("1", "SDA_AUX"), ("2", "GND")),
            _part("C1", "CAP", ("1", "+3V3"), ("2", "GND")),
        ]
    )
    findings = R.schematic_findings(project)
    assert [f["net"] for f in findings if f["source"] == "cli/i2c-pullup"] == ["SDA_AUX"]
