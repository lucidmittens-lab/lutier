# Bug report: dock-widget tabify drop corrupts layout instead of forming tabs

**Filed:** 2026-08-07, during a cross-app comparison session investigating a similar bug in
a sibling project (CODEier). Not yet confirmed by a human-driven repro on real
hardware — everything below was observed via simulated mouse/keyboard automation
(`cliclick`), which itself may have contributed noise. Treat as a lead, not a
confirmed diagnosis.

## Symptom

Holding Shift and dragging one docked panel's title bar onto a sibling panel already
stacked in the same column (e.g. dragging Stack Memories onto CDL Library) does not
form a tab group. Instead, the dropped panel ends up occupying/overlapping the same
screen region as the target, rather than a clean split or a working tab strip.

Specifically observed: after the drop, the target panel's title (e.g. "CDL Library")
stops being painted anywhere on screen -- but its accessibility element still exists at
the expected position and size, and its Workspace-menu visibility checkbox stays
checked. Floating the panel that's covering it and moving it aside reveals the target
was there the whole time, still fully intact. So this reads as a **paint/layout
overlap bug**, not a real hide or destroy -- the dock widget survives, it's just not
given its own non-overlapping region during the drop.

## Suspected trigger: scaled/emulated display resolution

This was first noticed while the display was set to an emulated 1440x900 resolution
(the mode used when a second display is plugged in). Switching to a native-feeling
1680x1050 resolution was tried as a fix, and the repro steps were re-run -- the same
overlap symptom appeared to recur, but that re-test was itself automated (not
human-driven) and wasn't fully controlled (window bounds were being re-established at
the same time), so it should not be taken as confirmation the resolution wasn't the
cause. This needs a clean, manual, human-driven re-test at a real (non-scaled)
resolution before concluding anything about a resolution/scaling relationship one way
or the other.

## Repro steps (as tested)

1. Launch LUTier, default layout (Browser / Image Library / LUT Library / CDL Library
   / Stack Memories stacked in the left column).
2. Hold Shift.
3. Drag Stack Memories' title bar onto CDL Library (tried both near the shared
   border and squarely in CDL Library's body -- same result either way).
4. Release.

**Expected:** a real, clickable tab strip forms, showing both panel names.
**Observed:** CDL Library's title stops rendering; Stack Memories occupies the region
Formerly showing both; CDL Library's dock widget is still alive (see above) but not
visibly distinguishable from Stack Memories' cell.

## What's NOT yet known

- Whether this reproduces on a real, non-scaled display with a human actually
  performing the drag (highest-priority thing to check first).
- Whether it reproduces with a fresh/default LUTier session, or only after prior
  window-arrangement changes in the same run.
- Whether `tabifiedDockWidgets()` actually reports the two docks as grouped
  internally despite the paint issue (LUTier doesn't currently have a diagnostic for
  this the way CODEier's `_update_tab_diagnostic_title` does -- might be worth
  porting that over temporarily to check).

## Context

A sibling project (CODEier, a video player app built with the same panel/docking
conventions as LUTier) has an extensively-documented version of what looks like the
same class of bug (tabbing never producing a working tab strip), chased across 7+
rounds without a fix. This session set out to test whether LUTier -- the reference
CODEier's docking code was repeatedly matched against -- has the same problem, since a
prior round's claim that "LUTier works fine" was never actually confirmed with a human
on a real display, only inferred from documentation. Initial testing today did appear
to reproduce a similar-looking failure in LUTier too, but under the display-scaling
conditions described above, so it's not yet a clean confirmation either way. Worth
revisiting with a controlled, human-driven test.
