"""T-035 -- the v2 analysis document is checked against the arithmetic that produced it.

T-010's standard is that no number in an analysis is estimated, rounded from memory, or carried over
from an earlier run. That is a claim about a *document*, and the only way to hold a document to it is
mechanically.

## The two halves, and why only one of them can run here

`model.report_v2` re-derives every figure from the corpus and writes
`docs/results/t035-figures.json`. That half needs a **running Postgres holding a verified ingest**
(D-043) -- CI has a Postgres service container but no data release, because the corpus never enters
git. So it runs locally, and its output is committed.

This half reads the committed JSON and asserts every figure appears **verbatim** in the prose. It
needs no data at all, so it runs in CI on every push. A number edited in the document and not in the
data fails the build; a number that moves in the data and not in the document fails it too, because
regenerating the JSON is what a new measurement looks like.

Same shape as T-033's scoring contract: a committed intermediary, checked from both ends, with the
end that can be automated automated.

## What this does and does not catch

It catches a figure that disagrees with the run that produced it -- the failure T-010 exists to
prevent, and the one nobody notices, because a wrong number in a table reads exactly like a right
one.

It does not catch a *sentence* that misreads a correct figure. "The model improved" over a number
that fell is a claim no string match can evaluate. That failure is left to review, and saying so here
is better than implying a coverage this does not have.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIGURES_PATH = REPO / "docs" / "results" / "t035-figures.json"
DOCUMENT_PATH = REPO / "docs" / "analysis" / "MODELING-V2-RESULT.md"
PHASE_1_PATH = REPO / "docs" / "analysis" / "PHASE-1-RESULT.md"


@pytest.fixture(scope="module")
def figures() -> dict:
    assert FIGURES_PATH.exists(), (
        f"{FIGURES_PATH} is missing. It is committed on purpose -- CI has no corpus and cannot "
        "regenerate it. Run `python -m model.report_v2 $DATABASE_URL --spend-the-sealed-fold "
        f"--out {FIGURES_PATH.relative_to(REPO)}`."
    )
    return json.loads(FIGURES_PATH.read_text())


@pytest.fixture(scope="module")
def document() -> str:
    return DOCUMENT_PATH.read_text()


def dec(value: float, places: int = 4) -> str:
    """`.6800`, the way the document writes a probability-scale figure -- no leading zero.

    The formatting is part of the check. A document writing `0.68` where the figure is `.6800` would
    pass a looser comparison while being harder to read against its own tables.
    """
    text = f"{value:.{places}f}"
    return text.lstrip("0") if text.startswith("0.") else text


def signed(value: float, places: int = 4) -> str:
    """`+.0220` / `−.0142`. The minus is U+2212, which is what the document uses."""
    body = dec(abs(value), places)
    return f"+{body}" if value >= 0 else f"−{body}"


def _claims(figures: dict) -> list[tuple[str, str]]:
    """`(what it is, the exact string that must appear)`.

    Written out rather than walked, because the point is that *these* claims are checked. A generic
    walk over the JSON would also assert on `generated_utc` and every intermediate, and a check that
    fails for uninteresting reasons gets weakened until it fails for no reasons.
    """
    sealed = figures["sealed"]
    spread = figures["spread"]
    ablation = figures["ablation"]
    failures = figures["failures"]
    claims: list[tuple[str, str]] = [
        ("sealed accuracy", dec(sealed["accuracy"])),
        ("sealed log loss", dec(sealed["log_loss"])),
        ("sealed AUC", dec(sealed["roc_auc"])),
        ("sealed constant log loss", dec(sealed["constant_log_loss"])),
        ("sealed model version", sealed["model_version"]),
        ("sealed n_train", str(sealed["n_train"])),
        ("sealed n_test", str(sealed["n_test"])),
        ("accuracy spread", dec(spread["accuracy_spread"])),
        ("accuracy stdev", dec(spread["accuracy_stdev"])),
        ("evaluation games", str(spread["evaluation_games"])),
        ("corpus games", str(figures["corpus"]["games"])),
        ("corpus seasons", str(figures["corpus"]["seasons"])),
        ("ablation full AUC", dec(ablation["full"]["auc"])),
        ("ablation full log loss", dec(ablation["full"]["log_loss"])),
        ("ablation full accuracy", dec(ablation["full"]["accuracy"])),
        ("early-season games", str(failures["early_season"]["n"])),
        ("early-season accuracy", dec(failures["early_season"]["accuracy"])),
        ("rest-of-season games", str(failures["rest_of_season"]["n"])),
        ("rest-of-season accuracy", dec(failures["rest_of_season"]["accuracy"])),
        ("home calls", str(failures["home_calls"]["n"])),
        ("home call accuracy", dec(failures["home_calls"]["accuracy"])),
        ("away calls", str(failures["away_calls"]["n"])),
        ("away call accuracy", dec(failures["away_calls"]["accuracy"])),
    ]

    # Fold-to-fold increments. Stated in the prose as evidence the trend is consistency rather
    # than a growth curve, so they have to be checked like any other figure.
    ordered = sorted(figures["folds"], key=lambda f: f["test_season"])
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        claims.append((
            f"increment {earlier['test_season']}->{later['test_season']}",
            signed(later["accuracy"] - earlier["accuracy"]),
        ))

    # The early-season gap -- the number F-141's null result turns on.
    claims.append((
        "early-season accuracy gap",
        dec(failures["rest_of_season"]["accuracy"] - failures["early_season"]["accuracy"]),
    ))

    for fold in figures["folds"]:
        season = fold["test_season"]
        claims += [
            (f"{season} accuracy", dec(fold["accuracy"])),
            (f"{season} log loss", dec(fold["log_loss"])),
            (f"{season} AUC", dec(fold["roc_auc"])),
            (f"{season} constant log loss", dec(fold["constant_log_loss"])),
            (f"{season} n_train", str(fold["n_train"])),
            (f"{season} n_test", str(fold["n_test"])),
            (f"{season} model version", fold["model_version"]),
        ]

    for name, entry in ablation["without"].items():
        claims += [
            (f"AUC without {name}", dec(entry["auc"])),
            (f"AUC cost of {name}", signed(entry["auc_cost"])),
            (f"log loss cost of {name}", signed(entry["log_loss_cost"])),
        ]

    for name, coefficient in sealed["coefficients"].items():
        claims.append((f"coefficient {name}", signed(coefficient)))
    claims.append(("intercept", signed(sealed["intercept"])))

    for season, delta in figures["delta_vs_phase_1"].items():
        claims += [
            (f"delta accuracy {season}", signed(delta["accuracy"])),
            (f"delta log loss {season}", signed(delta["log_loss"])),
            (f"delta AUC {season}", signed(delta["roc_auc"])),
        ]

    for band, entry in failures["by_band"].items():
        if entry["n"] == 0:
            continue
        claims += [
            (f"band {band} games", str(entry["n"])),
            (f"band {band} accuracy", dec(entry["accuracy"])),
            (f"band {band} mean confidence", dec(entry["mean_confidence"])),
        ]

    # Calibration bins, skipping the two the document explicitly omits as noise and says so.
    for entry in sealed["calibration"]:
        if entry["count"] < 50:
            continue
        claims += [
            (f"calibration [{entry['lower']},{entry['upper']}) count", str(entry["count"])),
            (f"calibration [{entry['lower']},{entry['upper']}) predicted",
             dec(entry["mean_predicted"], 3)),
            (f"calibration [{entry['lower']},{entry['upper']}) observed",
             dec(entry["observed_rate"], 3)),
        ]
    return claims


def test_every_figure_appears_in_the_prose(figures, document):
    """**The check.** A number in the document that disagrees with the run that produced it fails
    the build -- which is the failure T-010 exists to prevent and the one nobody notices, because a
    wrong number in a table reads exactly like a right one."""
    missing = [
        f"{what} = {text!r}" for what, text in _claims(figures) if text not in document
    ]
    assert missing == [], (
        f"{len(missing)} figure(s) from {FIGURES_PATH.name} do not appear in "
        f"{DOCUMENT_PATH.name}. Either the prose is stale or the figures were regenerated without "
        f"the document being updated: {missing}"
    )


def _rows_containing(document: str, needle: str) -> list[str]:
    """Markdown table rows containing `needle`."""
    return [
        line for line in document.splitlines()
        if line.lstrip().startswith("|") and needle in line
    ]


def test_each_fold_row_carries_its_own_figures(figures, document):
    """Row-level, because "appears somewhere" is not the same claim as "appears here".

    A figure swapped for another *genuine* figure from elsewhere in the document passes both the
    forward and reverse checks: the forward one still finds the original somewhere, and the reverse
    one finds the substitute accounted for. Verified by sabotage -- replacing the sealed AUC in §1
    with fold 2024's AUC passed everything until this test existed.

    So each fold's row is checked as a unit, anchored on its `model_version`, which is unique to it.
    """
    for fold in figures["folds"]:
        rows = _rows_containing(document, fold["model_version"])
        assert rows, f"no table row names model {fold['model_version']}"
        row = rows[0]
        for label, text in [
            ("accuracy", dec(fold["accuracy"])),
            ("log loss", dec(fold["log_loss"])),
            ("AUC", dec(fold["roc_auc"])),
            ("constant log loss", dec(fold["constant_log_loss"])),
            ("n_train", str(fold["n_train"])),
            ("n_test", str(fold["n_test"])),
        ]:
            assert text in row, (
                f"the row for model {fold['model_version']} does not carry its own {label} "
                f"({text}): {row.strip()}"
            )


def test_the_verdict_table_carries_the_sealed_figures(figures, document):
    """The three numbers a reader takes away, checked in the row each belongs to."""
    sealed = figures["sealed"]
    for label, text in [
        ("Accuracy", dec(sealed["accuracy"])),
        ("Log loss", dec(sealed["log_loss"])),
        ("AUC", dec(sealed["roc_auc"])),
    ]:
        rows = [r for r in _rows_containing(document, label) if "|" in r]
        assert any(text in r for r in rows), (
            f"§1's {label} row does not carry the sealed {label.lower()} ({text})"
        )


def test_the_row_check_would_notice_a_swap(figures, document):
    """Non-vacuity for the two tests above, using the exact mutation that defeated the others: put a
    genuine figure from one fold into another fold's row."""
    folds = sorted(figures["folds"], key=lambda f: f["test_season"])
    victim, donor = folds[-1], folds[0]
    tampered = document.replace(dec(victim["roc_auc"]), dec(donor["roc_auc"]))
    rows = _rows_containing(tampered, victim["model_version"])
    assert rows and dec(victim["roc_auc"]) not in rows[0], (
        "the mutation must actually remove the figure from its row, or this proves nothing"
    )


def test_the_check_would_notice_a_changed_figure(figures, document):
    """Non-vacuity. A checker that matched nothing would pass forever, and this one is a substring
    search over a 280-line document -- exactly the kind that can pass by accident."""
    tampered = json.loads(json.dumps(figures))
    tampered["sealed"]["accuracy"] = 0.9123
    missing = [f"{w} = {t!r}" for w, t in _claims(tampered) if t not in document]
    assert any("sealed accuracy" in m for m in missing), (
        "the checker must notice a sealed accuracy the document does not contain"
    )


def test_the_claims_list_is_not_quietly_emptied(figures):
    """The other half of non-vacuity: a claim list that shrank to nothing would pass the check above
    and assert nothing at all."""
    claims = _claims(figures)
    assert len(claims) >= 100, f"only {len(claims)} claims are checked; the list has been narrowed"
    assert len({text for _, text in claims}) >= 60, "too many claims collapse to the same string"


def test_the_document_states_its_own_provenance(document):
    """A figures file nobody can regenerate is a figures file nobody can check."""
    assert "report_v2.py" in document
    assert "--spend-the-sealed-fold" in document
    assert "D-043" in document, "the Postgres dependency has to be stated where it is relied on"


def test_phase_1_reproducibility_claim_is_amended(figures):
    """T-035's other half: §6 said the numbers regenerate 'from committed code and the
    content-pinned data release', which D-038/D-043 made untrue.

    Checked mechanically because a documentation amendment is exactly the kind of task that gets
    reported as done and then found undone a year later.
    """
    text = PHASE_1_PATH.read_text()
    assert "D-043" in text, "§6 must name the decision that made Postgres a prerequisite"
    assert "Amended 2026-09-06" in text, "the amendment must be dated and visible, not silent"
    assert "MODELING-V2-RESULT.md" in text, "§6 must point a reader at the current result"
    assert "content-pinned data release" in text, (
        "the superseded sentence must be quoted rather than deleted -- a reader who saw the old "
        "claim needs to find out what happened to it"
    )


def test_the_sealed_log_agrees_with_the_reported_headline(figures):
    """Every sealed evaluation is logged, and the log is only useful if the document agrees with it.

    Two lines carrying the same `model_version` are a re-derivation; two carrying different versions
    are a second attempt. This asserts the reported headline is the one every entry recorded.
    """
    log = REPO / "docs" / "results" / "t030-sealed-runs.jsonl"
    entries = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert entries, "the sealed-fold log is empty"
    versions = {e["model_version"] for e in entries}
    assert versions == {figures["sealed"]["model_version"]}, (
        f"the log holds versions {versions} but the document reports "
        f"{figures['sealed']['model_version']} -- a second attempt was made and not accounted for"
    )
    for entry in entries:
        assert entry["accuracy"] == pytest.approx(figures["sealed"]["accuracy"])


def test_no_stray_four_decimal_figure_in_the_document_is_unaccounted_for(figures, document):
    """The reverse direction: numbers in the prose that no figure explains.

    Weaker than the forward check by necessity -- the document legitimately quotes Phase 1's figures,
    thresholds like .0020, and noise estimates -- so those are listed. What it catches is a *new*
    four-decimal number appearing in the prose with nothing behind it, which is what "rounded from
    memory" looks like.
    """
    claims = _claims(figures)
    accounted = {text for _, text in claims}
    # A signed figure may legitimately appear unsigned in prose -- "the magnitudes .1510 against
    # .1334" is a statement about size, not direction, and the signed form is checked in the tables.
    accounted |= {text.lstrip("+−-") for _, text in claims if text[0] in "+−-"}
    # Quoted from PHASE-1-RESULT.md §1-§2, and marked as quoted in §6.
    accounted |= {dec(v) for fold in figures["phase_1"]["folds"].values() for v in fold.values()}
    accounted |= {dec(figures["phase_1"]["accuracy_stdev"]), dec(figures["phase_1"]["accuracy_spread"])}
    # Committed constants, not measurements: the base rate, the materiality threshold, F-141's
    # measured deltas, and the ship bar.
    accounted |= {".55534", ".0020", "+.0009", ".62", ".7260", ".6075", ".6686"}

    found = set(re.findall(r"[+−-]?\.\d{4}\b", document))
    unaccounted = sorted(found - accounted)
    assert unaccounted == [], (
        f"four-decimal figures in the prose with no entry in {FIGURES_PATH.name}: {unaccounted}. "
        "Either add them to the figures file or remove them -- a number nobody can regenerate is "
        "the thing this check exists to prevent."
    )
