"""A question only the knowledge base answers: what the parts database keeps of a part's Value.

The answer must agree with the builtin entry library-part-value-is-numeric: any Value that is not
a number with an SI multiplier, 100nF included, is stored as 0; write 100n instead. No
Xpedition instance or project is needed.
"""
import re

from _report import plain

SCENARIO = "knowledge_lookup"
COMMANDS = ["knowledge search", "knowledge get"]
TURNS = ["I want to give a capacitor in the Xpedition central library the Value 100nF. What will the parts database store "
         "as its Value, and how should I write the Value instead?"]
FAKE_HOST = {"instances": [], "files": []}
ENTRY = "library-part-value-is-numeric"


def grade(run):
    words = plain(run.messages[-1] if run.messages else "")
    run.check(re.search(r"(?<![\w.])0(?![\w.]*\d)|\bzero\b|零", words),
              "the answer does not say that the parts database stores 100nF as 0")
    run.check(re.search(r"(?<![\w.])100\s?n(?![a-z])", words), "the answer does not say to write the Value as 100n")
    run.check(any(c.ok and c.value("--id") == ENTRY for c in run.ran("knowledge get")),
              f"the Agent never read the knowledge entry {ENTRY}")
    run.check(not run.write_calls(), f"a write command ran: {run.write_calls()}")


def right(agent, turn):
    found = agent.run("knowledge", "search", "--query", "part value").data["items"]
    entry = next((i["id"] for i in found if i["id"] == ENTRY), ENTRY)
    body = agent.run("knowledge", "get", "--id", entry).stdout
    stored = "0" if "stored as 0" in body else "unknown"
    agent.say(f"The parts database keeps Value only as a number with an SI multiplier, so 100nF is stored as {stored} "
              f"(knowledge entry {entry}). Write the Value as 100n.")


def wrong_guess(agent, turn):
    agent.say("The parts database keeps the text as you give it, so the Value stays 100nF.")


def wrong_unread(agent, turn):
    # The right answer from memory, without reading the entry.
    agent.say("100nF is stored as 0; write the Value as 100n.")
