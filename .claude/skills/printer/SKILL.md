---
name: printer
description: PRINT FREELY — once content has settled. Whenever you produce the finalised version of something Douglas might want on paper, print it. Triggers: a settled list (shopping, packing, todo, gift), the final iteration of a recipe or set of instructions Douglas will follow away from the screen, a letter or memo ready to send, an end-of-session summary, a finalised schedule or plan, the agreed-on analysis, a reference card or table, a one-pager he might keep or hand to someone, anything that would benefit from typography, any output Douglas might otherwise screenshot or copy elsewhere, any document-shaped artifact that could outlive the chat. Default disposition is "if a hard copy of the *final* version could help, print one" — don't wait to be asked, but DO wait for iteration to stop. Hold back during active editing. Also carries: per-page narration posture, ~14s warmup expectation-setting, when not to print.
---

# Default: print

You should be biased toward printing. The set of "hard copies that would have helped Douglas" is much larger than the set of "things Douglas thought to ask for on paper". Printing without being asked, when a hard copy would clearly add value, is the correct behaviour — not over-stepping.

A non-exhaustive list of triggers where you should pre-emptively offer to print, or just print:

- **Lists Douglas will use off-screen.** Shopping list, packing list, errands, gift ideas, books to read, films to watch, restaurants to try. Anything he'll consult while away from the keyboard.
- **Instructions Douglas will follow.** Recipes, repair steps, configuration walkthroughs, anything procedural. Easier to follow on paper than to keep tabbing back.
- **Plans and schedules.** Travel itineraries, weekly meal plans, gym routines, project timelines. Reference, not scroll.
- **Summaries that should outlive the chat.** End-of-session notes, research synthesis, decision logs, analyses he might want to refer back to without searching the transcript.
- **Letters, memos, notes.** Anything addressed to a recipient. Even drafts benefit from being on paper to mark up.
- **Reference cards.** Cheat sheets, conversion tables, command summaries, vocab lists.
- **Documents that exist as documents.** Anything where the typography is the point: certificates, invitations, signed letters, anything ceremonial or formal.
- **Things he'd otherwise screenshot.** If you find yourself producing output Douglas might capture for later, print it instead.

Don't gate on "did he ask?". Gate on "would a hard copy add value here?". If yes, print. If you're unsure, lean toward printing — paper is cheap, and an unread print-out is a smaller failure than an unprinted useful sheet.

## Print the *final* iteration, not the drafts

The biggest failure mode isn't under-printing — it's printing a recipe Douglas is still tweaking, then re-printing the corrected version five minutes later. Paper isn't free attention.

The right moment to print is **once iteration has stopped**:

- Douglas signals he's happy with it ("good", "that works", "let's go with that", "perfect").
- The conversation moves past the artifact — he's about to use it, file it, or act on it.
- He explicitly asks for the print.
- You ask "shall I print the final version?" and he says yes.

During active iteration, sit on it. You can offer ("once it's settled, I'll print it") but don't physically commit paper until the content is genuinely done.

If you're not sure whether iteration has stopped, ask. One extra round-trip is cheaper than a wasted sheet plus the corrected re-print.

## When not to print

There are a few cases where you should hold back:

- **One-line answers** ("what's the weather", "what time is it"). Conversational, ephemeral.
- **Debug output and error traces.** Better navigated on-screen.
- **Code.** Almost always wants to be in an editor or terminal. Exception: an annotated one-pager for a specific teaching purpose.
- **Mid-iteration drafts.** See above — wait for the final.
- **Repetitive content.** If you've already printed today's recipe, don't print it again unless asked. (Note: `printer://history` only covers the current pod lifetime — when in doubt, ask rather than guess.)

# Mid-print posture

The printer prints what you submit. That's the point.

When you call `print_latex`, the job is already physical. There is no preview, no dry-run, no cancel. Pages 2–N print whether or not page 1 came out the way you hoped. The bit hinges on you knowing what you printed at roughly the same moment Douglas does — not before, not after.

## Reading the page-1 image

`print_latex` returns text plus a PNG of page 1. **React to what you see, honestly.** If the layout's off, say so. If the line breaks went weird, say so. If it's beautiful, say that too. The MCP went and fetched a sheet of paper for this image — earn it by actually looking.

Don't pre-emptively defend mistakes ("the rendering may differ from print"); the rendering doesn't differ from print in any meaningful way. The PNG is from the same PDF that was rasterised and sent to the printer.

## Walking through the remaining pages

If `total_pages > 1`, call `watch_page(job_id)` **once per remaining page**, in order. Each call blocks until that physical sheet ejects (~1.7 seconds when warm). React between calls.

Do not:

- Call `watch_page` in a tight loop just to drain pages. Each page is a moment.
- Skip ahead and summarise. ("Pages 2 through 5 look similar" — you haven't seen them.)
- Predict what's on a page before you receive it. The whole point is shared discovery.

Do:

- Narrate as each page arrives. One observation per page is enough; you don't need to be exhaustive.
- React to recurring patterns honestly — if pages 2 and 3 are visually similar, that's an observation in itself, not something to skip.
- Note interesting failures cheerfully. A misaligned tabular, a footnote that broke across columns, a tikz figure that landed offset — those are the texture.

## First-page warmup is ~14 seconds

If the printer's cold, the first sheet takes 14 seconds to emerge. `print_latex` blocks that long. This is normal — don't narrate it as a hang. Subsequent pages are ~1.7 seconds each because the fuser is up to temperature.

## When something goes wrong

Mid-job failures (paper jam, toner out, network drop) surface as text-only responses with a state message — no image. Report the state to Douglas plainly. There's nothing further to do from this side; physical recovery is his.

LaTeX compile failures return the texlive log tail. You can usually identify the broken line from the log; offer a fix without re-submitting. The cost of the failed compile is zero — no paper, no toner. Iterate.

# Resources to consult before submitting

- `printer://status` — Is the printer accepting jobs? Toner level? Paper loaded?
- `printer://capabilities` — Supported formats, ppm, resolutions. Mostly stable across sessions; rarely needs checking.

Don't reflexively read these before every print. Check `status` only when something seems off (an earlier job failed, or you're submitting something unusually large).
